"""
Two separate mechanisms, as the platform has two separate limits:
  * SlidingWindow — rate: at most N sends in ANY rolling window of W seconds (a log of send times).
                    Separate windows for chat and embeddings (separate quotas).
  * Semaphore     — concurrency: at most MAX_CONCURRENT requests in flight.

FelCloud refuses the 21st request of a minute and stays closed for the full 60 s (probe A, 2026-10-26),
so a token bucket with a burst reserve overshoots it; a sliding log can never send more than N in any
W seconds, which also holds under a fixed server window.

The window is kept honest with server feedback: when a 200 reports fewer remaining requests than we
count locally (other traffic on the same key), phantom entries make up the difference, and a stated
quota delay blocks the window until it expires.
TokenBucket is kept for the constraint simulator, which models the provider's own bucket.
Clock and sleep are injectable so tests run in virtual time.
"""
from __future__ import annotations

import asyncio
import bisect
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

from config import Settings
from gatekeeper.errors import QuotaInfo

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]
Kind = Literal["chat", "embed"]
_EPS = 1e-9                                       # tokens; below this a deficit is rounding noise


class _Gate:
    """FIFO waiting shared by the rate gates; subclasses give wait_time() and try_take()."""
    _clock: Clock
    _lock: asyncio.Lock | None = None

    def wait_time(self, n: float = 1.0) -> float: ...
    def try_take(self, n: float = 1.0) -> float: ...

    async def acquire(self, sleep: Sleep = asyncio.sleep,
                      on_wait: Callable[[float], None] | None = None) -> float:
        """Wait (FIFO) until a token is available and take it; returns seconds waited."""
        start = self._clock()
        await self.wait_available(sleep, on_wait)
        while self.try_take() > 0:                 # lost a race: wait again
            await self.wait_available(sleep)
        return self._clock() - start

    async def wait_available(self, sleep: Sleep = asyncio.sleep,
                             on_wait: Callable[[float], None] | None = None) -> float:
        """Wait (FIFO) until a token is available WITHOUT taking it; returns seconds waited."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        start = self._clock()
        notified = False
        async with self._lock:
            while (w := self.wait_time()) > 0:
                if on_wait and not notified:
                    on_wait(w)
                    notified = True
                await sleep(w)
        return self._clock() - start


class TokenBucket(_Gate):
    def __init__(self, rate_per_min: float, burst: int, clock: Clock = time.monotonic):
        if rate_per_min <= 0 or burst < 1:
            raise ValueError("rate_per_min must be > 0 and burst >= 1")
        self.rate = rate_per_min / 60.0          # tokens per second
        self.capacity = float(burst)
        self._clock = clock
        self._tokens = float(burst)
        self._t = clock()
        self._blocked_until = 0.0
        self._lock: asyncio.Lock | None = None

    def _refill(self) -> float:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._t) * self.rate)
        self._t = now
        if self._blocked_until and now >= self._blocked_until:
            # the provider told us when to come back: honour exactly that, not later
            self._tokens = max(self._tokens, 1.0)
            self._blocked_until = 0.0
        return now

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    def wait_time(self, n: float = 1.0) -> float:
        now = self._refill()
        if self._blocked_until > now:
            return self._blocked_until - now
        deficit = n - self._tokens
        # float refill can stop at 0.999999999999: a sub-ulp sleep never advances the clock
        return 0.0 if deficit <= _EPS else deficit / self.rate

    def try_take(self, n: float = 1.0) -> float:
        """Take n tokens if available and return 0, else return the seconds to wait."""
        w = self.wait_time(n)
        if w <= 0:
            self._tokens -= n
            return 0.0
        return w

    def block_for(self, seconds: float) -> None:
        """Provider stated a delay (quota exhausted): no request before it expires."""
        now = self._refill()
        self._tokens = min(self._tokens, 0.0)
        self._blocked_until = max(self._blocked_until, now + max(0.0, seconds))

    def refund(self, n: float = 1.0) -> None:
        self._refill()
        self._tokens = min(self.capacity, self._tokens + n)

    def sync_remaining(self, remaining: int | None) -> None:
        """Server-side remaining count can only lower our estimate, never raise it."""
        if remaining is None:
            return
        self._refill()
        self._tokens = min(self._tokens, float(remaining))


class SlidingWindow(_Gate):
    """At most `limit` sends in any rolling `window_s` seconds. Same interface as TokenBucket
    (`tokens` = free slots), so the queue, the router and the retry layer need no change."""

    def __init__(self, limit: int, window_s: float = 60.0, clock: Clock = time.monotonic, slack_s: float = 0.0):
        if limit < 1 or window_s <= 0:
            raise ValueError("limit must be >= 1 and window_s > 0")
        self.limit, self.window_s, self.slack_s = int(limit), float(window_s), float(slack_s)
        self._clock = clock
        self._log: list[tuple[float, bool]] = []        # (time, phantom), ascending
        self._blocked_until = 0.0

    def _span(self) -> float:
        return self.window_s + self.slack_s

    def _prune(self) -> float:
        now = self._clock()
        cut = 0
        while cut < len(self._log) and self._log[cut][0] + self._span() <= now + _EPS:
            cut += 1
        del self._log[:cut]
        return now

    @property
    def tokens(self) -> float:
        now = self._prune()
        return 0.0 if self._blocked_until > now else float(max(0, self.limit - len(self._log)))

    def wait_time(self, n: float = 1.0) -> float:
        now = self._prune()
        w = max(0.0, self._blocked_until - now)
        need = len(self._log) + int(n) - self.limit      # sends that must expire before n more may go
        if need > 0:
            w = max(w, self._log[min(need, len(self._log)) - 1][0] + self._span() - now)
        return 0.0 if w <= _EPS else w

    def try_take(self, n: float = 1.0) -> float:
        """Record n sends now if the window has room and return 0, else return the seconds to wait."""
        w = self.wait_time(n)
        if w > 0:
            return w
        now = self._clock()
        for _ in range(int(n)):
            self._log.append((now, False))
        return 0.0

    def block_for(self, seconds: float) -> None:
        """Provider stated a delay (quota exhausted): no request before it expires."""
        now = self._prune()
        self._blocked_until = max(self._blocked_until, now + max(0.0, seconds))

    def refund(self, n: float = 1.0) -> None:
        """A slot was taken but nothing was sent: drop the oldest real entry. Dropping the oldest can only
        leave later timestamps than the truth, i.e. never admit a send the server would still count."""
        self._prune()
        for _ in range(int(n)):
            i = next((i for i, (_, phantom) in enumerate(self._log) if not phantom), None)
            if i is not None:
                del self._log[i]

    def sync_remaining(self, remaining: int | None, at: float | None = None) -> None:
        """The server counts more requests than we do (other traffic on the key): add phantom entries,
        stamped when the server counted them (`at`, the send time), so local never exceeds the server."""
        if remaining is None:
            return
        self._prune()
        extra = (self.limit - len(self._log)) - max(0, int(remaining))
        if extra > 0:
            t = min(at if at is not None else self._clock(), self._clock())
            for _ in range(extra):
                bisect.insort(self._log, (t, True))


@dataclass
class SlotInfo:
    rate_wait_s: float = 0.0
    concurrency_wait_s: float = 0.0

    @property
    def wait_s(self) -> float:
        return self.rate_wait_s + self.concurrency_wait_s


class Limiter:
    def __init__(self, settings: Settings, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep):
        self._clock, self._sleep = clock, sleep
        self.buckets: dict[str, SlidingWindow] = {      # name kept from the token-bucket days
            "chat": SlidingWindow(int(settings.rate_per_min), settings.rate_window_s, clock, settings.rate_window_slack_s),
            "embed": SlidingWindow(int(settings.embed_rate_per_min), settings.embed_rate_window_s, clock,
                                   settings.rate_window_slack_s),
        }
        # ASSUMPTION(felcloud): the 2-concurrent cap is per key, shared by chat and embeddings.
        self.concurrency = asyncio.Semaphore(settings.max_concurrent)
        self.max_concurrent = settings.max_concurrent
        self.in_flight = 0

    async def acquire(self, kind: Kind = "chat",
                      on_wait: Callable[[str, float], None] | None = None) -> SlotInfo:
        """Wait for a rate slot to be available, take a concurrency slot, then record the send
        right before sending. Spending earlier would let requests queued on the semaphore
        reach the provider as a burst bigger than our bucket allowed; waiting for the token
        while holding the slot would block embeddings behind a chat quota wait."""
        info = SlotInfo()
        bucket = self.buckets[kind]
        notify = (lambda w: on_wait("waiting_quota", w)) if on_wait else None
        while True:
            t0 = self._clock()
            await bucket.wait_available(self._sleep, notify)
            t1 = self._clock()
            info.rate_wait_s += t1 - t0
            if self.concurrency.locked() and on_wait:
                on_wait("waiting_slot", 0.0)
            await self.concurrency.acquire()
            info.concurrency_wait_s += self._clock() - t1
            if bucket.try_take() == 0:
                break
            self.concurrency.release()             # token went to someone else meanwhile
            notify = None
        self.in_flight += 1
        return info

    def release(self, refund: Kind | None = None) -> None:
        """Free the concurrency slot; refund=kind gives the rate token back (request never sent)."""
        self.in_flight -= 1
        self.concurrency.release()
        if refund:
            self.buckets[refund].refund()

    @asynccontextmanager
    async def slot(self, kind: Kind = "chat", on_wait: Callable[[str, float], None] | None = None):
        info = await self.acquire(kind, on_wait)
        try:
            yield info
        finally:
            self.release()

    def observe(self, kind: Kind, quota: QuotaInfo, sent_at: float | None = None) -> None:
        self.buckets[kind].sync_remaining(quota.remaining_requests, sent_at)

    def block(self, kind: Kind, seconds: float) -> None:
        self.buckets[kind].block_for(seconds)

    def wait_time(self, kind: Kind = "chat") -> float:
        """Seconds until the rate window admits one more request (0 = now); includes a stated quota delay."""
        return self.buckets[kind].wait_time()
