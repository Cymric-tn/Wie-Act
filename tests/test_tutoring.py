"""Manual-testing fixes: partial result, explain v3 / language policy, contamination guard, exercise bank
(quiz, grade, stored hints), simulator profiles, console flows. Fake providers only."""
import json
import random

import pytest

from engine import Engine
from engine.exercises import ExerciseBank, answer_matches
from engine.language import contamination
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient
from metrics import MemorySink
from rag.chunker import chunk_dir
from rag.store import save_chunks
from scripts.chat import Console, TeeSink
from simulator.transport import ConstraintSimulator
from tests.conftest import FakeProvider, FnProvider, chat_ok, make_settings

BANK = ExerciseBank.load("data/exercises/derivees.yaml")


@pytest.fixture
def index(tmp_path):
    path = str(tmp_path / "chunks.json")
    save_chunks(path, chunk_dir("data/docs"))
    return path


def engine(vt, index, fn, **env):
    s = make_settings(RAG_INDEX_PATH=index, **env)
    fake, sink = FnProvider(fn), TeeSink(MemorySink())
    eng = Engine.create(s, client=LLMClient(s, fake), sink=sink, cache=ExactCache(":memory:", s.cache_ttl_s),
                        clock=vt.clock, sleep=vt.sleep, rng=random.Random(1))
    return eng, fake, sink


def system_of(req):
    return req["body"]["messages"][0]["content"]


# ====================================================================== 1. partial result
async def test_partial_result_before_midsize_call_verbatim_and_free(vt, index):
    seen = []
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("Explication."))
    r = await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?"}, on_event=seen.append)
    names = [e.name for e in seen]
    assert names.index("partial_result") < names.index("generating")
    excerpts = next(e for e in seen if e.name == "partial_result").data["excerpts"]
    texts = [c.text for c in chunk_dir("data/docs")]
    assert 1 <= len(excerpts) <= 3 and all(any(x["text"] in t for t in texts) for x in excerpts)
    assert len(fake.requests) == 1 and r.data["body"] == "Explication."      # no extra request


# ====================================================================== 2-3. explain v3 + language policy
async def test_explain_v4_model_writes_only_the_body_scaffolding_is_templates(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("La tangente a pour pente f'(a)."))
    q = {"question": "c'est quoi une tangente ?"}
    high = await eng.run("explain", q, {"confusion": "HIGH"})
    low = await eng.run("explain", q, {"confusion": "LOW"})
    sysmsg = system_of(fake.requests[0])
    assert eng.prompts["explain"].version == "4" and "nothing else" in sysmsg
    assert "practice" not in sysmsg and "acknowledge" not in sysmsg and "Darija" not in sysmsg
    assert len(fake.requests) == 1 and low.from_cache                     # body cached across confusion levels
    assert high.text.startswith("C'est une notion qui demande du temps") and low.text.startswith("Voici l'essentiel")
    for r in (high, low):
        assert r.data["body"] == "La tangente a pour pente f'(a)." and r.data["exercise"]["concept"] == "tangente"
        assert r.text.endswith(r.data["exercise"]["statement"]) and "À toi :" in r.text


async def test_explain_practice_follows_difficulty_and_exclusions(vt, index):
    eng, _, _ = engine(vt, index, lambda b: chat_ok("ok"))
    q = {"question": "Comment trouver l'équation de la tangente ?"}
    r1 = await eng.run("explain", q)
    r2 = await eng.run("explain", {**q, "exclude_exercises": [r1.data["exercise"]["id"]]}, {"difficulty": 3})
    assert (r1.data["exercise"]["id"], r2.data["exercise"]["id"]) == ("d12", "d13")
    r3 = await eng.run("explain", {**q, "exclude_exercises": ["d12", "d13"]})
    assert "exercise" not in r3.data and "À toi" not in r3.text                 # concept exhausted: no practice


async def test_explain_uses_top_two_chunks(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("ok"))
    r = await eng.run("explain", {"question": "Quelles sont les erreurs fréquentes avec les puissances, "
                                              "les constantes et le produit ?"})
    assert len(r.notes["retrieved"]) <= 2
    assert fake.requests[0]["body"]["messages"][-1]["content"].count("\n\n[") <= 1     # at most [1] and [2]


async def test_darija_learner_french_chunks_french_body_tunisian_templates(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("La tangente a pour équation y = f'(a)(x − a) + f(a)."))
    r = await eng.run("explain", {"question": "كيفاش نكتب معادلة المماس؟"}, {"confusion": "HIGH"})
    sysmsg = system_of(fake.requests[0])
    assert "Write the explanation in French" in sysmsg and "متاع" not in sysmsg
    assert all(x["id"].split("#")[0].endswith(".fr") for x in r.notes["retrieved"])   # French chunks only
    assert r.text.startswith("ماكش وحدك") and "جرّب إنت:" in r.text and not r.degraded


async def test_arabic_learner_prefers_arabic_chunks_then_french(vt, index):
    eng, _, _ = engine(vt, index, lambda b: chat_ok("العدد المشتق هو ميل المماس."))
    r = await eng.run("explain", {"question": "ما هو تعريف العدد المشتق؟"})
    assert r.notes["retrieved"][0]["id"] == "mochtaqa.ar#تعريف-العدد-المشتق"
    r2 = await eng.run("explain", {"question": "كيف أكتب معادلة المماس؟"}, {"language": "ARABIC"})
    assert r2.notes["retrieved"][0]["id"] == "derivee-definition.fr#interpretation-graphique-la-tangente"


async def test_arabic_answer_gets_relevant_terms_and_french_answer_drifts(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("La tangente est une droite."))
    r = await eng.run("explain", {"question": "ما هو تعريف العدد المشتق؟"})
    sysmsg = system_of(fake.requests[0])
    assert "Arabic math terms to use:" in sysmsg and "العدد المشتق (nombre dérivé)" in sysmsg
    assert "مماس" not in sysmsg.split("Arabic math terms to use:")[1] or "tangente" in sysmsg
    assert r.degrade_reason == "explain_guard:language_drift"                  # MSA expected, French came back


async def test_french_answer_gets_no_arabic_terms(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("ok"))
    await eng.run("explain", {"question": "c'est quoi le nombre dérivé ?"})
    assert "Arabic math terms" not in system_of(fake.requests[0])


# ====================================================================== 4. contamination guard
def test_contamination_detector():
    allowed = {"derivee", "tangente"}
    assert contamination("المشتقة maka هي ميل المماس", allowed) == ["maka"]
    assert contamination("مشتقة f(x) = x² هي 2x و lim و dérivée", allowed) == []      # notation, course term
    assert contamination("This line is Latin\nسطر عربي فقط", allowed) == []            # Latin lines ignored


async def test_arabic_hint_with_contamination_is_degraded_not_cached_logged(vt, index, caplog):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("جرب maka تعوض x ب 3 في 2x + 6؟"))
    inputs = {"problem": "حل المعادلة 2x + 6 = 0", "attempt": "x = 3"}
    with caplog.at_level("WARNING"):
        r = await eng.run("hint", inputs, {"language": "ARABIC"})
    assert (r.degraded, r.degrade_reason) == (True, "hint_guard:contamination")
    assert r.notes["contamination"] == ["maka"] and "contamination" in caplog.text
    await eng.run("hint", inputs, {"language": "ARABIC"})
    assert len(fake.requests) == 2                                             # never cached


async def test_arabic_explain_contamination(vt, index):
    eng, _, _ = engine(vt, index, lambda b: chat_ok("العدد المشتق هو maka ميل المماس f'(a)."))
    r = await eng.run("explain", {"question": "ما هو تعريف العدد المشتق؟"})
    assert r.degrade_reason == "explain_guard:contamination" and r.notes["contamination"] == ["maka"]


# ====================================================================== 5. classify confidence cap
async def test_rules_keep_confidence_one(vt, index):
    eng, _, _ = engine(vt, index, lambda b: chat_ok("{}"))
    assert (await eng.run("classify", {"message": "Merci !"})).data["confidence"] == 1.0


# ====================================================================== 6. exercise bank, quiz, grade, hints
def test_bank_is_complete_and_valid():
    assert len(BANK.exercises) == 15 and {e.level for e in BANK.exercises} == {1, 2, 3}
    for e in BANK.exercises:
        assert e.statement and e.solution and len(e.hints) == 3 and e.common_mistakes
        assert not any(answer_matches(h, e.solution) for h in e.hints)          # hints never are the answer


def test_bank_rejects_invalid(tmp_path):
    bad = tmp_path / "b.yaml"
    bad.write_text("exercises:\n  - {id: x, concept: c, level: 1, statement: s, solution: s, hints: [a], "
                   "common_mistakes: [{answer: a, diagnosis: d}]}\n")
    with pytest.raises(ValueError):
        ExerciseBank.load(str(bad))


@pytest.mark.parametrize("answer,expected,match", [
    ("f'(x)=4x^3", "f'(x) = 4x³", True), ("4x3", "f'(x) = 4x³", True), ("y=4x-4", "y = 4x − 4", True),
    ("x³", "f'(x) = 4x³", False), ("", "f'(x) = 4x³", False)])
def test_answer_normalization(answer, expected, match):
    assert answer_matches(answer, expected) is match


async def test_quiz_is_deterministic_from_the_bank(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("Un exercice sur la tangente."))
    r = await eng.run("quiz", {"message": "donne moi un exercice sur la tangente", "intro": False})
    assert [e["id"] for e in r.data["exercises"]] == ["d12"] and fake.requests == []    # no LLM at all
    assert "solution" not in r.data["exercises"][0] and r.mode == "bank"
    r2 = await eng.run("quiz", {"message": "a3tini exercice 3al el mamas", "exclude": ["d12"], "intro": False})
    assert [e["id"] for e in r2.data["exercises"]] == ["d13"]                          # glossary: mamas -> tangente


async def test_quiz_intro_is_one_cached_compact_line(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("Voici un exercice sur la tangente."))
    r1 = await eng.run("quiz", {"message": "un exercice sur la tangente", "intro": "llm"})
    r2 = await eng.run("quiz", {"message": "un exercice sur la tangente", "exclude": ["d12"], "intro": "llm"})
    r3 = await eng.run("quiz", {"message": "un exercice sur la tangente"})                 # default: template
    assert r1.text.startswith("Voici un exercice sur la tangente.") and r1.data["intro"]
    assert len(fake.requests) == 1 and fake.requests[0]["body"]["max_tokens"] == 60   # intro cached (QUIZ_INTRO_MAX_TOKENS)
    assert r3.data["intro"] == "Voici un exercice pour t'entraîner :" and len(fake.requests) == 1


async def test_diagnostic_three_levels_template_intro_no_request(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("x"))
    r = await eng.run("quiz", {"diagnostic": True}, {"language": "FRENCH"})
    assert [e["level"] for e in r.data["exercises"]] == [1, 2, 3] and fake.requests == []
    assert r.text.startswith("Trois exercices courts") and "1) " in r.text and "3) " in r.text


async def test_quiz_exhausted_says_so(vt, index):
    eng, _, _ = engine(vt, index, lambda b: chat_ok("x"))
    r = await eng.run("quiz", {"exclude": [e.id for e in BANK.exercises], "intro": False})
    assert r.data["exercises"] == [] and r.text.startswith("Tu as fait tous")


def grade_inputs(ex_id, attempt):
    e = BANK.get(ex_id)
    return {"problem": e.statement, "solution": e.solution, "attempt": attempt,
            "common_mistakes": [list(m) for m in e.common_mistakes]}


async def test_grade_sympy_correct_known_mistake_generic_wrong_zero_requests(vt, index):
    eng, fake, sink = engine(vt, index, lambda b: chat_ok("{}"))
    correct = await eng.run("grade", grade_inputs("d05", "x + 3 + x"))          # another correct form
    known = await eng.run("grade", grade_inputs("d01", "x³"))
    wrong = await eng.run("grade", grade_inputs("d01", "5x^2"))
    assert fake.requests == []                                                   # all three: no request
    assert (correct.data["verdict"], correct.data["graded_by"]) == ("CORRECT", "sympy")
    assert known.data["verdict"] == "INCORRECT" and "multiplier par l'exposant" in known.data["feedback"]
    assert wrong.data["feedback"] == "Ce n'est pas encore ça : reprends ton calcul étape par étape."
    assert {r.graded_by for r in sink.records if r.kind == "run"} == {"sympy"}


async def test_grade_unparseable_goes_to_llm_and_never_leaks(vt, index):
    replies = iter([chat_ok(json.dumps({"verdict": "INCORRECT", "mistake": "signe",
                                        "feedback": "La bonne réponse est maximum 5 atteint en x = 2."})),
                    chat_ok(json.dumps({"verdict": "partial", "feedback": "Vérifie l'abscisse."}))])
    eng, fake, sink = engine(vt, index, lambda b: next(replies))
    leaked = await eng.run("grade", grade_inputs("d15", "je pense que c'est un minimum"))
    assert leaked.data["feedback"] == "" and leaked.notes["guard"] == "feedback_leaked_solution"
    partial = await eng.run("grade", grade_inputs("d15", "maximum en x = 2"))
    assert partial.data["verdict"] == "PARTIAL" and fake.requests[0]["body"]["response_format"]
    assert {r.graded_by for r in sink.records if r.kind == "run"} == {"llm"} and len(fake.requests) == 2


@pytest.mark.parametrize("text,expected", [
    ("x^2", "x**2"), ("x²", "x**2"), ("2x", "2*x"), ("x(x+3)", "x*(x + 3)"), ("f'(x) = 4x³", "4*x**3"),
    ("y = 4x − 4", "4*x - 4"), ("2,5x", "2.5*x"), ("√x", "sqrt(x)"), ("−1/(x + 1)²", "-1/(x + 1)**2"),
    ("f'(3) = 6", "6")])
def test_answer_parser_formats(text, expected):
    from engine.mathcheck import parse_answer
    import sympy
    assert parse_answer(text) == sympy.sympify(expected, locals={"x": sympy.Symbol("x")})


@pytest.mark.parametrize("text", ["je pense que c'est 2x", "__import__('os').system('ls')", "2y", "x = 3",
                                  "maximum 5 atteint en x = 2", "lambda: 1", "x" * 200])
def test_answer_parser_refuses_sentences_and_code(text):
    from engine.mathcheck import parse_answer
    assert parse_answer(text) is None


def test_every_bank_solution_parses_except_text_answers():
    from engine.mathcheck import parse_answer
    unparsed = [e.id for e in BANK.exercises if parse_answer(e.solution) is None]
    assert unparsed == ["d14", "d15"]                                            # sentences: LLM grader


async def test_stored_hints_are_served_in_order_without_requests(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("x"))
    e = BANK.get("d10")
    got = [(await eng.run("hint", {"problem": e.statement, "stored_hints": list(e.hints), "hint_level": n})).text
           for n in (1, 2, 3, 4)]
    assert got == [*e.hints, e.hints[-1]] and fake.requests == []


# ====================================================================== 7. simulator profiles
async def test_simulator_profiles_switch_at_runtime(vt):
    s = make_settings(SIMULATE_CONSTRAINTS="true", SIM_SATURATION_PROB="1")
    sim = ConstraintSimulator(FakeProvider(), s, vt.clock, vt.sleep)
    c = LLMClient(s, sim)
    from config import ModelClass
    from gatekeeper.client import ChatRequest
    msg = ({"role": "user", "content": "hi"},)
    assert (await c.chat(ChatRequest(ModelClass.MIDSIZE, msg))).status_code == 503
    sim.apply_profile("calm")
    assert sim.cfg.saturation_prob == 0 and sim.cfg.latency_scale == 0
    assert (await c.chat(ChatRequest(ModelClass.MIDSIZE, msg))).status_code == 200
    sim.apply_profile("saturated")
    sim.apply_profile("off")
    r = await c.chat(ChatRequest(ModelClass.MIDSIZE, msg))
    assert r.status_code == 200 and "x-ratelimit-api_key-remaining-requests" not in r.headers  # pass-through


# ====================================================================== console flows
def label(intent, language="FRENCH", confusion="LOW"):
    return chat_ok(json.dumps({"intent": intent, "language": language, "confusion": confusion,
                               "complexity": 2, "confidence": 0.8}))


@pytest.fixture
def console(vt, index):
    state = {"intent": "QUIZ"}

    def fn(body):
        sysmsg = body["messages"][0]["content"]
        if sysmsg.startswith("You label ONE message"):
            return label(state["intent"])
        if sysmsg.startswith("You check a student's answer"):
            return chat_ok(json.dumps({"verdict": "INCORRECT", "feedback": "Vérifie le calcul."}))
        return chat_ok("Voici un exercice.")
    eng, fake, sink = engine(vt, index, fn)
    lines = []
    return Console(eng, sink, out=lines.append), lines, state


async def test_console_quiz_wrong_answer_then_stored_hint_then_correct(console):
    c, lines, state = console
    await c.handle("donne moi un exercice")
    assert c.practice and c.practice.exercise.id == "d01"
    await c.handle("x³")                                       # a known mistake: graded by rules
    out = "\n".join(lines)
    assert "-> grade" in out and "not yet" in out and "oublié de multiplier" in out and c.practice
    lines.clear()
    await c.handle("/hint")
    assert BANK.get("d01").hints[0] in "\n".join(lines)
    await c.handle("f'(x) = 4x^3")
    assert "correct" in "\n".join(lines) and c.practice is None


async def test_console_diagnostic_series_and_score(console):
    c, lines, state = console
    state["intent"] = "DIAGNOSTIC"
    await c.handle("teste mon niveau")
    assert c.practice.diagnostic and len(c.practice.queue) == 2
    for answer in ("4x³", "x = 1", "y = 0"):
        await c.handle(answer)
    assert "diagnostic score: 1/3" in "\n".join(lines) and c.practice is None


async def test_console_prints_partial_excerpt_and_sim_command(console):
    c, lines, state = console
    state["intent"] = "EXPLAIN"
    await c.handle("Comment trouver l'équation de la tangente ?")
    out = "\n".join(lines)
    explain = out[out.index("-> explain"):]
    assert explain.index("course excerpt") < explain.index("· generating")     # excerpt before the mid-size call
    await c.handle("/sim saturated")
    assert "simulator not active" in lines[-1] or "simulator = saturated" in lines[-1]


async def test_console_open_exercise_does_not_swallow_a_new_request(console):
    c, lines, state = console
    state["intent"] = "DIAGNOSTIC"
    await c.handle("teste mon niveau")
    first = c.practice.exercise.id
    state["intent"] = "QUIZ"
    await c.handle("donne moi un exercice")                      # no math: classified, not graded
    assert "-> grade" not in "\n".join(lines) and c.practice.exercise.id != first and not c.practice.diagnostic


def test_looks_like_answer():
    from scripts.chat import looks_like_answer
    assert all(map(looks_like_answer, ["x³", "f'(x) = 4x^3", "6x - 4", "y = 4x − 4", "maximum 5 en x = 2"]))
    assert not any(map(looks_like_answer, ["donne moi un exercice", "ma fhemtch", "teste mon niveau",
                                           "chnowa ya3ni limite? ma fhemt chay", "3lech hiya hakka"]))
    assert looks_like_answer("12") and looks_like_answer("4x") and looks_like_answer("x")


async def test_degenerate_explain_falls_back_to_course_excerpts_not_cached(vt, index):
    loop = "La mélange des concepts de " * 40
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("Voici le cours :" if b["model"] == "compact-a" else loop))
    q = {"question": "c'est quoi le nombre dérivé ?"}
    r = await eng.run("explain", q)
    assert (r.degrade_reason, r.mode) == ("explain_guard:degenerate", "fast_extractive")
    assert "mélange" not in r.text and r.data["excerpts"]
    again = await eng.run("explain", q)
    assert not again.from_cache and again.mode == "fast_extractive"                   # not cached: negative marker
    assert [x["body"]["model"] for x in fake.requests].count("mid-a") == 1


async def test_darija_one_liners_are_templates_no_request(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("كيفاش هتستعلم كل شئ عن ..."))
    r = await eng.run("quiz", {"message": "a3tini exercice", "intro": "llm"}, {"language": "ARABIZI"})
    assert r.data["intro"] == "Hak exercice bech tetdarreb:" and fake.requests == []


# ====================================================================== explain practice, concepts, frequency penalty
def test_every_chunk_has_a_concept_with_at_least_one_exercise():
    concepts = {e.concept for e in BANK.exercises}
    for c in chunk_dir("data/docs"):
        assert c.concept, c.chunk_id
        assert c.concept in concepts, (c.chunk_id, c.concept)


def test_language_plan():
    from rag.retriever import language_plan
    assert language_plan("DARIJA") == language_plan("ARABIZI") == [{"language": "FRENCH"}]
    assert language_plan("ARABIC") == [{"language": "ARABIC"}, {"language": "FRENCH"}]
    assert language_plan("ENGLISH") == [{"language": "ENGLISH"}, {"language": "FRENCH"}]
    assert language_plan("FRENCH") == language_plan(None) == [None]


async def test_frequency_penalty_on_explain_only_and_dropped_once_if_rejected(vt, index, caplog):
    seen = []

    def fn(body):
        seen.append("frequency_penalty" in body)
        if "frequency_penalty" in body:
            return __import__("tests.conftest", fromlist=["error"]).error(
                400, "invalid_request_error", "Unsupported parameter: frequency_penalty")
        return chat_ok("ok")
    eng, fake, _ = engine(vt, index, fn)
    with caplog.at_level("WARNING"):
        r1 = await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?"})
        r2 = await eng.run("explain", {"question": "c'est quoi le nombre dérivé ?"})
        await eng.run("hint", {"problem": "Résoudre 2x + 6 = 0"})
    assert seen == [True, False, False, False] and not r1.degraded and not r2.degraded
    assert caplog.text.count("rejected frequency_penalty") == 1
    assert fake.requests[1]["body"].get("frequency_penalty") is None


async def test_frequency_penalty_sent_when_accepted(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("ok"))
    await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?"})
    assert fake.requests[0]["body"]["frequency_penalty"] == 0.3


async def test_console_explain_opens_its_practice_exercise_and_stats_show_graded_by(console):
    c, lines, state = console
    state["intent"] = "EXPLAIN"
    await c.handle("Comment trouver l'équation de la tangente ?")
    assert c.practice and c.practice.exercise.id == "d12" and "À toi :" in "\n".join(lines)
    await c.handle("y = 4x")                                    # known mistake, sympy
    await c.handle("y = 4x - 4")                                # correct, sympy
    assert c.practice is None and "graded_by=sympy" in "\n".join(lines)
    await c.handle("/stats")
    assert "graded_by sympy 2 / llm 0" in lines[-1]


# ====================================================================== confusion override (routing layer)
AZI_1 = "chnowa ya3ni limite? ma fhemt chay fel cours"          # classify eval item azi-1


@pytest.mark.parametrize("intent,confusion,task,overridden", [
    ("DIAGNOSTIC", "HIGH", "explain", True), ("QUIZ", "HIGH", "explain", True),
    ("DIAGNOSTIC", "LOW", "diagnostic", False), ("QUIZ", "MEDIUM", "quiz", False),
    ("EXPLAIN", "HIGH", "explain", False), ("HINT", "HIGH", "hint", False), ("CHITCHAT", "HIGH", None, False)])
def test_follow_up_routing_with_confusion_override(intent, confusion, task, overridden):
    from router.policy import Router
    sink = MemorySink()
    f = Router(sink, 2).follow_up({"intent": intent, "confusion": confusion})
    assert (f.task, f.overridden) == (task, overridden)
    [rec] = sink.records
    assert rec.kind == "route" and rec.task == "follow_up" and rec.detail == f.reason
    assert rec.outcome == ("overridden" if overridden else "routed")


async def test_console_azi1_misclassified_as_diagnostic_but_confused_gets_an_explanation(vt, index):
    def fn(body):
        if body["messages"][0]["content"].startswith("You label ONE message"):
            return label("DIAGNOSTIC", "ARABIZI", "HIGH")         # what the model actually says for azi-1
        return chat_ok("La limite décrit le comportement de f quand x s'approche d'une valeur.")
    eng, fake, sink = engine(vt, index, fn)
    lines = []
    c = Console(eng, sink, out=lines.append)
    await c.handle(AZI_1)
    out = "\n".join(lines)
    assert "-> explain" in out and "-> quiz" not in out and "teach before testing" in out
    assert any(r.kind == "route" and r.outcome == "overridden" for r in sink.records)


# ====================================================================== multilingual exercises
def test_every_exercise_renders_in_french_english_and_arabic():
    from engine.language import arabic_ratio
    for e in BANK.exercises:
        fr, en, ar = e.render("fr"), e.render("en"), e.render("ar")
        for text in (fr, en, ar):
            assert text and "{" not in text and e.expression in text, (e.id, text)
        assert fr == e.statement and fr != en != ar
        assert arabic_ratio(ar) > 0.5 and not any(w in en for w in ("Calculer", "Soit", "dérivée"))


def test_exercise_free_text_statement_for_exercises_that_fit_no_template(tmp_path):
    p = tmp_path / "ex.yaml"
    p.write_text("exercises:\n  - id: z1\n    concept: puissance\n    level: 1\n"
                 "    statement: {fr: 'Énoncé libre', en: 'Free statement', ar: 'نص حر'}\n"
                 "    solution: '1'\n    hints: [a, b, c]\n    common_mistakes: [{expr: '0', feedback: f}]\n")
    e = ExerciseBank.load(str(p)).get("z1")
    assert (e.render("fr"), e.render("en"), e.render("ar")) == ("Énoncé libre", "Free statement", "نص حر")


async def test_quiz_and_explain_render_the_learner_language(vt, index):
    eng, _, _ = engine(vt, index, lambda b: chat_ok("ok"))
    q = await eng.run("quiz", {"concept": "tangente"}, {"language": "ENGLISH"})
    assert q.text.endswith("Give the equation of the tangent line to the graph of f(x) = x² at the point with "
                           "x-coordinate a = 2.")
    r = await eng.run("explain", {"question": "ما هو تعريف العدد المشتق؟"})
    assert r.data["exercise"]["statement"].startswith("لتكن f(x) =")        # MSA exercise for an MSA learner
    d = await eng.run("explain", {"question": "كيفاش نكتب معادلة المماس؟"})
    assert d.data["exercise"]["statement"].startswith("Donner l'équation")  # Darija learner: French phrase


# ====================================================================== grounding
def test_equation_extraction_in_three_languages():
    from engine.grounding import equations
    assert equations("Pour f(x) = x², on a f'(x) = 2x.") == [("f(x)", "x²"), ("f'(x)", "2x")]
    assert equations("If f(x) = x² then f'(a) = 2a.") == [("f(x)", "x²"), ("f'(a)", "2a")]
    assert equations("إذا كانت f(x) = x² فإنّ f'(a) = 2a.") == [("f(x)", "x²"), ("f'(a)", "2a")]


@pytest.mark.parametrize("answer,grounded", [
    ("La tangente : y = f'(a)(x − a) + f(a). Pour f(x) = x² en a = 1, y = 2x − 1.", True),
    ("Ici f'(x) = 5 + 6x.", True),                                    # equivalent form of the course's 6x + 5
    ("Si f(x) = ln(x), alors f'(x) = 1/x.", False),                    # grounding alone: course has only 1/x
    ("f'(x) = lim (f(x + h) − f(x))/h", False),
    ("Pour a = 3, f'(3) = 6.", False),                                 # a new computation
])
def test_grounding_against_course_chunks(answer, grounded):
    from engine.grounding import ungrounded
    sources = [c.text for c in chunk_dir("data/docs") if c.language == "FRENCH"]
    assert (ungrounded(answer, sources) == []) is grounded


async def test_unverifiable_formula_is_degraded_logged_and_not_cached(vt, index, caplog):
    body = "La dérivée se calcule comme f'(x) = lim(h → 0) [f(x + h) - f(x)]/h."
    eng, fake, sink = engine(vt, index, lambda b: chat_ok(body))
    q = {"question": "C'est quoi la dérivée de 1/x et de racine de x ?"}
    with caplog.at_level("WARNING"):
        r = await eng.run("explain", q)
    assert (r.degraded, r.degrade_reason) == (True, "ungrounded_formula")
    assert r.notes["ungrounded"] == ["f'(x) = lim(h → 0) [f(x + h) - f(x)]/h"] and "ungrounded formula" in caplog.text
    again = await eng.run("explain", q)
    assert not again.from_cache and again.mode == "fast_extractive"   # never cached: negative marker instead
    assert len(fake.requests) == 1


async def test_wrong_math_is_degraded_and_correct_new_example_passes(vt, index):
    replies = iter(["Par exemple, si f(x) = ln(x), alors f'(x) = 1/x.",
                    "Par exemple, si f(x) = x³, alors f'(x) = 3x et f'(2) = 12."])
    eng, fake, _ = engine(vt, index, lambda b: chat_ok(next(replies)))
    q = {"question": "C'est quoi la dérivée de 1/x et de racine de x ?"}
    good = await eng.run("explain", q)
    eng.cache.db.execute("DELETE FROM exact")                            # same question, fresh generation
    bad = await eng.run("explain", q)
    assert not good.degraded and good.notes["new_examples"] == ["f(x) = ln(x)"] and good.notes["formulas_verified"] == 1
    assert bad.degrade_reason == "wrong_math" and bad.notes["wrong_math"] == ["f'(x) = 3x"]
    assert bad.notes["formulas_verified"] == 1                          # f'(2) = 12 is right


@pytest.mark.parametrize("answer,expected", [
    ("Pour f(x) = x², f'(x) = 2x et f'(3) = 6.", {"verified": 2, "new_examples": 1}),   # no sources: all new
    ("Pour f(x) = x², f'(x) = 3x.", {"wrong": 1, "new_examples": 1}),
    ("Soit f(t) = t^2, alors f'(t) = 2t.", {"verified": 1, "new_examples": 1}),
    ("f(x) = x² + x donc f'(x) = (x²)' + (x)' = 2x + 1.", {"verified": 2, "new_examples": 1}),
    ("On a 3 × 2 = 7.", {"wrong": 1}),
    ("Ici u = 1 et v = x + 2.", {"skipped": 2}),
    ("On résout 2x − 4 = 0.", {"skipped": 1}),
    ("(3x²)' = 3 × 2x", {"verified": 1}),
    ("On a f'(x) = 2x + 1, où f(x) = x² + x.", {"verified": 1, "new_examples": 1}),       # claim before definition
    ("(x(x + 1))' = x'·(x + 1) + x·(x + 1)'", {"verified": 1}),                            # bare x'
    ("d/dx (x(x+1)) = d/dx (x² + x)", {"verified": 1}),                                    # d/dx notation
    ("Rappel : 1/x = x⁻¹)", {"verified": 1}),                                              # stray parenthesis
    ("Pour f(x) = x² + 7, f'(x) = 2x, et non f'(x) = 2x + 7.", {"verified": 1, "skipped": 1, "new_examples": 1}),
    ("We solve 2x − 4 = 0 → 2x = 4 → x = 2.", {"skipped": 3}),                              # arrow chain
    ("Si f(x) = sin(x), alors f'(x) = cos(x).", {"verified": 1, "new_examples": 1}),
    ("f'(x) = lim(h → 0) [f(x + h) - f(x)]/h", {"ungrounded": 1}),
    ("Pour f(x) = x² − 4x + 1, on pose f'(x) = 0, donc 2x − 4 = 0.", {"skipped": 2, "new_examples": 1}),  # a condition
    ("Si f(x) = xⁿ, alors f'(x) = n·xⁿ⁻¹. Ici n = 2, donc f'(x) = 2x.", {"verified": 2, "skipped": 1, "new_examples": 1}),
])
def test_check_formulas_correctness_over_grounding(answer, expected):
    from engine.grounding import check_formulas
    got = {k: len(v) for k, v in check_formulas(answer, []).items() if v}
    assert got == expected


def test_loop_salvage_keeps_complete_sentences_before_the_loop():
    from engine.tasks import salvage_before_loop
    good = ("La dérivée mesure la vitesse à laquelle une fonction change en un point. "
        "Si elle est positive, la fonction augmente ; si elle est négative, elle diminue. "
        "On la note f prime et on la calcule avec des règles simples. "
        "Par exemple, la dérivée de x² est 2x, et celle d'une constante est nulle.")
    loop = " La mélange des concepts de la mélange des concepts de la mélange des concepts de la mélange"
    kept = salvage_before_loop(good + loop)
    assert kept == good and len(kept.split()) >= 40
    assert salvage_before_loop("Phrase courte. " + loop) is None              # < 40 words: fast mode
    assert salvage_before_loop(good + " et puis" + loop) == good               # cut at the last full sentence


async def test_explain_loop_truncated_or_fast_mode(vt, index):
    good = ("La dérivée mesure la vitesse à laquelle une fonction change en un point. "
        "Si elle est positive, la fonction augmente ; si elle est négative, elle diminue. "
        "On la note f prime et on la calcule avec des règles simples. "
        "Par exemple, la dérivée de x² est 2x, et celle d'une constante est nulle.")
    loop = " de la tangente de la tangente de la tangente de la tangente de la tangente"
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("Voici :" if b["model"] == "compact-a" else good + loop))
    r = await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?"})
    assert (r.degrade_reason, r.mode, r.data["body"]) == ("explain_guard:loop_truncated", None, good)
    again = await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?"})
    assert not again.from_cache and again.mode == "fast_extractive"          # not cached: negative marker
    assert [x["body"]["model"] for x in fake.requests].count("mid-a") == 1


async def test_english_question_falls_back_to_french_chunks(vt, index):
    eng, _, _ = engine(vt, index, lambda b: chat_ok("ok"))
    r = await eng.run("explain", {"question": "How do I write the equation of the tangent line?"})
    assert r.notes["retrieved"][0]["id"] == "derivee-definition.fr#interpretation-graphique-la-tangente"
    e = await eng.run("explain", {"question": "When does a zero of the derivative give a local maximum?"})
    assert e.notes["retrieved"][0]["id"].startswith("derivative-variations.en#")


@pytest.mark.parametrize("text,expected", [
    ("Pour un quotient : (u/v)' = (u'·v − u·v') / v². C'est la règle.", [("(u/v)'", "(u'·v − u·v') / v²")]),
    ("Si f(x) = √x (racine carrée de x), alors", [("f(x)", "√x")]),
    ("donc f'(x) = 2x - 4 > 0 pour x > 2", [("f'(x)", "2x - 4")]),
    ("(-7)' = -0 (puisque c'est une constante)", [("(-7)'", "-0")]),
])
def test_equation_extraction_stops_at_prose_periods_and_inequalities(text, expected):
    from engine.grounding import equations
    assert equations(text) == expected


async def test_cached_ungrounded_entry_is_checked_on_hit_and_dropped(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("ok"))
    q = {"question": "C'est quoi la dérivée de 1/x et de racine de x ?"}
    first = await eng.run("explain", q)                            # grounded body, cached
    assert not first.degraded
    key = next(iter(k for (k,) in eng.cache.db.execute("SELECT k FROM exact WHERE task='explain'")))
    import json as _j
    eng.cache.db.execute("UPDATE exact SET v=? WHERE k=?",            # an entry cached before the rule existed
                         (_j.dumps({"text": "On a f'(x) = lim(h → 0) [f(x + h) - f(x)]/h.", "data": None,
                                    "model_class": "midsize", "model": "mid-a"}), key))
    hit = await eng.run("explain", q)
    assert hit.from_cache and hit.degrade_reason == "ungrounded_formula"
    assert eng.cache.get(key) is None                                  # dropped


def test_standalone_formulas_without_equals_are_checked():
    from engine.grounding import formulas, ungrounded
    text = "It is computed by finding the limit as h approaches zero of [f(x + h) - f(x)]/h. In the example..."
    assert formulas(text) == ["[f(x + h) - f(x)]/h"]
    en = [c.text for c in chunk_dir("data/docs") if c.language == "ENGLISH"]
    fr = [c.text for c in chunk_dir("data/docs") if c.chunk_id.endswith("taux-d-accroissement")]
    assert ungrounded(text, en) == ["[f(x + h) - f(x)]/h"]
    assert ungrounded("le quotient (f(a + h) − f(a)) / h tend vers f'(a)", fr) == []   # in the course


@pytest.mark.parametrize("text,lost", [
    (AZI_1, True), ("ma fhemtch el tangente", True), ("ما فهمتش النهاية", True), ("je comprends rien", True),
    ("I don't understand derivatives", True), ("لم أفهم المشتقة", True),
    ("donne moi un exercice", False), ("teste mon niveau", False), ("chouf niveau mte3i", False)])
def test_says_lost(text, lost):
    from engine.language import says_lost
    assert says_lost(text) is lost


async def test_azi1_end_to_end_with_the_label_the_model_actually_gives(vt, index):
    """Live, llama3.2:3b labels azi-1 DIAGNOSTIC with MEDIUM confusion: the rules floor makes it HIGH,
    and the routing override then sends the learner to an explanation."""
    def fn(body):
        if body["messages"][0]["content"].startswith("You label ONE message"):
            return label("DIAGNOSTIC", "ARABIZI", "MEDIUM")
        return chat_ok("La limite décrit le comportement de f.")
    eng, _, sink = engine(vt, index, fn)
    c = await eng.run("classify", {"message": AZI_1})
    assert c.data["confusion"] == "HIGH" and c.notes["model_confusion"] == "MEDIUM"
    assert eng.router.follow_up(c.data).task == "explain"


async def test_classify_rules_are_reapplied_on_cache_hits(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: label("DIAGNOSTIC", "ARABIZI", "MEDIUM"))
    import json as _j
    first = await eng.run("classify", {"message": AZI_1})
    key = next(iter(k for (k,) in eng.cache.db.execute("SELECT k FROM exact WHERE task='classify'")))
    old = _j.loads(eng.cache.db.execute("SELECT v FROM exact WHERE k=?", (key,)).fetchone()[0])
    old["data"]["confusion"] = "MEDIUM"                              # as cached before the floor existed
    eng.cache.db.execute("UPDATE exact SET v=? WHERE k=?", (_j.dumps(old), key))
    hit = await eng.run("classify", {"message": AZI_1})
    assert hit.from_cache and hit.data["confusion"] == "HIGH" and len(fake.requests) == 1


@pytest.mark.parametrize("text", [
    "Le taux d'accroissement entre a et a + h est le quotient. Pour obtenir le nombre dérivé, on laisse h tendre "
    "vers 0. Si le taux d'accroissement se rapproche d'un nombre fini, on l'appelle nombre dérivé. Par exemple, "
    "pour f(x) = x², le taux d'accroissement vaut 2a + h.",
    "La pente de la tangente au point a est f'(a). L'équation de la tangente est y = f'(a)(x − a) + f(a), où "
    "f'(a) est la pente de la tangente. Pour f(x) = x² en a = 1, la pente de la tangente est f'(1) = 2."])
def test_a_phrase_used_three_times_in_a_normal_answer_is_not_a_loop(text):
    from engine.tasks import degenerate
    assert not degenerate(text)                       # both sent good answers to fast mode before


def test_real_loops_are_detected():
    from engine.tasks import degenerate
    assert degenerate("La mélange des concepts de la mélange des concepts de la mélange des concepts de la")
    assert degenerate("Mab3na, 0/0 chtouf, tafawt 9iwa x = 1, kem 9iwa x = 1, kem 9iwa x = 1, kem 9iwa x = 1")


# ====================================================================== negative cache
NEG_GOOD = ("La dérivée mesure la vitesse à laquelle une fonction change en un point. "
            "Si elle est positive, la fonction augmente ; si elle est négative, elle diminue. "
            "On la note f prime et on la calcule avec des règles simples. "
            "Par exemple, la dérivée de x² est 2x, et celle d'une constante est nulle.")
NEG_CASES = {
    "ungrounded_formula": ("C'est quoi la dérivée de 1/x et de racine de x ?",
                           "La dérivée se calcule comme f'(x) = lim(h → 0) [f(x + h) - f(x)]/h."),
    "wrong_math": ("C'est quoi la dérivée de 1/x et de racine de x ?",
                   "Par exemple, si f(x) = x³, alors f'(x) = 3x."),
    "explain_guard:loop_truncated": ("Comment trouver l'équation de la tangente ?",
                                     NEG_GOOD + " de la tangente de la tangente de la tangente de la tangente"),
}


def neg_engine(vt, index, body, **env):
    s = make_settings(RAG_INDEX_PATH=index, **env)
    fake, sink = FnProvider(lambda b: chat_ok(body)), MemorySink()
    eng = Engine.create(s, client=LLMClient(s, fake), sink=sink,
                        cache=ExactCache(":memory:", s.cache_ttl_s, clock=vt.clock),
                        clock=vt.clock, sleep=vt.sleep, rng=random.Random(1))
    return eng, fake, sink


@pytest.mark.parametrize("reason", sorted(NEG_CASES))
async def test_negative_cache_serves_a_failed_explain_repeat_in_fast_mode_with_no_request(vt, index, reason):
    question, body = NEG_CASES[reason]
    eng, fake, sink = neg_engine(vt, index, body)
    first = await eng.run("explain", {"question": question})
    assert first.degrade_reason == reason and len(fake.requests) == 1
    again = await eng.run("explain", {"question": question})
    assert len(fake.requests) == 1                                          # no provider call, framing included
    assert (again.degraded, again.degrade_reason, again.mode) == (True, reason, "fast_extractive")
    assert not again.from_cache and again.notes["negative_cache"] and again.data["excerpts"]
    assert "negative_cache_hit" in again.event_names
    run = [r for r in sink.records if r.kind == "run"][-1]
    assert run.from_negative_cache and run.degrade_reason == reason and run.mode == "fast_extractive"
    marker = [r for r in sink.records if r.outcome == "negative_cache_hit"]
    assert len(marker) == 1 and marker[0].from_negative_cache and marker[0].kind == "chat"
    vt.t += 601                                                             # NEGATIVE_CACHE_TTL_S default 600
    await eng.run("explain", {"question": question})
    assert len(fake.requests) == 2                                          # expired: generated again


async def test_negative_cache_ttl_zero_turns_it_off(vt, index):
    question, body = NEG_CASES["ungrounded_formula"]
    eng, fake, _ = neg_engine(vt, index, body, NEGATIVE_CACHE_TTL_S=0)
    await eng.run("explain", {"question": question})
    again = await eng.run("explain", {"question": question})
    assert len(fake.requests) == 2 and again.mode is None and again.degrade_reason == "ungrounded_formula"


async def test_negative_marker_expires_and_is_dropped(vt):
    c = ExactCache(":memory:", 60, clock=vt.clock)
    c.mark_negative("k", "explain", ["ungrounded_formula"], 600)
    assert c.get_negative("k") == ["ungrounded_formula"] and c.get_negative("other") is None
    vt.t += 600
    assert c.get_negative("k") is None                                      # expired and dropped
    assert c.db.execute("SELECT count(*) FROM negative").fetchone()[0] == 0


async def test_saturation_fallback_leaves_no_negative_marker(vt, index):
    from tests.conftest import error
    question, _ = NEG_CASES["wrong_math"]
    eng, fake, _ = neg_engine(vt, index, None)
    fake.fn = lambda b: (error(503, "service_unavailable", "busy", {"Retry-After": "1"})
                         if b["model"].startswith("mid") else chat_ok("Voici :"))
    first = await eng.run("explain", {"question": question})
    assert first.mode == "fast_extractive" and first.degrade_reason == "midsize_saturated"
    assert eng.cache.db.execute("SELECT count(*) FROM negative").fetchone()[0] == 0   # transient: not marked


async def test_capacity_t011_t016_repeat_is_no_longer_a_repeated_identical_request(vt, index):
    """Replay of the load-engine-v1 capacity case: turn t011 got an ungrounded explain (degraded, not
    cached); turn t016 asked the same question and re-sent the identical mid-size request."""
    from scripts.metrics_report import analyze
    question, body = NEG_CASES["ungrounded_formula"]
    classify = json.dumps({"intent": "EXPLAIN", "language": "FRENCH", "confusion": "LOW", "complexity": 2,
                           "confidence": 0.8})
    eng, fake, sink = neg_engine(vt, index, None)
    fake.fn = lambda b: chat_ok(classify if b["model"].startswith("compact") else body)
    for turn in ("t011", "t016"):
        c = await eng.run("classify", {"message": question}, {"turn_id": turn})
        assert eng.router.follow_up(c.data).task == "explain"
        r = await eng.run("explain", {"question": question}, {"turn_id": turn})
        vt.t += 38                                                          # t016 came 38 s after t011
    assert r.mode == "fast_extractive" and r.degrade_reason == "ungrounded_formula"
    m = analyze([rec.model_dump() for rec in sink.records])
    assert m[("cache", "repeated identical provider requests")] == 0
    assert m[("cache", "negative cache hits (fast mode, 0 requests)")] == 1
    assert m[("volume", "provider requests (all attempts)")] == 2          # classify + the first explain


async def test_concurrent_identical_follower_uses_the_leaders_negative_marker(vt, index):
    import asyncio
    question, body = NEG_CASES["wrong_math"]
    eng, fake, _ = neg_engine(vt, index, body)
    a, b = await asyncio.gather(eng.run("explain", {"question": question}),
                                eng.run("explain", {"question": question}))
    assert len(fake.requests) == 1
    assert {a.mode, b.mode} == {None, "fast_extractive"} and a.degrade_reason == b.degrade_reason == "wrong_math"


async def test_negative_cache_covers_degenerate_explain(vt, index):
    question = "Comment trouver l'équation de la tangente ?"
    loop = "La mélange des concepts de la mélange des concepts de la mélange des concepts de la mélange des concepts"
    eng, fake, sink = neg_engine(vt, index, None)
    fake.fn = lambda b: chat_ok("Voici :" if b["model"].startswith("compact") else loop)
    first = await eng.run("explain", {"question": question})
    assert (first.degrade_reason, first.mode) == ("explain_guard:degenerate", "fast_extractive")
    sent = len(fake.requests)                                    # the mid-size call (+ its framing line)
    again = await eng.run("explain", {"question": question})
    assert len(fake.requests) == sent                            # no provider call on the repeat
    assert (again.degrade_reason, again.mode) == ("explain_guard:degenerate", "fast_extractive")
    assert again.notes["negative_cache"] and [r for r in sink.records if r.kind == "run"][-1].from_negative_cache
