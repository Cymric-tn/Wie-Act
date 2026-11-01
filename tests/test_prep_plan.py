"""prep_plan (no model) and generate_page."""
import datetime as dt

import pytest

from engine import prep
from engine.course import load_course
from engine.exercises import ExerciseBank
from engine.mathcheck import X, equivalent, parse_answer
from rag.chunker import Chunk
from tests.conftest import chat_ok
from tests.test_engine import _index, make_engine, runs  # noqa: F401  (_index: the autouse course index)
from tests.test_retry_v11 import quota_429

COURSE = load_course("data/courses/analyse.yaml")
BANK = ExerciseBank.load("data/exercises/derivees.yaml")
START = "2026-11-05"
UNIT = {"unit_ids": ["derivees"]}


def plan(scope=UNIT, deadline="2026-11-10", pace=40, mastery=None, lang="FRENCH", **kw):
    return prep.build_plan(COURSE, scope, deadline, pace, mastery, lang, BANK, START, **kw)


def ids(data, kind=None):
    return [f["concept_id"] for f in data["focus_points"] if kind in (None, f["page_kind"])]


# ------------------------------------------------------------------ order
def test_prerequisites_come_first_then_lowest_mastery_and_drafts_are_skipped():
    data, notes = plan(mastery={})                                  # nothing known: pure prerequisite order
    order = ids(data)
    assert "limite" not in order and notes["skipped_draft"] == ["limite"]
    assert len(order) == 11 and order[0] == "nombre dérivé"
    for c in order:
        assert all(order.index(r) < order.index(c) for r in COURSE.prerequisites(c) if r in order), c
    # among concepts that are ready, the lowest mastery goes first
    data2, _ = plan(mastery={"tangente": 0.0, "constante": 0.5, "puissance": 0.3, "nombre dérivé": 0.1})
    o2 = ids(data2)
    assert o2[0] == "nombre dérivé"
    assert o2[1:3] == ["tangente", "puissance"]                     # ready after the first: mastery 0, then .3
    # puissance unlocks inverse and racine carrée (mastery 0 each): they now outrank constante (0.5)
    assert o2.index("inverse") < o2.index("constante") and o2.index("racine carrée") < o2.index("constante")


def test_a_low_mastery_concept_still_waits_for_its_unlearned_prerequisite():
    data, _ = plan(mastery={"produit": 0.0, "polynôme": 0.9, "puissance": 0.6, "constante": 0.7, "nombre dérivé": 0.8})
    o = ids(data, "learn")
    # polynôme is mastered (review only), so produit is not blocked by it; puissance (0.6) < constante... are all < 0.85 learn pages
    assert "polynôme" not in o and ids(data, "review") == ["polynôme"]
    assert o.index("produit") < o.index("quotient")                 # quotient needs produit
    assert o.index("puissance") < o.index("inverse")                # inverse needs puissance


def test_mastered_concepts_get_a_review_page_only_after_the_pages_to_learn():
    m = {"nombre dérivé": 0.9, "tangente": 0.85, "constante": 0.2}
    data, _ = plan(mastery=m)
    kinds = {f["concept_id"]: f["page_kind"] for f in data["focus_points"]}
    assert kinds["nombre dérivé"] == "review" and kinds["tangente"] == "review" and kinds["constante"] == "learn"
    pts = data["focus_points"]
    first_review = next(i for i, f in enumerate(pts) if f["page_kind"] == "review")
    assert all(f["page_kind"] == "review" for f in pts[first_review:])
    assert [f["concept_id"] for f in pts[first_review:]] == ["nombre dérivé", "tangente"]      # course order
    # a mastered prerequisite does not block what needs it
    assert "constante" in ids(data, "learn") and "puissance" in ids(data, "learn")
    assert all(f["minutes"] == (8 if f["page_kind"] == "review" else 20) for f in pts)


def test_every_focus_point_has_the_contract_fields_and_a_reason_in_the_learner_s_language():
    data, _ = plan(mastery={"nombre dérivé": 0.1, "tangente": 0.9}, lang="ENGLISH")
    f = data["focus_points"][0]
    assert set(f) == {"concept_id", "day_index", "reason", "reason_code", "page_kind", "minutes"}
    assert f["concept_id"] == "nombre dérivé" and f["reason_code"] == "prerequisite"
    assert f["reason"].startswith("Prerequisite of “") and "Low mastery (10%)" in f["reason"]
    rev = next(x for x in data["focus_points"] if x["concept_id"] == "tangente")
    assert rev["reason"] == "Already mastered (90%): quick review" and rev["reason_code"] == "review"
    ar, _ = plan(mastery={}, lang="ARABIC")
    assert "شرط مسبق" in ar["focus_points"][0]["reason"]
    fr, _ = plan(mastery={"tangente": 0.6})             # tangente: nothing depends on it, not weak: just to consolidate
    assert next(x for x in fr["focus_points"] if x["concept_id"] == "tangente")["reason"] == "À consolider (60%)"


# ------------------------------------------------------------------ days
def test_pages_are_spread_over_the_days_until_the_deadline_without_exceeding_the_pace():
    data, notes = plan(deadline="2026-11-10", pace=60, mastery={})         # 5 study days, 11 learn pages = 220 min
    days = [f["day_index"] for f in data["focus_points"]]
    assert days == sorted(days) and min(days) == 0 and max(days) <= 4
    per_day = {d: sum(f["minutes"] for f in data["focus_points"] if f["day_index"] == d) for d in set(days)}
    assert all(v <= 60 for v in per_day.values()), per_day
    assert len(per_day) == 4 or len(per_day) == 5                         # spread, not piled on the first days
    assert notes["days"] == 5 and notes["minutes_needed"] == 220 and notes["minutes_available"] == 300
    assert "over_capacity" not in notes


def test_a_short_deadline_puts_the_overflow_on_the_last_day_and_says_so():
    data, notes = plan(deadline="2026-11-07", pace=30, mastery={})        # 2 days x 30 min for 220 min
    assert max(f["day_index"] for f in data["focus_points"]) == 1
    assert notes["over_capacity"] is True and notes["overflow_minutes"] > 0
    today, n2 = plan(deadline=START, pace=30, mastery={})                  # deadline today: one day, flagged
    assert {f["day_index"] for f in today["focus_points"]} == {0} and n2["deadline_not_in_future"] is True
    past, n3 = plan(deadline="2026-11-01", pace=30, mastery={})
    assert {f["day_index"] for f in past["focus_points"]} == {0} and n3["deadline_not_in_future"] is True


def test_a_generous_pace_still_spreads_the_pages_instead_of_piling_them_on_day_zero():
    data, _ = plan(scope={"concept_ids": ["puissance", "polynôme", "produit"]}, deadline="2026-11-08", pace=600,
                   mastery={})
    assert [f["day_index"] for f in data["focus_points"]] == [0, 1, 2]


# ------------------------------------------------------------------ scope, assessment, errors
def test_scope_by_concept_ids_and_unknown_ids_are_reported_not_fatal():
    data, notes = plan(scope={"concept_ids": ["produit", "zzz"], "unit_ids": ["nope"]}, mastery={})
    assert ids(data) == ["produit"] and notes["unknown_concepts"] == ["zzz"] and notes["unknown_units"] == ["nope"]
    with pytest.raises(prep.PlanError, match="names no concept"):
        plan(scope={"concept_ids": ["zzz"]})


def test_assessment_exercises_follow_the_plan_and_get_harder_with_mastery():
    low, _ = plan(scope={"concept_ids": ["puissance", "polynôme"]}, mastery={"puissance": 0.1, "polynôme": 0.1})
    high, _ = plan(scope={"concept_ids": ["puissance", "polynôme"]}, mastery={"puissance": 0.8, "polynôme": 0.8})
    lv = lambda d: [BANK.get(i).level for i in d["assessment_exercise_ids"]]
    assert len(set(low["assessment_exercise_ids"])) == len(low["assessment_exercise_ids"]) == 2
    assert [BANK.get(i).concept for i in low["assessment_exercise_ids"]] == ["puissance", "polynôme"]
    assert lv(high)[1] > lv(low)[1]
    capped, _ = plan(mastery={}, max_assessment=3)
    assert len(capped["assessment_exercise_ids"]) == 3


@pytest.mark.parametrize("kw,msg", [({"deadline": "demain"}, "deadline must be a date"), ({"pace": 0}, "at least 1"),
                                    ({"pace": "vite"}, "number of minutes"), ({"scope": {}}, "scope needs")])
def test_bad_plan_inputs_are_plan_errors(kw, msg):
    with pytest.raises(prep.PlanError, match=msg):
        plan(**kw)


def test_mastery_values_are_clamped_and_garbage_counts_as_unknown():
    data, _ = plan(mastery={"nombre dérivé": 7, "tangente": "n/a", "constante": -2})
    kinds = {f["concept_id"]: f["page_kind"] for f in data["focus_points"]}
    assert kinds["nombre dérivé"] == "review" and kinds["tangente"] == "learn" and kinds["constante"] == "learn"


# ------------------------------------------------------------------ through the engine
async def test_the_task_runs_with_no_request_and_the_contract_result(vt):
    eng, fake, sink = make_engine(vt, lambda b: pytest.fail("prep_plan must not call a model"))
    r = await eng.run("prep_plan", {"scope": UNIT, "deadline": "2026-11-10", "pace_min_per_day": 40, "start": START,
                                    "mastery": {"nombre dérivé": 0.9}, "language": "ENGLISH", "level": "beginner"},
                      {"course_id": "analyse", "learner_id": "L1"})
    assert not r.degraded and fake.requests == [] and r.mode == "rules" and r.model_class_used is None
    assert r.event_names == ["answered_by_rules"] and set(r.data) == {"focus_points", "assessment_exercise_ids"}
    assert r.data["focus_points"][-1] == {**r.data["focus_points"][-1], "page_kind": "review"}
    assert r.notes["days"] == 5 and runs(sink)[-1].learner_id == "L1"
    assert r.data["focus_points"][0]["reason"].startswith("Low mastery")      # language from the inputs (nombre dérivé is mastered)
    # the only course is the default
    r2 = await eng.run("prep_plan", {"scope": UNIT, "deadline": "2026-11-10", "pace_min_per_day": 40, "start": START})
    assert r2.data["focus_points"] and (r2.tokens_in, r2.tokens_out) == (0, 0)


async def test_material_ids_resolve_through_the_chunks_concept_tags(tmp_path, vt):
    from tests.test_retrieval_dynamic import make_engine as dyn
    eng, _ = dyn(tmp_path, vt)
    await eng.add_material([Chunk("professor/prof-7/polycopie#a", "Polycopié — Produit\nLa règle du produit.", "derivative",
                                  "beginner", "FRENCH", "polycopie.md", concept="produit", course_id="analyse",
                                  origin="professor", owner="prof-7")])
    r = await eng.run("prep_plan", {"scope": {"material_ids": ["professor/prof-7/polycopie", "ghost"]},
                                    "deadline": "2026-11-10", "pace_min_per_day": 30, "start": START}, {"course_id": "analyse"})
    assert [f["concept_id"] for f in r.data["focus_points"]] == ["produit"] and r.notes["unknown_materials"] == ["ghost"]


async def test_caller_bugs_raise_value_error(vt):
    eng, _, _ = make_engine(vt, lambda b: chat_ok("x"))
    base = {"scope": UNIT, "deadline": "2026-11-10", "pace_min_per_day": 40}
    with pytest.raises(ValueError, match="deadline must be a date"):
        await eng.run("prep_plan", {**base, "deadline": "soon"})
    with pytest.raises(ValueError, match="course_id"):
        await eng.run("prep_plan", base, {"course_id": "physique"})
    with pytest.raises(ValueError, match="needs inputs"):
        await eng.run("prep_plan", {"scope": UNIT})


# ------------------------------------------------------------------ generate_page
EXPLAIN = "La dérivée d'une puissance s'obtient en multipliant par l'exposant puis en baissant l'exposant de 1."
FP = {"concept_id": "puissance", "day_index": 0, "page_kind": "learn", "reason_code": "low_mastery", "mastery": 0.2}


async def test_a_page_has_an_explanation_a_worked_example_and_three_quiz_items(vt):
    eng, fake, sink = make_engine(vt, lambda b: chat_ok(EXPLAIN, 900, 40))
    page = await eng.generate_page({}, FP, {"language": "FRENCH", "learner_id": "L1", "course_id": "analyse"})
    assert page["concept_id"] == "puissance" and page["title"] == "Dérivée d'une puissance" and not page["degraded"]
    assert EXPLAIN in page["explanation"] and page["explain_request_id"] and page["explanation_sections"]["body"] == EXPLAIN
    w = page["worked_example"]
    assert w["source"] == "bank" and w["exercise_id"] and len(w["steps"]) == 3 and w["solution"].startswith("f'(x)")
    assert len(page["quiz"]) == 3 and len({q["id"] for q in page["quiz"]}) == 3 and w["exercise_id"] not in {q["id"] for q in page["quiz"]}
    assert all(q["statement"] and q["concept"] for q in page["quiz"])
    ex = page["explanation_sections"].get("exercise")
    assert ex is None or ex["id"] not in {q["id"] for q in page["quiz"]} | {w["exercise_id"]}
    mid = [x for x in sink.records if x.kind == "chat" and x.model_class == "midsize"]
    assert len(mid) == 1 and mid[0].priority == "BACKGROUND" and mid[0].learner_id == "L1"


async def test_the_page_shares_the_tutor_s_cache(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(EXPLAIN, 900, 40))
    tutor = await eng.run("explain", {"question": "C'est quoi : Dérivée d'une puissance ?"}, {"language": "FRENCH"})
    assert not tutor.from_cache and len(fake.requests) == 1
    page = await eng.generate_page({}, FP, {"language": "FRENCH"})
    assert page["from_cache"] and len(fake.requests) == 1                    # the page cost no request
    again = await eng.generate_page({}, FP, {"language": "FRENCH"})
    assert again["from_cache"] and len(fake.requests) == 1
    assert eng.cache.invalidate(page["explain_request_id"]) == 1             # a professor can reject it


async def test_a_refused_explanation_still_gives_the_exercises(vt):
    eng, _, _ = make_engine(vt, lambda b: quota_429(60))
    page = await eng.generate_page({}, FP, {"language": "FRENCH"})
    assert page["degraded"] and page["degrade_reason"] == "quota_exhausted" and page["eta_seconds"] == 60
    assert page["worked_example"] and len(page["quiz"]) == 3


def test_variants_fill_the_quiz_when_the_bank_runs_out_and_their_solutions_are_computed():
    one = ExerciseBank(exercises=[e for e in BANK.exercises if e.id == "d01"])
    worked, quiz = prep.page_exercises(one, None, "puissance", 0.2, "FRENCH")
    assert worked["exercise_id"] == "d01" and len(quiz) == 3 and all(q["source"] == "variant" for q in quiz)
    assert [q["id"] for q in quiz] == ["d01~v1", "d01~v2", "d01~v3"]
    for q in quiz:
        f = parse_answer(q["expression"])
        assert equivalent(parse_answer(q["solution"]), __import__("sympy").diff(f, X))
        assert q["statement"].startswith("Dérive f(x) =")
    # text-only exercises (variations) have no variant: fewer items rather than made-up ones
    txt = ExerciseBank(exercises=[e for e in BANK.exercises if e.id == "d14"])
    w2, q2 = prep.page_exercises(txt, None, "variations", 0.2, "FRENCH")
    assert w2["exercise_id"] == "d14" and q2 == []


async def test_the_page_language_follows_the_context(vt):
    eng, _, _ = make_engine(vt, lambda b: chat_ok("Derivative of a power: multiply by the exponent, lower it by 1.", 500, 30))
    page = await eng.generate_page({}, FP, {"language": "ENGLISH"})
    assert page["title"] == "Derivative of a power" and page["worked_example"]["statement"].startswith(("Differentiate", "Give", "Compute", "Find", "Calculate", "Compute"))
