"""v1.2 step 5: a request the client gave up on keeps a local slot while the server still counts it."""
import asyncio
import random

import httpx
import pytest

from config import ModelClass
from gatekeeper.client import ChatRequest, LLMClient
from gatekeeper.errors import ConcurrencyLimited, TransientError
from gatekeeper.limiter import Limiter
from gatekeeper.retry import CallContext, RetryingCaller, RetryPolicy
from metrics import MemorySink
from tests.conftest import FnProvider, chat_ok, make_settings
from tests.test_errors import real_429
from tests.outage_harness import Timeline, build, end_outage, EXPLAINS
from tests.vloop import run_virtual

M, C = ModelClass.MIDSIZE, ModelClass.COMPACT
MSG = ({"role": "user", "content": "hi"},)


# ------------------------------------------------------------------ limiter level
async def test_a_ghost_keeps_the_concurrency_slot_and_the_class_slot_until_it_expires(vt):
    lim = Limiter(make_settings(MAX_CONCURRENT=2, MIDSIZE_MAX_SLOTS=2), vt.clock, vt.sleep)
    info = await lim.acquire("chat")
    lim.claim(M)
    lim.release(cls=M, ghost_for_s=50)
    assert lim.ghost_count(M) == 1 and lim.class_in_use[M] == 1
    await lim.acquire("chat")                                  # the second slot is free ...
    assert lim.concurrency.locked()                            # ... the ghost holds the first
    lim.release()
    vt.t += 49.9
    assert lim.ghost_count(M) == 1 and lim.concurrency.locked() is False
    vt.t += 0.2
    assert lim.ghost_count(M) == 0 and lim.class_in_use[M] == 0       # expired by the clock, nobody had to wait for it
    assert lim.concurrency._value == 2


async def test_a_later_success_of_the_class_clears_its_ghosts(vt):
    lim = Limiter(make_settings(MAX_CONCURRENT=2), vt.clock, vt.sleep)
    await lim.acquire("chat"); lim.claim(M); lim.release(cls=M, ghost_for_s=80)
    await lim.acquire("chat"); lim.claim(C); lim.release(cls=C, ghost_for_s=80)
    lim.clear_ghosts(M)
    assert lim.ghost_count(M) == 0 and lim.ghost_count(C) == 1
    assert lim.concurrency._value == 1


async def test_next_ghost_expiry(vt):
    lim = Limiter(make_settings(), vt.clock, vt.sleep)
    assert lim.next_ghost_expiry() is None
    await lim.acquire("chat"); lim.claim(M); lim.release(cls=M, ghost_for_s=30)
    vt.t += 10
    assert lim.next_ghost_expiry() == pytest.approx(20)


# ------------------------------------------------------------------ retry layer
def caller(vt, fn, **env):
    s = make_settings(**env)
    fake = FnProvider(fn)
    rc = RetryingCaller(LLMClient(s, fake), Limiter(s, vt.clock, vt.sleep), MemorySink(),
                        RetryPolicy.from_settings(s), s.input_token_budget, vt.clock, vt.sleep, random.Random(1))
    return rc, fake


def ctx(priority="INTERACTIVE"):
    ev = []
    return CallContext(task="explain", priority=priority, on_event=lambda n, d: ev.append(n)), ev


async def test_a_timeout_leaves_a_ghost_that_lasts_until_the_server_timeout(vt):
    def fn(body):
        vt.t += 40                                              # the request ran 40 s before the client gave up
        raise httpx.ReadTimeout("no answer")
    rc, _ = caller(vt, fn, MIDSIZE_MAX_SLOTS=0)
    sent = vt.t
    with pytest.raises(TransientError):
        await rc.chat(ChatRequest(M, MSG), ctx()[0])
    assert rc.limiter.ghost_count(M) == 1
    assert rc.limiter.ghosts[0].until == pytest.approx(sent + 90)       # SERVER_TIMEOUT_S after it was SENT
    vt.t = sent + 89.9
    assert rc.limiter.ghost_count(M) == 1
    vt.t = sent + 90.1
    assert rc.limiter.ghost_count(M) == 0


async def test_a_success_of_the_class_clears_the_ghost(vt):
    state = {"n": 0}

    def fn(body):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ReadTimeout("no answer")
        return chat_ok("ok")
    rc, _ = caller(vt, fn, MIDSIZE_MAX_SLOTS=0)
    with pytest.raises(TransientError):
        await rc.chat(ChatRequest(M, MSG), ctx()[0])
    assert rc.limiter.ghost_count(M) == 1
    await rc.chat(ChatRequest(M, MSG), ctx()[0])
    assert rc.limiter.ghost_count(M) == 0 and rc.limiter.concurrency._value == 2


async def test_a_429_concurrency_after_a_timeout_is_not_retried_on_midsize(vt):
    state = {"n": 0}

    def fn(body):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ReadTimeout("no answer")
        return real_429("concurrency")                          # the real FelCloud body and headers
    rc, fake = caller(vt, fn, MIDSIZE_MAX_SLOTS=0)
    with pytest.raises(TransientError):
        await rc.chat(ChatRequest(M, MSG), ctx()[0])
    slept = len(vt.slept)
    with pytest.raises(ConcurrencyLimited) as e:
        await rc.chat(ChatRequest(M, MSG), ctx()[0])
    assert e.value.after_timeout and e.value.attempts == 1 and len(fake.requests) == 2
    assert len(vt.slept) == slept                               # no wait_slot sleep, no second attempt


async def test_a_429_concurrency_without_a_timeout_before_it_still_waits_and_retries(vt):
    state = {"n": 0}

    def fn(body):
        state["n"] += 1
        return real_429("concurrency") if state["n"] == 1 else chat_ok("ok")
    rc, _ = caller(vt, fn, MIDSIZE_MAX_SLOTS=0)
    r = await rc.chat(ChatRequest(M, MSG), ctx()[0])
    assert r.attempts == 2 and vt.slept[-1] == pytest.approx(2.0)


# ------------------------------------------------------------------ end to end (virtual time)
def test_the_429_after_a_timeout_does_not_open_the_breaker_by_itself_and_the_engine_falls_back():
    """BREAKER_TIMEOUT_THRESHOLD=3: the timeout alone does not open it; the 429 that follows (the server still
    counts the hung request) must neither count nor be retried: the explain is answered in fast mode."""
    async def main(loop):
        eng, sim, provider, sink = build(loop, BREAKER_TIMEOUT_THRESHOLD=3, MIDSIZE_MAX_SLOTS=0,
                                         INTERACTIVE_DEADLINE_S=500)
        async with eng:
            tl = Timeline(eng, loop)
            await tl.explain("e1", 0, EXPLAINS[0])             # times out at 40 s, ghost until 90 s
            assert tl.calls["e1"]["result"].degrade_reason == "midsize_unavailable"
            sim.cfg = sim.cfg.__class__(**{**sim.cfg.__dict__, "hang_midsize": False})   # healthy again ...
            sim.in_flight = 2                                  # ... but the server still counts two hung requests
            await tl.explain("e2", 41, EXPLAINS[1])
            r = tl.calls["e2"]["result"]
            assert r.mode == "fast_extractive" and r.degrade_reason == "midsize_unavailable"
            assert tl.took("e2") < 1                           # one refused attempt, no 2 s wait_slot
            assert eng.router.breakers[M].closed               # a 429 is not a timeout: not counted
    run_virtual(main)
