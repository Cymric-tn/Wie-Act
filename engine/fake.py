"""
FakeEngine: the same `run` signature as engine.Engine, canned but realistic Results, no LLM, no network.

For the UIs (B and C): every state the real engine can produce can be shown by choosing a mode or an outcome.

    fake = FakeEngine(mode="normal")                  # normal | slow | degraded
    r = await fake.run("explain", {"question": "..."}, {"language": "FRENCH"}, Priority.INTERACTIVE,
                       on_event=print)
    r = await fake.run("check_claim", {"claim": "..."}, {"fake_outcome": "NOT_IN_COURSE"})   # force one state

  normal    the happy outcome of each task, with the timings of FelCloud (compact 2-5 s, mid-size 8-15 s).
  slow      the same outcomes, 3x slower; explain misses the 25 s interactive deadline and returns fast mode.
  degraded  each task's typical degraded outcome (fast mode, quota wait with eta_seconds, NOT_IN_COURSE, ...).

`context["fake_outcome"]` overrides the mode for one call; FakeEngine.OUTCOMES lists the names per task.
Events are emitted with the names and the t_ms the real engine uses (docs/ENGINE_API.md). `speed` scales the real
sleeping (1.0 = real time, 0 = instant); t_ms and latency_ms always report the engine time, not the scaled one.
room_busy is real: a second concurrent run with the same room_id is refused, as in the engine.
Pure data and asyncio: nothing here imports a provider, the gatekeeper or the cache.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from engine.result import Event, Result
from gatekeeper.queue import Priority

MODES = ("normal", "slow", "degraded")
SLOW_FACTOR = 3.0
INTERACTIVE_DEADLINE_S = 25.0
ROOM_EXPECTED_S = 8.0


@dataclass
class _Plan:
    """What a call will look like: the events (engine time, name, data) and the result fields."""
    events: list[tuple[float, str, dict]]
    end_s: float
    fields: dict[str, Any] = field(default_factory=dict)


def _ev(*items) -> list[tuple[float, str, dict]]:
    return [(t, n, d or {}) for t, n, d in items]


# ---------------------------------------------------------------------------- canned content (synthetic)
EXCERPTS = [
    {"chunk_id": "derivee-definition.fr#nombre-derive", "text": "Le nombre dérivé de f en a est la limite, quand h tend vers 0, du taux d'accroissement (f(a+h) − f(a))/h."},
    {"chunk_id": "derivee-definition.fr#nombre-derive", "text": "Géométriquement, c'est le coefficient directeur de la tangente à la courbe au point d'abscisse a."},
    {"chunk_id": "derivee-formules.fr#puissance", "text": "Pour tout entier n ≥ 1, (xⁿ)' = n·xⁿ⁻¹."},
]
EXERCISE = {"id": "d01", "concept": "puissance", "level": 1, "statement": "Dérive f(x) = x⁴.", "expression": "x⁴"}
EXPLAIN_BODY = ("Le nombre dérivé mesure la pente de la courbe en un point. On regarde le taux d'accroissement "
                "(f(a+h) − f(a))/h entre a et a+h ; quand h devient très petit, ce taux se stabilise : sa limite est f'(a). "
                "Par exemple, pour f(x) = x², le taux vaut 2a + h, donc f'(a) = 2a.")
OPENER = "Bonne question, on la prend étape par étape."
FRAMING = "Je n'ai pas pu rédiger l'explication complète à l'instant ; voici ce que dit le cours :"
OCR_TEXT = "Dériver f(x) = 3x² + 5x\nf'(x) = 6x + 5"
OCR_LATEX = "Dériver f(x) = 3x^{2} + 5x \\\\ f'(x) = 6x + 5"
CONCEPTS = ["limite", "taux-accroissement", "nombre-derive", "tangente", "puissance", "polynome", "constante",
            "somme", "produit", "quotient", "composee"]


def _tokens(task: str, key: str) -> tuple[int, int]:
    h = int(hashlib.sha256(f"{task}{key}".encode()).hexdigest()[:6], 16)
    return 120 + h % 400, 20 + h % 120


def _hint_text(lang: str) -> str:
    return {"ENGLISH": "What rule gives the derivative of xⁿ? Try it on the exponent first.",
            "ARABIC": "ما هي القاعدة التي تعطي مشتقة xⁿ ؟ ابدأ بالأس.",
            }.get(lang, "Quelle règle donne la dérivée de xⁿ ? Commence par regarder l'exposant.")


class FakeEngine:
    """Drop-in stand-in for Engine on the UI side. Same run(); extras are no-ops that keep calling code unchanged."""

    OUTCOMES: dict[str, tuple[str, ...]] = {
        "classify": ("ok", "cache_hit", "safe_default", "quota_exhausted", "superseded", "room_busy"),
        "hint": ("ok", "stored", "quota_exhausted", "superseded", "room_busy"),
        "explain": ("ok", "cache_hit", "fast_extractive", "deadline", "quota_exhausted", "not_in_course",
                    "superseded", "room_busy"),
        "quiz": ("ok", "diagnostic"),
        "grade": ("correct", "incorrect", "quota_exhausted"),
        "compress": ("ok",),
        "ocr_transcribe": ("ok", "math", "low_confidence", "invalid_image", "quota_exhausted", "superseded",
                           "room_busy"),
        "check_claim": ("SUPPORTED", "CONTRADICTED", "PARTLY", "NOT_IN_COURSE", "CONTRADICTED_llm", "llm_failed",
                        "quota_exhausted", "superseded", "room_busy"),
        "check_answer": ("ok", "wrong_step", "rule", "llm_feedback", "unchecked", "quota_exhausted", "room_busy"),
        "prep_plan": ("ok", "all_mastered"),
    }
    DEGRADED_DEFAULT = {"classify": "safe_default", "hint": "quota_exhausted", "explain": "fast_extractive",
                        "quiz": "ok", "grade": "quota_exhausted", "compress": "ok", "ocr_transcribe": "low_confidence",
                        "check_claim": "NOT_IN_COURSE", "check_answer": "unchecked", "prep_plan": "all_mastered"}
    GENERIC = ("quota_exhausted", "superseded", "room_busy")

    def __init__(self, mode: str = "normal", *, speed: float = 1.0, sleep: Callable = asyncio.sleep):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode, self.speed, self._sleep = mode, speed, sleep
        self._rooms: set[str] = set()
        self.calls: list[tuple[str, str]] = []              # (task, outcome) of every call, for UI tests
        self.cache = _FakeCache()
        self.materials: list = []                           # chunks added through add_material / ingest_file(add=True)

    # ---------------------------------------------------------------- lifecycle (same as Engine)
    async def aclose(self) -> None:
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def status(self) -> dict:
        """Same shape as Engine.status(); the mode picks how loaded the pretend platform looks."""
        busy = {"normal": (20, 0, 0), "slow": (14, 1, 2), "degraded": (0, 2, 5)}[self.mode]
        return {
            "breaker": {"midsize": ({"state": "open", "retry_in_s": 24.0, "trips": 1} if self.mode == "degraded"
                                    else {"state": "closed", "retry_in_s": 0.0, "trips": 0})},
            "window": {"limit": 20, "window_s": 60.0, "remaining": busy[0], "in_flight": busy[1], "max_concurrent": 2},
            "queue": {"depth": busy[2]},
        }

    # ---------------------------------------------------------------- course material (real parsing, no model)
    async def ingest_file(self, file: str, course_id: str, unit_id: str = "", owner: str = "",
                          visibility: str = "public", source: str = "professor", *, class_id: str = "",
                          ocr: bool = True, add: bool = False, **kw):
        """The real reader (rag.ingest.ingest) without the OCR of scanned pages: pages that need it stay in
        `needs_ocr`. add=True keeps the chunks in memory, as Engine.ingest_file puts them in the index."""
        from rag import ingest as I
        res = I.ingest(file, course_id, unit_id, owner, visibility, source, class_id=class_id,
                       language=kw.get("language"))
        if add and res.chunks:
            await self.add_material(res.chunks)
        return res

    async def add_material(self, chunks: list) -> int:
        ids = {c.chunk_id for c in chunks}
        self.materials = [c for c in self.materials if c.chunk_id not in ids] + list(chunks)
        return len(chunks)

    async def remove_material(self, chunk_ids=(), *, source: str | None = None, course_id: str | None = None,
                              owner: str | None = None) -> int:
        def gone(c) -> bool:
            return c.chunk_id in set(chunk_ids) or (source is not None and c.source == source
                                                    and (course_id is None or c.course_id == course_id)
                                                    and (owner is None or c.owner == owner))
        keep = [c for c in self.materials if not gone(c)]
        n, self.materials = len(self.materials) - len(keep), keep
        return n

    # ---------------------------------------------------------------- public
    async def run(self, task: str, inputs: Mapping[str, Any] | None = None, context: Mapping[str, Any] | None = None,
                  priority: Priority | str = Priority.INTERACTIVE,
                  on_event: Callable[[Event], None] | None = None) -> Result:
        inputs, context = dict(inputs or {}), dict(context or {})
        if task not in self.OUTCOMES:
            raise ValueError(f"unknown or not yet implemented task {task!r}; have {sorted(self.OUTCOMES)}")
        prio = Priority(priority)
        missing = [k for k in _REQUIRED.get(task, ()) if not str(inputs.get(k) or "").strip()
                   and not (k == "steps" and inputs.get(k))]
        if missing:
            raise ValueError(f"task {task!r} needs inputs {missing}")
        outcome = self._outcome(task, context)
        room = str(context["room_id"]) if context.get("room_id") is not None else None
        if room is not None and room in self._rooms:
            outcome = "room_busy"
        elif room is not None:
            self._rooms.add(room)
        try:
            plan = self._plan(task, outcome, inputs, context, prio)
            self.calls.append((task, outcome))
            return await self._play(task, plan, on_event)
        finally:
            if room is not None and outcome != "room_busy":
                self._rooms.discard(room)

    async def generate_page(self, plan: Mapping[str, Any], focus_point: Mapping[str, Any],
                            context: Mapping[str, Any] | None = None, on_event=None) -> dict:
        """Same contract as Engine.generate_page: one prep page (explanation, worked example, 3 quiz items)."""
        context = dict(context or {})
        explain = await self.run("explain", {"question": f"{focus_point['concept_id']}"}, context,
                                 Priority.BACKGROUND, on_event)
        return _page(focus_point, explain)

    # ---------------------------------------------------------------- internals
    def _outcome(self, task: str, context: Mapping) -> str:
        forced = context.get("fake_outcome")
        if forced:
            if forced not in self.OUTCOMES[task] and forced not in self.GENERIC:
                raise ValueError(f"fake outcome {forced!r} unknown for {task}; have {self.OUTCOMES[task]}")
            return forced
        if self.mode == "degraded":
            return self.DEGRADED_DEFAULT[task]
        if self.mode == "slow" and task == "explain":
            return "deadline"
        return self.OUTCOMES[task][0]

    async def _play(self, task: str, plan: _Plan, on_event) -> Result:
        events, t_prev = [], 0.0
        for t, name, data in plan.events:
            if self.speed > 0 and t > t_prev:
                await self._sleep((t - t_prev) * self.speed)
            t_prev = max(t_prev, t)
            ev = Event(name=name, t_ms=round(t * 1000, 1), data=data)
            events.append(ev)
            if on_event:
                on_event(ev)
        if self.speed > 0 and plan.end_s > t_prev:
            await self._sleep((plan.end_s - t_prev) * self.speed)
        f = copy.deepcopy(plan.fields)
        return Result(task=task, request_id=uuid.uuid4().hex[:12], events=events,
                      latency_ms=round(plan.end_s * 1000, 1), **f)

    def _scale(self) -> float:
        return SLOW_FACTOR if self.mode == "slow" else 1.0

    def _plan(self, task: str, outcome: str, inputs: dict, context: dict, prio: Priority) -> _Plan:
        k = self._scale()
        lang = str(context.get("language") or "FRENCH").upper()
        if outcome in self.GENERIC:
            return _generic(task, outcome, k)
        return _PLANS[task](outcome, inputs, lang, k, context)


class _FakeCache:
    """engine.cache.invalidate(request_id): the same call, remembered for UI tests."""

    def __init__(self):
        self.invalidated: list[str] = []

    def invalidate(self, request_id: str) -> int:
        self.invalidated.append(request_id)
        return 1


_REQUIRED = {"classify": ("message",), "hint": ("problem",), "explain": ("question",), "grade": ("problem", "solution", "attempt"),
             "compress": ("question", "chunks"), "ocr_transcribe": ("image",), "check_claim": ("claim",),
             "check_answer": ("problem", "steps"), "prep_plan": ("scope", "deadline", "pace_min_per_day")}


# ---------------------------------------------------------------------------- generic degraded outcomes
def _generic(task: str, outcome: str, k: float) -> _Plan:
    if outcome == "quota_exhausted":
        eta = 60.0                    # real: the 429's retry-after is the whole delay (interactive waits at most 30 s)
        return _Plan(_ev((0.0, "checking_cache", None), (0.01, "queued", {"position": 0, "eta_s": 2.0}),
                         (0.02, "generating", {"attempt": 1, "model_class": "compact"}),
                         (0.4, "gave_up", {"error": "quota_exhausted", "retry_after_s": 60.0}),
                         (0.4, "queued", {"reason": "quota_exhausted", "eta_s": eta, "error": "quota_exhausted"})),
                     0.4, {"degraded": True, "degrade_reason": "quota_exhausted", "eta_seconds": eta,
                           "route_reason": "quota window full", **_empty_body(task)})
    if outcome == "superseded":
        return _Plan(_ev((0.0, "checking_cache", None), (0.01, "queued", {"position": 1, "eta_s": 6.0}),
                         (2.1, "gave_up", {"error": "superseded", "retry_after_s": None}),
                         (2.1, "unavailable", {"reason": "superseded", "eta_s": None, "error": "superseded"})),
                     2.1, {"degraded": True, "degrade_reason": "superseded", **_empty_body(task)})
    return _Plan(_ev((0.0, "room_busy", {"room_id": "room-1", "eta_s": 8.0})), 0.0,
                 {"degraded": True, "degrade_reason": "room_busy", "eta_seconds": 8.0, "route_reason": "room_busy",
                  "notes": {"room_id": "room-1"}})


def _empty_body(task: str) -> dict:
    """What the engine returns when nothing could be produced: the task's safe default, or nothing."""
    if task == "classify":
        return {"data": {"intent": "EXPLAIN", "language": "FRENCH", "confusion": "MEDIUM", "complexity": 3,
                         "confidence": 0.3}, "safe_default": True}
    if task == "grade":
        return {"data": {"verdict": "UNKNOWN", "mistake": None, "feedback": "", "graded_by": "llm"}, "safe_default": True}
    if task == "check_claim":
        return {"data": {"verdict": "PARTLY", "correction": "", "source_chunk_id": None, "checked_by": "llm"},
                "safe_default": True}
    if task == "hint":
        return {"text": "Relis l'énoncé, puis dis-moi ce que tu as déjà essayé."}
    if task == "explain":
        return {"text": "Je ne peux pas répondre pour le moment, réessaie dans un instant."}
    return {}


# ---------------------------------------------------------------------------- per-task plans
def _classify(outcome, inputs, lang, k, ctx) -> _Plan:
    data = {"intent": "EXPLAIN", "language": lang, "confusion": "MEDIUM", "complexity": 2, "confidence": 0.9}
    if outcome == "cache_hit":
        return _Plan(_ev((0.0, "checking_cache", None), (0.005, "cache_hit", None)), 0.005,
                     {"data": data, "model_class_used": "compact", "model": "compact-a", "from_cache": True,
                      "route_reason": "compact by table"})
    if outcome == "safe_default":
        return _Plan(_ev((0.0, "checking_cache", None), (0.01, "queued", {"position": 0, "eta_s": 2.0}),
                         (0.02, "generating", {"attempt": 1, "model_class": "compact"}),
                         (3.4 * k, "repairing_json", {"error": "invalid JSON: Expecting value"}),
                         (3.4 * k + 0.01, "queued", {"position": 0, "eta_s": 2.0}),
                         (3.4 * k + 0.02, "generating", {"attempt": 1, "model_class": "compact"}),
                         (6.5 * k, "using_safe_default", {"error": "invalid JSON: Expecting value"})), 6.5 * k,
                     {"data": {**data, "confidence": 0.3}, "model_class_used": "compact", "model": "compact-a",
                      "repaired": True, "safe_default": True,
                      "tokens_in": 310, "tokens_out": 95, "route_reason": "compact by table"})
    tin, tout = _tokens("classify", inputs["message"])
    return _Plan(_ev((0.0, "checking_cache", None), (0.01, "queued", {"position": 0, "eta_s": 2.0}),
                     (0.02, "generating", {"attempt": 1, "model_class": "compact"})), 2.6 * k,
                 {"data": data, "model_class_used": "compact", "model": "compact-a", "tokens_in": tin,
                  "tokens_out": tout, "route_reason": "compact by table"})


def _hint(outcome, inputs, lang, k, ctx) -> _Plan:
    if outcome == "stored":
        return _Plan(_ev((0.0, "answered_by_rules", None)), 0.004,
                     {"text": _hint_text(lang), "notes": {"source": "bank", "exercise_id": "d01", "hint_level": 1}})
    return _Plan(_ev((0.0, "checking_cache", None), (0.01, "queued", {"position": 0, "eta_s": 2.0}),
                     (0.02, "generating", {"attempt": 1, "model_class": "compact"})), 3.8 * k,
                 {"text": _hint_text(lang), "model_class_used": "compact", "model": "compact-a",
                  "tokens_in": 260, "tokens_out": 38, "route_reason": "compact by table"})


def _explain_body(lang: str) -> dict:
    return {"text": f"{OPENER}\n\n{EXPLAIN_BODY}\n\nÀ toi : {EXERCISE['statement']}",
            "data": {"body": EXPLAIN_BODY, "opener": OPENER, "exercise": EXERCISE}}


def _explain(outcome, inputs, lang, k, ctx) -> _Plan:
    retr = [(0.0, "retrieving", None), (0.04, "expanding_query", {"glossary": ["nombre dérivé"]}),
            (0.05, "checking_cache", None)]
    if outcome == "not_in_course":
        return _Plan(_ev((0.0, "retrieving", None), (0.05, "no_relevant_context", None)), 0.05,
                     {"text": "Ce sujet ne figure pas dans le cours : je préfère ne pas inventer de réponse.",
                      "route_reason": "mid-size by table", "notes": {"retrieval": "no_relevant_chunks"}})
    if outcome == "cache_hit":
        return _Plan(_ev(*retr, (0.06, "cache_hit", None)), 0.06,
                     {**_explain_body(lang), "from_cache": True, "model_class_used": "midsize", "model": "mid-a",
                      "route_reason": "mid-size by table", "notes": {"sources": [EXCERPTS[0]["chunk_id"]]}})
    partial = (0.06, "partial_result", {"excerpts": EXCERPTS[:2]})
    gen = [(0.07, "queued", {"position": 0, "eta_s": 6.0}), (0.08, "generating", {"attempt": 1, "model_class": "midsize"})]
    if outcome == "ok":
        t = 9.5 * k
        return _Plan(_ev(*retr, partial, *gen), t,
                     {**_explain_body(lang), "model_class_used": "midsize", "model": "mid-a", "tokens_in": 1150,
                      "tokens_out": 214, "route_reason": "mid-size by table",
                      "notes": {"sources": [EXCERPTS[0]["chunk_id"]], "formulas_verified": 1}})
    fast_data = {"framing": FRAMING, "excerpts": EXCERPTS, "exercise": EXERCISE, "opener": OPENER}
    fast_text = FRAMING + "\n\n" + "\n".join(f"- {e['text']}" for e in EXCERPTS)
    base = {"text": fast_text, "data": fast_data, "degraded": True, "mode": "fast_extractive",
            "followup_available": True, "model_class_used": "compact", "model": "compact-a",
            "notes": {"sources": [EXCERPTS[0]["chunk_id"], EXCERPTS[2]["chunk_id"]]}}
    if outcome == "deadline":
        t = INTERACTIVE_DEADLINE_S
        return _Plan(_ev(*retr, partial, *gen,
                         (t, "deadline", {"deadline_s": INTERACTIVE_DEADLINE_S, "generation_continues": True}),
                         (t, "fast_extractive", {"reason": "deadline"})), t,
                     {**base, "degrade_reason": "deadline", "route_reason": "mid-size by table; interactive deadline"})
    # fast_extractive: mid-size saturated
    return _Plan(_ev(*retr, partial, *gen, (4.2 * k, "falling_back_to_compact", {"reason": "midsize_saturated"}),
                     (4.2 * k, "fast_extractive", {"reason": "midsize_saturated"})), 4.2 * k + 0.01,
                 {**base, "degrade_reason": "midsize_saturated", "tokens_in": 40, "tokens_out": 20,
                  "route_reason": "fallback to compact: mid-size saturated"})


def _quiz(outcome, inputs, lang, k, ctx) -> _Plan:
    ex = [EXERCISE] if outcome == "ok" else [
        {"id": "d01", "concept": "puissance", "level": 1, "statement": "1) Dérive f(x) = x⁴.", "expression": "x⁴"},
        {"id": "d05", "concept": "produit", "level": 2, "statement": "2) Dérive f(x) = x²·(x+1).", "expression": "x²(x+1)"},
        {"id": "d09", "concept": "composee", "level": 3, "statement": "3) Dérive f(x) = (2x+1)³.", "expression": "(2x+1)³"}]
    intro = "Voici un exercice pour t'entraîner :" if outcome == "ok" else "Trois exercices pour situer ton niveau :"
    body = "\n".join(e["statement"] for e in ex)
    return _Plan(_ev((0.0, "picking_exercise", None)), 0.01,
                 {"text": f"{intro}\n\n{body}", "mode": "bank", "notes": {"source": "bank"},
                  "data": {"exercises": ex, "requested_concepts": [], "diagnostic": outcome == "diagnostic",
                           "intro": intro}})


def _grade(outcome, inputs, lang, k, ctx) -> _Plan:
    if outcome == "correct":
        return _Plan(_ev((0.0, "answered_by_rules", None)), 0.01,
                     {"data": {"verdict": "CORRECT", "mistake": None, "feedback": "", "graded_by": "sympy"},
                      "notes": {"source": "sympy"}})
    return _Plan(_ev((0.0, "checking_cache", None), (0.01, "queued", {"position": 0, "eta_s": 2.0}),
                     (0.02, "generating", {"attempt": 1, "model_class": "compact"})), 3.2 * k,
                 {"data": {"verdict": "INCORRECT", "mistake": "x³",
                           "feedback": "Tu as baissé l'exposant mais oublié de multiplier par l'exposant.",
                           "graded_by": "llm"},
                  "model_class_used": "compact", "model": "compact-a", "tokens_in": 280, "tokens_out": 45})


def _compress(outcome, inputs, lang, k, ctx) -> _Plan:
    return _Plan(_ev((0.0, "checking_cache", None), (0.01, "queued", {"position": 0, "eta_s": 2.0}),
                     (0.02, "generating", {"attempt": 1, "model_class": "compact"})), 2.2 * k,
                 {"data": {"method": "llm", "sentences": [dict(e) for e in EXCERPTS[:2]]},
                  "model_class_used": "compact", "model": "compact-a", "tokens_in": 700, "tokens_out": 18})


def _ocr(outcome, inputs, lang, k, ctx) -> _Plan:
    if outcome == "invalid_image":
        return _Plan(_ev((0.0, "unavailable", {"reason": "invalid_image", "eta_s": None, "error": "image"})), 0.0,
                     {"degraded": True, "degrade_reason": "invalid_image", "route_reason": "invalid image",
                      "notes": {"error": "not a readable image (UnidentifiedImageError)"}})
    math = outcome in ("ok", "math")
    data = {"text": OCR_TEXT, "latex": OCR_LATEX if math else None,
            "confidence_note": "" if math else "Écriture peu lisible à partir de la 2e ligne : à vérifier par l'élève."}
    if not math:
        data["text"] = "Dériver f(x) = 3x² + 5x\nf'(x) = 6x ? 5"
    return _Plan(_ev((0.0, "resizing_image", {"from": "2048x1536", "to": "1154x866"}), (0.15, "checking_cache", None),
                     (0.2, "queued", {"position": 0, "eta_s": 4.0}),
                     (0.21, "generating", {"attempt": 1, "model_class": "vision"})), 14.0 * k,
                 {"text": data["text"], "data": data, "model_class_used": "vision", "model": "deepseek-ocr",
                  "tokens_in": 820, "tokens_out": 64, "route_reason": "vision by table",
                  "degraded": outcome == "low_confidence", "degrade_reason": "low_confidence" if outcome == "low_confidence" else None,
                  "notes": {"image": {"sha": "c0ffee123456", "size": "1154x866", "resized": True, "est_tokens": 1000}}})


def _claim(outcome, inputs, lang, k, ctx) -> _Plan:
    cid = "derivee-formules.fr#produit-et-quotient"
    if outcome in ("SUPPORTED", "CONTRADICTED"):          # settled by sympy: no retrieval, no model
        sup = outcome == "SUPPORTED"
        return _Plan(_ev((0.0, "answered_by_rules", None)), 0.03,
                     {"data": {"verdict": outcome, "correction": "" if sup else "Plutôt : (x³)' = 3x²",
                               "source_chunk_id": None, "checked_by": "sympy"}, "notes": {"source": "rules"}})
    if outcome == "NOT_IN_COURSE":
        return _Plan(_ev((0.0, "retrieving", None), (0.05, "no_relevant_context", None)), 0.05,
                     {"data": {"verdict": "NOT_IN_COURSE", "correction": "", "source_chunk_id": None,
                               "checked_by": "rules"}, "notes": {"retrieval": "no_relevant_chunks"}})
    base = [(0.0, "retrieving", None), (0.04, "expanding_query", {"glossary": ["dérivée", "produit"]}),
            (0.05, "checking_cache", None), (0.06, "queued", {"position": 0, "eta_s": 2.0}),
            (0.07, "generating", {"attempt": 1, "model_class": "compact"})]
    if outcome == "llm_failed":
        return _Plan(_ev(*base, (3.0 * k, "repairing_json", {"error": "invalid JSON: Expecting value"}),
                         (3.0 * k + 0.01, "queued", {"position": 0, "eta_s": 2.0}),
                         (3.0 * k + 0.02, "generating", {"attempt": 1, "model_class": "compact"}),
                         (6.0 * k, "using_safe_default", {"error": "invalid JSON: Expecting value"})), 6.0 * k,
                     {"data": {"verdict": "PARTLY", "correction": "", "source_chunk_id": cid, "checked_by": "llm"},
                      "model_class_used": "compact", "model": "compact-a", "safe_default": True, "degraded": True,
                      "degrade_reason": "json_invalid", "repaired": True, "tokens_in": 640, "tokens_out": 60})
    verdict = outcome.removesuffix("_llm") if outcome != "PARTLY" else "PARTLY"
    corr = "" if verdict == "SUPPORTED" else "La règle du produit est (u·v)' = u'·v + u·v', pas u'·v'."
    return _Plan(_ev(*base), 3.1 * k,
                 {"data": {"verdict": verdict, "correction": corr, "source_chunk_id": cid, "checked_by": "llm"},
                  "model_class_used": "compact", "model": "compact-a", "tokens_in": 420, "tokens_out": 52,
                  "route_reason": "table: check_claim -> compact"})


def _answer(outcome, inputs, lang, k, ctx) -> _Plan:
    steps_in = list(inputs.get("steps") or [])
    n = max(1, len(steps_in))
    verdicts, notes = ["OK"] * n, [""] * n
    first_wrong, final, feedback, by, mode = None, "CORRECT", "", "sympy", "rules"
    if outcome in ("wrong_step", "rule", "llm_feedback"):
        i = min(1, n - 1)
        verdicts[i], first_wrong, final = "WRONG", i, "INCORRECT"
        notes[i] = "Cette ligne ne correspond pas à la précédente ni à la dérivée."
        feedback, by = {"wrong_step": ("Tu as baissé l'exposant mais oublié de multiplier par l'exposant.", "rules"),
                        "rule": ("Tu as multiplié par l'exposant mais tu n'as pas baissé l'exposant de 1.", "rules"),
                        "llm_feedback": ("Que devient l'exposant de x quand on dérive ?", "llm")}[outcome]
        mode = None if by == "llm" else "rules"
    elif outcome == "unchecked":
        verdicts = ["UNCHECKED"] * n
        notes = ["Ligne non reconnue : je ne peux pas la vérifier."] * n
        final = "UNKNOWN"
    data = {"steps": [{"index": i, "verdict": v, "note": notes[i]} for i, v in enumerate(verdicts)],
            "first_wrong": first_wrong, "final_verdict": final, "feedback": feedback, "graded_by": by}
    ev = [(0.0, "parsing_steps", {"steps": n}), (0.03, "checking_steps", None)]
    if outcome == "llm_feedback":
        ev += [(0.04, "queued", {"position": 0, "eta_s": 2.0}), (0.05, "generating", {"attempt": 1, "model_class": "compact"})]
        return _Plan(_ev(*ev), 3.5 * k, {"data": data, "model_class_used": "compact", "model": "compact-a",
                                          "tokens_in": 300, "tokens_out": 30, "route_reason": "table: check_answer -> compact"})
    return _Plan(_ev(*ev), 0.06, {"data": data, "mode": mode, "route_reason": "table: check_answer -> compact"})


def _prep(outcome, inputs, lang, k, ctx) -> _Plan:
    """The real planner (engine.prep) over the real course graph, so a UI gets plans that follow its inputs; a canned
    plan only if the course files are not there."""
    mastered = outcome == "all_mastered"
    try:
        from engine import prep
        from engine.course import load_courses
        from engine.exercises import ExerciseBank
        courses = load_courses("data/courses")
        course = courses.get(ctx.get("course_id")) or next(iter(courses.values()))
        scope = inputs.get("scope") or {"unit_ids": [u.id for u in course.units]}
        mastery = {c: 0.9 for c in course.concepts} if mastered else dict(inputs.get("mastery") or {})
        bank = ExerciseBank.load("data/exercises/derivees.yaml")
        data, notes = prep.build_plan(course, scope, inputs.get("deadline") or "2099-01-01",
                                      inputs.get("pace_min_per_day") or 30, mastery, lang, bank, inputs.get("start"))
    except Exception:
        pts = [{"concept_id": c, "day_index": i // 2, "reason": "Maîtrise faible (0%)", "reason_code": "low_mastery",
                "page_kind": "review" if mastered else "learn", "minutes": 20} for i, c in enumerate(CONCEPTS[:5])]
        data, notes = {"focus_points": pts, "assessment_exercise_ids": ["d01", "d05", "d09"]}, {"source": "canned"}
    return _Plan(_ev((0.0, "answered_by_rules", None)), 0.02, {"data": data, "mode": "rules", "notes": {"source": "rules", **notes}})


def _page(fp: Mapping, explain: Result) -> dict:
    from engine.exercises import ExerciseBank
    return {"concept_id": fp["concept_id"], "page_kind": fp.get("page_kind", "learn"), "title": fp["concept_id"],
            "language": "FRENCH", "explanation": explain.text, "explanation_sections": explain.data,
            "explain_request_id": explain.request_id, "from_cache": explain.from_cache, "degraded": explain.degraded,
            "degrade_reason": explain.degrade_reason, "eta_seconds": explain.eta_seconds,
            "worked_example": {"source": "bank", "exercise_id": "d01", "statement": "Dérive f(x) = x⁴.",
                               "steps": ["Quelle règle donne la dérivée de xⁿ ?", "(xⁿ)' = n·xⁿ⁻¹ : ici, que vaut n ?",
                                         "Multiplie par l'exposant, puis baisse l'exposant de 1."],
                               "solution": "f'(x) = 4x³"},
            "quiz": [{"id": i, "concept": fp["concept_id"], "level": 1, "statement": f"Dérive l'exemple {i}.",
                      "expression": "x²", "source": "bank"} for i in ("d02", "d03", "d04")]}


_PLANS = {"classify": _classify, "hint": _hint, "explain": _explain, "quiz": _quiz, "grade": _grade,
          "compress": _compress, "ocr_transcribe": _ocr, "check_claim": _claim, "check_answer": _answer,
          "prep_plan": _prep}
