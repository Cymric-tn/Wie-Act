"""EdNova engine bridge: the one service the web app talks to.

Hosts ONE EdNova LLM engine per process (the engine's limiter, queue, breaker and cache live in memory, so the guide asks
for one engine process per API key) and serves it to the Node server over HTTP on localhost.

    ENGINE_SERVICE_TOKEN=... python bridge.py                      # the real engine, from the repo root
    ENGINE_SERVICE_TOKEN=... python bridge.py --fake --fake-mode slow   # the engine's own FakeEngine (no model, no key)
    python bridge.py --engine-dir /path/to/wie --port 8765

Standard library only; the engine brings its own requirements.txt. The bridge binds to 127.0.0.1, only answers a Host of
127.0.0.1 / localhost, and every route needs the header X-Engine-Token (= ENGINE_SERVICE_TOKEN, the same value the Node
server reads). A second bridge, or the engine's own services/engine_server.py, for the same API key refuses to start
(exit status 3): they share one lock file per key. Never run both.

Endpoints (JSON; /run and /generate_page stream NDJSON):
    GET    /health                 {ok, engine, fake, version, tasks, status}
    POST   /run                    {task, inputs, context, priority}
                                   -> {"type":"event","event":{...}}\\n ... {"type":"result","result":{...}}\\n
    POST   /generate_page          {focus_point, plan?, context} -> events, then {"type":"result","result":{page}}
    POST   /follow_up              {classification} -> {task, reason, overridden}
    GET    /exercises/<id>         {exercise}   (full: solution, hints, common_mistakes)
    GET    /courses/<id>           {course}
    POST   /materials              {id, course_id, path, visibility, class_id, learner_id, source, ...}
                                   -> {source, chunks:[{id,title,concept,confidence,low_confidence,page}], needs_ocr, ...}
    DELETE /materials/<id>?course_id=   removes every chunk of that upload
    PATCH  /materials/chunks/<chunk_id> {concept}   a professor confirms or fixes a concept tag
    POST   /cache/invalidate       {request_id} -> {removed}
    GET    /metrics/summary?since= the engine's own numbers (runs, cache, degraded, latency, 429s)
"""

import argparse
import asyncio
import dataclasses
import enum
import hmac
import inspect
import json
import os
import queue
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

MAX_BODY = 30_000_000
CALL_TIMEOUT_S = 300
TOKEN_HEADER = "X-Engine-Token"
SOURCES = {"professor": "professor", "learner": "learner", "course": "course"}   # anything else (the app's "upload") = learner


def jsonable(obj):
    """Result, Event, pydantic models, dataclasses and enums -> plain JSON values."""
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if isinstance(obj, enum.Enum):
        return obj.name if not isinstance(obj.value, (str, int, float)) else obj.value
    if hasattr(obj, "model_dump"):
        return jsonable(obj.model_dump())
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: jsonable(v) for k, v in vars(obj).items() if not k.startswith("_")}
    return str(obj)


class EngineHost:
    """Owns the engine and the asyncio loop it runs on (one background thread).

    engine_factory=...: called on the engine's own loop (tests: an Engine over a scripted provider); otherwise the engine
    is created from engine_dir (the repo root: the engine reads .env, data/ and prompts/ relative to it)."""

    def __init__(self, engine_dir=None, fake=False, fake_mode="normal", engine=None, lock=True, engine_factory=None):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="engine-loop", daemon=True).start()
        self._lock = None
        self.fake = fake
        if engine is not None or engine_factory is not None:
            self.engine = engine if engine is not None else self.call(engine_factory)
            self.engine_dir = None
        else:
            self.engine_dir = os.path.abspath(engine_dir)
            if not os.path.isdir(os.path.join(self.engine_dir, "engine")):
                raise SystemExit(f"No engine package found in {self.engine_dir} (expected {self.engine_dir}/engine/). "
                                 "Pass --engine-dir.")
            sys.path.insert(0, self.engine_dir)
            os.chdir(self.engine_dir)
            if not fake and lock:
                self._take_lock()
            self.engine = self.call(self._create, fake, fake_mode)
        import engine as engine_pkg  # noqa: E402  (path set above)
        self.version = getattr(engine_pkg, "__version__", None)
        from engine import Priority  # noqa: E402
        self.Priority = Priority

    def _take_lock(self) -> None:
        from config import load_settings
        from services.instance_lock import InstanceLock, lock_path
        self._lock = InstanceLock(lock_path(load_settings().api_key))
        self._lock.acquire()                      # SystemExit when another engine process holds this key

    async def _create(self, fake, fake_mode):
        if fake:
            from engine.fake import FakeEngine
            return FakeEngine(mode=fake_mode)
        from engine import Engine
        return Engine.create()

    def close(self):
        aclose = getattr(self.engine, "aclose", None)
        if callable(aclose) and self.engine_dir:
            try:
                self.call(aclose, timeout=10)
            except Exception:
                pass
        if self._lock:
            self._lock.release()

    def call(self, fn, *args, timeout=CALL_TIMEOUT_S, **kwargs):
        """Runs fn on the engine loop (sync or async) and waits for its value."""
        async def go():
            value = fn(*args, **kwargs)
            if inspect.isawaitable(value):
                value = await value
            return value
        return asyncio.run_coroutine_threadsafe(go(), self.loop).result(timeout)

    def priority(self, name):
        try:
            return self.Priority[str(name or "INTERACTIVE").upper()]
        except KeyError:
            return self.Priority["INTERACTIVE"]

    def tasks(self):
        registry = getattr(self.engine, "registry", None)
        if registry is not None:
            return sorted(s.name for s in registry)
        return sorted(getattr(self.engine, "OUTCOMES", {}) or []) or None

    # ----- a streaming call: events as they happen, then the result -----
    def stream(self, make_coro, write_line):
        """make_coro(on_event) -> the coroutine to run on the engine loop. Writes {"type":"event"} lines, then one
        {"type":"result"}. A ValueError (unknown task, missing input) before anything was written propagates (HTTP 400)."""
        events = queue.Queue()
        future = asyncio.run_coroutine_threadsafe(make_coro(lambda ev: events.put(jsonable(ev))), self.loop)
        future.add_done_callback(lambda _f: events.put(None))
        wrote_any = False
        while True:
            payload = events.get()
            if payload is None:
                break
            write_line({"type": "event", "event": payload})
            wrote_any = True
        try:
            result = future.result()
        except ValueError as error:
            if not wrote_any:
                raise
            write_line({"type": "error", "kind": "value", "error": str(error)})
            return
        write_line({"type": "result", "result": jsonable(result)})

    def run(self, task, inputs, context, priority, write_line):
        prio = self.priority(priority)
        self.stream(lambda on_event: self.engine.run(task, inputs or {}, context or {}, prio, on_event=on_event), write_line)

    def generate_page(self, body, write_line):
        focus = body.get("focus_point")
        if not isinstance(focus, dict) or not focus.get("concept_id"):
            raise ValueError("focus_point with a concept_id is required")
        self.stream(lambda on_event: self.engine.generate_page(body.get("plan") or {}, focus, body.get("context") or {},
                                                               on_event=on_event), write_line)

    # ----- helpers around the feature-layer calls -----
    def follow_up(self, classification):
        return jsonable(self.call(self.engine.router.follow_up, classification or {}))

    def exercise(self, exercise_id):
        bank = getattr(self.engine, "exercises", None)
        return jsonable(bank.get(exercise_id)) if bank is not None else None

    def course(self, course_id):
        course = (getattr(self.engine, "courses", None) or {}).get(course_id)
        return jsonable(course) if course is not None else None

    def add_material(self, body):
        """The app's upload -> Engine.ingest_file(add=True): the chunks join the live index, tagged with the uploader as
        owner. `source` professor stays professor (boosted in answers), anything else is a learner's upload."""
        path = body.get("path")
        if not path or not os.path.exists(path):
            raise ValueError("path of the stored file is required")
        source = SOURCES.get(str(body.get("source") or "").lower(), "learner")
        owner = str(body.get("learner_id") or body.get("owner") or "")
        res = self.call(self.engine.ingest_file, path, body.get("course_id"), body.get("unit_id") or "", owner,
                        body.get("visibility") or "public", source, class_id=body.get("class_id") or "",
                        add=True, ocr=body.get("ocr", True), **({"language": body["language"]} if body.get("language") else {}))
        low = {c.chunk_id for c in getattr(res, "low_confidence", []) or []}
        chunks = [{"id": c.chunk_id, "title": c.title, "section": c.section, "concept": c.concept or None,
                   "confidence": c.confidence, "low_confidence": c.chunk_id in low, "page": c.page}
                  for c in res.chunks]
        pages = list(getattr(res, "needs_ocr", []) or [])
        ocr = getattr(res, "ocr", None) or {}
        return {"source": os.path.basename(path), "chunks": chunks, "needs_ocr": bool(pages), "needs_ocr_pages": pages,
                "low_confidence": len(low), "ocr": jsonable(ocr), "pages": getattr(res, "pages", 0),
                "warnings": list(getattr(res, "warnings", []) or [])}

    def remove_material(self, material_id, course_id=None, owner=None):
        """Every chunk of the upload whose stored file is <material_id>.<ext>. Removing by course alone would match
        nothing in the engine: the file is the identity."""
        chunks = getattr(getattr(self.engine, "retriever", None), "chunks", None)
        if chunks is None:
            chunks = getattr(self.engine, "materials", [])
        stems = {os.path.splitext(c.source)[0] for c in chunks}
        if material_id not in stems:
            return {"removed": 0}
        sources = {c.source for c in chunks if os.path.splitext(c.source)[0] == material_id}
        removed = 0
        for source in sources:
            removed += self.call(self.engine.remove_material, source=source, course_id=course_id, owner=owner or None)
        return {"removed": removed}

    def confirm_chunk(self, chunk_id, concept):
        value = self.call(self.engine.confirm_chunk, chunk_id, concept or None)
        return {"chunk": jsonable(value)} if value else None

    def invalidate(self, request_id):
        cache = getattr(self.engine, "cache", None)
        if cache is None or not hasattr(cache, "invalidate"):
            return {"removed": 0}
        value = self.call(cache.invalidate, request_id)
        return {"removed": value if isinstance(value, int) else int(bool(value))}

    def metrics(self, since):
        from metrics.review import parse_since
        from metrics.summary import summarize
        return summarize(self.engine.metrics_records(), parse_since(since))

    def health(self):
        status = self.engine.status() if hasattr(self.engine, "status") else {}
        return {"ok": True, "engine": "engine-fake" if self.fake else "engine", "fake": self.fake, "version": self.version,
                "tasks": self.tasks(), "status": jsonable(status)}


def make_handler(host, token, port):
    want = token.encode()
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost"}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # the stream ends when the connection closes
        server_version = "EdNovaBridge/2"

        def log_message(self, fmt, *args):
            if os.environ.get("BRIDGE_VERBOSE"):
                super().log_message(fmt, *args)

        def _json(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length = int(self.headers.get("content-length") or 0)
            if length > MAX_BODY:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(length) or b"{}") if length else {}

        def _allowed(self):
            if (self.headers.get("host") or "") not in hosts:
                self._json(400, {"error": "bad Host header"})
                return False
            given = (self.headers.get(TOKEN_HEADER) or "").encode("utf-8", "replace")
            if not hmac.compare_digest(given, want):
                self._json(401, {"error": f"missing or wrong {TOKEN_HEADER}"})
                return False
            return True

        def _guard(self, fn):
            if not self._allowed():
                return
            try:
                fn()
            except ValueError as error:
                self._json(400, {"error": str(error)})
            except Exception as error:  # the Node side turns this into a degraded result
                traceback.print_exc()
                try:
                    self._json(500, {"error": f"{type(error).__name__}: {error}"})
                except Exception:
                    pass

        def _stream(self, start):
            started = []

            def write_line(obj):
                if not started:
                    self.send_response(200)
                    self.send_header("content-type", "application/x-ndjson; charset=utf-8")
                    self.end_headers()
                    started.append(True)
                self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
            try:
                start(write_line)
            except (BrokenPipeError, ConnectionResetError):
                pass                                    # the browser left; the run finishes into the cache anyway

        def do_GET(self):
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/") if p]

            def go():
                if parts == ["health"]:
                    return self._json(200, host.health())
                if len(parts) == 2 and parts[0] == "exercises":
                    ex = host.exercise(parts[1])
                    return self._json(200 if ex else 404, {"exercise": ex})
                if len(parts) == 2 and parts[0] == "courses":
                    course = host.course(parts[1])
                    return self._json(200 if course else 404, {"course": course})
                if parts == ["metrics", "summary"]:
                    return self._json(200, host.metrics((parse_qs(url.query).get("since") or [None])[0]))
                self._json(404, {"error": "not found"})
            self._guard(go)

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")

            def go():
                body = self._body()
                if path == "/run":
                    if not isinstance(body.get("task"), str):
                        raise ValueError("task is required")
                    return self._stream(lambda w: host.run(body["task"], body.get("inputs"), body.get("context"),
                                                           body.get("priority"), w))
                if path == "/generate_page":
                    return self._stream(lambda w: host.generate_page(body, w))
                if path == "/follow_up":
                    return self._json(200, host.follow_up(body.get("classification") or {}))
                if path == "/materials":
                    return self._json(200, host.add_material(body))
                if path == "/cache/invalidate":
                    return self._json(200, host.invalidate(str(body.get("request_id", ""))))
                self._json(404, {"error": "not found"})
            self._guard(go)

        def do_PATCH(self):
            parts = [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]

            def go():
                if len(parts) == 3 and parts[:2] == ["materials", "chunks"]:
                    body = self._body()
                    out = host.confirm_chunk(parts[2], body.get("concept"))
                    return self._json(200 if out else 404, out or {"error": "unknown chunk"})
                self._json(404, {"error": "not found"})
            self._guard(go)

        def do_DELETE(self):
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/") if p]

            def go():
                if len(parts) == 2 and parts[0] == "materials":
                    q = parse_qs(url.query)
                    out = host.remove_material(parts[1], (q.get("course_id") or [None])[0], (q.get("owner") or [None])[0])
                    return self._json(200, {"ok": True, **out})
                self._json(404, {"error": "not found"})
            self._guard(go)

    return Handler


def serve(host, token, port, address="127.0.0.1"):
    server = ThreadingHTTPServer((address, port), BaseHTTPRequestHandler)
    server.RequestHandlerClass = make_handler(host, token, server.server_address[1])     # port 0 = a free one
    return server


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Serve the EdNova LLM engine to the web app.")
    parser.add_argument("--engine-dir", default=os.environ.get("EDNOVA_ENGINE_DIR", os.path.join(here, "..", "..")),
                        help="the repo root that holds engine/, data/, prompts/ and .env (default: this repo)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "8765")))
    parser.add_argument("--fake", action="store_true", help="use the engine's own engine.fake.FakeEngine (no LLM, no key)")
    parser.add_argument("--fake-mode", default="normal", choices=["normal", "slow", "degraded"])
    args = parser.parse_args()
    token = os.environ.get("ENGINE_SERVICE_TOKEN", "")
    if not token:
        print("ENGINE_SERVICE_TOKEN is not set (the same value the Node server reads)", file=sys.stderr)
        return 2
    try:
        host = EngineHost(args.engine_dir, fake=args.fake, fake_mode=args.fake_mode)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 3
    server = serve(host, token, args.port)
    print(f"EdNova engine bridge on http://127.0.0.1:{args.port} "
          f"({'FakeEngine' if args.fake else 'engine'} from {host.engine_dir})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        host.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
