"""The web app's bridge (app/bridge/bridge.py) against the REAL Engine over a scripted provider (no network, no model):
the contract the Node server relies on, the fixes of the integration audit, the token and the one-process-per-key lock."""
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from urllib.parse import quote

import httpx
import pytest

from engine import Engine
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient
from metrics import MemorySink
from tests.conftest import FnProvider, chat_ok, make_settings

spec = importlib.util.spec_from_file_location("ednova_bridge", "app/bridge/bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

TOKEN = "bridge-token-for-tests"
AUTH = {bridge.TOKEN_HEADER: TOKEN}
JSON_REPLY = json.dumps({"intent": "EXPLAIN", "language": "FRENCH", "confusion": "LOW", "complexity": 2, "confidence": 0.9,
                         "verdict": "SUPPORTED", "correction": ""})
NOTES = ("# Asymptotes obliques\n\nUne asymptote oblique est une droite y = ax + b dont la courbe se rapproche quand x tend "
         "vers l'infini. Mes notes de cours sur les asymptotes obliques.\n")
Q_ASYMPTOTE = "c'est quoi une asymptote oblique ?"


class Rig:
    """A bridge on a free port over a real Engine whose provider is scripted; `prompts` records what the model saw."""

    def __init__(self, tmp_path, reply=None):
        self.prompts = []

        def model(body):
            self.prompts.append(json.dumps(body["messages"], ensure_ascii=False))
            if body.get("response_format"):
                return chat_ok(JSON_REPLY, 120, 30)
            return chat_ok(reply or "Une asymptote est une droite dont la courbe se rapproche.", 120, 30)

        def factory():
            s = make_settings(RAG_COURSE_INDEX_DIR=str(tmp_path / "courses"))
            return Engine.create(s, client=LLMClient(s, FnProvider(model)), sink=MemorySink(),
                                 cache=ExactCache(str(tmp_path / "cache.sqlite"), s.cache_ttl_s))
        self.host = bridge.EngineHost(engine_factory=factory, lock=False)
        self.server = bridge.serve(self.host, TOKEN, 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", headers=AUTH, timeout=60)
        (tmp_path / "up").mkdir()
        self.dir = tmp_path / "up"

    def lines(self, path, body):
        r = self.http.post(path, json=body)
        assert r.status_code == 200, r.text
        out = [json.loads(x) for x in r.text.splitlines() if x]
        return [x for x in out if x["type"] == "event"], next(x["result"] for x in out if x["type"] == "result")

    def upload(self, name, text=NOTES, **kw):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        body = {"id": path.stem, "course_id": "analyse", "path": str(path), "visibility": "class", "class_id": "C1",
                "learner_id": "prof-1", "source": "professor", **kw}
        return self.http.post("/materials", json=body)

    def close(self):
        self.server.shutdown()
        self.host.close()
        self.host.engine and self.host.call(self.host.engine.aclose, timeout=10)


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


# ---------------------------------------------------------------- access
def test_every_route_needs_the_token_and_a_local_host(rig):
    bare = httpx.Client(base_url=f"http://127.0.0.1:{rig.port}")
    assert bare.get("/health").status_code == 401
    assert bare.get("/health", headers={bridge.TOKEN_HEADER: "wrong"}).status_code == 401
    assert bare.post("/run", json={"task": "hint", "inputs": {"problem": "x"}}).status_code == 401
    assert bare.delete("/materials/x").status_code == 401 and bare.patch("/materials/chunks/x", json={}).status_code == 401
    assert rig.http.get("/health", headers={"host": "evil.example"}).status_code == 400
    assert rig.http.get("/health").status_code == 200


def test_health_reports_the_real_engine_and_its_version(rig):
    h = rig.http.get("/health").json()
    import engine
    assert h["ok"] and h["engine"] == "engine" and h["fake"] is False and h["version"] == engine.__version__ == "1.4"
    assert {"check_answer", "check_claim", "prep_plan", "explain"} <= set(h["tasks"])
    assert h["status"]["window"]["limit"] == 20


# ---------------------------------------------------------------- the feature-layer reads
def test_exercises_courses_and_the_follow_up_rule_come_from_the_engine(rig):
    ex = rig.http.get("/exercises/d01").json()["exercise"]
    assert ex["solution"] and ex["hints"] and rig.http.get("/exercises/nope").status_code == 404
    assert set(ex["statement"]) == {"fr", "en", "ar"} and "x⁴" in ex["statement"]["fr"] and ex["common_mistakes"][0] == ["x³", ex["common_mistakes"][0][1]]
    c = rig.http.get("/courses/analyse").json()["course"]
    assert c["id"] == "analyse" and c["units"] and rig.http.get("/courses/nope").status_code == 404
    f = rig.http.post("/follow_up", json={"classification": {"intent": "QUIZ", "confusion": "HIGH"}}).json()
    assert f == {"task": "explain", "reason": f["reason"], "overridden": True}
    assert rig.http.post("/follow_up", json={"classification": {"intent": "CHITCHAT"}}).json()["task"] is None


# ---------------------------------------------------------------- tasks with the real input names
def test_run_streams_the_engine_events_then_the_result(rig):
    events, result = rig.lines("/run", {"task": "hint", "inputs": {"problem": "Dérive f(x) = 3x² + 5x"},
                                        "context": {"language": "FRENCH", "learner_id": "L1"}})
    assert [e["event"]["name"] for e in events] == [e["name"] for e in result["events"]] and result["task"] == "hint"
    assert rig.http.post("/run", json={"task": "hint", "inputs": {}}).status_code == 400
    assert rig.http.post("/run", json={"task": "nope", "inputs": {"x": 1}}).status_code == 400


def test_check_answer_takes_steps_not_lines(rig):
    ok = {"task": "check_answer", "inputs": {"problem": "Dérive f(x) = x⁴", "exercise_id": "d01",
                                              "steps": ["f(x) = x⁴", "f'(x) = x³"]}, "context": {"language": "FRENCH"}}
    _, r = rig.lines("/run", ok)
    assert r["data"]["final_verdict"] == "INCORRECT" and r["data"]["first_wrong"] == 1 and r["data"]["steps"][1]["verdict"] == "WRONG"
    assert rig.http.post("/run", json={**ok, "inputs": {"problem": "x", "lines": ["a"]}}).status_code == 400


def test_prep_plan_takes_scope_deadline_and_pace(rig):
    plan = {"scope": {"unit_ids": ["derivees"]}, "deadline": "2099-01-01", "pace_min_per_day": 45,
            "mastery": {"puissance": 0.2}}
    _, r = rig.lines("/run", {"task": "prep_plan", "inputs": plan, "priority": "BACKGROUND"})
    assert r["data"]["focus_points"] and r["data"]["assessment_exercise_ids"] and r["tokens_in"] == 0
    assert rig.http.post("/run", json={"task": "prep_plan", "inputs": {"course_id": "analyse", "days_left": 7}}).status_code == 400


def test_generate_page_is_a_route_not_a_task(rig):
    _, plan = rig.lines("/run", {"task": "prep_plan", "priority": "BACKGROUND", "inputs": {
        "scope": {"unit_ids": ["derivees"]}, "deadline": "2099-01-01", "pace_min_per_day": 45}})
    focus = plan["data"]["focus_points"][0]
    events, page = rig.lines("/generate_page", {"focus_point": focus, "context": {"language": "FRENCH", "course_id": "analyse"}})
    assert page["concept_id"] == focus["concept_id"] and page["worked_example"] and len(page["quiz"]) >= 1
    assert page["explanation"] and not page["degraded"] and events
    assert rig.http.post("/generate_page", json={"focus_point": {}}).status_code == 400
    assert rig.http.post("/run", json={"task": "generate_page", "inputs": {"concept": "puissance"}}).status_code == 400


def test_check_claim_returns_correction_and_parts(rig):
    _, r = rig.lines("/run", {"task": "check_claim", "inputs": {"claim": "(x²)' = 2x et (x³)' = 2x²"},
                              "context": {"language": "FRENCH"}})
    assert r["data"]["verdict"] == "PARTLY" and len(r["data"]["parts"]) == 2 and "correction" in r["data"]


# ---------------------------------------------------------------- materials
def test_a_professor_upload_is_indexed_and_answers_for_its_class_only(rig):
    up = rig.upload("mat-prof1.md")
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["source"] == "mat-prof1.md" and body["chunks"] and body["needs_ocr"] is False
    assert {"id", "title", "concept", "confidence", "low_confidence", "page"} <= set(body["chunks"][0])
    assert body["chunks"][0]["id"].startswith("professor/prof-1/mat-prof1#")
    ctx = {"language": "FRENCH", "course_id": "analyse", "class_id": "C1", "learner_id": "L9"}
    events, r = rig.lines("/run", {"task": "explain", "inputs": {"question": Q_ASYMPTOTE}, "context": ctx})
    assert "no_relevant_context" not in [e["event"]["name"] for e in events] and not r["degraded"]
    assert any(c in r["notes"]["sources"][0] for c in ("mat-prof1",)) and "asymptotes obliques" in rig.prompts[-1]
    _, other = rig.lines("/run", {"task": "explain", "inputs": {"question": Q_ASYMPTOTE}, "context": {**ctx, "class_id": "C2"}})
    assert other["notes"]["retrieval"] == "no_relevant_chunks"


def test_a_learner_upload_is_private_to_its_owner(rig):
    up = rig.upload("mat-me.md", visibility="private", learner_id="L1", source="upload", class_id="")
    assert up.status_code == 200 and up.json()["chunks"][0]["id"].startswith("learner/l1/mat-me#")   # "upload" = a learner
    ctx = {"language": "FRENCH", "course_id": "analyse"}
    _, mine = rig.lines("/run", {"task": "explain", "inputs": {"question": Q_ASYMPTOTE}, "context": {**ctx, "learner_id": "L1"}})
    _, theirs = rig.lines("/run", {"task": "explain", "inputs": {"question": Q_ASYMPTOTE}, "context": {**ctx, "learner_id": "L2"}})
    assert not mine["degraded"] and "mat-me" in mine["notes"]["sources"][0]
    assert theirs["notes"]["retrieval"] == "no_relevant_chunks"


def test_deleting_an_upload_removes_its_chunks_and_nothing_else(rig):
    rig.upload("mat-a.md")
    rig.upload("mat-b.md", text="# Branches paraboliques\n\nUne branche parabolique apparaît quand la courbe s'éloigne "
                                "de toute droite : la limite de f(x)/x est infinie.\n")
    chunks = lambda: {c.chunk_id for c in rig.host.engine.retriever.chunks}
    n_before = len(chunks())
    gone = rig.http.delete("/materials/mat-a?course_id=analyse").json()
    assert gone["ok"] and gone["removed"] >= 1 and len(chunks()) == n_before - gone["removed"]
    assert not any("mat-a#" in c for c in chunks()) and any("mat-b#" in c for c in chunks())
    assert any(c.startswith("derivee-") for c in chunks())                       # the course itself is untouched
    assert rig.http.delete("/materials/mat-a?course_id=analyse").json()["removed"] == 0
    _, r = rig.lines("/run", {"task": "explain", "inputs": {"question": Q_ASYMPTOTE},
                              "context": {"language": "FRENCH", "course_id": "analyse", "class_id": "C1", "learner_id": "L9"}})
    assert r["notes"]["retrieval"] == "no_relevant_chunks"


def test_upload_errors(rig):
    assert rig.http.post("/materials", json={"id": "x", "course_id": "analyse", "path": "/nope.md"}).status_code == 400
    bad = rig.upload("mat-bad.md", visibility="class", class_id="")
    assert bad.status_code == 400 and "class_id" in bad.text                    # the engine's IngestError is a bad request


def test_confirming_a_concept_tag_reaches_the_index(rig):
    cid = rig.upload("mat-tag.md").json()["chunks"][0]["id"]
    out = rig.http.patch(f"/materials/chunks/{quote(cid, safe='')}", json={"concept": "tangente"})
    assert out.status_code == 200 and out.json()["chunk"]["concept"] == "tangente" and out.json()["chunk"]["confidence"] == 1.0
    assert next(c for c in rig.host.engine.retriever.chunks if c.chunk_id == cid).concept == "tangente"
    assert rig.http.patch(f"/materials/chunks/{quote(cid, safe='')}", json={"concept": "pas-un-concept"}).status_code == 400
    assert rig.http.patch("/materials/chunks/unknown", json={"concept": "tangente"}).status_code == 404


def test_cache_invalidate_and_metrics(rig):
    ctx = {"language": "FRENCH", "learner_id": "L1"}
    _, first = rig.lines("/run", {"task": "hint", "inputs": {"problem": "Dérive f(x) = 3x² + 5x"}, "context": ctx})
    assert rig.http.post("/cache/invalidate", json={"request_id": first["request_id"]}).json()["removed"] >= 1
    s = rig.http.get("/metrics/summary").json()
    assert s["runs"]["total"] >= 1 and s["server_429"]["count"] == 0


# ---------------------------------------------------------------- processes
def _env(tmp_path, port):
    return {**os.environ, "ENGINE_SERVICE_TOKEN": TOKEN, "ENGINE_SERVICE_LOCK_DIR": str(tmp_path / "locks"),
            "LLM_BASE_URL": "http://127.0.0.1:9/v1", "LLM_API_KEY": "key-for-the-lock-test", "MODEL_COMPACT": "c",
            "MODEL_MIDSIZE": "m", "MODEL_VISION": "v", "SIMULATE_CONSTRAINTS": "false", "ENV_FILE": "/nonexistent",
            "CACHE_PATH": str(tmp_path / "c.sqlite"), "METRICS_PATH": str(tmp_path / "m.jsonl"),
            "RAG_COURSE_INDEX_DIR": str(tmp_path / "courses"), "BRIDGE_PORT": str(port)}


def _wait(port, proc, seconds=30):
    end = time.time() + seconds
    while time.time() < end and proc.poll() is None:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health", headers=AUTH, timeout=1).status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.2)
    return False


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_second_bridge_for_the_same_key_refuses_to_start(tmp_path):
    env = _env(tmp_path, _free_port())
    cmd = [sys.executable, "app/bridge/bridge.py"]
    first = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _wait(int(env["BRIDGE_PORT"]), first)
        second = subprocess.run(cmd, env={**env, "BRIDGE_PORT": str(_free_port())}, capture_output=True, text=True, timeout=60)
        assert second.returncode == 3 and "one process per key" in second.stderr
        assert "key-for-the-lock-test" not in second.stderr + second.stdout
        engine_service = subprocess.run([sys.executable, "-m", "services.engine_server"], env=env, capture_output=True,
                                        text=True, timeout=60)
        assert engine_service.returncode == 3                                  # the engine's own service shares the lock
    finally:
        first.terminate()
        first.wait(10)
    assert subprocess.run(cmd + ["--fake", "--port", "1"], env={**env, "ENGINE_SERVICE_TOKEN": ""}, capture_output=True,
                          text=True, timeout=30).returncode == 2               # no token: refuses


def test_the_fake_engine_serves_the_same_routes_without_a_key(tmp_path):
    port = _free_port()
    env = {**_env(tmp_path, port), "LLM_API_KEY": ""}
    p = subprocess.Popen([sys.executable, "app/bridge/bridge.py", "--fake"], env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    try:
        assert _wait(port, p)
        c = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=AUTH, timeout=30)
        h = c.get("/health").json()
        assert h["fake"] is True and "explain" in h["tasks"]
        assert c.get("/exercises/d01").json()["exercise"]["solution"] and c.get("/courses/analyse").status_code == 200
        assert c.post("/follow_up", json={"classification": {"intent": "HINT"}}).json()["task"] == "hint"
        r = c.post("/run", json={"task": "explain", "inputs": {"question": "x"}, "context": {"fake_outcome": "not_in_course"}})
        assert json.loads(r.text.splitlines()[-1])["result"]["notes"]["retrieval"] == "no_relevant_chunks"
        page = c.post("/generate_page", json={"focus_point": {"concept_id": "puissance", "page_kind": "learn"}})
        assert json.loads(page.text.splitlines()[-1])["result"]["concept_id"] == "puissance"
        f = tmp_path / "n.md"
        f.write_text(NOTES, encoding="utf-8")
        up = c.post("/materials", json={"id": "n", "course_id": "analyse", "path": str(f), "visibility": "public",
                                         "learner_id": "p", "source": "professor"}).json()
        assert up["chunks"] and c.delete("/materials/n").json()["removed"] >= 1
    finally:
        p.terminate()
        p.wait(10)


def test_the_apps_course_map_is_generated_from_the_engines_course_graph():
    """app/client/src/lib/analyse.generated.js must match data/courses/analyse.yaml (run app/scripts/gen_course.py)."""
    spec = importlib.util.spec_from_file_location("gen_course", "app/scripts/gen_course.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert open(gen.OUT, encoding="utf-8").read() == gen.build()
