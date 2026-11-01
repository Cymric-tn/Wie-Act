"""
HTTP + WebSocket front for the engine, for the app side (Node). One process per API key, on 127.0.0.1 only.

    ENGINE_SERVICE_TOKEN=... python -m services.engine_server [--port N] [--fake [--fake-mode slow]]

  POST   /run               {task, inputs, context, priority} -> Result          (JSON, or multipart with an `image`)
  WS     /run/stream        first message = the same body; then one frame per Event, then the Result
  POST   /materials         multipart: file + course_id ... -> ingest_file(add=True)
  DELETE /materials/{id}    the id the upload returned
  POST   /cache/invalidate  {request_id} -> {removed}
  GET    /review/flagged, POST /review/flagged/{request_id}, GET /review/content-gaps, GET /metrics/summary
                            the professor dashboard's data (filtered by class_id / course_id / since)
  GET    /health            engine version, breaker, window capacity, queue depth

Every route needs the header X-Engine-Token (constant-time compare with ENGINE_SERVICE_TOKEN). The service never logs
a body or a token. A run is never tied to its connection: if the client goes away (HTTP or WebSocket) the run finishes
and fills the cache. --fake serves FakeEngine behind the identical API.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import os
import shutil
import sys
import tempfile
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from engine.result import Event, Result
from gatekeeper.queue import Priority
from metrics.review import ACTIONS, parse_since
from metrics.summary import summarize
from rag.ingest import IngestError

log = logging.getLogger("engine_service")

HOST = "127.0.0.1"                      # not configurable: the service is for the app on the same machine
TOKEN_HEADER = "X-Engine-Token"
DEFAULT_PORT = 8765
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
FIRST_MESSAGE_TIMEOUT_S = 10.0          # a stream client must send its body this fast
SHUTDOWN_GRACE_S = 10.0                 # runs still going at shutdown get this long to reach the cache


# ====================================================================== body
class RunBody(BaseModel):
    task: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    priority: Priority = Priority.INTERACTIVE

    @field_validator("priority", mode="before")
    @classmethod
    def _case(cls, v):
        return v.upper() if isinstance(v, str) else v


class DecisionBody(BaseModel):
    action: str

    @field_validator("action")
    @classmethod
    def _action(cls, v):
        if v not in ACTIONS:
            raise ValueError(f"action must be one of {list(ACTIONS)}")
        return v


MAX_PAGE = 200


class InvalidateBody(BaseModel):
    request_id: str = Field(min_length=1)


def material_id(course_id: str, owner: str, file: str) -> str:
    """Opaque and stateless: what DELETE /materials/{id} needs to find the chunks again."""
    return base64.urlsafe_b64encode(json.dumps([course_id, owner, file], ensure_ascii=False).encode()).decode().rstrip("=")


def parse_material_id(mid: str) -> tuple[str, str, str]:
    try:
        course_id, owner, file = json.loads(base64.urlsafe_b64decode(mid + "=" * (-len(mid) % 4)))
        if not all(isinstance(x, str) for x in (course_id, owner, file)):
            raise ValueError
        return course_id, owner, file
    except Exception:
        raise HTTPException(404, detail="unknown material id") from None


def _error(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse({"error": error, "detail": detail}, status_code=status)


def _json(result: Result) -> dict:
    return result.model_dump(mode="json")


# ====================================================================== app
def create_app(engine=None, *, token: str, engine_factory=None, fake: bool = False, fake_mode: str | None = None,
               version: str | None = None, max_upload_bytes: int = MAX_UPLOAD_BYTES) -> FastAPI:
    """engine: an Engine or a FakeEngine (same run(); the caller closes it). Or engine_factory: called at startup,
    inside the server's event loop, and closed at shutdown (the engine owns asyncio objects that must live there)."""
    if (engine is None) == (engine_factory is None):
        raise ValueError("give an engine or an engine_factory")
    if not token:
        raise ValueError("ENGINE_SERVICE_TOKEN must be set")
    if version is None:
        from engine import __version__ as version
    want = token.encode()
    runs: set[asyncio.Task] = set()                 # runs outlive their connection
    box: dict[str, Any] = {"engine": engine}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if engine_factory is not None:
            box["engine"] = engine_factory()
        yield
        if runs:                                    # let a learner's answer reach the cache before we go
            _, pending = await asyncio.wait(set(runs), timeout=SHUTDOWN_GRACE_S)
            for t in pending:
                t.cancel()
        if engine_factory is not None:
            await box["engine"].aclose()

    app = FastAPI(title="EdNova engine service", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.state.runs = runs

    def authorised(given: str | None) -> bool:
        return given is not None and hmac.compare_digest(given.encode("utf-8", "replace"), want)

    def require_token(x_engine_token: str | None = Header(None, alias=TOKEN_HEADER)) -> None:
        if not authorised(x_engine_token):
            raise HTTPException(401, detail="missing or wrong " + TOKEN_HEADER)

    def start(body: RunBody, on_event=None) -> asyncio.Task:
        task = asyncio.ensure_future(box["engine"].run(body.task, body.inputs, body.context, body.priority, on_event=on_event))
        runs.add(task)

        def done(t: asyncio.Task) -> None:
            runs.discard(t)
            if not t.cancelled() and t.exception() is not None and not isinstance(t.exception(), ValueError):
                log.error("run failed: %s", type(t.exception()).__name__)       # the type only: no text, no inputs
        task.add_done_callback(done)
        return task

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException):
        return _error(exc.status_code, "unauthorized" if exc.status_code == 401 else "error", str(exc.detail))

    async def read_body(request: Request) -> RunBody:
        ctype = request.headers.get("content-type", "")
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_upload_bytes:
            raise HTTPException(413, detail="request too large")
        try:
            if ctype.startswith("multipart/form-data"):
                form = await request.form(max_part_size=max_upload_bytes)
                raw = {k: form.get(k) for k in ("task", "priority") if form.get(k)}
                for k in ("inputs", "context"):
                    if form.get(k):
                        raw[k] = json.loads(form[k])
                body = RunBody(**raw)
                image = form.get("image")
                if image is not None:
                    if body.task != "ocr_transcribe":
                        raise HTTPException(422, detail="an uploaded image is only for ocr_transcribe")
                    data = await image.read()
                    if len(data) > max_upload_bytes:
                        raise HTTPException(413, detail="image too large")
                    body.inputs["image"] = data
                return body
            raw_bytes = await request.body()
            if len(raw_bytes) > max_upload_bytes:
                raise HTTPException(413, detail="request too large")
            return RunBody(**json.loads(raw_bytes or b"{}"))
        except (ValidationError, ValueError, TypeError) as e:
            raise HTTPException(422, detail=_why(e)) from None

    @app.post("/run", dependencies=[Depends(require_token)])
    async def run(request: Request):
        body = await read_body(request)
        try:
            result = await asyncio.shield(start(body))       # the run itself is never cancelled with this request
        except ValueError as e:                              # unknown task, missing input
            return _error(422, "invalid_request", str(e))
        return _json(result)

    @app.websocket("/run/stream")
    async def stream(ws: WebSocket):
        if not authorised(ws.headers.get(TOKEN_HEADER.lower())):
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            text = await asyncio.wait_for(ws.receive_text(), FIRST_MESSAGE_TIMEOUT_S)
            body = RunBody(**json.loads(text))
        except (asyncio.TimeoutError, ValidationError, ValueError, TypeError) as e:
            detail = "no body received" if isinstance(e, asyncio.TimeoutError) else _why(e)
            await _send(ws, {"type": "error", "error": "invalid_request", "detail": detail})
            await _close(ws, 1003)
            return
        except WebSocketDisconnect:
            return
        events: asyncio.Queue = asyncio.Queue()
        task = start(body, on_event=events.put_nowait)
        watch = asyncio.ensure_future(ws.receive())          # only to notice the client leaving
        try:
            while True:
                get = asyncio.ensure_future(events.get())
                done, _ = await asyncio.wait({get, task, watch}, return_when=asyncio.FIRST_COMPLETED)
                if get in done:
                    if not await _send(ws, {"type": "event", "event": _event(get.result())}):
                        return
                    continue
                get.cancel()
                if watch in done:
                    if watch.result().get("type") == "websocket.disconnect":
                        return                                # the run goes on into the cache
                    watch = asyncio.ensure_future(ws.receive())
                    continue
                while not events.empty():
                    if not await _send(ws, {"type": "event", "event": _event(events.get_nowait())}):
                        return
                exc = task.exception()
                if isinstance(exc, ValueError):
                    await _send(ws, {"type": "error", "error": "invalid_request", "detail": str(exc)})
                    await _close(ws, 1003)
                elif exc is not None:
                    await _send(ws, {"type": "error", "error": "internal", "detail": "the run failed"})
                    await _close(ws, 1011)
                else:
                    await _send(ws, {"type": "result", "result": _json(task.result())})
                    await _close(ws, 1000)
                return
        finally:
            watch.cancel()

    @app.post("/materials", dependencies=[Depends(require_token)])
    async def add_material(request: Request):
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            return _error(422, "invalid_request", "send multipart/form-data with a `file` part")
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_upload_bytes:
            return _error(413, "error", "request too large")
        form = await request.form(max_part_size=max_upload_bytes)
        upload = form.get("file")
        course_id = str(form.get("course_id") or "").strip()
        if upload is None or not getattr(upload, "filename", None) or not course_id:
            return _error(422, "invalid_request", "`file` and `course_id` are required")
        name = os.path.basename(upload.filename.replace("\\", "/"))
        data = await upload.read()
        if len(data) > max_upload_bytes:
            return _error(413, "error", "file too large")
        kw = {k: str(form[k]) for k in ("unit_id", "owner", "visibility", "source", "class_id", "language") if form.get(k)}
        language = kw.pop("language", None)
        tmp = tempfile.mkdtemp(prefix="ednova-upload-")
        try:
            path = os.path.join(tmp, name)                  # the chunk ids and `source` carry the file's own name
            with open(path, "wb") as fh:
                fh.write(data)
            try:
                res = await box["engine"].ingest_file(path, course_id, add=True, ocr=_flag(form.get("ocr"), True),
                                                      **({"language": language} if language else {}), **kw)
            except IngestError as e:
                return _error(422, "ingest_error", str(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        owner = kw.get("owner", "")
        return {"material_id": material_id(course_id, owner, name), "file": name, "course_id": course_id,
                "chunks_added": len(res.chunks), "pages": res.pages, "warnings": list(res.warnings),
                "needs_ocr": list(res.needs_ocr), "ocr": dict(res.ocr),
                "chunks": [{"chunk_id": c.chunk_id, "title": c.title, "concept": c.concept,
                            "confidence": round(c.confidence, 2), "page": c.page} for c in res.chunks],
                "low_confidence": [c.chunk_id for c in res.low_confidence]}

    @app.delete("/materials/{mid}", dependencies=[Depends(require_token)])
    async def delete_material(mid: str):
        course_id, owner, file = parse_material_id(mid)
        removed = await box["engine"].remove_material(source=file, course_id=course_id, owner=owner)
        return {"material_id": mid, "removed": removed}

    @app.post("/cache/invalidate", dependencies=[Depends(require_token)])
    async def invalidate(request: Request):
        try:
            body = InvalidateBody(**json.loads(await request.body() or b"{}"))
        except (ValidationError, ValueError, TypeError) as e:
            return _error(422, "invalid_request", _why(e))
        return {"request_id": body.request_id, "removed": box["engine"].cache.invalidate(body.request_id)}

    # ---------------------------------------------------------------- professor dashboard (data only)
    def _since(value: str | None) -> float | None:
        try:
            return parse_since(value)
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from None

    @app.get("/review/flagged", dependencies=[Depends(require_token)])
    async def review_flagged(class_id: str | None = None, course_id: str | None = None, since: str | None = None,
                             limit: int = 50, offset: int = 0):
        if not 1 <= limit <= MAX_PAGE or offset < 0:
            return _error(422, "invalid_request", f"limit must be 1..{MAX_PAGE} and offset >= 0")
        return box["engine"].review.flagged(class_id, course_id, _since(since), limit, offset)

    @app.post("/review/flagged/{request_id}", dependencies=[Depends(require_token)])
    async def review_decide(request_id: str, request: Request):
        try:
            body = DecisionBody(**json.loads(await request.body() or b"{}"))
        except (ValidationError, ValueError, TypeError) as e:
            return _error(422, "invalid_request", _why(e))
        engine_ = box["engine"]
        try:
            decision = engine_.review.decide(request_id, body.action)
        except KeyError:
            return _error(404, "unknown_item", "no flagged item with this request_id")
        except ValueError as e:
            return _error(409, "already_decided", str(e))
        removed = 0
        if body.action == "reject" and not decision["already"]:
            removed = engine_.cache.invalidate(request_id)       # the answer must not be served again
        return {**decision, "removed": removed}

    @app.get("/review/content-gaps", dependencies=[Depends(require_token)])
    async def review_gaps(class_id: str | None = None, course_id: str | None = None, since: str | None = None):
        return box["engine"].review.gaps(class_id, course_id, _since(since))

    @app.get("/metrics/summary", dependencies=[Depends(require_token)])
    async def metrics_summary(since: str | None = None, class_id: str | None = None, course_id: str | None = None):
        return summarize(box["engine"].metrics_records(), _since(since), class_id, course_id)

    @app.get("/health", dependencies=[Depends(require_token)])
    async def health():
        return {"status": "ok", "engine_version": version, "fake": fake,
                **({"fake_mode": fake_mode} if fake else {}), **box["engine"].status()}

    return app


def _event(e: Event) -> dict:
    return e.model_dump(mode="json")


def _flag(value: Any, default: bool) -> bool:
    return default if value is None else str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _why(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(str(p) for p in x['loc'])}: {x['msg']}" for x in e.errors())
    return str(e) or type(e).__name__


async def _send(ws: WebSocket, frame: dict) -> bool:
    try:
        await ws.send_text(json.dumps(frame, ensure_ascii=False))
        return True
    except (WebSocketDisconnect, RuntimeError):           # the client left: the run is not our concern any more
        return False


async def _close(ws: WebSocket, code: int) -> None:
    with contextlib.suppress(RuntimeError, WebSocketDisconnect):
        await ws.close(code=code)


# ====================================================================== one process per key
class InstanceLock:
    """A pid file. A second start finds a live pid and refuses; a dead pid (crash) is taken over."""

    def __init__(self, path: str):
        self.path = path

    @staticmethod
    def _alive(pid: int) -> bool:
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        with contextlib.suppress(OSError):                 # a recycled pid belongs to some other program
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                return b"engine_server" in fh.read()
        return True

    def holder(self) -> int | None:
        try:
            pid = int(open(self.path).read().split()[0])
        except (OSError, ValueError, IndexError):
            return None
        return pid if self._alive(pid) else None

    def acquire(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pid = self.holder()
                if pid is not None:
                    raise SystemExit(f"another engine service (pid {pid}) already runs for this key; "
                                     f"one process per key ({self.path})")
                with contextlib.suppress(FileNotFoundError):
                    os.remove(self.path)                    # stale: its process is gone
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(f"{os.getpid()}\n")
            return
        raise SystemExit(f"could not take the instance lock {self.path}")

    def release(self) -> None:
        if self.holder() == os.getpid():
            with contextlib.suppress(FileNotFoundError):
                os.remove(self.path)


def lock_path(api_key: str, directory: str | None = None) -> str:
    """One lock per key. The name carries a short domain-separated digest, never the key."""
    tag = hashlib.sha256(b"ednova-engine-service-lock:" + api_key.encode()).hexdigest()[:10]
    return os.path.join(directory or os.environ.get("ENGINE_SERVICE_LOCK_DIR", "runtime"), f"engine-{tag}.lock")


# ====================================================================== entry point
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("ENGINE_SERVICE_PORT", DEFAULT_PORT)))
    ap.add_argument("--fake", action="store_true", help="serve FakeEngine (no provider, no key) behind the same API")
    ap.add_argument("--fake-mode", default="normal", choices=("normal", "slow", "degraded"))
    ap.add_argument("--fake-speed", type=float, default=0.2, help="1.0 = the real timings, 0 = instant")
    a = ap.parse_args(argv)
    token = os.environ.get("ENGINE_SERVICE_TOKEN", "")
    if not token:
        print("ENGINE_SERVICE_TOKEN is not set", file=sys.stderr)
        return 2
    import uvicorn
    lock = None
    if a.fake:
        from engine.fake import FakeEngine
        factory = lambda: FakeEngine(a.fake_mode, speed=a.fake_speed)
    else:
        from config import load_settings
        from engine import Engine
        settings = load_settings()
        lock = InstanceLock(lock_path(settings.api_key))
        try:
            lock.acquire()
        except SystemExit as e:
            print(e, file=sys.stderr)
            return 3
        factory = lambda: Engine.create(settings)
    app = create_app(engine_factory=factory, token=token, fake=a.fake, fake_mode=a.fake_mode if a.fake else None)
    try:
        uvicorn.run(app, host=HOST, port=a.port, log_level="info")
    finally:
        if lock:
            lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
