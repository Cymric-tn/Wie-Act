import asyncio
import json
import random

import pytest

from config import ModelClass
from gatekeeper.client import ChatRequest, LLMClient
from gatekeeper.limiter import Limiter
from gatekeeper.queue import Priority, PriorityQueue, rank
from gatekeeper.retry import CallContext, RetryingCaller, RetryPolicy
from metrics import MemorySink
from tests.conftest import FakeProvider, make_settings


class GatedProvider(FakeProvider):
    """Each request is held until the test opens its gate: in-flight state is exact, no timing."""

    def __init__(self):
        super().__init__()
        self.gates: list[asyncio.Event] = []
        self.arrivals: list[str] = []                 # labels in the order they reached the provider

    async def handle_async_request(self, request):
        gate = asyncio.Event()
        self.gates.append(gate)
        self.arrivals.append(json.loads(request.content)["messages"][0]["content"])
        await gate.wait()
        return await super().handle_async_request(request)


def caller(transport, **env):
    s = make_settings(**{"RATE_LIMIT_PER_MIN": 6000, "RATE_BURST": 200, "MAX_CONCURRENT": 2, **env})
    return RetryingCaller(LLMClient(s, transport), Limiter(s), MemorySink(), RetryPolicy.from_settings(s),
                          s.input_token_budget, rng=random.Random(1))


def send(rc, label, priority):
    req = ChatRequest(ModelClass.COMPACT, ({"role": "user", "content": label},))
    return asyncio.create_task(rc.chat(req, CallContext(priority=priority)))


def sent_labels(fake):
    return getattr(fake, "arrivals", None) or [r["body"]["messages"][0]["content"] for r in fake.requests]


async def _settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


async def test_interactive_is_next_out_despite_50_background():
    fake = GatedProvider()
    rc = caller(fake)
    bg = [send(rc, f"bg-{i}", Priority.BACKGROUND) for i in range(50)]
    await _settle()
    assert len(fake.gates) == 2                                   # both slots busy with background
    inter = send(rc, "INTERACTIVE", Priority.INTERACTIVE)
    await _settle()
    assert len(fake.gates) == 2                                   # nothing jumps the concurrency cap
    fake.gates[0].set()                                           # one slot frees...
    await _settle()
    assert len(fake.gates) == 3
    fake.gates[2].set()
    await inter
    assert sent_labels(fake)[2] == "INTERACTIVE"                  # ...and interactive goes out next
    for g in fake.gates:
        g.set()
    while len(fake.gates) < 51:                                   # release the rest as they arrive
        await _settle()
        for g in fake.gates:
            g.set()
    await asyncio.gather(*bg)
    await rc.queue.aclose()


async def test_background_drains_in_order_when_nothing_interactive_waits():
    fake = FakeProvider(delay=0.001)
    rc = caller(fake)
    await asyncio.gather(*[send(rc, f"bg-{i:02d}", Priority.BACKGROUND) for i in range(50)])
    assert sent_labels(fake) == [f"bg-{i:02d}" for i in range(50)]   # all progress, FIFO
    assert fake.max_in_flight == 2 and rc.limiter.in_flight == 0
    await rc.queue.aclose()


async def test_interactive_waiters_are_fifo_among_themselves():
    fake = GatedProvider()
    rc = caller(fake, MAX_CONCURRENT=1)
    first = send(rc, "hold", Priority.BACKGROUND)
    await _settle()
    ts = [send(rc, f"i-{i}", Priority.INTERACTIVE) for i in range(3)] + [send(rc, "late-bg", Priority.BACKGROUND)]
    await _settle()
    while not all(t.done() for t in [first, *ts]):
        for g in fake.gates:
            g.set()
        await _settle()
    assert sent_labels(fake) == ["hold", "i-0", "i-1", "i-2", "late-bg"]
    await rc.queue.aclose()


async def test_cancelled_waiter_does_not_leak_a_slot():
    fake = GatedProvider()
    rc = caller(fake, MAX_CONCURRENT=1)
    a = send(rc, "a", Priority.INTERACTIVE)
    b = send(rc, "b", Priority.INTERACTIVE)
    await _settle()
    b.cancel()
    fake.gates[0].set()
    await a
    with pytest.raises(asyncio.CancelledError):
        await b
    c = send(rc, "c", Priority.BACKGROUND)
    await _settle()
    fake.gates[-1].set()
    await c
    assert sent_labels(fake) == ["a", "c"] and rc.limiter.in_flight == 0
    await rc.queue.aclose()


async def test_queued_event_reports_position_and_eta():
    fake = GatedProvider()
    rc = caller(fake, MAX_CONCURRENT=1)
    seen = []
    send(rc, "x", Priority.BACKGROUND)
    await _settle()
    req = ChatRequest(ModelClass.COMPACT, ({"role": "user", "content": "y"},))
    t = asyncio.create_task(rc.chat(req, CallContext(priority="INTERACTIVE", on_event=lambda n, d: seen.append((n, d)))))
    await _settle()
    name, data = seen[0]
    assert name == "queued" and data["position"] == 0 and data["eta_s"] > 0
    for _ in range(3):
        for g in fake.gates:
            g.set()
        await _settle()
    await t
    await rc.queue.aclose()


def test_rank_unknown_priority_is_background():
    assert rank(Priority.INTERACTIVE) < rank(Priority.BACKGROUND) == rank("SOMETHING_ELSE")
    assert rank(None) == rank(Priority.INTERACTIVE)
