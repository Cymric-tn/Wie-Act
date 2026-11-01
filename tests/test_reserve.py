"""v1.3 background reserve: BACKGROUND waits while fewer than BACKGROUND_RESERVE sends remain in the window."""
import asyncio
import random

from config import ModelClass
from gatekeeper.client import ChatRequest, LLMClient
from gatekeeper.limiter import Limiter
from gatekeeper.queue import Priority, PriorityQueue
from gatekeeper.retry import CallContext, RetryingCaller, RetryPolicy
from metrics import MemorySink
from tests.conftest import FakeProvider, make_settings
from tests.vloop import run_virtual


def build(loop, limit=10, reserve=6):
    s = make_settings(RATE_LIMIT_PER_MIN=limit, MAX_CONCURRENT=2, BACKGROUND_RESERVE=reserve)
    lim = Limiter(s, loop.time, asyncio.sleep)
    q = PriorityQueue(lim, loop.time, background_reserve=s.background_reserve)
    fake = FakeProvider()
    rc = RetryingCaller(LLMClient(s, fake), lim, MemorySink(), RetryPolicy.from_settings(s), s.input_token_budget,
                        loop.time, asyncio.sleep, random.Random(1), q)
    return rc, fake, lim


async def call(rc, loop, label, prio, times, events):
    ctx = CallContext(priority=prio.value, on_event=lambda n, d: events.append((label, n, round(loop.time() - t0[0]))))
    await rc.chat(ChatRequest(ModelClass.COMPACT, ({"role": "user", "content": label},)), ctx)
    times[label] = round(loop.time() - t0[0])


t0 = [0.0]


def test_background_waits_for_the_reserve_and_interactive_does_not():
    times, events = {}, []

    async def main(loop):
        t0[0] = loop.time()
        rc, fake, lim = build(loop, limit=10, reserve=6)
        for i in range(4):                                             # 4 interactive sends: 6 remain
            await call(rc, loop, f"i{i}", Priority.INTERACTIVE, times, events)
        await call(rc, loop, "bg0", Priority.BACKGROUND, times, events)    # 6 remain: allowed (literal rule), 5 left
        assert times["bg0"] == 0 and lim.buckets["chat"].tokens == 5
        bg1 = asyncio.create_task(call(rc, loop, "bg1", Priority.BACKGROUND, times, events))
        await asyncio.sleep(1)
        await call(rc, loop, "i4", Priority.INTERACTIVE, times, events)    # the learner is not held by the reserve
        assert times["i4"] <= 1 and not bg1.done()
        await bg1
        return fake

    fake = run_virtual(main)
    assert times["bg1"] >= 60                                          # waited for the first send to leave the window
    assert [e for e in events if e[0] == "bg1" and e[1] == "waiting_reserve"], events
    assert sum(1 for e in events if e[1] == "waiting_reserve") == 1    # noted once, not on every wake-up


def test_reserve_zero_disables_it_and_embeddings_are_not_affected():
    times, events = {}, []

    async def main(loop):
        t0[0] = loop.time()
        rc, fake, lim = build(loop, limit=10, reserve=0)
        for i in range(10):
            await call(rc, loop, f"bg{i}", Priority.BACKGROUND, times, events)

    run_virtual(main)
    assert max(times.values()) == 0 and not [e for e in events if e[1] == "waiting_reserve"]
