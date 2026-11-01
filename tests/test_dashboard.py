"""The professor dashboard's data: /review/flagged, POST /review/flagged/{id}, /review/content-gaps, /metrics/summary.
Mostly through the real Engine over a scripted provider (the flags come from the real guards); the store and the summary
also have plain unit tests, and the fake engine serves the same endpoints."""
import json
import time

import httpx
import pytest

from engine.fake import FakeEngine
from engine.result import Result
from metrics.review import ReviewStore, iso, parse_since
from metrics.summary import percentile, read_jsonl, summarize
from rag.chunker import chunk_dir
from rag.store import save_chunks
from services import engine_server as S
from tests import test_engine
from tests.conftest import chat_ok
from tests.test_engine import make_engine

TOKEN = "service-token-for-tests"
AUTH = {S.TOKEN_HEADER: TOKEN}
WRONG = "Par exemple, si f(x) = x³, alors f'(x) = 3x."                               # sympy refutes f'(x) = 3x
UNGROUNDED = "La dérivée se calcule comme f'(x) = lim(h → 0) [f(x + h) - f(x)]/h."
Q_WRONG = "Comment calculer la dérivée d'une fonction gamma ?"
Q_UNGROUNDED = "C'est quoi la dérivée de 1/x et de racine de x ?"
IDS = {"language": "FRENCH", "class_id": "C1", "course_id": "analyse", "learner_id": "L1"}


@pytest.fixture(autouse=True, scope="module")
def _index(tmp_path_factory):
    test_engine.INDEX = str(tmp_path_factory.mktemp("idx") / "chunks.json")
    save_chunks(test_engine.INDEX, chunk_dir("data/docs"))


def model(body):
    prompt = json.dumps(body["messages"], ensure_ascii=False)
    return chat_ok(WRONG if "gamma" in prompt else UNGROUNDED if "racine" in prompt else "Une dérivée mesure la pente.")


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=AUTH)


class Clock:
    """review timestamps: one second per call, from a fixed start."""
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        self.t += 1
        return self.t


async def flagged_engine(vt):
    eng, fake, sink = make_engine(vt, model)
    eng.review._clock = Clock()
    a = await eng.run("explain", {"question": Q_WRONG}, IDS)
    b = await eng.run("explain", {"question": Q_UNGROUNDED}, {**IDS, "class_id": "C2", "learner_id": "L2"})
    ok = await eng.run("explain", {"question": "C'est quoi le nombre dérivé ?"}, IDS)
    return eng, a, b, ok


# ---------------------------------------------------------------- the store
def test_parse_since():
    assert parse_since(None) is None and parse_since("") is None
    assert parse_since("1800000000") == 1_800_000_000.0 and parse_since(5) == 5.0
    assert parse_since("2026-11-02") == parse_since("2026-11-02T00:00:00Z") == parse_since("2026-11-02T01:00:00+01:00")
    assert iso(parse_since("2026-11-02T08:30:00Z")) == "2026-11-02T08:30:00Z"
    with pytest.raises(ValueError, match="ISO-8601"):
        parse_since("yesterday")


def result(task, **kw):
    return Result(task=task, request_id=kw.pop("request_id", "r1"), **kw)


def test_observe_records_flagged_answers_of_the_three_tasks_and_ignores_the_rest():
    s = ReviewStore(clock=Clock())
    bad = dict(degraded=True, degrade_reason="wrong_math", notes={"wrong_math": ["f'(x) = 3x"]})
    s.observe("explain", {"question": "q1"}, {"class_id": "C1"}, result("explain", request_id="e", text="body", **bad))
    s.observe("check_claim", {"claim": "c1"}, {"class_id": "C1"},
              result("check_claim", request_id="c", data={"verdict": "PARTLY", "correction": "x"},
                     degraded=True, degrade_reason="ungrounded_formula", notes={"ungrounded": ["f'(x) = 1/x"]}))
    s.observe("check_answer", {"problem": "p1", "steps": ["a"]}, {"class_id": "C1"},
              result("check_answer", request_id="a", data={"feedback": "f"}, degraded=True, degrade_reason="x",
                     notes={"degrade_reasons": ["x", "wrong_math"], "wrong_math": ["2 + 2 = 5"]}))
    s.observe("explain", {"question": "q"}, {}, result("explain", request_id="d", degraded=True, degrade_reason="deadline"))
    s.observe("hint", {"problem": "p"}, {}, result("hint", request_id="h", degraded=True, degrade_reason="wrong_math"))
    s.observe("explain", {"question": "q"}, {}, result("explain", request_id="hit", from_cache=True, **bad))
    page = s.flagged()
    assert [i["request_id"] for i in page["items"]] == ["a", "c", "e"] and page["total"] == 3     # newest first
    a = page["items"][0]
    assert a["task"] == "check_answer" and a["question"] == "p1" and a["spans"] == ["2 + 2 = 5"] and a["reasons"] == ["wrong_math"]
    assert json.loads(a["answer"]) == {"feedback": "f"}


def test_gaps_group_by_nearest_concept_with_counts_and_at_most_five_examples():
    s = ReviewStore(clock=Clock())
    for i in range(7):
        s.observe("explain", {"question": f"limite {i} ?"}, {"course_id": "analyse"},
                  result("explain", request_id=f"l{i}", notes={"retrieval": "no_relevant_chunks", "nearest_concept": "limite"}))
    s.observe("explain", {"question": "limite 0 ?"}, {"course_id": "analyse"},
              result("explain", request_id="dup", notes={"retrieval": "no_relevant_chunks", "nearest_concept": "limite"}))
    s.observe("check_claim", {"claim": "sans rapport"}, {"course_id": "analyse"},
              result("check_claim", request_id="n", data={"verdict": "NOT_IN_COURSE"}))
    s.observe("check_claim", {"claim": "pente"}, {"course_id": "physique"},
              result("check_claim", request_id="p", data={"verdict": "NOT_IN_COURSE"}, notes={"nearest_concept": "pente"}))
    g = s.gaps(course_id="analyse")
    assert g["total"] == 9 and [x["concept"] for x in g["gaps"]] == ["limite", None]
    first = g["gaps"][0]
    assert first["count"] == 8 and len(first["examples"]) == 5 and len(set(first["examples"])) == 5
    assert g["gaps"][1] == {"concept": None, "count": 1, "examples": ["sans rapport"], "last_at": g["gaps"][1]["last_at"]}
    assert [x["concept"] for x in s.gaps(course_id="physique")["gaps"]] == ["pente"]


def test_decisions_remove_the_item_are_idempotent_and_do_not_flip():
    s = ReviewStore(clock=Clock())
    s.observe("explain", {"question": "q"}, {}, result("explain", request_id="e", text="t", degraded=True,
                                                       degrade_reason="wrong_math"))
    with pytest.raises(KeyError):
        s.decide("nope", "approve")
    with pytest.raises(ValueError, match="action"):
        s.decide("e", "maybe")
    first = s.decide("e", "approve")
    assert first["already"] is False and s.flagged()["total"] == 0
    assert s.decide("e", "approve")["already"] is True
    with pytest.raises(ValueError, match="already decided: approve"):
        s.decide("e", "reject")


def test_decisions_survive_a_restart(tmp_path):
    path = str(tmp_path / "cache.sqlite")
    s = ReviewStore(path)
    s.observe("explain", {"question": "q"}, {}, result("explain", request_id="e", degraded=True, degrade_reason="wrong_math"))
    s.observe("explain", {"question": "q2"}, {}, result("explain", request_id="f", degraded=True, degrade_reason="wrong_math"))
    s.decide("e", "reject")
    s.close()
    assert [i["request_id"] for i in ReviewStore(path).flagged()["items"]] == ["f"]


# ---------------------------------------------------------------- GET /review/flagged
async def test_flagged_lists_what_the_real_guards_flagged_newest_first_with_every_field(vt):
    eng, a, b, ok = await flagged_engine(vt)
    assert a.degrade_reason == "wrong_math" and b.degrade_reason == "ungrounded_formula" and not ok.degraded
    async with client(S.create_app(eng, token=TOKEN)) as c:
        d = (await c.get("/review/flagged")).json()
    assert [i["request_id"] for i in d["items"]] == [b.request_id, a.request_id]            # newest first, ok one absent
    assert d["total"] == 2 and d["next_offset"] is None
    x, y = d["items"]
    assert set(x) == {"request_id", "task", "course_id", "class_id", "learner_id", "question", "answer", "spans",
                      "reasons", "created_at"}
    assert (x["task"], x["class_id"], x["learner_id"], x["course_id"]) == ("explain", "C2", "L2", "analyse")
    assert x["question"] == Q_UNGROUNDED and x["reasons"] == ["ungrounded_formula"] and x["spans"] == b.notes["ungrounded"]
    assert y["reasons"] == ["wrong_math"] and y["spans"] == ["f'(x) = 3x"] and y["question"] == Q_WRONG
    assert y["answer"] == a.text and y["created_at"].endswith("Z")


async def test_flagged_filters_by_class_course_and_since_and_paginates(vt):
    eng, a, b, _ = await flagged_engine(vt)
    async with client(S.create_app(eng, token=TOKEN)) as c:
        get = lambda **p: c.get("/review/flagged", params=p)
        assert [i["request_id"] for i in (await get(class_id="C1")).json()["items"]] == [a.request_id]
        assert [i["request_id"] for i in (await get(class_id="C2")).json()["items"]] == [b.request_id]
        assert (await get(class_id="C3")).json()["items"] == [] and (await get(course_id="physique")).json()["total"] == 0
        assert (await get(course_id="analyse")).json()["total"] == 2
        newer = eng.review.db.execute("SELECT created_at FROM review_items WHERE request_id=?", (b.request_id,)).fetchone()[0]
        assert [i["request_id"] for i in (await get(since=iso(newer))).json()["items"]] == [b.request_id]
        assert [i["request_id"] for i in (await get(since=str(newer + 1))).json()["items"]] == []
        p1 = (await get(limit=1)).json()
        p2 = (await get(limit=1, offset=p1["next_offset"])).json()
        assert [i["request_id"] for i in p1["items"] + p2["items"]] == [b.request_id, a.request_id]
        assert p1["next_offset"] == 1 and p2["next_offset"] is None and p2["total"] == 2
        assert (await get(since="yesterday")).status_code == 422
        assert (await get(limit=0)).status_code == 422 and (await get(limit=201)).status_code == 422
        assert (await get(offset=-1)).status_code == 422


# ---------------------------------------------------------------- POST /review/flagged/{id}
async def test_approve_records_the_decision_and_leaves_the_cache_alone(vt):
    eng, a, b, _ = await flagged_engine(vt)
    eng.cache.invalidate = lambda rid: pytest.fail("approve must not touch the cache")
    async with client(S.create_app(eng, token=TOKEN)) as c:
        r = await c.post(f"/review/flagged/{a.request_id}", json={"action": "approve"})
        assert r.status_code == 200 and r.json()["action"] == "approve" and r.json()["already"] is False
        left = (await c.get("/review/flagged")).json()
        assert [i["request_id"] for i in left["items"]] == [b.request_id]
        again = await c.post(f"/review/flagged/{a.request_id}", json={"action": "approve"})
        assert again.status_code == 200 and again.json()["already"] is True


async def test_reject_invalidates_the_cache_entry_and_records_the_decision(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok("Une dérivée mesure la pente."))
    good = await eng.run("hint", {"problem": "Dérive f(x) = 3x² + 5x"}, IDS)       # a cached answer to reject
    assert eng.cache.invalidate(good.request_id) == 1 and (await eng.run("hint", {"problem": "Dérive f(x) = 3x² + 5x"}, IDS)).request_id
    eng.review.observe("explain", {"question": "q"}, IDS, Result(task="explain", request_id=good.request_id, text="t",
                                                                degraded=True, degrade_reason="wrong_math"))
    calls = []
    real = eng.cache.invalidate
    eng.cache.invalidate = lambda rid: calls.append(rid) or real(rid)
    async with client(S.create_app(eng, token=TOKEN)) as c:
        r = await c.post(f"/review/flagged/{good.request_id}", json={"action": "reject"})
        assert r.status_code == 200 and r.json()["action"] == "reject" and "removed" in r.json()
        assert calls == [good.request_id]
        assert (await c.get("/review/flagged")).json()["total"] == 0
        again = await c.post(f"/review/flagged/{good.request_id}", json={"action": "reject"})
        assert again.json()["already"] is True and calls == [good.request_id]           # not invalidated twice


async def test_decision_errors(vt):
    eng, a, _, _ = await flagged_engine(vt)
    async with client(S.create_app(eng, token=TOKEN)) as c:
        post = lambda rid, body: c.post(f"/review/flagged/{rid}", content=body)
        assert (await post("nope", json.dumps({"action": "approve"}))).status_code == 404
        assert (await post(a.request_id, json.dumps({"action": "maybe"}))).status_code == 422
        assert (await post(a.request_id, "not json")).status_code == 422
        assert (await post(a.request_id, "{}")).status_code == 422
        await post(a.request_id, json.dumps({"action": "approve"}))
        flip = await post(a.request_id, json.dumps({"action": "reject"}))
        assert flip.status_code == 409 and flip.json()["error"] == "already_decided"


# ---------------------------------------------------------------- GET /review/content-gaps
async def test_content_gaps_group_by_the_nearest_concept_even_below_the_gate(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok("ok"))
    eng.review._clock = Clock()
    asked = ["la continuité d'une fonction ?", "explique la continuité d'une fonction", "Comment fonctionne un moteur à combustion ?"]
    for q in asked:
        r = await eng.run("explain", {"question": q}, IDS)
        assert r.notes["retrieval"] == "no_relevant_chunks" and not r.degraded
    await eng.run("explain", {"question": asked[0]}, {**IDS, "class_id": "C2"})
    async with client(S.create_app(eng, token=TOKEN)) as c:
        d = (await c.get("/review/content-gaps", params={"class_id": "C1", "course_id": "analyse"})).json()
        assert d["total"] == 3
        top = d["gaps"][0]
        assert top["concept"] == "nombre dérivé" and top["count"] == 2           # the best BM25 hit, below the gate
        assert top["examples"] == [asked[1], asked[0]]                            # newest first, no duplicates
        assert d["gaps"][1]["concept"] is None and d["gaps"][1]["examples"] == [asked[2]]
        assert (await c.get("/review/content-gaps", params={"class_id": "C2"})).json()["total"] == 1
        assert (await c.get("/review/content-gaps", params={"since": "2999-01-01"})).json() == {"gaps": [], "total": 0}


async def test_check_claim_not_in_course_counts_as_a_gap_with_the_best_chunk_s_concept(vt):
    verdict = json.dumps({"verdict": "NOT_IN_COURSE", "correction": ""})
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(verdict, 100, 20))
    r = await eng.run("check_claim", {"claim": "La dérivée de la fonction gamma est nulle."}, IDS)
    assert r.data["verdict"] == "NOT_IN_COURSE" and r.notes["nearest_concept"]
    none = await eng.run("check_claim", {"claim": "Le moteur à combustion chauffe."}, IDS)
    assert none.data["verdict"] == "NOT_IN_COURSE" and none.data["checked_by"] == "rules"
    async with client(S.create_app(eng, token=TOKEN)) as c:
        d = (await c.get("/review/content-gaps")).json()
    assert d["total"] == 2 and {g["concept"] for g in d["gaps"]} == {r.notes["nearest_concept"], None}


# ---------------------------------------------------------------- GET /metrics/summary
def rec(kind="run", **kw):
    return {"ts": 1000.0, "kind": kind, "request_id": "r", **kw}


def test_summary_numbers():
    records = [rec(task="explain", latency_ms=ms, model_class_used="x", degraded=False) for ms in (100, 200, 300, 400, 1000)]
    records += [rec(task="explain", latency_ms=50, from_cache=True),
                rec(task="hint", latency_ms=10, degraded=True, degrade_reason="quota_exhausted"),
                rec(task="hint", latency_ms=20, degraded=True, degrade_reason="deadline"),
                rec(task="hint", latency_ms=30, degraded=True, degrade_reason="quota_exhausted")]
    records += [rec("chat", model_class="compact", model="c-a", status=200), rec("chat", model_class="compact", model="c-a", status=200),
                rec("chat", model_class="midsize", model="m-a", status=200),
                rec("chat", model_class="midsize", model="m-a", status=429, outcome="quota_exhausted"),
                rec("chat", model_class="compact", model="c-a", outcome="cache_hit", from_cache=True),
                rec("route", task="explain"), rec("breaker")]
    s = summarize(records)
    assert s["runs"] == {"total": 9, "by_task": {"explain": 6, "hint": 3}}
    assert s["provider_requests"] == {"total": 4, "by_class": {"compact": 2, "midsize": 2}, "by_model": {"c-a": 2, "m-a": 2}}
    assert s["cache"] == {"hits": 1, "runs": 9, "hit_rate": 0.1111}
    assert s["degraded"]["count"] == 3 and s["degraded"]["share"] == 0.3333
    assert s["degraded"]["by_reason"] == {"deadline": {"count": 1, "share": 0.1111}, "quota_exhausted": {"count": 2, "share": 0.2222}}
    assert s["latency_ms"]["explain"] == {"count": 6, "p50": 200.0, "p95": 1000.0}      # 50 100 200 300 400 1000
    assert s["latency_ms"]["hint"] == {"count": 3, "p50": 20.0, "p95": 30.0}
    assert s["server_429"] == {"count": 1, "by_outcome": {"quota_exhausted": 1}}
    assert summarize([])["cache"]["hit_rate"] == 0.0 and summarize([])["latency_ms"] == {}


def test_summary_filters_and_percentile():
    records = [rec(task="a", latency_ms=1, ts=10.0, class_id="C1"), rec(task="a", latency_ms=2, ts=20.0, class_id="C2", course_id="x")]
    assert summarize(records, since=15.0)["runs"]["total"] == 1 and summarize(records, class_id="C1")["runs"]["total"] == 1
    assert summarize(records, course_id="x")["runs"]["by_task"] == {"a": 1}
    assert summarize(records, since=15.0)["since"] == iso(15.0)
    assert percentile([], 0.5) is None and percentile([5.0], 0.95) == 5.0 and percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0


async def test_summary_through_the_service_from_the_real_engines_records(vt):
    eng, fake, sink = make_engine(vt, model)
    await eng.run("hint", {"problem": "Dérive f(x) = 3x² + 5x"}, IDS)
    await eng.run("hint", {"problem": "Dérive f(x) = 3x² + 5x"}, IDS)                      # a cache hit
    await eng.run("explain", {"question": "C'est quoi le nombre dérivé ?"}, {**IDS, "class_id": "C2"})
    async with client(S.create_app(eng, token=TOKEN)) as c:
        d = (await c.get("/metrics/summary")).json()
        assert d["runs"]["total"] == 3 and d["runs"]["by_task"] == {"explain": 1, "hint": 2}
        assert d["cache"]["hits"] == 1 and d["provider_requests"]["total"] == len(fake.requests) == 2
        assert d["provider_requests"]["by_class"] == {"compact": 1, "midsize": 1} and d["server_429"]["count"] == 0
        assert set(d["latency_ms"]) == {"explain", "hint"}
        assert (await c.get("/metrics/summary", params={"class_id": "C2"})).json()["runs"]["total"] == 1
        assert (await c.get("/metrics/summary", params={"since": "2999-01-01"})).json()["runs"]["total"] == 0
        assert (await c.get("/metrics/summary", params={"since": "soon"})).status_code == 422


def test_summary_reads_the_metrics_jsonl_and_skips_a_half_written_line(tmp_path):
    from metrics import CallRecord, JsonlSink
    path = str(tmp_path / "calls.jsonl")
    sink = JsonlSink(path)
    sink.write(CallRecord(request_id="a", kind="run", task="hint", latency_ms=12.0))
    sink.write(CallRecord(request_id="a", kind="chat", model_class="compact", model="c", status=429, outcome="quota_exhausted"))
    sink.close()
    with open(path, "a") as f:
        f.write('{"kind": "run", "tas')
    s = summarize(read_jsonl(path))
    assert s["runs"]["total"] == 1 and s["server_429"]["count"] == 1 and read_jsonl(str(tmp_path / "none.jsonl")) == []


def test_ten_thousand_records_summarize_in_well_under_200_ms(tmp_path):
    """The dashboard reads the JSONL on every call; at this size no index is needed."""
    path = tmp_path / "calls.jsonl"
    with open(path, "w") as f:
        for i in range(10_000):
            f.write(json.dumps({"ts": 1000.0 + i, "kind": "run" if i % 2 else "chat", "request_id": f"r{i}", "task": "explain",
                                "model_class": "midsize", "model": "m", "status": 429 if i % 50 == 0 else 200,
                                "outcome": "ok", "latency_ms": float(i % 900), "degraded": i % 7 == 0,
                                "degrade_reason": "deadline" if i % 7 == 0 else None, "class_id": "C1",
                                "learner_id": f"L{i % 300}", "tokens_in": 900, "tokens_out": 120}) + "\n")
    t = time.perf_counter()
    s = summarize(read_jsonl(str(path)))
    took = time.perf_counter() - t
    assert s["runs"]["total"] == 5000 and took < 0.2, f"{took * 1000:.0f} ms"


# ---------------------------------------------------------------- auth + the fake engine
async def test_every_dashboard_route_needs_the_token(vt):
    eng, a, _, _ = await flagged_engine(vt)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=S.create_app(eng, token=TOKEN)), base_url="http://127.0.0.1") as c:
        for method, url in (("GET", "/review/flagged"), ("POST", f"/review/flagged/{a.request_id}"),
                            ("GET", "/review/content-gaps"), ("GET", "/metrics/summary")):
            assert (await c.request(method, url, content='{"action":"approve"}')).status_code == 401
        assert (await c.request("GET", "/review/flagged", headers={S.TOKEN_HEADER: "wrong"})).status_code == 401
    assert eng.review.flagged()["total"] == 2                                            # nothing happened


async def test_the_fake_engine_serves_the_same_endpoints():
    fake = FakeEngine(speed=0)
    async with client(S.create_app(fake, token=TOKEN, fake=True)) as c:
        bad = (await c.post("/run", json={"task": "explain", "inputs": {"question": "dérivée de x³ ?"},
                                          "context": {**IDS, "fake_outcome": "wrong_math"}})).json()
        await c.post("/run", json={"task": "explain", "inputs": {"question": "les intégrales ?"},
                                   "context": {**IDS, "fake_outcome": "not_in_course"}})
        await c.post("/run", json={"task": "check_claim", "inputs": {"claim": "x"}, "context": {**IDS, "fake_outcome": "NOT_IN_COURSE"}})
        await c.post("/run", json={"task": "hint", "inputs": {"problem": "p"}, "context": IDS})
        assert bad["degrade_reason"] == "wrong_math"
        flagged = (await c.get("/review/flagged", params={"class_id": "C1"})).json()
        assert [i["request_id"] for i in flagged["items"]] == [bad["request_id"]] and flagged["items"][0]["spans"] == ["f'(x) = 5x"]
        gaps = (await c.get("/review/content-gaps")).json()
        assert gaps["total"] == 2 and {g["concept"] for g in gaps["gaps"]} == {"nombre dérivé", "produit"}
        rej = await c.post(f"/review/flagged/{bad['request_id']}", json={"action": "reject"})
        assert rej.status_code == 200 and fake.cache.invalidated == [bad["request_id"]]
        assert (await c.get("/review/flagged")).json()["total"] == 0
        summary = (await c.get("/metrics/summary")).json()
        assert summary["runs"]["total"] == 4 and summary["degraded"]["by_reason"]["wrong_math"]["count"] == 1
