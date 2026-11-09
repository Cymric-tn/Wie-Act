"""The diagram package inside the project: the real course graph, the worker process and its time limit, the bridge routes."""
import importlib.util
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "app"))
from diagrams import Diagram  # noqa: E402
from diagrams.course import load_repo_course  # noqa: E402
from diagrams.worker import handle  # noqa: E402

spec = importlib.util.spec_from_file_location("lumi_bridge_diagrams", ROOT / "app/bridge/bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

COURSES = str(ROOT / "data" / "courses")
SLOW = "x+((((9**99)+1)**99+1)**99+1)**99"     # passes the parser's whitelist, then runs for minutes
TOKEN = "diagram-token-for-tests"


def build(text, **kw):
    return handle({"op": "build", "text": text, "course_id": "analyse", **kw}, COURSES)


def test_concept_map_is_the_real_course_without_its_drafts():
    raw = load_repo_course(COURSES, "analyse")
    ids = {c["id"] for u in raw["units"] for c in u["concepts"]}
    assert "nombre dérivé" in ids and "limite" not in ids             # `limite` is status: draft in data/courses/analyse.yaml
    nodes = build("carte des notions", lang="fr")["spec"]["payload"]["nodes"]
    assert {n["id"] for n in nodes} == ids and all(n["label"] != n["id"] or n["id"] == "tangente" for n in nodes if n["id"] == "produit")


def test_titles_follow_the_language_and_focus_is_found():
    assert build("concept map around product rule", lang="en")["spec"]["payload"]["focus"] == "produit"
    labels = {n["id"]: n["label"] for n in build("خريطة المفاهيم", lang="ar")["spec"]["payload"]["nodes"]}
    assert labels["tangente"] == "المماس"


def test_mastery_uses_the_real_concept_ids():
    nodes = build("carte des notions", lang="fr", mastery={"nombre dérivé": 0.7})["spec"]["payload"]["nodes"]
    assert [n["mastery"] for n in nodes if n["id"] == "nombre dérivé"] == [0.7]


@pytest.mark.parametrize("course_id", ["../../etc/passwd", "nope", "", None])
def test_unknown_or_unsafe_course_is_refused_for_a_map_only(course_id):
    assert "refused" in handle({"op": "build", "text": "carte des notions", "course_id": course_id, "lang": "fr"}, COURSES)
    assert "spec" in handle({"op": "build", "text": "courbe de x^2", "course_id": course_id, "lang": "fr"}, COURSES)


@pytest.mark.parametrize("lang,word", [("fr", "lire"), ("en", "read"), ("ar", "قراءة")])
def test_unreadable_expression_is_refused_in_the_learners_language(lang, word):
    reply = build("courbe de x.x.x", lang=lang, type="function_graph")
    assert word in reply["refused"]


def test_check_refuses_anything_but_a_table():
    graph = build("courbe de x^2", lang="en")["spec"]
    assert "refused" in handle({"op": "check", "spec": graph, "answer": {}, "lang": "en"}, COURSES)
    assert "refused" in handle({"op": "check", "spec": {"forged": True}, "answer": {}, "lang": "en"}, COURSES)


def test_schema_file_matches_the_models():
    assert json.loads((ROOT / "docs/diagram.schema.json").read_text(encoding="utf-8")) == Diagram.model_json_schema(by_alias=True)


class StubHost:
    def __init__(self, worker):
        self.diagrams = worker


@pytest.fixture
def rig():
    worker = bridge.DiagramWorker(COURSES, timeout_s=2.5)
    server = bridge.serve(StubHost(worker), TOKEN, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", headers={bridge.TOKEN_HEADER: TOKEN}, timeout=90) as http:
        yield http, worker
    server.shutdown()
    worker.close()


def test_bridge_builds_checks_and_refuses(rig):
    http, _ = rig
    table = http.post("/diagrams", json={"text": "tableau de variations de x^3-3x", "lang": "fr", "practice": True})
    assert table.status_code == 200 and table.json()["spec"]["payload"]["mode"] == "practice"
    empty = http.post("/diagrams/check", json={"spec": table.json()["spec"], "answer": {}})
    assert empty.status_code == 200 and empty.json()["correct"] is False
    refused = http.post("/diagrams", json={"text": "bonjour", "lang": "fr"})
    assert refused.status_code == 400 and "Essayez" in refused.json()["error"]


def test_a_runaway_expression_is_killed_and_the_next_request_works(rig):
    http, worker = rig
    assert http.post("/diagrams", json={"text": "courbe de x^2", "lang": "fr"}).status_code == 200    # started: sympy is loaded
    first = worker._proc
    started = time.monotonic()
    slow = http.post("/diagrams", json={"text": SLOW, "lang": "fr", "type": "function_graph"})
    assert slow.status_code == 503 and slow.json() == {"error": "diagram_timeout"}
    assert time.monotonic() - started < 6
    assert first.poll() is not None
    assert http.post("/diagrams", json={"text": "courbe de x^2", "lang": "fr"}).status_code == 200


def test_a_crashed_worker_is_replaced(rig):
    http, worker = rig
    assert http.post("/diagrams", json={"text": "courbe de x^2", "lang": "fr"}).status_code == 200
    worker._proc.kill()
    worker._proc.wait()
    assert http.post("/diagrams", json={"text": "courbe de x^2", "lang": "fr"}).status_code == 200


def test_the_routes_need_the_token(rig):
    http, _ = rig
    assert httpx.post(str(http.base_url).rstrip("/") + "/diagrams", json={"text": "courbe de x^2"}).status_code == 401
