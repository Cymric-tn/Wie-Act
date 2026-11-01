"""
prep_plan and the exercise parts of generate_page: no model.

  build_plan(course, ...)   -> {focus_points: [{concept_id, day_index, reason, reason_code, page_kind, minutes}],
                                assessment_exercise_ids: [...]} + notes

Rules
  * scope = unit ids and/or concept ids and/or material ids (uploaded files: the concepts their chunks are tagged
    with). Draft concepts (no course text or no exercises yet) are left out and listed in notes.
  * Mastery p (missing = 0). p >= MASTERED (0.85): a REVIEW page only, after the pages to learn. Below: a LEARN page.
  * Order of the learn pages: prerequisites first (a prerequisite that is in the scope and not mastered must come
    before what needs it; a mastered prerequisite does not block), then lowest mastery, then the course's order.
  * Days: the study days are start .. deadline - 1; the pages are spread evenly over them without going over
    `pace_min_per_day` (a LEARN page costs LEARN_MIN minutes, a REVIEW page REVIEW_MIN). If the days cannot hold the
    work, the last day takes the rest and notes.over_capacity says by how many minutes.
  * assessment_exercise_ids: one bank exercise per concept in plan order, harder when mastery is higher.

Exercise variants (generate_page): when the bank has no further exercise for a concept, a derivative exercise is
varied deterministically (a different coefficient or constant, the solution computed with sympy), so a page always has
its three quiz items and a worked example.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

import sympy

from engine.claims import pretty
from engine.course import Course
from engine.exercises import ExerciseBank, phrase_language
from engine.mathcheck import X, parse_answer

MASTERED = 0.85
LEARN_MIN, REVIEW_MIN = 20, 8
ASSESSMENT_MAX = 10

REASONS = {
    "prerequisite": {"fr": "Prérequis de « {dep} »", "en": "Prerequisite of “{dep}”", "ar": "شرط مسبق لـ «{dep}»"},
    "low_mastery": {"fr": "Maîtrise faible ({p})", "en": "Low mastery ({p})", "ar": "إتقان ضعيف ({p})"},
    "learn": {"fr": "À consolider ({p})", "en": "To consolidate ({p})", "ar": "للتثبيت ({p})"},
    "review": {"fr": "Déjà maîtrisé ({p}) : révision rapide", "en": "Already mastered ({p}): quick review",
               "ar": "متقن ({p}): مراجعة سريعة"},
}


class PlanError(ValueError):
    pass


def parse_date(value: Any, what: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise PlanError(f"{what} must be a date (YYYY-MM-DD), got {value!r}") from None


def _p(m: Mapping[str, Any], cid: str) -> float:
    try:
        return min(1.0, max(0.0, float(m.get(cid, 0.0))))
    except (TypeError, ValueError):
        return 0.0


def scope_concepts(course: Course, scope: Mapping[str, Any], material_concepts: Callable[[str], set[str]] | None,
                   ) -> tuple[list[str], dict]:
    """Concept ids named by the scope, in course order; and what could not be resolved."""
    notes: dict[str, Any] = {}
    ids: set[str] = set()
    for u in scope.get("unit_ids") or []:
        try:
            ids |= set(course.concepts_of(str(u)))
        except KeyError:
            notes.setdefault("unknown_units", []).append(str(u))
    for c in scope.get("concept_ids") or []:
        if str(c) in course.concepts:
            ids.add(str(c))
        else:
            notes.setdefault("unknown_concepts", []).append(str(c))
    for m in scope.get("material_ids") or []:
        found = (material_concepts(str(m)) if material_concepts else set()) & set(course.concepts)
        if found:
            ids |= found
        else:
            notes.setdefault("unknown_materials", []).append(str(m))
    drafts = sorted(c for c in ids if course.concepts[c].draft)
    if drafts:
        notes["skipped_draft"] = drafts
    return [c for c in course.order() if c in ids and not course.concepts[c].draft], notes


def order_learn(course: Course, learn: list[str], mastery: Mapping[str, Any]) -> list[str]:
    """Prerequisites first, then lowest mastery, then course order. Only prerequisites that are themselves to be
    learned gate a concept (out of scope or mastered ones do not)."""
    rank = {c: i for i, c in enumerate(course.order())}
    gates = {c: {r for r in course.prerequisites(c) if r in learn} for c in learn}
    placed: list[str] = []
    left = set(learn)
    while left:
        ready = [c for c in left if gates[c] <= set(placed)]
        nxt = min(ready, key=lambda c: (_p(mastery, c), rank[c]))          # a DAG always has a ready concept
        placed.append(nxt)
        left.remove(nxt)
    return placed


def spread(costs: Sequence[int], days: int, pace: int) -> tuple[list[int], int]:
    """Day index of each item (in order): fill the days evenly without exceeding `pace` minutes a day.
    Returns (days, overflow minutes): the last day absorbs what the days cannot hold."""
    total = sum(costs)
    target = max(1, min(pace, math.ceil(total / max(1, days))))
    out, day, used = [], 0, 0
    for c in costs:
        if used and used + c > max(target, c) and day < days - 1:
            day, used = day + 1, 0
        out.append(day)
        used += c
    last_min = sum(c for c, d in zip(costs, out) if d == days - 1)
    return out, max(0, last_min - pace) if total > days * pace else 0


def pick_difficulty(p: float) -> int:
    return 1 if p < 0.34 else 2 if p < 0.67 else 3


def build_plan(course: Course, scope: Mapping[str, Any], deadline: Any, pace_min_per_day: Any,
               mastery: Mapping[str, Any] | None, language: str | None, bank: ExerciseBank | None,
               start: Any = None, material_concepts: Callable[[str], set[str]] | None = None,
               learn_min: int = LEARN_MIN, review_min: int = REVIEW_MIN, max_assessment: int = ASSESSMENT_MAX,
               ) -> tuple[dict, dict]:
    mastery = dict(mastery or {})
    try:
        pace = int(float(pace_min_per_day))
    except (TypeError, ValueError):
        raise PlanError(f"pace_min_per_day must be a number of minutes, got {pace_min_per_day!r}") from None
    if pace < 1:
        raise PlanError("pace_min_per_day must be at least 1")
    if not isinstance(scope, Mapping) or not any(scope.get(k) for k in ("unit_ids", "concept_ids", "material_ids")):
        raise PlanError("scope needs unit_ids, concept_ids or material_ids")
    d_end = parse_date(deadline, "deadline")
    d_start = parse_date(start, "start") if start else dt.date.today()
    days = (d_end - d_start).days
    concepts, notes = scope_concepts(course, scope, material_concepts)
    if not concepts:
        raise PlanError("the scope names no concept of this course" + (f" ({notes})" if notes else ""))
    mastered = [c for c in concepts if _p(mastery, c) >= MASTERED]
    learn = order_learn(course, [c for c in concepts if c not in mastered], mastery)
    review = [c for c in concepts if c in mastered]
    seq = [(c, "learn") for c in learn] + [(c, "review") for c in review]
    costs = [learn_min if kind == "learn" else review_min for _, kind in seq]
    n_days = max(1, days)
    if days < 1:
        notes["deadline_not_in_future"] = True
    day_of, overflow = spread(costs, n_days, pace)
    lang = phrase_language(language)
    dependents = {c: [d for d in learn if c in course.prerequisites(d)] for c in learn}
    points = []
    for (cid, kind), day, minutes in zip(seq, day_of, costs):
        p = _p(mastery, cid)
        if kind == "review":
            code = "review"
        elif dependents.get(cid):
            code = "prerequisite"
        else:
            code = "low_mastery" if p < 0.5 else "learn"
        dep = ", ".join(course.title(d, lang) for d in dependents.get(cid, [])[:2])
        reason = REASONS[code][lang].format(dep=dep, p=f"{p:.0%}")
        if code == "prerequisite" and p < 0.5:
            reason += " ; " + REASONS["low_mastery"][lang].format(p=f"{p:.0%}")
        points.append({"concept_id": cid, "day_index": day, "reason": reason, "reason_code": code, "page_kind": kind,
                       "minutes": minutes})
    chosen: list[str] = []
    if bank is not None:
        for cid, _ in seq:
            ex = bank.pick([cid], pick_difficulty(_p(mastery, cid)), tuple(chosen), strict=True)
            if ex is not None and len(chosen) < max_assessment:
                chosen.append(ex.id)
    notes.update(days=n_days, minutes_needed=sum(costs), minutes_available=n_days * pace, pace_min_per_day=pace,
                 start=d_start.isoformat(), deadline=d_end.isoformat())
    if overflow:
        notes["over_capacity"] = True
        notes["overflow_minutes"] = overflow
    return {"focus_points": points, "assessment_exercise_ids": chosen}, notes


# ------------------------------------------------------------------ exercises for a page
def variant_of(ex, n: int = 1) -> dict | None:
    """A deterministic variant of a derivative exercise: the function times (n + 1), the solution recomputed. None when
    the exercise is not a plain 'derive f' one."""
    if ex.solution.strip().startswith(("y", "f'(") and ("f'(x",)) and not ex.solution.strip().startswith("f'(x)"):
        return None
    f = parse_answer(ex.expression) if ex.expression else None
    if f is None or not f.has(X) or not ex.solution.strip().startswith("f'(x)"):
        return None
    k = n + 1
    g = sympy.expand(k * f) if f.is_polynomial(X) else k * f
    d = sympy.diff(g, X)
    expr = pretty(g)
    lang = {"fr": "Dérive f(x) = {e}.", "en": "Differentiate f(x) = {e}.", "ar": "اشتق الدالة f(x) = {e}."}
    return {"id": f"{ex.id}~v{n}", "concept": ex.concept, "level": ex.level, "expression": expr,
            "statement": {k2: v.format(e=expr) for k2, v in lang.items()}, "solution": f"f'(x) = {pretty(d)}",
            "source": "variant", "generated": True}


def exercise_item(ex, lang: str) -> dict:
    return {"id": ex.id, "concept": ex.concept, "level": ex.level, "statement": ex.render(lang),
            "expression": ex.expression, "source": "bank"}


def page_exercises(bank: ExerciseBank | None, course: Course | None, concept_id: str, mastery_p: float, language: str | None,
                   n_quiz: int = 3) -> tuple[dict | None, list[dict]]:
    """(worked example, quiz items) for a page: bank exercises of the concept first, then of its prerequisites, then
    deterministic variants, so there are always `n_quiz` quiz items and, when any exercise exists, a worked example."""
    lang = phrase_language(language)
    if bank is None:
        return None, []
    want = pick_difficulty(mastery_p)
    pool_ids = [concept_id] + (course.prerequisites(concept_id)[::-1] if course and concept_id in course.concepts else [])
    exclude: list[str] = []
    picked = []
    for cid in pool_ids:
        while len(picked) < n_quiz + 1:
            ex = bank.pick([cid], want, tuple(exclude), strict=True)
            if ex is None:
                break
            exclude.append(ex.id)
            picked.append(ex)
        if len(picked) >= n_quiz + 1:
            break
    base = picked[0] if picked else None
    if base is None:
        return None, []
    # the worked example is the easiest exercise of the concept (it comes with its graded hints as the steps)
    own = [e for e in picked if e.concept == concept_id] or picked
    worked_ex = min(own, key=lambda e: (e.level, e.id))
    worked = {"source": "bank", "exercise_id": worked_ex.id, "statement": worked_ex.render(lang),
              "steps": list(worked_ex.hints), "solution": worked_ex.solution}
    quiz = [exercise_item(e, lang) for e in picked if e.id != worked_ex.id][:n_quiz]
    n = 1
    while len(quiz) < n_quiz:
        v = variant_of(worked_ex, n)
        if v is None:
            break
        quiz.append({"id": v["id"], "concept": v["concept"], "level": v["level"], "statement": v["statement"][lang],
                     "expression": v["expression"], "source": "variant", "solution": v["solution"]})
        n += 1
    return worked, quiz[:n_quiz]
