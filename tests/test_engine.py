"""engine.run end to end against fake providers (no LLM). Virtual time: retries cost nothing."""
import json
import random

import pytest

from engine import Engine, Priority
from engine.prompts import PromptTemplate
from engine.result import Result
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient
from gatekeeper.errors import QuotaInfo
from metrics import MemorySink
from rag.chunker import chunk_dir
from rag.store import save_chunks
from tests.conftest import FnProvider, chat_ok, error, make_settings

GOOD = {"intent": "HINT", "language": "FRENCH", "confusion": "MEDIUM", "complexity": 2, "confidence": 0.9}
MSG = {"message": "je bloque sur cet exercice de suites, une piste ?"}


INDEX = None


@pytest.fixture(autouse=True, scope="module")
def _index(tmp_path_factory):
    """A real index of data/docs for the grounded (explain) tests."""
    global INDEX
    INDEX = str(tmp_path_factory.mktemp("idx") / "chunks.json")
    save_chunks(INDEX, chunk_dir("data/docs"))


def make_engine(vt, fn, registry=None, **env):
    s = make_settings(**{"RAG_INDEX_PATH": INDEX, **env})
    sink = MemorySink()
    fake = FnProvider(fn)
    eng = Engine.create(s, client=LLMClient(s, fake), sink=sink, cache=ExactCache(":memory:", s.cache_ttl_s),
                        registry=registry, clock=vt.clock, sleep=vt.sleep, rng=random.Random(1))
    return eng, fake, sink


def always(resp_factory):
    return lambda body: resp_factory()


def by_model(**replies):
    """model name -> response factory; fallback: 200 'ok'."""
    return lambda body: replies.get(body["model"].replace("-", "_"), lambda: chat_ok("ok"))()


def runs(sink):
    return [r for r in sink.records if r.kind == "run"]


# ------------------------------------------------------------------ classify
async def test_classify_happy_path_events_json_mode_and_cache(vt):
    eng, fake, sink = make_engine(vt, always(lambda: chat_ok(json.dumps(GOOD), 50, 20)))
    r = await eng.run("classify", MSG)
    assert r.data == GOOD and not r.degraded and not r.from_cache
    assert r.model_class_used == "compact"
    assert r.event_names == ["checking_cache", "queued", "generating"]
    assert fake.requests[0]["body"]["response_format"] == {"type": "json_object"}
    assert (r.tokens_in, r.tokens_out) == (50, 20)

    r2 = await eng.run("classify", MSG)
    assert r2.from_cache and r2.data == r.data and r2.event_names == ["checking_cache", "cache_hit"]
    assert len(fake.requests) == 1 and (r2.tokens_in, r2.tokens_out) == (0, 0)
    hit = [x for x in sink.records if x.outcome == "cache_hit" and x.kind == "chat"]
    assert len(hit) == 1 and hit[0].from_cache and hit[0].task == "classify"


async def test_classify_normalizes_sloppy_but_valid_json_without_repair(vt):
    sloppy = '```json\n{"intent": "answer attempt", "language": "french", "confusion": "low", ' \
             '"complexity": "2", "confidence": 1.4}\n```'
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok(sloppy)))
    r = await eng.run("classify", {"message": "j'ai trouvé x = 2, c'est juste ?"})
    assert r.data["intent"] == "ANSWER_ATTEMPT" and r.data["complexity"] == 2 and r.data["confidence"] == 0.9
    assert r.notes["model_confidence"] == 1.0                            # capped: 1.0 is for rule matches
    assert not r.repaired and len(fake.requests) == 1


async def test_classify_invalid_json_one_repair_on_compact(vt):
    replies = iter([chat_ok("intent: HINT (sure!)"), chat_ok(json.dumps(GOOD))])
    eng, fake, sink = make_engine(vt, lambda body: next(replies))
    r = await eng.run("classify", MSG)
    assert r.repaired and not r.safe_default and r.data["intent"] == "HINT"
    assert "repairing_json" in r.event_names
    repair = fake.requests[1]["body"]["messages"]
    assert repair[-2] == {"role": "assistant", "content": "intent: HINT (sure!)"} and "not valid" in repair[-1]["content"]
    chats = [x for x in sink.records if x.kind == "chat"]
    assert [x.purpose for x in chats] == [None, "repair"] and chats[1].model_class == "compact"
    assert runs(sink)[-1].outcome == "repaired"


async def test_classify_repair_fails_safe_default_counted_and_not_cached(vt):
    eng, fake, sink = make_engine(vt, always(lambda: chat_ok('{"intent": "WHATEVER"}')))
    r = await eng.run("classify", MSG)
    assert r.safe_default and r.data["intent"] == "EXPLAIN" and r.data["confidence"] == 0
    assert r.data["language"] == "FRENCH"                     # from rules, still useful
    assert r.event_names[-3:] == ["queued", "generating", "using_safe_default"]
    assert "repairing_json" in r.event_names
    assert runs(sink)[-1].outcome == "safe_default" and len(fake.requests) == 2
    await eng.run("classify", MSG)
    assert len(fake.requests) == 4                            # defaults are never cached


async def test_classify_skips_json_mode_for_models_that_break_on_it(vt):
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok(json.dumps(GOOD))), MODELS_WITHOUT_JSON_MODE="compact-a")
    await eng.run("classify", MSG)
    assert "response_format" not in fake.requests[0]["body"]


async def test_classify_language_corrected_by_script_rules(vt):
    wrong = {**GOOD, "language": "FRENCH"}
    eng, _, _ = make_engine(vt, always(lambda: chat_ok(json.dumps(wrong))))
    r = await eng.run("classify", {"message": "شنوة معناها الدالة الأصلية؟ ما فهمتش"})
    assert r.data["language"] == "DARIJA"
    assert r.notes["model_language"] == "FRENCH" and r.notes["language_source"] == "rules"
    assert r.data["confusion"] == "HIGH" and r.notes["model_confusion"] == "MEDIUM"   # "ما فهمتش" 


async def test_greeting_costs_no_request(vt):
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok(json.dumps(GOOD))))
    r = await eng.run("classify", {"message": "Merci !"})
    assert r.data["intent"] == "CHITCHAT" and r.model_class_used is None and fake.requests == []
    assert r.event_names == ["answered_by_rules"]


# ------------------------------------------------------------------ hint
async def test_hint_prompt_language_example_and_cleanup(vt):
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok('Indice : "Que vaut f(0) ?"')))
    r = await eng.run("hint", {"problem": "Calculer la limite de f(x) = (x²-1)/(x-1) quand x tend vers 1"},
                      {"level": "lycée"})
    assert r.text == "Que vaut f(0) ?" and not r.degraded
    msgs = fake.requests[0]["body"]["messages"]
    assert "Reply in French." in msgs[0]["content"] and "lycée" in msgs[0]["content"]
    assert msgs[1]["role"] == "user" and msgs[2]["role"] == "assistant"          # one example shot
    assert "Student's attempt: (none yet)" in msgs[3]["content"]


async def test_hint_never_reveals_the_stored_solution(vt):
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok("Tu y es presque : x = -3 car 2x = -6.")))
    inputs = {"problem": "Résoudre 2x + 6 = 0", "attempt": "x = 3", "solution": "x = -3"}
    r = await eng.run("hint", inputs, {"language": "french"})
    assert r.degraded and r.degrade_reason == "hint_guard:answer_leak"
    assert "-3" not in r.text and r.text.startswith("Relis")
    await eng.run("hint", inputs, {"language": "french"})
    assert len(fake.requests) == 2                                               # guarded output not cached


async def test_hint_language_drift_falls_back_to_template(vt):
    eng, _, _ = make_engine(vt, always(lambda: chat_ok("What is the derivative of the function here?")))
    r = await eng.run("hint", {"problem": "c'est quoi la dérivée de x² ?"}, {"language": "FRENCH"})
    assert r.degrade_reason == "hint_guard:language_drift" and r.text.startswith("Relis")


async def test_hint_replies_in_arabizi_for_arabizi_learner(vt):
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok("Chnowa ysir ki t3awedh x b 1?")))
    r = await eng.run("hint", {"problem": "chnowa ya3ni limite mta3 (x²-1)/(x-1) ki x yemchi l 1"})
    assert "Arabizi" in fake.requests[0]["body"]["messages"][0]["content"] and not r.degraded


# ------------------------------------------------------------------ resilience: never raise
Q_EXPLAIN = {"question": "C'est quoi le taux d'accroissement et le nombre dérivé ?"}
SAT = lambda: error(503, "model_saturated", "busy", {"Retry-After": "4"})


def chunk_texts():
    return [c.text for c in chunk_dir("data/docs")]


def assert_verbatim_fast_mode(r):
    assert (r.mode, r.degraded, r.followup_available) == ("fast_extractive", True, True)
    framing, body, *practice = r.text.split("\n\n")
    lines = body.splitlines()
    if "exercise" in r.data:                                              # bank practice item, appended
        assert practice == [f"À toi : {r.data['exercise']['statement']}"]
    assert framing == r.data["framing"] and lines and len(lines) == len(r.data["excerpts"])
    texts = chunk_texts()
    for line, ex in zip(lines, r.data["excerpts"]):
        assert line == f"- {ex['text']}"
        assert any(ex["text"] in t for t in texts), ex["text"]               # verbatim from the course


async def test_both_classes_saturated_explain_still_gets_extractive_answer(vt):
    eng, fake, sink = make_engine(vt, always(SAT), SATURATION_RETRIES=1, COMPACT_MAX_RETRIES=3)
    r = await eng.run("explain", Q_EXPLAIN)                               # must not raise
    assert r.degrade_reason == "midsize_saturated"
    assert_verbatim_fast_mode(r)
    assert r.data["framing"] == "Voici les passages du cours qui répondent à ta question :"   # framing call failed
    assert fake.models() == ["mid-a"] * 2 + ["compact-a"]                # framing: no saturation retries
    assert Result.model_validate(r.model_dump()) == r                    # well-formed, serializable
    assert runs(sink)[-1].mode == "fast_extractive"
    await eng.run("explain", Q_EXPLAIN)
    assert len(fake.requests) == 6                                       # never cached


async def test_midsize_saturated_explain_falls_back_to_fast_mode_visibly(vt):
    eng, fake, sink = make_engine(vt, by_model(mid_a=SAT, compact_a=lambda: chat_ok("Voici ce que dit le cours :")))
    r = await eng.run("explain", Q_EXPLAIN)
    assert (r.model_class_used, r.degrade_reason) == ("compact", "midsize_saturated")
    assert r.data["framing"] == "Voici ce que dit le cours :"
    assert_verbatim_fast_mode(r)
    assert r.event_names == ["retrieving", "checking_cache", "partial_result", "queued", "generating",
                             "retry_after_interval", "queued", "generating", "gave_up", "falling_back_to_compact",
                             "fast_extractive", "queued", "generating"]
    framing_req = fake.requests[-1]["body"]
    assert framing_req["max_tokens"] == 40 and "Course material" not in framing_req["messages"][-1]["content"]
    routes = [x.detail for x in sink.records if x.kind == "route"]
    assert routes == ["table: explain -> midsize", "midsize saturated after retries -> fallback compact"]


async def test_hint_both_classes_saturated_is_service_busy(vt):
    eng, _, _ = make_engine(vt, always(SAT))
    r = await eng.run("hint", {"problem": "Résoudre 2x + 6 = 0"})
    assert (r.degrade_reason, r.mode) == ("service_busy", None) and r.eta_seconds >= 4


async def test_compact_saturated_classify_degrades_with_safe_default(vt):
    eng, fake, _ = make_engine(vt, always(SAT), COMPACT_MAX_RETRIES=3)
    t0 = vt.t
    r = await eng.run("classify", MSG)
    assert (r.degraded, r.degrade_reason, r.safe_default) == (True, "service_busy", True)
    assert r.data["intent"] == "EXPLAIN" and r.eta_seconds >= 4
    assert len(fake.requests) == 4 and vt.t - t0 == pytest.approx(12)    # Retry-After honored 3 times


async def test_compact_saturated_hint_gives_template_not_exception(vt):
    eng, _, _ = make_engine(vt, always(SAT))
    r = await eng.run("hint", {"problem": "Solve x + 2 = 5"})
    assert r.degrade_reason == "service_busy" and r.text.startswith("Reread") and r.eta_seconds


@pytest.mark.parametrize("resp,reason,eta", [
    (lambda: error(401, "auth_error", "bad key"), "provider_error", False),
    (lambda: error(429, "quota_exhausted", "Team quota exhausted", {"Retry-After": "900"}), "quota_exhausted", True),
    (lambda: error(500, "server_error", "boom"), "service_unavailable", True),
    (lambda: error(400, "invalid_request_error", "max_tokens exceeds ceiling 4"), "request_too_large", False),
])
async def test_run_never_raises_provider_errors(vt, resp, reason, eta):
    eng, _, _ = make_engine(vt, always(resp))
    r = await eng.run("classify", MSG)
    assert r.degraded and r.degrade_reason == reason and r.data["intent"] == "EXPLAIN"
    assert (r.eta_seconds is not None) == eta
    if reason == "quota_exhausted":
        assert r.eta_seconds >= 900


async def test_quota_low_serves_explain_in_fast_mode_unless_escalated(vt):
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok("Voici le cours :")), QUOTA_LOW_THRESHOLD=5)
    eng.caller.limiter.observe("chat", QuotaInfo(remaining_requests=1))
    r = await eng.run("explain", Q_EXPLAIN)
    assert (r.model_class_used, r.degrade_reason) == ("compact", "quota_low")
    assert_verbatim_fast_mode(r)
    assert r.event_names[:5] == ["retrieving", "checking_cache", "partial_result", "falling_back_to_compact",
                                 "fast_extractive"]
    assert fake.models() == ["compact-a"]                                # one framing call, no mid-size
    r2 = await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?"}, {"escalate": True})
    assert r2.model_class_used == "midsize" and not r2.degraded and r2.mode is None


# ------------------------------------------------------------------ interface
async def test_caller_bugs_raise_value_error_before_any_request(vt):
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok("x")))
    with pytest.raises(ValueError):
        await eng.run("summary", {"text": "x"})                 # in the routing table, not implemented yet
    with pytest.raises(ValueError):
        await eng.run("hint", {"attempt": "x=1"})
    with pytest.raises(ValueError):
        await eng.run("hint", {"problem": "p"}, priority="URGENT")
    assert fake.requests == []


async def test_live_event_callback_and_background_priority(vt):
    seen = []
    eng, _, sink = make_engine(vt, always(lambda: chat_ok(json.dumps(GOOD))))
    r = await eng.run("classify", MSG, priority=Priority.BACKGROUND, on_event=seen.append)
    assert [e.name for e in seen] == r.event_names
    assert {x.priority for x in sink.records if x.kind in ("chat", "run")} == {"BACKGROUND"}


def test_prompt_versions_loaded():
    from engine.prompts import load_prompt
    for name in ("classify", "hint", "compress", "explain", "quiz", "grade"):
        p: PromptTemplate = load_prompt("prompts", name)
        assert p.task == name and p.version


async def test_hint_repetition_loop_is_guarded_and_not_cached(vt):
    loop = "Mab3na, 0/0 chtouf, tafawt 9iwa x = 1, kem 9iwa x = 1, kem 9iwa x = 1, kem 9iwa x = 1, kem 9"
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok(loop, 100, 80)))
    inputs = {"problem": "chnowa limite mta3 (x²-1)/(x-1) ki x yemchi l 1 ?"}
    r = await eng.run("hint", inputs)
    assert r.degrade_reason == "hint_guard:degenerate" and r.text.startswith("3awed")
    await eng.run("hint", inputs)
    assert len(fake.requests) == 2


async def test_hint_too_long_keeps_leading_question(vt):
    long = "Que vaut le numérateur en x = 1 ? " + " ".join(f"mot{i}" for i in range(50))
    eng, _, _ = make_engine(vt, always(lambda: chat_ok(long)))
    r = await eng.run("hint", {"problem": "limite de (x²-1)/(x-1) en 1"}, {"language": "FRENCH"})
    assert r.text == "Que vaut le numérateur en x = 1 ?" and not r.degraded


async def test_identical_concurrent_requests_are_coalesced(vt):
    import asyncio
    eng, fake, sink = make_engine(vt, always(lambda: chat_ok(json.dumps(GOOD))))
    rs = await asyncio.gather(*[eng.run("classify", MSG) for _ in range(5)])
    assert len(fake.requests) == 1                                       # one provider call for 5 callers
    assert sum(r.from_cache for r in rs) == 4 and all(r.data == rs[0].data for r in rs)
    assert sum("waiting_identical_request" in r.event_names for r in rs) == 4


async def test_followers_of_a_degraded_leader_make_their_own_call(vt):
    import asyncio
    calls = []

    def fn(body):
        calls.append(1)
        return SAT() if len(calls) <= 4 else chat_ok(json.dumps(GOOD))   # leader: 1 + 3 compact retries fail
    eng, fake, _ = make_engine(vt, fn, COMPACT_MAX_RETRIES=3)
    a, b = await asyncio.gather(eng.run("classify", MSG), eng.run("classify", MSG))
    assert a.degrade_reason == "service_busy" and not b.degraded and not b.from_cache


async def test_fast_mode_framing_line_is_cached_but_result_is_not(vt):
    eng, fake, sink = make_engine(vt, by_model(mid_a=SAT, compact_a=lambda: chat_ok("Voici ce que dit le cours :")),
                                  BREAKER_THRESHOLD=100)
    r1 = await eng.run("explain", Q_EXPLAIN)
    r2 = await eng.run("explain", Q_EXPLAIN)
    assert r1.mode == r2.mode == "fast_extractive" and not r2.from_cache          # result not cached
    assert fake.models().count("compact-a") == 1 and fake.models().count("mid-a") == 4   # framing cached
    assert [x.purpose for x in sink.records if x.outcome == "cache_hit"] == ["framing"]
