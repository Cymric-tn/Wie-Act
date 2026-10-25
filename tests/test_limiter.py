import asyncio

import pytest

from gatekeeper.errors import QuotaInfo
from gatekeeper.limiter import Limiter, TokenBucket
from tests.conftest import make_settings


def test_burst_then_refusal_with_wait(vt):
    b = TokenBucket(20, 20, vt.clock)
    assert all(b.try_take() == 0 for _ in range(20))
    assert b.try_take() == pytest.approx(3.0)          # 20/min -> one token every 3 s


def test_continuous_refill(vt):
    b = TokenBucket(20, 20, vt.clock)
    for _ in range(20):
        b.try_take()
    vt.t += 1.5
    assert b.tokens == pytest.approx(0.5)               # partial refill, not per-minute windows
    vt.t += 1.5
    assert b.try_take() == 0


def test_refill_capped_at_burst(vt):
    b = TokenBucket(20, 5, vt.clock)
    vt.t += 3600
    assert b.tokens == 5


def test_stated_delay_blocks_then_exactly_one_token(vt):
    b = TokenBucket(20, 20, vt.clock)
    b.block_for(10)
    assert b.try_take() == pytest.approx(10)
    vt.t += 9.9
    assert b.try_take() > 0
    vt.t += 0.1
    assert b.try_take() == 0                            # retry right when the provider said


def test_server_remaining_only_lowers(vt):
    b = TokenBucket(20, 20, vt.clock)
    b.sync_remaining(3)
    assert b.tokens == pytest.approx(3)
    b.sync_remaining(15)
    assert b.tokens == pytest.approx(3)
    b.sync_remaining(None)
    assert b.tokens == pytest.approx(3)


async def test_acquire_waits_in_virtual_time(vt):
    b = TokenBucket(60, 1, vt.clock)
    assert await b.acquire(vt.sleep) == 0
    waited = await b.acquire(vt.sleep)
    assert waited == pytest.approx(1.0)


async def test_acquire_reports_wait_once(vt):
    b = TokenBucket(60, 1, vt.clock)
    await b.acquire(vt.sleep)
    seen = []
    await b.acquire(vt.sleep, seen.append)
    assert seen == [pytest.approx(1.0)]


async def test_semaphore_caps_concurrency_at_two():
    lim = Limiter(make_settings(RATE_LIMIT_PER_MIN=6000, RATE_BURST=100, MAX_CONCURRENT=2))
    peak = 0

    async def job():
        nonlocal peak
        async with lim.slot("chat"):
            peak = max(peak, lim.in_flight)
            await asyncio.sleep(0.01)

    await asyncio.gather(*[job() for _ in range(10)])
    assert peak == 2 and lim.in_flight == 0


async def test_chat_and_embed_buckets_are_separate(vt):
    lim = Limiter(make_settings(RATE_LIMIT_PER_MIN=1, RATE_BURST=1), vt.clock, vt.sleep)
    async with lim.slot("chat"):
        pass
    assert lim.buckets["chat"].wait_time() > 0
    async with lim.slot("embed") as info:                # embeddings unaffected
        assert info.rate_wait_s == 0


async def test_rate_wait_does_not_hold_a_concurrency_slot(vt):
    lim = Limiter(make_settings(RATE_LIMIT_PER_MIN=1, RATE_BURST=1, MAX_CONCURRENT=1), vt.clock, vt.sleep)
    lim.block("chat", 30)
    waiter = asyncio.create_task(_enter(lim, "chat"))
    await asyncio.sleep(0)
    assert not lim.concurrency.locked()                  # still free for others while chat waits
    await waiter


async def _enter(lim, kind):
    async with lim.slot(kind):
        pass


def test_observe_feeds_server_quota_into_bucket():
    lim = Limiter(make_settings())
    lim.observe("chat", QuotaInfo(remaining_requests=0))
    assert lim.buckets["chat"].wait_time() > 0


async def test_30_requests_hold_concurrency_and_rate():
    """Fire 30 at once through the caller: never more than 2 at the provider, burst of 20,
    then ~20/min. Scaled clock: 1 real second = 20 virtual seconds (test runs ~1.5 s)."""
    import random
    import time

    from config import ModelClass
    from gatekeeper.client import ChatRequest, LLMClient
    from gatekeeper.retry import RetryingCaller, RetryPolicy
    from metrics import MemorySink
    from tests.conftest import FakeProvider

    scale, t0 = 20.0, time.monotonic()
    clock = lambda: (time.monotonic() - t0) * scale
    sleep = lambda s: asyncio.sleep(max(0.0, s) / scale)

    class Timed(FakeProvider):
        sent: list[float] = []

        async def handle_async_request(self, request):
            self.sent.append(clock())
            return await super().handle_async_request(request)

    s = make_settings(RATE_LIMIT_PER_MIN=20, RATE_BURST=20, MAX_CONCURRENT=2)
    fake = Timed(delay=0.1 / scale)                          # 0.1 virtual s of provider latency
    rc = RetryingCaller(LLMClient(s, fake), Limiter(s, clock, sleep), MemorySink(),
                        RetryPolicy.from_settings(s), s.input_token_budget, clock, sleep, random.Random(1))
    msg = ({"role": "user", "content": "hi"},)
    await asyncio.gather(*[rc.chat(ChatRequest(ModelClass.COMPACT, msg)) for _ in range(30)])

    sent = sorted(fake.sent)
    assert len(sent) == 30 and fake.max_in_flight == 2
    assert sent[19] < 2.0                                    # burst reserve: 20 go at once (2 at a time)
    steady = 60 * (len(sent[20:]) - 1) / (sent[29] - sent[20])
    assert steady == pytest.approx(20, rel=0.1)              # then the sustained rate, ~20/min
    assert sent[29] == pytest.approx(30, abs=1.5)            # 10 extra tokens at 3 s each
    assert all(sum(t <= x < t + 60 for x in sent) <= 20 + 20 for t in sent)   # never > burst + rate/min


async def test_float_rounding_deficit_does_not_spin(vt):
    """Regression: server said remaining=0, refill stopped at 0.999999999999 tokens and the
    bucket asked for a 9e-15 s sleep, which never advances the clock (infinite loop)."""
    b = TokenBucket(6000, 100, vt.clock)
    vt.t = 1000.0
    b.sync_remaining(0)
    vt.t = 1000.01                                       # 0.01 s * 100/s = 0.99999999999909 in float
    assert b.wait_time() == 0 and b.try_take() == 0
