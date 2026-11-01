"""services/engine_server.py: every endpoint through httpx.ASGITransport (no socket), the WebSocket through a small ASGI
driver on the same event loop, the fake mode, the auth, and the one-process-per-key lock. No provider is called."""
import asyncio
import base64
import json
import os
import subprocess
import sys

import httpx
import pytest

from engine.fake import FakeEngine
from services import engine_server as S
from services.testing import ASGIWebSocket
from tests.conftest import chat_ok, make_settings
from tests.test_engine import make_engine
from tests.test_ocr_task import data_url, photo

TOKEN = "service-token-for-tests"
AUTH = {S.TOKEN_HEADER: TOKEN}
CLASSIFY = json.dumps({"intent": "EXPLAIN", "language": "FRENCH", "confusion": "LOW", "complexity": 2, "confidence": 0.9})


def client(app, headers=AUTH):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=headers)


class SpyEngine(FakeEngine):
    """FakeEngine that remembers what reached run()."""

    def __init__(self, **kw):
        super().__init__(speed=0, **kw)
        self.seen: list[dict] = []

    async def run(self, task, inputs=None, context=None, priority="INTERACTIVE", on_event=None):
        self.seen.append({"task": task, "inputs": dict(inputs or {}), "context": dict(context or {}), "priority": priority})
        return await super().run(task, inputs, context, priority, on_event)


@pytest.fixture
def fake_app():
    return S.create_app(SpyEngine(), token=TOKEN, fake=True, fake_mode="normal")


def WS(app, headers=AUTH):
    return ASGIWebSocket(app, headers)


# ------------------------------------------------------------------ auth
ROUTES = [("POST", "/run", {"json": {"task": "hint", "inputs": {"problem": "x"}}}),
          ("POST", "/materials", {}), ("DELETE", "/materials/abc", {}),
          ("POST", "/cache/invalidate", {"json": {"request_id": "r"}}), ("GET", "/health", {})]


@pytest.mark.parametrize("method,path,kw", ROUTES)
@pytest.mark.parametrize("headers", [{}, {S.TOKEN_HEADER: "wrong"}, {S.TOKEN_HEADER: TOKEN + "x"}, {S.TOKEN_HEADER: ""}])
async def test_every_http_route_refuses_a_missing_or_wrong_token(fake_app, method, path, kw, headers):
    async with client(fake_app, headers) as c:
        r = await c.request(method, path, **kw)
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"
    assert fake_app.state.runs == set() and fake_app.state.runs is not None


async def test_the_token_is_compared_in_constant_time(fake_app, monkeypatch):
    seen = []
    real = S.hmac.compare_digest
    monkeypatch.setattr(S.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    async with client(fake_app) as c:
        assert (await c.get("/health")).status_code == 200
        assert (await c.get("/health", headers={S.TOKEN_HEADER: "ünï".encode()})).status_code == 401       # non-ASCII: no TypeError
    assert seen[0] == (TOKEN.encode(), TOKEN.encode()) and all(isinstance(x, bytes) for p in seen for x in p)


def test_the_service_needs_a_token():
    with pytest.raises(ValueError):
        S.create_app(FakeEngine(), token="")
    with pytest.raises(ValueError):
        S.create_app(token="t")


async def test_only_local_host_names_are_served(fake_app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fake_app), base_url="http://evil.example",
                                 headers=AUTH) as c:
        assert (await c.get("/health")).status_code == 400
    async with client(fake_app) as c:
        assert (await c.get("/openapi.json")).status_code == 404          # no unauthenticated schema either


# ------------------------------------------------------------------ POST /run
async def test_run_returns_the_result_json_and_passes_the_body_through(fake_app):
    body = {"task": "hint", "inputs": {"problem": "Dériver 3x^2"}, "context": {"language": "FRENCH", "learner_id": "L1"},
            "priority": "background"}
    async with client(fake_app) as c:
        r = await c.post("/run", json=body)
    assert r.status_code == 200
    d = r.json()
    assert d["task"] == "hint" and d["degraded"] is False and d["text"] and [e["name"] for e in d["events"]]


async def test_run_priority_defaults_to_interactive_and_context_reaches_the_engine():
    eng = SpyEngine()
    app = S.create_app(eng, token=TOKEN, fake=True)
    async with client(app) as c:
        await c.post("/run", json={"task": "hint", "inputs": {"problem": "x"}, "context": {"room_id": "R"}})
        await c.post("/run", json={"task": "hint", "inputs": {"problem": "x"}, "priority": "background"})
    assert [(s["priority"].value, s["context"]) for s in eng.seen] == [("INTERACTIVE", {"room_id": "R"}),
                                                                       ("BACKGROUND", {})]


@pytest.mark.parametrize("body,fragment", [
    ({"task": "nope", "inputs": {}}, "unknown"),
    ({"task": "hint", "inputs": {}}, "needs inputs"),
])
async def test_run_turns_an_engine_refusal_into_422(fake_app, body, fragment):
    async with client(fake_app) as c:
        r = await c.post("/run", json=body)
    assert r.status_code == 422 and r.json()["error"] == "invalid_request" and fragment in r.json()["detail"]


@pytest.mark.parametrize("raw", [b"not json", b"[]", json.dumps({"inputs": {}}).encode(),
                                 json.dumps({"task": "hint", "priority": "urgent"}).encode()])
async def test_run_rejects_a_malformed_body_with_422(fake_app, raw):
    async with client(fake_app) as c:
        r = await c.post("/run", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["error"] == "error"


async def test_run_accepts_an_image_as_base64_in_json():
    eng = SpyEngine()
    app = S.create_app(eng, token=TOKEN, fake=True)
    url = data_url(photo(600, 300))
    async with client(app) as c:
        r = await c.post("/run", json={"task": "ocr_transcribe", "inputs": {"image": url}})
    assert r.status_code == 200 and r.json()["task"] == "ocr_transcribe" and eng.seen[0]["inputs"]["image"] == url


async def test_run_accepts_an_image_as_multipart_and_gives_the_engine_its_bytes():
    eng = SpyEngine()
    app = S.create_app(eng, token=TOKEN, fake=True)
    raw = photo(600, 300)
    async with client(app) as c:
        r = await c.post("/run", data={"task": "ocr_transcribe", "context": json.dumps({"language": "FRENCH"}),
                                       "priority": "INTERACTIVE"},
                         files={"image": ("work.png", raw, "image/png")})
        other = await c.post("/run", data={"task": "hint", "inputs": json.dumps({"problem": "x"})},
                             files={"image": ("work.png", raw, "image/png")})
    assert r.status_code == 200 and r.json()["task"] == "ocr_transcribe"
    assert eng.seen[0]["inputs"]["image"] == raw and eng.seen[0]["context"] == {"language": "FRENCH"}
    assert other.status_code == 422 and "only for ocr_transcribe" in other.json()["detail"]


async def test_multipart_image_goes_through_the_real_vision_pipeline(vt):
    eng, fake, _ = make_engine(vt, lambda body: chat_ok("f'(x) = 6x + 5", 820, 40))
    app = S.create_app(eng, token=TOKEN)
    async with client(app) as c:
        r = await c.post("/run", data={"task": "ocr_transcribe"}, files={"image": ("w.png", photo(), "image/png")})
    d = r.json()
    assert r.status_code == 200 and d["model_class_used"] == "vision" and d["data"]["text"] == "f'(x) = 6x + 5"
    assert "resizing_image" in [e["name"] for e in d["events"]]


async def test_a_bad_image_is_a_degraded_result_not_an_http_error():
    eng = SpyEngine()
    app = S.create_app(eng, token=TOKEN, fake=True)
    async with client(app) as c:
        r = await c.post("/run", json={"task": "ocr_transcribe", "inputs": {"image": "x"},
                                       "context": {"fake_outcome": "invalid_image"}})
    assert r.status_code == 200 and r.json()["degrade_reason"] == "invalid_image"


async def test_oversized_requests_are_refused():
    app = S.create_app(FakeEngine(speed=0), token=TOKEN, fake=True, max_upload_bytes=2000)
    async with client(app) as c:
        r = await c.post("/run", json={"task": "hint", "inputs": {"problem": "x" * 5000}})
        m = await c.post("/materials", data={"course_id": "analyse"}, files={"file": ("a.md", b"# t\n" + b"x" * 5000)})
    assert r.status_code == 413 and m.status_code == 413


async def test_a_client_that_goes_away_does_not_cancel_the_run():
    gate, finished = asyncio.Event(), []

    class Slow(FakeEngine):
        async def run(self, task, inputs=None, context=None, priority="INTERACTIVE", on_event=None):
            await gate.wait()
            finished.append(task)
            return await super().run(task, inputs, context, priority, on_event)

    app = S.create_app(Slow(speed=0), token=TOKEN, fake=True)
    async with client(app) as c:
        req = asyncio.ensure_future(c.post("/run", json={"task": "hint", "inputs": {"problem": "x"}}))
        await asyncio.sleep(0.05)
        assert len(app.state.runs) == 1
        req.cancel()                                        # the app side hung up
        await asyncio.gather(req, return_exceptions=True)
    assert len(app.state.runs) == 1 and finished == []      # still running, not cancelled
    gate.set()
    await asyncio.wait(set(app.state.runs), timeout=5)
    assert finished == ["hint"] and not app.state.runs


# ------------------------------------------------------------------ GET /health
async def test_health_on_the_fake_engine_shows_the_mode():
    async with client(S.create_app(FakeEngine("degraded", speed=0), token=TOKEN, fake=True, fake_mode="degraded")) as c:
        d = (await c.get("/health")).json()
    assert d["status"] == "ok" and d["fake"] is True and d["fake_mode"] == "degraded"
    assert d["breaker"]["midsize"]["state"] == "open" and d["window"]["remaining"] == 0 and d["queue"]["depth"] == 5


async def test_health_on_the_real_engine_reports_version_breaker_window_and_queue(vt):
    import engine as E
    eng, _, _ = make_engine(vt, lambda body: chat_ok(CLASSIFY, 80, 20))
    app = S.create_app(eng, token=TOKEN)
    async with client(app) as c:
        before = (await c.get("/health")).json()
        r = await c.post("/run", json={"task": "classify", "inputs": {"message": "c'est quoi une dérivée ?"}})
        after = (await c.get("/health")).json()
    assert r.status_code == 200 and before["fake"] is False and "fake_mode" not in before
    assert before["engine_version"] == E.__version__
    assert before["breaker"]["midsize"] == {"state": "closed", "retry_in_s": 0.0, "trips": 0}
    assert before["window"] == {"limit": 20, "window_s": 60.0, "remaining": 20, "in_flight": 0, "max_concurrent": 2}
    assert before["queue"] == {"depth": 0}
    assert after["window"]["remaining"] == 19                   # the classify request counted


# ------------------------------------------------------------------ POST /cache/invalidate
async def test_cache_invalidate_removes_the_entry_behind_a_request(vt):
    calls = []
    eng, _, _ = make_engine(vt, lambda body: calls.append(1) or chat_ok(CLASSIFY, 80, 20))
    app = S.create_app(eng, token=TOKEN)
    body = {"task": "classify", "inputs": {"message": "c'est quoi une dérivée ?"}}
    async with client(app) as c:
        first = (await c.post("/run", json=body)).json()
        again = (await c.post("/run", json=body)).json()
        gone = await c.post("/cache/invalidate", json={"request_id": first["request_id"]})
        third = (await c.post("/run", json=body)).json()
        unknown = await c.post("/cache/invalidate", json={"request_id": "nope"})
        bad = await c.post("/cache/invalidate", json={})
    assert again["from_cache"] is True and gone.json() == {"request_id": first["request_id"], "removed": 1}
    assert third["from_cache"] is False and len(calls) == 2
    assert unknown.json()["removed"] == 0 and bad.status_code == 422


async def test_cache_invalidate_on_the_fake_engine(fake_app):
    async with client(fake_app) as c:
        r = await c.post("/cache/invalidate", json={"request_id": "abc"})
    assert r.json() == {"request_id": "abc", "removed": 1}
    assert fake_app.state.runs == set()


# ------------------------------------------------------------------ /materials
NOTES = b"# Asymptotes\n\nUne asymptote oblique est une droite dont la courbe se rapproche a l'infini.\n\n" \
        b"## Branches infinies\n\nOn etudie la limite de f(x)/x en l'infini pour trouver la direction.\n"


async def test_upload_then_delete_a_material_on_the_fake_engine():
    eng = FakeEngine(speed=0)
    app = S.create_app(eng, token=TOKEN, fake=True)
    async with client(app) as c:
        up = await c.post("/materials", data={"course_id": "analyse", "owner": "prof-7", "visibility": "class",
                                              "class_id": "C1", "source": "professor"},
                          files={"file": ("../notes asymptotes.md", NOTES, "text/markdown")})
        d = up.json()
        assert up.status_code == 200 and d["file"] == "notes asymptotes.md" and d["chunks_added"] == len(eng.materials) >= 2
        assert d["course_id"] == "analyse" and all(ch["chunk_id"].startswith("professor/prof-7/") for ch in d["chunks"])
        assert {c.visibility for c in eng.materials} == {"class"} and {c.class_id for c in eng.materials} == {"C1"}
        gone = await c.delete(f"/materials/{d['material_id']}")
        again = await c.delete(f"/materials/{d['material_id']}")
        junk = await c.delete("/materials/not-an-id")
    assert gone.json() == {"material_id": d["material_id"], "removed": d["chunks_added"]} and eng.materials == []
    assert again.json()["removed"] == 0 and junk.status_code == 404


async def test_upload_errors_are_422():
    app = S.create_app(FakeEngine(speed=0), token=TOKEN, fake=True)
    async with client(app) as c:
        no_course = await c.post("/materials", files={"file": ("a.md", NOTES)})
        no_file = await c.post("/materials", data={"course_id": "analyse"})
        not_multipart = await c.post("/materials", json={"course_id": "analyse"})
        bad_vis = await c.post("/materials", data={"course_id": "analyse", "visibility": "secret"},
                               files={"file": ("a.md", NOTES)})
        bad_kind = await c.post("/materials", data={"course_id": "analyse"}, files={"file": ("a.xyz", b"data")})
    assert [r.status_code for r in (no_course, no_file, not_multipart, bad_vis, bad_kind)] == [422] * 5
    assert bad_vis.json()["error"] == "ingest_error" and bad_kind.json()["error"] == "ingest_error"


async def test_upload_on_the_real_engine_adds_to_the_live_index_and_delete_removes_it(vt, tmp_path):
    eng, _, _ = make_engine(vt, lambda body: chat_ok("ok"), RAG_COURSE_INDEX_DIR=str(tmp_path / "courses"))
    app = S.create_app(eng, token=TOKEN)
    await eng._ensure_index()
    n0 = len(eng.retriever.chunks)
    async with client(app) as c:
        d = (await c.post("/materials", data={"course_id": "analyse", "owner": "prof-7"},
                          files={"file": ("asymptotes.md", NOTES)})).json()
        assert d["chunks_added"] >= 2 and len(eng.retriever.chunks) == n0 + d["chunks_added"]
        assert all(ch["chunk_id"] in {x.chunk_id for x in eng.retriever.chunks} for ch in d["chunks"])
        gone = (await c.delete(f"/materials/{d['material_id']}")).json()
    assert gone["removed"] == d["chunks_added"] and len(eng.retriever.chunks) == n0


def test_a_material_id_round_trips_and_is_url_safe():
    mid = S.material_id("analyse", "prof é", "cours dérivées.pdf")
    assert S.parse_material_id(mid) == ("analyse", "prof é", "cours dérivées.pdf") and "/" not in mid and "=" not in mid


# ------------------------------------------------------------------ WebSocket /run/stream
async def test_stream_sends_every_event_then_the_result(fake_app):
    async with WS(fake_app) as ws:
        assert ws.accepted
        await ws.send({"task": "explain", "inputs": {"question": "C'est quoi une dérivée ?"},
                       "context": {"language": "FRENCH"}})
        frames = await ws.frames()
    kinds = [f["type"] for f in frames]
    assert kinds[-2:] == ["result", "websocket.close"] and set(kinds[:-2]) == {"event"}
    names = [f["event"]["name"] for f in frames if f["type"] == "event"]
    result = frames[-2]["result"]
    assert names == [e["name"] for e in result["events"]] and len(names) >= 3
    assert result["task"] == "explain" and frames[-1]["code"] == 1000
    assert all({"name", "t_ms", "data"} == set(f["event"]) for f in frames if f["type"] == "event")


async def test_stream_events_arrive_while_the_run_is_still_going():
    gate = asyncio.Event()

    class Staged(FakeEngine):
        async def run(self, task, inputs=None, context=None, priority="INTERACTIVE", on_event=None):
            from engine.result import Event
            on_event(Event(name="checking_cache", t_ms=1.0))
            await gate.wait()
            return await super().run(task, inputs, context, priority, on_event)

    async with WS(S.create_app(Staged(speed=0), token=TOKEN, fake=True)) as ws:
        await ws.send({"task": "hint", "inputs": {"problem": "x"}})
        first = await ws.recv()
        assert first["type"] == "event" and first["event"]["name"] == "checking_cache"   # before the result exists
        gate.set()
        assert (await ws.frames())[-2]["type"] == "result"


async def test_stream_refuses_a_missing_or_wrong_token(fake_app):
    for headers in ({}, {S.TOKEN_HEADER: "wrong"}):
        async with WS(fake_app, headers) as ws:
            assert not ws.accepted and ws.first == {"type": "websocket.close", "code": 1008, "reason": ""}
    assert fake_app.state.runs == set()


@pytest.mark.parametrize("message,fragment", [("not json", "Expecting"), ('{"inputs": {}}', "task"),
                                              ('{"task": "nope", "inputs": {}}', "unknown"),
                                              ('{"task": "hint", "inputs": {}}', "needs inputs")])
async def test_stream_reports_a_bad_body_as_an_error_frame(fake_app, message, fragment):
    async with WS(fake_app) as ws:
        await ws.send(message)
        frames = await ws.frames()
    assert frames[0]["type"] == "error" and frames[0]["error"] == "invalid_request" and fragment in frames[0]["detail"]
    assert frames[-1]["type"] == "websocket.close" and frames[-1]["code"] == 1003


async def test_stream_gives_up_on_a_client_that_sends_nothing(fake_app, monkeypatch):
    monkeypatch.setattr(S, "FIRST_MESSAGE_TIMEOUT_S", 0.05)
    async with WS(fake_app) as ws:
        frames = await ws.frames()
    assert frames[0]["error"] == "invalid_request" and "no body" in frames[0]["detail"]


async def test_a_stream_client_that_disconnects_does_not_cancel_the_run():
    gate, finished = asyncio.Event(), []

    class Slow(FakeEngine):
        async def run(self, task, inputs=None, context=None, priority="INTERACTIVE", on_event=None):
            from engine.result import Event
            on_event(Event(name="queued", t_ms=2.0))
            try:
                await gate.wait()
                r = await super().run(task, inputs, context, priority, on_event)
                finished.append(r.request_id)               # "the cache is filled" in this stand-in
                return r
            except asyncio.CancelledError:
                finished.append("cancelled")
                raise

    app = S.create_app(Slow(speed=0), token=TOKEN, fake=True)
    async with WS(app) as ws:
        await ws.send({"task": "explain", "inputs": {"question": "q"}})
        assert (await ws.recv())["event"]["name"] == "queued"
        await ws.leave()
        await asyncio.wait_for(ws.task, 5)                  # the handler ends with the connection
    assert len(app.state.runs) == 1 and finished == []
    gate.set()
    await asyncio.wait(set(app.state.runs), timeout=5)
    assert len(finished) == 1 and finished[0] != "cancelled" and not app.state.runs


async def test_the_server_shutdown_waits_for_runs_in_flight(monkeypatch):
    done = []

    class Late(FakeEngine):
        async def run(self, *a, **k):
            await asyncio.sleep(0.05)
            done.append(1)
            return await super().run(*a, **k)

        async def aclose(self):
            done.append("closed")

    app = S.create_app(engine_factory=lambda: Late(speed=0), token=TOKEN, fake=True)
    async with app.router.lifespan_context(app):
        async with client(app) as c:
            req = asyncio.ensure_future(c.post("/run", json={"task": "hint", "inputs": {"problem": "x"}}))
            await asyncio.sleep(0.01)
            req.cancel()
            await asyncio.gather(req, return_exceptions=True)
    assert done == [1, "closed"]                            # the run finished, then the engine was closed


# ------------------------------------------------------------------ the fake mode is the same API
async def test_fake_mode_serves_the_identical_endpoints_and_result_shape(vt):
    real, _, _ = make_engine(vt, lambda body: chat_ok(CLASSIFY, 80, 20))
    shapes = {}
    for name, app in (("real", S.create_app(real, token=TOKEN)),
                      ("fake", S.create_app(FakeEngine(speed=0), token=TOKEN, fake=True))):
        async with client(app) as c:
            run = (await c.post("/run", json={"task": "classify", "inputs": {"message": "c'est quoi une dérivée ?"}})).json()
            health = (await c.get("/health")).json()
        shapes[name] = (set(run), set(health) - {"fake_mode"}, set(health["window"]), set(health["queue"]))
    assert shapes["real"] == shapes["fake"]


def test_the_fake_flag_serves_without_a_provider_or_a_key(monkeypatch):
    monkeypatch.setenv("ENGINE_SERVICE_TOKEN", TOKEN)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    captured = {}
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, log_level: captured.update(app=app, host=host, port=port))
    assert S.main(["--fake", "--fake-mode", "slow", "--port", "9123"]) == 0
    assert captured["host"] == "127.0.0.1" and captured["port"] == 9123


def test_the_service_has_no_option_to_bind_elsewhere(monkeypatch, capsys):
    monkeypatch.setenv("ENGINE_SERVICE_TOKEN", TOKEN)
    with pytest.raises(SystemExit):
        S.main(["--fake", "--host", "0.0.0.0"])
    assert S.HOST == "127.0.0.1"


def test_main_refuses_to_start_without_a_token(monkeypatch, capsys):
    monkeypatch.delenv("ENGINE_SERVICE_TOKEN", raising=False)
    assert S.main(["--fake"]) == 2 and "ENGINE_SERVICE_TOKEN" in capsys.readouterr().err


# ------------------------------------------------------------------ one process per key
def test_lock_refuses_a_second_live_instance_and_releases(tmp_path):
    path = str(tmp_path / "engine.lock")
    first = S.InstanceLock(path)
    first.acquire()
    assert open(path).read().split() == [str(os.getpid())]
    with pytest.raises(SystemExit) as e:
        S.InstanceLock(path).acquire()
    assert str(os.getpid()) in str(e.value) and "one process per key" in str(e.value)
    first.release()
    assert not os.path.exists(path)
    S.InstanceLock(path).acquire()                          # free again


def test_lock_of_a_dead_process_is_taken_over(tmp_path):
    path = str(tmp_path / "engine.lock")
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    open(path, "w").write(dead.stdout)
    lock = S.InstanceLock(path)
    assert lock.holder() is None
    lock.acquire()
    assert lock.holder() == os.getpid()


def test_a_lock_file_that_is_garbage_counts_as_stale(tmp_path):
    path = str(tmp_path / "engine.lock")
    open(path, "w").write("not a pid")
    lock = S.InstanceLock(path)
    lock.acquire()
    assert lock.holder() == os.getpid()


def test_release_does_not_remove_another_instances_lock(tmp_path):
    path = str(tmp_path / "engine.lock")
    mine = S.InstanceLock(path)
    mine.release()                                          # nothing to release
    open(path, "w").write("1\n")                            # pid 1 is alive and not ours
    mine.release()
    assert os.path.exists(path)


def test_the_lock_is_per_key_and_its_name_does_not_reveal_the_key(tmp_path):
    a, b = S.lock_path("key-one", str(tmp_path)), S.lock_path("key-two", str(tmp_path))
    assert a != b and a == S.lock_path("key-one", str(tmp_path)) and "key-one" not in a


def test_main_takes_the_lock_for_the_real_engine_and_refuses_a_second(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ENGINE_SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("ENGINE_SERVICE_LOCK_DIR", str(tmp_path))
    import config
    import uvicorn
    s = make_settings()
    monkeypatch.setattr(config, "load_settings", lambda *a, **k: s)
    ran = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: ran.append(os.listdir(tmp_path)))
    assert S.main(["--port", "9124"]) == 0
    assert len(ran) == 1 and len(ran[0]) == 1 and ran[0][0].startswith("engine-")        # held while serving
    assert os.listdir(tmp_path) == []                                                     # released after
    other = S.InstanceLock(S.lock_path(s.api_key, str(tmp_path)))
    other.acquire()
    assert S.main(["--port", "9124"]) == 3 and "already runs" in capsys.readouterr().err
    assert len(ran) == 1
