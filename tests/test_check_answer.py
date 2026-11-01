"""Task check_answer: a worked solution line by line (safe sympy parse, stored mistakes, buggy rules, final grade)."""
import json

import pytest

from engine.answers import buggy_derivatives, check_steps, parse_line, target_for
from engine.exercises import ExerciseBank
from engine.result import Result
from tests.conftest import chat_ok
from tests.test_engine import _index, make_engine, runs  # noqa: F401  (_index: the autouse course index)
from tests.test_retry_v11 import quota_429

BANK = ExerciseBank.load("data/exercises/derivees.yaml")
EX = {e.id: e for e in BANK.exercises}


def never(body):
    pytest.fail(f"a model request was sent where rules decide: {body['messages'][-1]['content'][:80]!r}")


def say(text):
    return lambda body: chat_ok(text, 200, 20)


async def check(vt, steps, problem="Dérive f(x) = x⁴", fn=never, ctx=None, **inputs):
    eng, fake, sink = make_engine(vt, fn)
    r = await eng.run("check_answer", {"problem": problem, "steps": steps, **inputs}, ctx or {"language": "FRENCH"})
    return r, fake, sink, eng


# ------------------------------------------------------------------ all 15 bank exercises
CORRECT = {   # a correct derivation for every exercise (the last line is the answer)
    "d10": ["f(x) = x²", "f'(x) = 2x", "f'(3) = 6"], "d11": ["f(x) = x³ − 2x", "f'(x) = 3x² − 2", "f'(1) = 1"],
    "d12": ["f'(x) = 2x", "f'(2) = 4", "f(2) = 4", "y = 4(x − 2) + 4", "y = 4x − 4"],
    "d13": ["f'(x) = 3x²", "f'(1) = 3", "f(1) = 1", "y = 3(x − 1) + 1", "y = 3x − 2"],
}
# stored mistakes that are valid lines on their own (f'(x) = 2x is the derivative; text answers cannot be parsed):
# the line passes and the FINAL verdict is what catches them. All the others are wrong lines.
FINAL_ONLY = {("d10", "2x"), ("d11", "3x² − 2"), ("d14", "croissante sur ]−∞, 3] et décroissante sur [3, +∞["),
              ("d14", "croissante sur ℝ"), ("d15", "minimum en x = 2"), ("d15", "maximum 2")}


def correct_steps(e):
    return CORRECT.get(e.id) or [f"f(x) = {e.expression}", e.solution]


def mistake_line(e, expr):
    return expr if expr.startswith(("y", "f")) else f"f'(x) = {expr}"


@pytest.mark.parametrize("ex_id", sorted(EX))
async def test_the_correct_derivation_passes_with_no_request(vt, ex_id):
    e = EX[ex_id]
    r, fake, _, _ = await check(vt, correct_steps(e), e.statement, exercise_id=ex_id)
    d = r.data
    assert isinstance(r, Result) and not r.degraded and fake.requests == []
    assert all(s["verdict"] in ("OK", "UNCHECKED") for s in d["steps"]), d
    assert d["first_wrong"] is None and d["final_verdict"] == "CORRECT" and d["feedback"] == ""
    assert d["graded_by"] == "sympy" and r.model_class_used is None and r.mode == "rules"
    assert set(d) == {"steps", "first_wrong", "final_verdict", "feedback", "graded_by"}
    if e.id in ("d14", "d15"):
        assert [s["verdict"] for s in d["steps"]][-1] == "UNCHECKED"          # text answers are never "wrong"


CASES = [(e.id, expr, fb) for e in BANK.exercises for expr, fb in e.common_mistakes]


@pytest.mark.parametrize("ex_id,expr,feedback", CASES, ids=[f"{a}:{b}" for a, b, _ in CASES])
async def test_every_stored_common_mistake_is_caught_on_the_right_step(vt, ex_id, expr, feedback):
    e = EX[ex_id]
    prefix = [f"f(x) = {e.expression}"] if ex_id in ("d14", "d15") or ex_id not in CORRECT else CORRECT[ex_id][:-1]
    if ex_id in ("d14", "d15"):
        steps = prefix + [expr]
    else:
        steps = (prefix if ex_id not in CORRECT else CORRECT[ex_id][:-1]) + [mistake_line(e, expr)]
    r, fake, _, _ = await check(vt, steps, e.statement, exercise_id=ex_id)
    d = r.data
    assert fake.requests == [] and not r.degraded and d["feedback"] == feedback, d
    assert d["final_verdict"] == "INCORRECT" and d["graded_by"] == "rules"
    if (ex_id, expr) in FINAL_ONLY:            # the mistaken line is a valid intermediate value or free text
        assert d["first_wrong"] is None
        assert d["steps"][-1]["verdict"] == ("UNCHECKED" if ex_id in ("d14", "d15") else "OK")
    else:                                      # the mistaken line is the last one: that is where it is caught
        assert d["first_wrong"] == len(steps) - 1 and d["steps"][-1]["verdict"] == "WRONG"
        assert all(s["verdict"] == "OK" for s in d["steps"][:-1])


# ------------------------------------------------------------------ UNCHECKED, never WRONG
@pytest.mark.parametrize("line", ["je dérive terme par terme", "u = x, v = x + 3", "f'(x) = 4h³", "f'(x) = lim h→0 ...",
                                  "x³ ≥ 0", "f'(x) = " + "9" * 100, "", "∫ 4x³ dx", "f'(x) = __import__('os')"])
def test_a_line_that_cannot_be_parsed_is_unchecked_never_wrong(line):
    t = target_for("Dérive f(x) = x⁴")
    got = check_steps([line], t)[0]
    assert got.verdict == "UNCHECKED"


def test_a_problem_that_is_not_a_derivative_problem_marks_every_line_unchecked():
    assert target_for("Résous 2x − 4 = 0") is None
    assert [r.verdict for r in check_steps(["2x = 4", "x = 2"], None)] == ["UNCHECKED", "UNCHECKED"]


async def test_non_derivative_problem_final_line_still_goes_through_grade(vt):
    r, fake, _, _ = await check(vt, ["2x = 4", "x = 2"], "Résous 2x − 4 = 0", solution="x = 2")
    assert [s["verdict"] for s in r.data["steps"]] == ["UNCHECKED", "UNCHECKED"] and r.data["first_wrong"] is None
    assert r.data["final_verdict"] == "CORRECT" and fake.requests == [] and r.data["graded_by"] == "sympy"
    grader = say(json.dumps({"verdict": "INCORRECT", "mistake": None, "feedback": "Vérifie en remplaçant x dans 2x − 4."}))
    r2, fake2, _, _ = await check(vt, ["2x = 4", "x = 3"], "Résous 2x − 4 = 0", solution="x = 2", fn=grader)
    assert r2.data["final_verdict"] == "INCORRECT" and r2.data["first_wrong"] is None      # lines unchecked, answer graded
    assert r2.data["feedback"] == "Vérifie en remplaçant x dans 2x − 4." and r2.data["graded_by"] == "llm"
    assert len(fake2.requests) == 1


async def test_chained_equalities_must_agree_with_themselves(vt):
    prob = "Dérive f(x) = 3x² + 5x − 7"
    ok, _, _, _ = await check(vt, ["f'(x) = 3 × 2x + 5 × 1 − 0 = 6x + 5"], prob)
    assert ok.data["steps"][0]["verdict"] == "OK" and ok.data["final_verdict"] == "CORRECT"
    bad, fake, _, _ = await check(vt, ["f'(x) = 6x + 5 = 6x + 7"], prob, fn=say("Vérifie chaque égalité de cette ligne."))
    assert bad.data["steps"][0]["verdict"] == "WRONG" and "égaux" in bad.data["steps"][0]["note"]
    assert bad.data["first_wrong"] == 0


async def test_a_rewrite_of_a_line_is_ok_only_if_the_line_before_it_was_ok(vt):
    prob = "Dérive f(x) = x(x + 3)"
    r, _, _, _ = await check(vt, ["f'(x) = 1", "f'(x) = 1 × 1"], prob, fn=say("Quelle règle pour un produit ?"))
    assert [s["verdict"] for s in r.data["steps"]] == ["WRONG", "WRONG"] and r.data["first_wrong"] == 0
    r2, _, _, _ = await check(vt, ["f'(x) = 1·(x + 3) + x·1", "f'(x) = x + 3 + x", "f'(x) = 2x + 3"], prob)
    assert [s["verdict"] for s in r2.data["steps"]] == ["OK", "OK", "OK"]


# ------------------------------------------------------------------ buggy rules (no exercise: the problem is read from the text)
@pytest.mark.parametrize("problem,line,rule", [
    ("Dérive f(x) = (3x + 1)(x² − 2)", "f'(x) = 3 × 2x", "PRODUCT_AS_PRODUCT"),
    ("Dérive f(x) = x⁵", "f'(x) = 5x⁵", "POW_NO_DECREMENT"),
    ("Dérive f(x) = (3x + 1)²", "f'(x) = 2(3x + 1)", "CHAIN_NO_INNER"),
    ("Dérive f(x) = x² + 5", "f'(x) = 2x + 5", "CONST_KEPT"),
    ("Derive f(x) = (2x + 1)³", "f'(x) = 3(2x + 1)²", "CHAIN_NO_INNER"),
    ("Dérive f(x) = 4x³ + 2x + 6", "f'(x) = 12x³ + 2", "POW_NO_DECREMENT")])
async def test_buggy_rules_name_the_mistake_with_no_request(vt, problem, line, rule):
    from engine.answers import RULE_FEEDBACK
    r, fake, _, _ = await check(vt, [line], problem)
    assert fake.requests == [] and r.data["first_wrong"] == 0 and r.data["graded_by"] == "rules"
    assert r.data["feedback"] == RULE_FEEDBACK[rule]["fr"] and r.notes["diagnosis"] == f"rule:{rule}"


def test_buggy_derivatives_are_listed_only_when_they_differ_from_the_true_derivative_and_are_precomputed():
    import sympy
    from engine.mathcheck import X
    assert dict(buggy_derivatives(X ** 5)) == {"POW_NO_DECREMENT": 5 * X ** 5}                   # no product, no const
    assert set(dict(buggy_derivatives((3 * X + 1) * (X ** 2 - 2)))) >= {"PRODUCT_AS_PRODUCT"}
    assert buggy_derivatives(X ** 5) is buggy_derivatives(X ** 5)                                  # cached
    assert "CHAIN_NO_INNER" not in dict(buggy_derivatives((X + 1) ** 2))                          # inner derivative is 1


async def test_rule_feedback_is_in_the_learner_s_language(vt):
    r, _, _, _ = await check(vt, ["f'(x) = 5x⁵"], "Derive f(x) = x⁵", ctx={"language": "ENGLISH"})
    assert r.data["feedback"].startswith("You multiplied by the exponent")
    r2, _, _, _ = await check(vt, ["f'(x) = 5x⁵"], "اشتق f(x) = x⁵", ctx={"language": "ARABIC"})
    assert r2.data["feedback"].startswith("ضربت في الأس") and r2.data["steps"][0]["note"].startswith("هذا السطر")


# ------------------------------------------------------------------ the compact guiding question
async def test_an_unexplained_wrong_line_gets_one_compact_guiding_question(vt):
    r, fake, sink, _ = await check(vt, ["f'(x) = 7x"], "Dérive f(x) = x⁴",
                                   fn=say("Que devient l'exposant de x quand on dérive ?"))
    assert r.data["first_wrong"] == 0 and r.data["feedback"] == "Que devient l'exposant de x quand on dérive ?"
    assert r.data["graded_by"] == "llm" and r.model_class_used == "compact" and r.mode is None
    assert len(fake.requests) == 1 and fake.requests[0]["body"]["model"] == "compact-a"
    body = fake.requests[0]["body"]["messages"]
    assert "Student's line 1: f'(x) = 7x" in body[-1]["content"] and "Never write the correct line" in body[0]["content"]
    assert r.notes["diagnosis"] == "llm" and "queued" in r.event_names and "generating" in r.event_names


@pytest.mark.parametrize("leaky", ["La réponse est f'(x) = 4x³, relis ton calcul.", "Essaie 4x³ : c'est la bonne forme.",
                                   "Tu devrais trouver 4x^3."])
async def test_a_guiding_question_that_states_the_answer_is_replaced_by_the_fixed_question(vt, leaky):
    r, fake, _, _ = await check(vt, ["f'(x) = 7x"], "Dérive f(x) = x⁴", fn=say(leaky))
    assert "4x" not in r.data["feedback"] and r.data["feedback"].startswith("Relis cette ligne")
    assert r.notes["diagnosis"] == "template" and "guarded" in r.event_names and r.data["graded_by"] != "llm"


async def test_darija_gets_the_fixed_question_with_no_request(vt):
    r, fake, _, _ = await check(vt, ["f'(x) = 7x"], "Dérive f(x) = x⁴", ctx={"language": "DARIJA"})
    assert fake.requests == [] and r.data["feedback"].startswith("عاود اقرا")


async def test_the_guiding_question_is_cached_across_learners(vt):
    eng, fake, _ = make_engine(vt, say("Que devient l'exposant ?"))
    for who in ("L1", "L2"):
        r = await eng.run("check_answer", {"problem": "Dérive f(x) = x⁴", "steps": ["f'(x) = 7x"]},
                          {"language": "FRENCH", "learner_id": who})
        assert r.data["feedback"] == "Que devient l'exposant ?"
    assert len(fake.requests) == 1


# ------------------------------------------------------------------ the final answer goes through grade
async def test_the_last_line_is_graded_by_the_grade_task_and_helpers_keep_ids_but_not_the_room(vt):
    replies = iter([chat_ok(json.dumps({"verdict": "CORRECT", "mistake": None, "feedback": ""}), 300, 20)])
    eng, fake, sink = make_engine(vt, lambda b: next(replies))
    r = await eng.run("check_answer", {"problem": "Explique pourquoi la dérivée d'une constante est nulle.",
                                       "steps": ["une constante ne varie pas, donc sa pente est 0"],
                                       "solution": "Une constante ne varie pas : sa pente est 0."},
                      {"language": "FRENCH", "learner_id": "L1", "room_id": "R1", "course_id": "analyse"})
    assert r.data["final_verdict"] == "CORRECT" and r.data["graded_by"] == "llm" and len(fake.requests) == 1
    assert r.degrade_reason != "room_busy" and not r.degraded and r.tokens_in == 300
    grade = [x for x in sink.records if x.task == "grade" and x.kind == "run"][0]
    assert (grade.learner_id, grade.course_id, grade.room_id) == ("L1", "analyse", None)
    assert "generating" in r.event_names


async def test_a_refusal_while_grading_degrades_the_answer_with_the_eta(vt):
    eng, fake, _ = make_engine(vt, lambda b: quota_429(60))
    r = await eng.run("check_answer", {"problem": "Explique pourquoi la dérivée d'une constante est nulle.",
                                       "steps": ["parce que ça ne bouge pas"],
                                       "solution": "Une constante ne varie pas : sa pente est 0."}, {"language": "FRENCH"})
    assert r.degraded and r.degrade_reason == "quota_exhausted" and r.eta_seconds == 60
    assert r.data["final_verdict"] == "UNKNOWN" and r.data["steps"][0]["verdict"] == "UNCHECKED"


async def test_the_solution_is_computed_when_none_is_given(vt):
    r, fake, _, _ = await check(vt, ["f'(x) = 4x³"], "Dérive f(x) = x⁴")
    assert r.data["final_verdict"] == "CORRECT" and fake.requests == []
    r2, _, _, _ = await check(vt, ["f'(x) = 4x³"], "Une phrase sans fonction lisible")
    assert r2.data["final_verdict"] == "UNKNOWN" and r2.data["steps"][0]["verdict"] == "UNCHECKED"


# ------------------------------------------------------------------ caller bugs, metrics
async def test_caller_bugs_raise_before_anything_is_sent(vt):
    eng, fake, _ = make_engine(vt, never)
    for steps in ([], ["", "  "], 42):
        with pytest.raises(ValueError):
            await eng.run("check_answer", {"problem": "Dérive f(x) = x⁴", "steps": steps})
    with pytest.raises(ValueError, match="unknown exercise_id"):
        await eng.run("check_answer", {"problem": "p", "steps": ["x"], "exercise_id": "zz9"})
    with pytest.raises(ValueError, match="needs inputs"):
        await eng.run("check_answer", {"problem": "Dérive f(x) = x⁴"})
    assert fake.requests == []


async def test_steps_may_be_one_multiline_string_and_one_run_record_is_written(vt):
    r, _, sink, _ = await check(vt, "f(x) = x⁴\nf'(x) = 4x³\n", ctx={"language": "FRENCH", "learner_id": "L9"})
    assert [s["verdict"] for s in r.data["steps"]] == ["OK", "OK"]
    rec = [x for x in runs(sink) if x.task == "check_answer"]
    assert len(rec) == 1 and rec[0].learner_id == "L9" and rec[0].model_class is None
    assert r.event_names[:2] == ["parsing_steps", "checking_steps"]
