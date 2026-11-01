"""
The routing policy: every compact-vs-mid-size decision lives here, with its reason.

  | task      | default  | escalation / fallback                                          |
  |-----------|----------|----------------------------------------------------------------|
  | classify  | compact  | none                                                           |
  | hint      | compact  | none                                                           |
  | grade     | compact  | none                                                           |
  | compress  | compact  | none                                                           |
  | summary   | compact  | none                                                           |
  | quiz      | compact  | none (exercise from the bank; compact writes a cached intro)   |
  | ocr_      | vision   | none (compact cannot read an image); shares the chat window    |
  | transcribe|          |                                                                |
  | check_    | compact  | none (sympy and rules decide first)                            |
  | claim     |          |                                                                |
  | check_    | compact  | none (rules first, one call only for the guiding question)     |
  | answer    |          |                                                                |
  | prep_plan | none     | no model call                                                  |
  | explain   | mid-size | -> compact if mid-size saturated, or if quota is low           |
  |           |          |    (unless escalated: escalate=True or complexity >= 4),       |
  |           |          |    or straight to compact while the mid-size breaker is open   |

Fallback is never silent: the decision carries degraded=True and a reason, and every decision
(initial route and runtime fallback) is written to metrics as a kind="route" record; breaker
state changes as kind="breaker" records.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Mapping

import time

from config import ModelClass, Settings
from gatekeeper.errors import LLMError, ModelSaturated, TransientError
from metrics import CallRecord, MetricsSink
from router.breaker import HALF_OPEN, CircuitBreaker

log = logging.getLogger("ednova.router")

C, M, V = ModelClass.COMPACT, ModelClass.MIDSIZE, ModelClass.VISION
ESCALATION_COMPLEXITY = 4


@dataclass(frozen=True)
class Route:
    default: ModelClass
    fallback: ModelClass | None = None


ROUTES: dict[str, Route] = {
    "classify": Route(C),
    "hint": Route(C),
    "grade": Route(C),
    "compress": Route(C),
    "summary": Route(C),
    "quiz": Route(C),          # exercise from the bank; compact only writes the optional intro line
    "explain": Route(M, fallback=C),
    "ocr_transcribe": Route(V),      # reads the image; no fallback (compact cannot see), no breaker
    "check_claim": Route(C),         # sympy / rules first; one compact JSON call when they do not decide
    "check_answer": Route(C),        # rules first; one compact call writes a guiding question when needed
    "prep_plan": Route(C),           # no model call at all (a rules task); the row exists for the registry
}


# Which task follows a classified learner message. "diagnostic" = quiz with diagnostic=True (level test).
INTENT_TASKS = {"EXPLAIN": "explain", "HINT": "hint", "ANSWER_ATTEMPT": "hint", "QUIZ": "quiz",
                "DIAGNOSTIC": "diagnostic"}
CONFUSED_TO_EXPLAIN = {"DIAGNOSTIC", "QUIZ"}     # a learner who is lost is taught first, not tested


@dataclass(frozen=True)
class FollowUp:
    task: str | None              # None = no tutoring follow-up (chitchat, off-topic)
    reason: str
    overridden: bool = False


@dataclass(frozen=True)
class Decision:
    task: str
    preferred: ModelClass           # what the table wants (also the cache-key class)
    model_class: ModelClass         # what we will actually call
    fallback: ModelClass | None
    reason: str
    escalated: bool = False
    degraded: bool = False
    degrade_reason: str | None = None
    probe: bool = False             # this request is the half-open breaker's probe


def escalated(context: Mapping[str, Any]) -> bool:
    c = context.get("complexity")
    return bool(context.get("escalate")) or (isinstance(c, (int, float)) and c >= ESCALATION_COMPLEXITY)


class Router:
    def __init__(self, sink: MetricsSink, quota_low_threshold: int, routes: Mapping[str, Route] = ROUTES,
                 breaker_threshold: int = 3, breaker_window_s: float = 60.0, breaker_cooldown_s: float = 30.0,
                 clock=time.monotonic, breaker_timeout_threshold: int = 1):
        self.sink, self.quota_low_threshold, self.routes = sink, quota_low_threshold, routes
        self.cooldown_s = breaker_cooldown_s
        # one breaker per class that some task can fall back FROM (today: mid-size)
        self.breakers: dict[ModelClass, CircuitBreaker] = {
            cls: CircuitBreaker(breaker_threshold, breaker_window_s, breaker_cooldown_s, clock,
                                on_change=lambda old, new, why, cls=cls: self._breaker_changed(cls, old, new, why),
                                timeout_threshold=breaker_timeout_threshold)
            for cls in {r.default for r in routes.values() if r.fallback is not None}}

    @classmethod
    def from_settings(cls, sink: MetricsSink, s: Settings, clock=time.monotonic) -> "Router":
        return cls(sink, s.quota_low_threshold, ROUTES, s.breaker_threshold, s.breaker_window_s,
                   s.breaker_cooldown_s, clock, s.breaker_timeout_threshold)

    def route_for(self, task: str) -> Route:
        if task not in self.routes:
            raise ValueError(f"no route for task {task!r}")
        return self.routes[task]

    def decide(self, task: str, context: Mapping[str, Any], quota_tokens: float | None, ctx=None) -> Decision:
        r = self.route_for(task)
        esc = escalated(context)
        d = Decision(task, r.default, r.default, r.fallback, f"table: {task} -> {r.default.value}", esc)
        if r.default is M and r.fallback is not None and quota_tokens is not None \
                and quota_tokens < self.quota_low_threshold:
            if esc:
                d = replace(d, reason=d.reason + f"; quota low ({quota_tokens:.1f} < {self.quota_low_threshold}) "
                                                 "but escalated -> keep mid-size")
            else:
                d = replace(d, model_class=r.fallback, degraded=True, degrade_reason="quota_low",
                            reason=d.reason + f"; quota low ({quota_tokens:.1f} < {self.quota_low_threshold})"
                                              f" -> {r.fallback.value}")
        br = self.breakers.get(d.model_class)
        if br is not None and r.fallback is not None:
            if not br.closed:       # escalation cannot help a class that is down; a learner is never the probe
                wait = br.retry_in()
                d = replace(d, model_class=r.fallback, degraded=True, degrade_reason="breaker_open",
                            reason=d.reason + f"; {d.model_class.value} breaker {br.state} (probe in {wait:.0f}s)"
                                              f" -> {r.fallback.value}")
                if ctx is not None:
                    ctx.emit("breaker_open", model_class=d.preferred.value, retry_in_s=round(wait, 1))
        self._log(d, ctx)
        return d

    def follow_up(self, classification: Mapping[str, Any], ctx=None) -> FollowUp:
        """intent -> task, with the confusion override: HIGH confusion + DIAGNOSTIC/QUIZ -> explain.
        Logged as a kind="route" record with its reason, like every other routing decision."""
        intent = str(classification.get("intent") or "").upper()
        confusion = str(classification.get("confusion") or "").upper()
        task = INTENT_TASKS.get(intent)
        f = FollowUp(task, f"intent {intent} -> {task or 'no follow-up'}")
        if confusion == "HIGH" and intent in CONFUSED_TO_EXPLAIN:
            f = FollowUp("explain", f"intent {intent} with confusion HIGH -> explain (teach before testing)", True)
        log.info("follow-up: %s", f.reason)
        self.sink.write(CallRecord(
            request_id=getattr(ctx, "request_id", None) or "-", turn_id=getattr(ctx, "turn_id", None),
            kind="route", task="follow_up", outcome="overridden" if f.overridden else "routed", detail=f.reason))
        return f

    def observe(self, cls: ModelClass, err: LLMError | None) -> None:
        """Called by the retry layer after every chat attempt."""
        br = self.breakers.get(cls)
        if br is None:
            return
        if err is None:
            br.record_success()
        elif isinstance(err, TransientError) and err.timeout:
            br.record_failure("timeout")        # one is enough: it cost a whole timeout period
        elif isinstance(err, (ModelSaturated, TransientError)):
            br.record_failure("saturation")     # saturation, 5xx, network: BREAKER_THRESHOLD within the window

    def probe_due(self, can_probe=lambda cls: True) -> list[ModelClass]:
        """Classes whose breaker is not closed and grants a probe now (open past its cooldown, or half-open with
        no probe out). The engine sends one tiny BACKGROUND request per class; the breaker closes or re-opens
        on its outcome. can_probe(cls) is checked first so a refused probe does not use up the grant."""
        return [cls for cls, br in self.breakers.items() if not br.closed and can_probe(cls) and br.allow()]

    def unavailable_for(self, cls: ModelClass) -> float | None:
        """None while the class is usable; else the seconds until it is probed again (never 0: a half-open class
        is still unavailable to learners). Wired to the retry layer so queued requests of a class that goes down
        are failed at once."""
        br = self.breakers.get(cls)
        if br is None or br.closed:
            return None
        return br.retry_in() or self.cooldown_s

    def retry_in(self, cls: ModelClass) -> float:
        """Seconds until the class is probed again (0 if its breaker is closed or already half-open)."""
        br = self.breakers.get(cls)
        return br.retry_in() if br is not None else 0.0
    def release_probe(self, d: Decision) -> None:
        if d.probe and (br := self.breakers.get(d.preferred)):
            br.release_probe()

    def _breaker_changed(self, cls: ModelClass, old: str, new: str, why: str) -> None:
        log.warning("breaker %s: %s -> %s (%s)", cls.value, old, new, why)
        self.sink.write(CallRecord(request_id="-", kind="breaker", model_class=cls.value, outcome=new,
                                   detail=f"{old} -> {new}: {why}"))

    def on_saturated(self, d: Decision, ctx=None) -> Decision | None:
        """Runtime fallback after the retry layer gave up on a saturated class. None = no fallback."""
        return self._runtime_fallback(d, "saturated", "saturated after retries", ctx)

    def on_unavailable(self, d: Decision, ctx=None) -> Decision | None:
        """Runtime fallback after the class timed out (no answer; no retry was spent) or its breaker opened while
        the request waited. Reason `<class>_unavailable`. None = no fallback."""
        return self._runtime_fallback(d, "unavailable", "timed out / unavailable", ctx)

    def _runtime_fallback(self, d: Decision, what: str, why: str, ctx) -> Decision | None:
        if d.fallback is None or d.model_class is d.fallback:
            self._log(replace(d, reason=f"{d.model_class.value} {what}; no fallback for {d.task}"), ctx)
            return None
        fb = replace(d, model_class=d.fallback, degraded=True, degrade_reason=f"{d.model_class.value}_{what}",
                     reason=f"{d.model_class.value} {why} -> fallback {d.fallback.value}")
        self._log(fb, ctx)
        return fb

    def _log(self, d: Decision, ctx) -> None:
        log.info("route %s: %s", d.task, d.reason)
        self.sink.write(CallRecord(
            request_id=getattr(ctx, "request_id", "-"), turn_id=getattr(ctx, "turn_id", None), kind="route",
            task=d.task,
            priority=getattr(ctx, "priority", None), model_class=d.model_class.value, outcome="routed",
            degraded=d.degraded, degrade_reason=d.degrade_reason, detail=d.reason))
