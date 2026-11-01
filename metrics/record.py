"""
One structured record per provider attempt (and, later, per cache hit).
This is the source of truth for what the judges track: calls per model, compact/mid split,
tokens in/out, repeated identical requests (request_hash), refusals and how they were handled.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Literal

from pydantic import BaseModel, Field


# Identity of the caller of the current engine.run (ids only, never names). engine.run sets it; every record
# created under that call (also in tasks it spawns: asyncio copies the context) picks it up as a default, so no
# call site has to pass it. Ids are not part of any cache key.
IDENTITY_KEYS = ("learner_id", "room_id", "course_id", "class_id")
identity: ContextVar[dict] = ContextVar("ednova_identity", default={})


def _id(key: str):
    return lambda: identity.get().get(key)


class CallRecord(BaseModel):
    ts: float = Field(default_factory=time.time)
    request_id: str                       # one logical engine call; shared by its retries
    turn_id: str | None = None            # one learner turn (context["turn_id"]), for requests-per-turn
    learner_id: str | None = Field(default_factory=_id("learner_id"))
    room_id: str | None = Field(default_factory=_id("room_id"))
    course_id: str | None = Field(default_factory=_id("course_id"))
    class_id: str | None = Field(default_factory=_id("class_id"))
    attempt: int = 1
    kind: Literal["chat", "embed", "route", "run", "breaker"] = "chat"
    # route = router decision, run = one engine.run, breaker = circuit-breaker state change
    task: str | None = None               # classify | hint | explain | grade | compress ...
    priority: str | None = None
    purpose: str | None = None            # "repair" for the JSON repair retry
    model_class: str | None = None
    model: str | None = None
    request_hash: str | None = None       # same hash twice = repeated identical request
    status: int | None = None             # HTTP status; None for network errors / cache hits
    outcome: str = "ok"                   # attempts: ok | quota_exhausted | saturated | ceiling | ...
                                          # cache hit: cache_hit | negative_cache_hit; run: ok | repaired | safe_default | degraded
    action: str | None = None             # what we did about it: wait_stated_delay | backoff | fix_request | raise
    wait_ms: float = 0.0                  # time spent waiting on limiter / retry delay before this attempt
    latency_ms: float = 0.0
    tokens_in: int | None = None
    tokens_out: int | None = None
    from_cache: bool = False
    from_negative_cache: bool = False     # served in fast mode because the same key failed a check recently
    degraded: bool = False
    degrade_reason: str | None = None
    mode: str | None = None               # run: "fast_extractive" when explain was served by extraction
    graded_by: str | None = None          # run (grade): "sympy" (deterministic) | "llm"
    truncated_output: bool = False        # completion_tokens >= the output cap (finish_reason is unreliable on FelCloud)
    quota_remaining: int | None = None
    eta_s: float | None = None            # degraded "service_busy": when to try again
    detail: str | None = None             # short provider message, never secrets
