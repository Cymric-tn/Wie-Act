"""EdNova engine bridge.

Hosts ONE EdNova LLM engine per process (the engine's limiter, queue, breaker
and cache live in memory, so the guide asks for one engine process per API
key) and serves it to the Node server over HTTP on localhost.

    python bridge.py                      # real engine from ../../engine
    python bridge.py --fake --fake-mode slow   # the engine's own FakeEngine
    python bridge.py --engine-dir /path/to/wie --port 8765

Standard library only; the engine brings its own requirements.txt.

Endpoints (all JSON; /run streams NDJSON):
    GET    /health                 {ok, engine, version, tasks}
    POST   /run                    {task, inputs, context, priority}
                                   -> {"type":"event","event":{...}}\\n ... {"type":"result","result":{...}}\\n
    POST   /follow_up              {classification} -> {task, reason, overridden}
    GET    /exercises/<id>         {exercise}   (full: solution, hints, common_mistakes)
    GET    /courses/<id>           {course}
    POST   /materials              {id, course_id, path, text, visibility, class_id, learner_id, source, ...}
    DELETE /materials/<id>?course_id=
    POST   /cache/invalidate       {request_id} -> {removed}
"""

import argparse
import asyncio
import dataclasses
import enum
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


def accepted_kwargs(fn, kwargs):
    """Keeps only the keyword arguments fn accepts (unless it takes **kwargs)."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return kwargs
    names = {p.name for p in params}
    return {k: v for k, v in kwargs.items() if k in names}


class EngineHost:
    """Owns the engine and the asyncio loop it runs on (one background thread)."""

    def __init__(self, engine_dir, fake=False, fake_mode="normal"):
        self.engine_dir = os.path.abspath(engine_dir)
        if not os.path.isdir(os.path.join(self.engine_dir, "engine")):
            raise SystemExit(f"No engine package found in {self.engine_dir} (expected {self.engine_dir}/engine/). "
                             "Copy the EdNova engine there or pass --engine-dir.")
        sys.path.insert(0, self.engine_dir)
        os.chdir(self.engine_dir)  # the engine reads .env, data/, prompts/ relative to its root
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, name="engine-loop", daemon=True).start()
        self.fake = fake
        self.engine = self.call(self._create, fake, fake_mode)
        import engine as engine_pkg  # noqa: E402  (path set above)
        self.version = getattr(engine_pkg, "__version__", None)
        try:
            from engine import Priority  # noqa: E402
            self.Priority = Priority
        except ImportError:
            self.Priority = None

    async def _create(self, fake, fake_mode):
        if fake:
            from engine.fake import FakeEngine
            return FakeEngine(**accepted_kwargs(FakeEngine, {"mode": fake_mode}))
        from engine import Engine
        created = Engine.create()
        if inspect.isawaitable(created):
            created = await created
        if hasattr(created, "__aenter__"):
            entered = await created.__aenter__()
            created = entered if entered is not None else created
        return created

    def call(self, fn, *args, timeout=CALL_TIMEOUT_S, **kwargs):
        """Runs fn on the engine loop (sync or async) and waits for its value."""
        async def go():
            value = fn(*args, **kwargs)
            if inspect.isawaitable(value):
                value = await value
            return value
        return asyncio.run_coroutine_threadsafe(go(), self.loop).result(timeout)

    def priority(self, name):
        if self.Priority is None:
            return name
        try:
            return self.Priority[str(name or "INTERACTIVE").upper()]
        except KeyError:
            return self.Priority["INTERACTIVE"]

    def tasks(self):
        registry = getattr(self.engine, "registry", None)
        for attr in ("names", "tasks", "specs", "_specs"):
            value = getattr(registry, attr, None)
            if callable(value):
                value = value()
            if isinstance(value, dict):
                return sorted(value.keys())
            if isinstance(value, (list, tuple, set)):
                return sorted(str(v) for v in value)
        return None

    # ----- one streaming run -----
    def run(self, task, inputs, context, priority, write_line):
        events = queue.Queue()

        def on_event(event):
            events.put(("event", jsonable(event)))

        future = asyncio.run_coroutine_threadsafe(
            self.engine.run(task, inputs or {}, context or {}, self.priority(priority), on_event=on_event), self.loop)
        future.add_done_callback(lambda _f: events.put(("done", None)))
        wrote_any = False
        while True:
            kind, payload = events.get()
            if kind == "done":
                break
            write_line({"type": "event", "event": payload})
            wrote_any = True
        try:
            result = future.result()
        except ValueError as error:  # unknown task / missing input: the engine's documented ValueError
            if not wrote_any:
                raise
            write_line({"type": "error", "kind": "value", "error": str(error)})
            return
        write_line({"type": "result", "result": jsonable(result)})

    # ----- helpers around the feature-layer calls -----
    def follow_up(self, classification):
        router = getattr(self.engine, "router", None)
        if router is not None and hasattr(router, "follow_up"):
            return jsonable(self.call(router.follow_up, classification))
        intent = (classification or {}).get("intent")
        if (classification or {}).get("confusion") == "HIGH" and intent in ("QUIZ", "DIAGNOSTIC"):
            return {"task": "explain", "reason": "HIGH confusion -> explain first", "overridden": True}
        task = {"EXPLAIN": "explain", "HINT": "hint", "ANSWER_ATTEMPT": "hint", "QUIZ": "quiz", "DIAGNOSTIC": "quiz"}.get(intent)
        return {"task": task, "reason": f"intent {intent} -> {task or 'no follow-up'}", "overridden": False}

    def exercise(self, exercise_id):
        bank = getattr(self.engine, "exercises", None)
        if bank is None:
            return None
        return jsonable(self.call(bank.get, exercise_id))

    def course(self, course_id):
        for owner, name in ((self.engine, "course"), (getattr(self.engine, "courses", None), "get")):
            fn = getattr(owner, name, None) if owner is not None else None
            if callable(fn):
                try:
                    value = self.call(fn, course_id)
                except (KeyError, FileNotFoundError):
                    return None
                if value is not None:
                    return jsonable(value)
        return None

    def add_material(self, body):
        kwargs = {
            "course_id": body.get("course_id"), "visibility": body.get("visibility"), "class_id": body.get("class_id"),
            "learner_id": body.get("learner_id"), "source": body.get("source"), "title": body.get("title"),
            "material_id": body.get("id"), "priority": self.priority("BACKGROUND"),
        }
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        ingest = getattr(self.engine, "ingest_file", None)
        if callable(ingest) and body.get("path"):
            value = self.call(ingest, body["path"], **accepted_kwargs(ingest, kwargs))
        else:
            add = getattr(self.engine, "add_material")
            value = self.call(add, **accepted_kwargs(add, {**kwargs, "text": body.get("text", ""), "path": body.get("path")}))
        value = jsonable(value)
        chunks = value.get("chunks") if isinstance(value, dict) else value if isinstance(value, list) else []
        out = []
        for chunk in chunks or []:
            if isinstance(chunk, dict):
                out.append({"id": chunk.get("id") or chunk.get("chunk_id"), "title": chunk.get("title"),
                            "concept": chunk.get("concept"), "confidence": chunk.get("concept_confidence", chunk.get("confidence")),
                            "needs_ocr": chunk.get("needs_ocr", False)})
        needs_ocr = bool(value.get("needs_ocr")) if isinstance(value, dict) else False
        return {"chunks": out, "needs_ocr": needs_ocr or any(c.get("needs_ocr") for c in out)}

    def remove_material(self, material_id, course_id):
        fn = getattr(self.engine, "remove_material")
        return self.call(fn, **accepted_kwargs(fn, {"material_id": material_id, "id": material_id, "course_id": course_id}))

    def invalidate(self, request_id):
        cache = getattr(self.engine, "cache", None)
        if cache is None or not hasattr(cache, "invalidate"):
            return {"removed": 0}
        value = self.call(cache.invalidate, request_id)
        return {"removed": value if isinstance(value, int) else int(bool(value))}


def make_handler(host):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # the stream ends when the connection closes
        server_version = "EdNovaBridge/1"

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

        def _guard(self, fn):
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

        def do_GET(self):
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/") if p]

            def go():
                if parts == ["health"]:
                    return self._json(200, {"ok": True, "engine": "engine-fake" if host.fake else "engine",
                                            "version": host.version, "tasks": host.tasks()})
                if len(parts) == 2 and parts[0] == "exercises":
                    ex = host.exercise(parts[1])
                    return self._json(200 if ex else 404, {"exercise": ex})
                if len(parts) == 2 and parts[0] == "courses":
                    course = host.course(parts[1])
                    return self._json(200 if course else 404, {"course": course})
                self._json(404, {"error": "not found"})
            self._guard(go)

        def do_POST(self):
            path = urlparse(self.path).path.rstrip("/")

            def go():
                body = self._body()
                if path == "/run":
                    if not isinstance(body.get("task"), str):
                        raise ValueError("task is required")
                    started = []

                    def write_line(obj):
                        if not started:
                            self.send_response(200)
                            self.send_header("content-type", "application/x-ndjson; charset=utf-8")
                            self.end_headers()
                            started.append(True)
                        self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                        self.wfile.flush()

                    return host.run(body["task"], body.get("inputs"), body.get("context"), body.get("priority"), write_line)
                if path == "/follow_up":
                    return self._json(200, host.follow_up(body.get("classification") or {}))
                if path == "/materials":
                    return self._json(200, host.add_material(body))
                if path == "/cache/invalidate":
                    return self._json(200, host.invalidate(str(body.get("request_id", ""))))
                self._json(404, {"error": "not found"})
            self._guard(go)

        def do_DELETE(self):
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.strip("/").split("/") if p]

            def go():
                if len(parts) == 2 and parts[0] == "materials":
                    course_id = (parse_qs(url.query).get("course_id") or [None])[0]
                    host.remove_material(parts[1], course_id)
                    return self._json(200, {"ok": True})
                self._json(404, {"error": "not found"})
            self._guard(go)

    return Handler


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="Serve the EdNova LLM engine to the web app.")
    parser.add_argument("--engine-dir", default=os.environ.get("EDNOVA_ENGINE_DIR", os.path.join(here, "..", "..", "engine")))
    parser.add_argument("--host", default=os.environ.get("BRIDGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "8765")))
    parser.add_argument("--fake", action="store_true", help="use the engine's own engine.fake.FakeEngine (no LLM)")
    parser.add_argument("--fake-mode", default="normal", choices=["normal", "slow", "degraded"])
    args = parser.parse_args()

    host = EngineHost(args.engine_dir, fake=args.fake, fake_mode=args.fake_mode)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(host))
    print(f"EdNova engine bridge on http://{args.host}:{args.port} "
          f"({'FakeEngine' if args.fake else 'engine'} from {host.engine_dir})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        aclose = getattr(host.engine, "aclose", None)
        if callable(aclose):
            try:
                host.call(aclose, timeout=10)
            except Exception:
                pass


if __name__ == "__main__":
    main()
