from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Event(BaseModel):
    name: str                         # checking_cache | cache_hit | queued | generating | falling_back_to_compact ...
    t_ms: float                       # since engine.run started
    data: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    task: str
    request_id: str
    text: str | None = None           # text tasks (hint, explain ...)
    data: dict[str, Any] | None = None    # JSON tasks (classify, grade ...), already validated
    model_class_used: str | None = None   # None = answered by rules
    model: str | None = None
    from_cache: bool = False
    degraded: bool = False
    degrade_reason: str | None = None     # midsize_saturated | quota_low | service_busy | quota_exhausted | ...
    eta_seconds: float | None = None      # set when the service is busy: when trying again makes sense
    events: list[Event] = Field(default_factory=list)
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    route_reason: str | None = None
    repaired: bool = False            # JSON needed the repair retry
    safe_default: bool = False        # JSON could not be obtained: data is the safe default
    mode: str | None = None           # "fast_extractive": course excerpts verbatim, no generated explanation
    followup_available: bool = False  # a full explanation can be asked again later (e.g. mid-size back)
    notes: dict[str, Any] = Field(default_factory=dict)

    @property
    def event_names(self) -> list[str]:
        return [e.name for e in self.events]
