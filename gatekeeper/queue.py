"""
Priority admission in front of the limiter: INTERACTIVE before BACKGROUND, FIFO within a priority.

One dispatcher task per kind (chat / embed) waits until the limiter has capacity (rate token +
concurrency slot) and only THEN pops the best waiter. Choosing at grant time is what prevents
starvation: an interactive request that arrives while 50 background ones wait is the next one
out as soon as a slot frees. With nothing interactive waiting, background drains in order.

A retry keeps its original sequence number (stored on the call context), so a request that
waited a stated quota delay is not sent to the back of its priority class.

v1.2: the dispatcher grants the best ELIGIBLE waiter. A request of a model class that already holds its slot
cap (MIDSIZE_MAX_SLOTS, ghosts included) is not eligible, so compact work behind a hung mid-size request is
never blocked; and a waiter whose class breaker opens meanwhile is failed at once (ClassUnavailable, nothing
is sent) instead of waiting for a slot it should not use.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

from gatekeeper.errors import ClassUnavailable
from gatekeeper.limiter import Clock, Kind, Limiter, SlotInfo


class Priority(str, Enum):
    INTERACTIVE = "INTERACTIVE"
    BACKGROUND = "BACKGROUND"


_RANK = {Priority.INTERACTIVE: 0, Priority.BACKGROUND: 1}


def rank(p: Priority | str | None) -> int:
    try:
        return _RANK[Priority(p)] if p is not None else _RANK[Priority.INTERACTIVE]
    except ValueError:
        return _RANK[Priority.BACKGROUND]          # unknown labels never jump the queue


class PriorityQueue:
    def __init__(self, limiter: Limiter, clock: Clock = time.monotonic, initial_hold_s: float = 4.0):
        self.limiter = limiter
        self._clock = clock
        self._seq = itertools.count()
        # entries: (rank, seq, future, model_class | None, unavailable() -> seconds | None)
        self._heaps: dict[str, list[tuple]] = {"chat": [], "embed": []}
        self._wake: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self.ewma_hold_s = initial_hold_s          # how long a request holds a slot (for ETAs)
        limiter.on_change = self.poke

    # ------------------------------------------------------------ introspection
    def depth(self, kind: Kind = "chat") -> int:
        return sum(1 for e in self._heaps[kind] if not e[2].done())

    def eta_s(self, ahead: int) -> float:
        """Rough wait for a request with `ahead` requests in front of it."""
        return round((ahead + 1) * self.ewma_hold_s / max(1, self.limiter.max_concurrent), 1)

    # ------------------------------------------------------------ changes
    def poke(self) -> None:
        """A slot, a ghost or a breaker changed: fail the waiters whose class is now down, wake the dispatchers."""
        for heap in self._heaps.values():
            self._fail_unavailable(heap)
        for ev in self._wake.values():
            ev.set()

    @staticmethod
    def _down(e: tuple) -> ClassUnavailable | None:
        wait = e[4]() if e[4] is not None else None
        if wait is None:
            return None
        return ClassUnavailable(message=f"{e[3].value if e[3] else 'model'} class unavailable (breaker open)",
                                retry_after_s=wait)

    def _fail_unavailable(self, heap) -> None:
        for e in list(heap):
            if not e[2].done() and (err := self._down(e)) is not None:
                e[2].set_exception(err)
        self._prune(heap)

    # ------------------------------------------------------------ admission
    @asynccontextmanager
    async def slot(self, kind: Kind = "chat", priority: Priority | str | None = None, ctx: Any = None,
                   model_class=None, unavailable=None):
        """ctx (duck-typed CallContext): .queue_seq is reused across retries, .emit(name, **data).
        model_class: counted against the class's slot cap. unavailable() -> seconds | None: not None means the
        class is down (breaker open): the request is not queued, or is failed if already waiting."""
        seq = getattr(ctx, "queue_seq", None)
        if seq is None:
            seq = next(self._seq)
            if ctx is not None:
                ctx.queue_seq = seq
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        entry = (rank(priority), seq, fut, model_class, unavailable)
        if (err := self._down(entry)) is not None:
            raise err
        heap = self._heaps[kind]
        ahead = sum(1 for e in heap if e[:2] < entry[:2] and not e[2].done())
        heapq.heappush(heap, entry)
        if ctx is not None:
            ctx.emit("queued", position=ahead, eta_s=self.eta_s(ahead + self.limiter.in_flight))
        self._ensure_dispatcher(kind)
        self._wake[kind].set()
        try:
            info: SlotInfo = await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled() and fut.exception() is None:   # granted, then cancelled unused
                self.limiter.release(refund=kind, cls=model_class)
            else:
                fut.cancel()
            raise
        t0 = self._clock()
        try:
            yield info
        finally:
            self.limiter.release(cls=model_class, ghost_for_s=info.ghost_for_s)
            self.ewma_hold_s = 0.8 * self.ewma_hold_s + 0.2 * (self._clock() - t0)

    # ------------------------------------------------------------ dispatcher
    def _ensure_dispatcher(self, kind: str) -> None:
        t = self._tasks.get(kind)
        if t is None or t.done():
            self._wake[kind] = asyncio.Event()
            self._tasks[kind] = asyncio.get_running_loop().create_task(self._dispatch(kind))

    def _prune(self, heap) -> None:
        while heap and heap[0][2].done():
            heapq.heappop(heap)

    def _best_eligible(self, heap) -> tuple | None:
        self.limiter.reap()
        ok = [e for e in heap if not e[2].done() and (e[3] is None or self.limiter.class_free(e[3]))]
        return min(ok, key=lambda e: e[:2]) if ok else None

    async def _idle(self, heap, wake: asyncio.Event) -> None:
        """Nothing can be granted: wait for a change (slot freed, new waiter, breaker) or the next ghost expiry."""
        wake.clear()
        exp = self.limiter.next_ghost_expiry() if heap else None
        if exp is None:
            await wake.wait()
            return
        waiter = asyncio.ensure_future(wake.wait())
        timer = asyncio.ensure_future(self.limiter._sleep(exp + 1e-6))
        try:
            await asyncio.wait({waiter, timer}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (waiter, timer):
                t.cancel()
            await asyncio.gather(waiter, timer, return_exceptions=True)

    async def _dispatch(self, kind: Kind) -> None:
        heap, wake = self._heaps[kind], self._wake[kind]
        while True:
            self._fail_unavailable(heap)
            if self._best_eligible(heap) is None:
                await self._idle(heap, wake)
                continue
            info = await self.limiter.acquire(kind)
            self._fail_unavailable(heap)             # the waiting may have taken a while: choose NOW
            pick = self._best_eligible(heap)
            if pick is None:
                self.limiter.release(refund=kind)
                continue
            heap.remove(pick)
            heapq.heapify(heap)
            self.limiter.claim(pick[3])
            pick[2].set_result(info)

    async def aclose(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        for t in self._tasks.values():
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
