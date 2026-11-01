"""Output caps the platform honours, truncation detection, context window and the input-truncation detector (v1.1)."""
import json
import random

import pytest

from config import ModelClass, load_settings
from engine import Engine, __version__
from engine.tasks import trim_to_last_sentence
from gatekeeper.cache import ExactCache
from gatekeeper.client import ChatRequest, LLMClient, estimate_tokens, set_chars_per_token
from gatekeeper.limiter import Limiter
from gatekeeper.retry import CallContext, RetryingCaller, RetryPolicy
from metrics import MemorySink
from tests.conftest import BASE_ENV, FakeProvider, FnProvider, chat_ok, make_settings

CLASSIFY_JSON = json.dumps({"intent": "EXPLAIN", "language": "FRENCH", "confusion": "LOW",
                            "complexity": 1, "confidence": 0.9})


def make_engine(vt, fn, **env):
    s = make_settings(**env)
    fake, sink = FnProvider(fn), MemorySink()
    eng = Engine.create(s, client=LLMClient(s, fake), sink=sink, cache=ExactCache(":memory:", s.cache_ttl_s),
                        clock=vt.clock, sleep=vt.sleep, rng=random.Random(1))
    return eng, fake, sink


def caps(body):
    return body.get("max_tokens"), (body.get("options") or {}).get("num_predict")


def test_version_is_1_3():
    assert __version__ == "1.3"


# ------------------------------------------------------------------------------ how the cap is sent
@pytest.mark.parametrize("mode,expected", [
    ("both", (40, 40)), ("options", (None, 40)), ("max_tokens", (40, None))])
def test_output_cap_mode_selects_the_fields(mode, expected):
    c = LLMClient(make_settings(OUTPUT_CAP_MODE=mode))
    body = c.chat_body(ChatRequest(ModelClass.COMPACT, ({"role": "user", "content": "x"},), max_tokens=40))
    assert caps(body) == expected
    assert body["stream"] is False


def test_default_mode_is_both_and_bad_mode_is_refused():
    assert make_settings().output_cap_mode == "both"
    with pytest.raises(ValueError):
        make_settings(OUTPUT_CAP_MODE="stop")


def test_the_class_default_cap_still_applies_when_a_request_has_none():
    body = LLMClient(make_settings()).chat_body(ChatRequest(ModelClass.MIDSIZE, ({"role": "user", "content": "x"},)))
    assert caps(body) == (550, 550)


# ------------------------------------------------------------------------------ per-task caps
def test_default_task_caps():
    s = make_settings()
    assert dict(s.task_max_tokens) == {"classify": 50, "hint": 80, "grade": 120, "compress": 30, "explain": 350,
                                       "quiz_intro": 60, "framing": 40,
                                           "ocr_transcribe": 700, "check_claim": 120, "check_answer": 80}


@pytest.mark.parametrize("task,inputs,context,reply,cap,var", [
    ("classify", {"message": "J'ai trouvé que la dérivée de 3x² est 6x, c'est bon ?"}, {}, CLASSIFY_JSON, 50,
     "CLASSIFY_MAX_TOKENS"),
    ("hint", {"problem": "Calculer la dérivée de f(x) = 7x^3 - 2x + 9", "attempt": "21x"}, {"language": "FRENCH"},
     "Pense à la règle des puissances.", 80, "HINT_MAX_TOKENS"),
    ("explain", {"question": "Comment trouver l'équation de la tangente ?"}, {"level": "lycée"},
     "La tangente passe par le point de la courbe.", 350, "EXPLAIN_MAX_TOKENS"),
])
async def test_each_task_sends_its_own_cap_and_the_env_overrides_it(vt, task, inputs, context, reply, cap, var):
    for env, expected in (({}, cap), ({var: 33}, 33)):
        eng, fake, _ = make_engine(vt, lambda b: chat_ok(reply, 100, 5), **env)
        await eng.run(task, inputs, context)
        assert caps(fake.requests[0]["body"]) == (expected, expected)


async def test_compress_cap(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok('{"keep": [1]}', 100, 5))
    chunks = [{"chunk_id": "a", "text": "H — S\nLa dérivée d'une constante est 0. Le ciel est bleu."}]
    await eng.run("compress", {"question": "dérivée constante", "chunks": chunks})
    assert caps(fake.requests[0]["body"]) == (30, 30)


async def test_quiz_intro_and_fast_framing_caps(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok("Voici un exercice pour toi.", 100, 8))
    await eng.run("quiz", {"message": "donne moi un exercice sur les polynômes", "intro": "llm"},
                  {"language": "FRENCH"})
    assert caps(fake.requests[0]["body"]) == (60, 60)
    eng, fake, _ = make_engine(vt, lambda b: chat_ok("Voici l'essentiel.", 100, 8), QUIZ_INTRO_MAX_TOKENS=25)
    await eng.run("quiz", {"message": "donne moi un exercice sur les polynômes", "intro": "llm"},
                  {"language": "FRENCH"})
    assert caps(fake.requests[0]["body"]) == (25, 25)


# ------------------------------------------------------------------------------ truncated output
def caller_for(vt, fake, **env):
    s = make_settings(**env)
    sink = MemorySink()
    return RetryingCaller(LLMClient(s, fake), Limiter(s, vt.clock, vt.sleep), sink, RetryPolicy.from_settings(s),
                          s.input_token_budget, vt.clock, vt.sleep, random.Random(1)), sink


MSG = ({"role": "user", "content": "explain limits"},)


@pytest.mark.parametrize("tout,cut", [(79, False), (80, True), (81, True)])
async def test_completion_tokens_at_or_above_the_cap_means_truncated_whatever_finish_reason_says(vt, tout, cut):
    rc, sink = caller_for(vt, FakeProvider([chat_ok("some text", 20, tout)]))      # finish_reason is always "stop"
    ev = []
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, MSG, max_tokens=80),
                      CallContext(task="hint", on_event=lambda n, d: ev.append(n)))
    assert r.truncated is cut and sink.records[0].truncated_output is cut
    assert ("output_truncated" in ev) is cut


@pytest.mark.parametrize("text,expected", [
    ("Premier point. Deuxième point incomplet et", "Premier point."),
    ("Une phrase. Une autre ? Et la fin coup", "Une phrase. Une autre ?"),
    ("Valeur 0.5 puis 2.5 mais la suite est cou", "Valeur 0.5 puis 2.5 mais la suite est cou"),   # decimals are no sentence end
    ("Pas de point final du tout", "Pas de point final du tout"),                            # nothing to cut back to
    ('Fini ! Et il dit "oui." Et enfin coupé', 'Fini ! Et il dit "oui."'),
    ("جملة كاملة. جملة ناقصة", "جملة كاملة."),
])
def test_trim_to_last_sentence(text, expected):
    assert trim_to_last_sentence(text) == expected


async def test_text_task_is_trimmed_to_the_last_complete_sentence_and_flagged(vt):
    cut = "Regarde d'abord le coefficient devant x. Ensuite pense à la règle de dérivation de x² qui est un peu"
    eng, fake, sink = make_engine(vt, lambda b: chat_ok(cut, 120, 80))                      # 80 = the hint cap
    r = await eng.run("hint", {"problem": "Calculer la dérivée de f(x) = 7x^3 - 2x + 9", "attempt": "21x"},
                      {"language": "FRENCH"})
    assert r.text == "Regarde d'abord le coefficient devant x."
    assert r.notes["truncated_output"] is True and "output_trimmed" in r.event_names
    assert [x.truncated_output for x in sink.records if x.kind == "chat" and x.outcome == "ok"] == [True]


async def test_text_task_below_the_cap_is_left_alone(vt):
    text = "Regarde d'abord le coefficient devant x. Ensuite pense à la règle de dérivation de x² qui est un peu"
    eng, _, _ = make_engine(vt, lambda b: chat_ok(text, 120, 79))
    r = await eng.run("hint", {"problem": "Calculer la dérivée de f(x) = 7x^3 - 2x + 9", "attempt": "21x"},
                      {"language": "FRENCH"})
    assert r.text == text and "truncated_output" not in r.notes


async def test_cut_json_goes_through_the_repair_ladder(vt):
    replies = iter([chat_ok('{"intent": "EXPLAIN", "language": "FRE', 900, 50), chat_ok(CLASSIFY_JSON, 950, 40)])
    eng, fake, sink = make_engine(vt, lambda b: next(replies))
    r = await eng.run("classify", {"message": "J'ai trouvé que la dérivée de 3x² est 6x, c'est bon ?"})
    assert len(fake.requests) == 2 and "repairing_json" in r.event_names
    assert r.data["intent"] == "EXPLAIN" and not r.safe_default
    chats = [x for x in sink.records if x.kind == "chat" and x.outcome == "ok"]
    assert [x.truncated_output for x in chats] == [True, False]


async def test_valid_json_that_ends_exactly_at_the_cap_is_not_repaired(vt):
    eng, fake, sink = make_engine(vt, lambda b: chat_ok(CLASSIFY_JSON, 900, 50))            # tokens_out == cap, JSON complete
    r = await eng.run("classify", {"message": "J'ai trouvé que la dérivée de 3x² est 6x, c'est bon ?"})
    assert len(fake.requests) == 1 and "repairing_json" not in r.event_names
    assert [x.truncated_output for x in sink.records if x.kind == "chat"] == [True]         # still counted in metrics


# ------------------------------------------------------------------------------ estimator and context window
def test_v11_defaults():
    s = load_settings({k: v for k, v in BASE_ENV.items()
                       if k not in ("ESTIMATE_CHARS_PER_TOKEN", "INPUT_TRUNCATION_CHARS_PER_TOKEN")})
    assert (s.estimate_chars_per_token, s.context_window, s.context_fill, s.input_token_budget) == (2.3, 4096, 0.85, 2800)
    assert s.truncation_chars_per_token == 5.0 and s.rag_top_k == 2


def test_estimator_follows_the_setting():
    set_chars_per_token(2.3)
    assert estimate_tokens([{"role": "user", "content": "x" * 2300}]) - 4 in (1000, 1001)    # float: 2300 / 2.3
    RetryingCaller(LLMClient(make_settings(ESTIMATE_CHARS_PER_TOKEN=4)), None, MemorySink(),
                   RetryPolicy(), 2800)                                  # building a caller applies the setting
    assert estimate_tokens([{"role": "user", "content": "x" * 4000}]) == 1000 + 4


def big_request(chars):
    return ChatRequest(ModelClass.COMPACT, ({"role": "system", "content": "Tu es un tuteur."},
                                            {"role": "user", "content": "x " * (chars // 2)}), max_tokens=300)


async def test_prompt_plus_output_cap_must_fit_the_context_window(vt):
    """BASE_ENV estimates 2 chars/token. Window 4096 * 0.85 = 3481; minus the 300-token cap = 3181 for the input,
    even when INPUT_TOKEN_BUDGET would allow more."""
    fake = FakeProvider()
    rc, sink = caller_for(vt, fake, INPUT_TOKEN_BUDGET=10000)
    ev = []
    await rc.chat(big_request(7000), CallContext(task="hint", on_event=lambda n, d: ev.append((n, d))))
    assert ("trimming_input", {"estimated_tokens": 3516, "budget": 3181}) in ev
    sent = fake.requests[0]["body"]["messages"]
    assert estimate_tokens(sent) + 300 <= 3481


async def test_a_prompt_that_fits_the_window_is_sent_untouched(vt):
    fake = FakeProvider()
    rc, _ = caller_for(vt, fake, INPUT_TOKEN_BUDGET=10000)
    await rc.chat(big_request(5000))                                            # est 2516 + 300 <= 3481
    assert fake.requests[0]["body"]["messages"][1]["content"] == "x " * 2500


async def test_the_input_budget_still_binds_when_it_is_the_smaller_limit(vt):
    fake = FakeProvider()
    rc, _ = caller_for(vt, fake, INPUT_TOKEN_BUDGET=1000, CONTEXT_WINDOW=100000)
    ev = []
    await rc.chat(big_request(4000), CallContext(task="hint", on_event=lambda n, d: ev.append((n, d))))
    assert any(n == "trimming_input" and d["budget"] == 1000 for n, d in ev)


async def test_context_window_setting_changes_the_limit(vt):
    fake = FakeProvider()
    rc, _ = caller_for(vt, fake, INPUT_TOKEN_BUDGET=10000, CONTEXT_WINDOW=2000, CONTEXT_FILL=1.0)
    ev = []
    await rc.chat(big_request(4000), CallContext(task="hint", on_event=lambda n, d: ev.append((n, d))))
    assert any(n == "trimming_input" and d["budget"] == 1700 for n, d in ev)    # 2000 - 300


def test_rag_assembly_leaves_room_for_the_answer_inside_the_window():
    from engine.core import Engine as E
    s = make_settings(CONTEXT_WINDOW=1000, CONTEXT_FILL=1.0)
    eng = E.create(s, client=LLMClient(s), sink=MemorySink(), cache=ExactCache(":memory:", 60))
    assert eng.limit == 1000 - 350                                              # not the 2520 the budget alone gives


# ------------------------------------------------------------------------------ silent input truncation
def chars_of(body):
    return sum(len(m["content"]) for m in body["messages"])


HINT = ("hint", {"problem": "Calculer la dérivée de f(x) = 7x^3 - 2x + 9. " + "Détail. " * 400, "attempt": "21x"},
        {"language": "FRENCH"})
BIG = {"INPUT_TRUNCATION_CHARS_PER_TOKEN": 5, "INPUT_TOKEN_BUDGET": 20000, "CONTEXT_WINDOW": 100000}


@pytest.mark.parametrize("ratio,flagged", [
    (3.9, False),                                                               # normal French
    (2.3, False),                                                               # the worst real text (Arabizi)
    (5.0, False),                                                               # exactly at the line: not below it
    (5.1, True),
    (20.0, True),                                                               # 4,200-token prompt reported as 2,050
])
async def test_input_truncation_detector(vt, ratio, flagged):
    def fn(body):
        return chat_ok("Pense à la règle des puissances.", int(chars_of(body) / ratio), 10)
    eng, fake, sink = make_engine(vt, fn, **BIG)
    r = await eng.run(*HINT)
    chars = chars_of(fake.requests[0]["body"])
    reported = int(chars / ratio)
    assert (r.degrade_reason == "input_truncated") is (reported < chars / 5)
    assert r.degraded is flagged or ratio == 5.0
    if flagged:
        assert "input_truncated" in r.event_names
        rec = next(x for x in sink.records if x.kind == "chat" and x.outcome == "ok")
        assert rec.detail.startswith("input_truncated") and str(reported) in rec.detail


async def test_a_truncated_input_result_is_degraded_logged_and_never_cached(vt):
    eng, fake, sink = make_engine(vt, lambda b: chat_ok("Pense à la règle des puissances.", chars_of(b) // 20, 10), **BIG)
    r1 = await eng.run(*HINT)
    r2 = await eng.run(*HINT)
    assert r1.degraded and r1.degrade_reason == "input_truncated" and r1.text
    assert not r2.from_cache and len(fake.requests) == 2                         # a repeat goes to the provider again
    assert [x.degrade_reason for x in sink.records if x.kind == "run"] == ["input_truncated"] * 2


async def test_a_normal_result_is_cached(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok("Pense à la règle des puissances.", int(chars_of(b) / 3.9), 10), **BIG)
    r1, r2 = await eng.run(*HINT), await eng.run(*HINT)
    assert not r1.degraded and r2.from_cache and len(fake.requests) == 1


def test_detector_can_be_turned_off():
    assert make_settings().truncation_chars_per_token == 0
    assert make_settings(INPUT_TRUNCATION_CHARS_PER_TOKEN=5).truncation_chars_per_token == 5
