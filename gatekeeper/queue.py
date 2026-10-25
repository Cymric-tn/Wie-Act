"""
Priority admission in front of the limiter: INTERACTIVE before BACKGROUND, FIFO within a priority.

One dispatcher task per kind (chat / embed) waits until the limiter has capacity (rate token +
concurrency slot) and only THEN pops the best waiter. Choosing at grant time is what prevents
starvation: an interactive request that arrives while 50 background ones wait is the next one
out as soon as a slot frees. With nothing interactive waiting, background drains in order.

A retry keeps its original sequence number (stored on the call context), so a request that
waited a stated quota delay is not sent to the back of its priority class.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any

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
        self._heaps: dict[str, list[tuple[int, int, asyncio.Future]]] = {"chat": [], "embed": []}
        self._wake: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self.ewma_hold_s = initial_hold_s          # how long a request holds a slot (for ETAs)

    # ------------------------------------------------------------ introspection
    def depth(self, kind: Kind = "chat") -> int:
        return sum(1 for *_, f in self._heaps[kind] if not f.done())

    def eta_s(self, ahead: int) -> float:
        """Rough wait for a request with `ahead` requests in front of it."""
        return round((ahead + 1) * self.ewma_hold_s / max(1, self.limiter.max_concurrent), 1)

    # ------------------------------------------------------------ admission
    @asynccontextmanager
    async def slot(self, kind: Kind = "chat", priority: Priority | str | None = None, ctx: Any = None):
        """ctx (duck-typed CallContext): .queue_seq is reused across retries, .emit(name, **data)."""
        seq = getattr(ctx, "queue_seq", None)
        if seq is None:
            seq = next(self._seq)
            if ctx is not None:
                ctx.queue_seq = seq
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        entry = (rank(priority), seq, fut)
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
            if fut.done() and not fut.cancelled():   # granted, but we were cancelled before using it
                self.limiter.release(refund=kind)
            else:
                fut.cancel()
            raise
        t0 = self._clock()
        try:
            yield info
        finally:
            self.limiter.release()
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

    async def _dispatch(self, kind: Kind) -> None:
        heap, wake = self._heaps[kind], self._wake[kind]
        while True:
            self._prune(heap)
            if not heap:
                wake.clear()
                await wake.wait()
                continue
            info = await self.limiter.acquire(kind)
            self._prune(heap)                        # pick the best waiter NOW, not when we started
            if not heap:
                self.limiter.release(refund=kind)
                continue
            heapq.heappop(heap)[2].set_result(info)

    async def aclose(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        for t in self._tasks.values():
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
