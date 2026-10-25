"""
Circuit breaker for one model class (mid-size). Counts SATURATION responses (503 / LiteLLM cooldown
429), not quota refusals: quota is per key, so it says nothing about the class being overloaded.

  closed     --(BREAKER_THRESHOLD saturations within BREAKER_WINDOW_S)-->  open
  open       --(BREAKER_COOLDOWN_S elapsed, next request)------------------>  half_open (that request = probe)
  half_open  --(probe succeeds)-->  closed        --(probe saturated)-->  open (cooldown restarts)

While open, the router sends the class's tasks straight to their fallback: no request is spent
discovering what we already know. Only one probe is let through in half_open; a probe that never
reports (cache hit, nothing retrieved, cancelled) is released by the engine or expires after a cooldown.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

StateChange = Callable[[str, str, str], None]          # (old, new, reason)


class CircuitBreaker:
    def __init__(self, threshold: int, window_s: float, cooldown_s: float,
                 clock: Callable[[], float] = time.monotonic, on_change: StateChange | None = None):
        if threshold < 1:
            raise ValueError("BREAKER_THRESHOLD must be >= 1")
        self.threshold, self.window_s, self.cooldown_s = threshold, window_s, cooldown_s
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

    def record_failure(self) -> None:
        now = self._clock()
        if self.state == HALF_OPEN:
            self._set(OPEN, "probe saturated")
            return
        if self.state == OPEN:
            return                                  # late answers from requests sent before the trip
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.window_s:
            self._failures.popleft()
        if len(self._failures) >= self.threshold:
            self._failures.clear()
            self._set(OPEN, f"{self.threshold} saturations within {self.window_s:g}s")

    def record_success(self) -> None:
        if self.state == HALF_OPEN:
            self._set(CLOSED, "probe succeeded")
