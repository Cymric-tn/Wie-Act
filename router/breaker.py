"""
Circuit breaker for one model class (mid-size). Counts SATURATION responses (503 / LiteLLM cooldown
429) and TIMEOUTS, not quota refusals: quota is per key, so it says nothing about the class being down.
A timeout is a whole timeout period of evidence, so it opens the breaker on its own (BREAKER_TIMEOUT_THRESHOLD,
default 1); saturations and 5xx / network errors need BREAKER_THRESHOLD within BREAKER_WINDOW_S.

  closed     --(1 timeout, or BREAKER_THRESHOLD failures within BREAKER_WINDOW_S)-->  open
  open       --(BREAKER_COOLDOWN_S elapsed, allow())------------------------------>  half_open (allow() = the probe)
  half_open  --(probe succeeds)-->  closed        --(probe fails)-->  open (cooldown restarts)

While the breaker is not closed the router sends the class's tasks straight to their fallback: no request is
spent discovering what we already know, and no learner request is ever the probe. The engine sends the one
probe itself (a tiny BACKGROUND request with a short timeout) when allow() grants it; a probe that never
reports is released or expires after a cooldown.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

StateChange = Callable[[str, str, str], None]          # (old, new, reason)


class CircuitBreaker:
    def __init__(self, threshold: int, window_s: float, cooldown_s: float,
                 clock: Callable[[], float] = time.monotonic, on_change: StateChange | None = None,
                 timeout_threshold: int = 1):
        if threshold < 1 or timeout_threshold < 1:
            raise ValueError("BREAKER_THRESHOLD and BREAKER_TIMEOUT_THRESHOLD must be >= 1")
        self.threshold, self.window_s, self.cooldown_s = threshold, window_s, cooldown_s
        self.timeout_threshold = timeout_threshold
        self._timeouts: deque[float] = deque()
        self._clock, self._on_change = clock, on_change
        self.state = CLOSED
        self._failures: deque[float] = deque()
        self._opened_at = 0.0
        self._probe_since: float | None = None
        self.trips = 0

    def _set(self, new: str, reason: str) -> None:
        old, self.state = self.state, new
        if new == OPEN:
            self._opened_at = self._clock()
            self.trips += old == CLOSED
        if new != HALF_OPEN:
            self._probe_since = None
        if self._on_change and old != new:
            self._on_change(old, new, reason)

    @property
    def closed(self) -> bool:
        return self.state == CLOSED

    def retry_in(self) -> float:
        return max(0.0, self._opened_at + self.cooldown_s - self._clock()) if self.state == OPEN else 0.0

    def allow(self) -> bool:
        """May a request use this class now? In half_open, True exactly once (the probe)."""
        now = self._clock()
        if self.state == CLOSED:
            return True
        if self.state == OPEN:
            if now - self._opened_at < self.cooldown_s:
                return False
            self._set(HALF_OPEN, f"cooldown {self.cooldown_s:g}s elapsed")
        if self._probe_since is None or now - self._probe_since >= self.cooldown_s:   # expired probe
            self._probe_since = now
            return True
        return False

    def release_probe(self) -> None:
        """The probe ended without calling the class (cache hit, nothing retrieved...): let another one through."""
        if self.state == HALF_OPEN:
            self._probe_since = None

    def record_failure(self, kind: str = "saturation") -> None:
        """kind: "saturation" (503 / cooldown, also 5xx and network errors) or "timeout" (no answer in time)."""
        now = self._clock()
        if self.state == HALF_OPEN:
            self._set(OPEN, f"probe failed ({kind})")
            return
        if self.state == OPEN:
            return                                  # late answers from requests sent before the trip
        log = self._timeouts if kind == "timeout" else self._failures
        need = self.timeout_threshold if kind == "timeout" else self.threshold
        log.append(now)
        while log and now - log[0] > self.window_s:
            log.popleft()
        if len(log) >= need:
            self._failures.clear()
            self._timeouts.clear()
            what = "timeout" if kind == "timeout" else "saturation"
            self._set(OPEN, f"{need} {what}{'s' if need > 1 else ''} within {self.window_s:g}s")

    def record_success(self) -> None:
        if self.state == HALF_OPEN:
            self._set(CLOSED, "probe succeeded")
