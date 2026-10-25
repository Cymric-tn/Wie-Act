"""
Two separate mechanisms, as the platform has two separate limits:
  * TokenBucket  — rate: N requests/min, continuous refill, burst reserve.
                   Separate buckets for chat and embeddings (separate quotas).
  * Semaphore    — concurrency: at most MAX_CONCURRENT requests in flight.

The bucket is kept honest with server feedback: quota headers can only lower our token
count, and a stated quota delay blocks the bucket until it expires.
Clock and sleep are injectable so tests run in virtual time.
"""
from __future__ import annotations

import asyncio
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


class TokenBucket:
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
        self.buckets: dict[str, TokenBucket] = {
            "chat": TokenBucket(settings.rate_per_min, settings.rate_burst, clock),
            "embed": TokenBucket(settings.embed_rate_per_min, settings.embed_burst, clock),
        }
        # ASSUMPTION(felcloud): the 2-concurrent cap is per key, shared by chat and embeddings.
        self.concurrency = asyncio.Semaphore(settings.max_concurrent)
        self.max_concurrent = settings.max_concurrent
        self.in_flight = 0

    async def acquire(self, kind: Kind = "chat",
                      on_wait: Callable[[str, float], None] | None = None) -> SlotInfo:
        """Wait for a rate token to be available, take a concurrency slot, then spend the token
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

    def observe(self, kind: Kind, quota: QuotaInfo) -> None:
        self.buckets[kind].sync_remaining(quota.remaining_requests)

    def block(self, kind: Kind, seconds: float) -> None:
        self.buckets[kind].block_for(seconds)
