"""Retry rules built from the observed FelCloud 429s (v1.1)."""
import random

import httpx
import pytest

from config import ModelClass
from engine import Engine
from gatekeeper.cache import ExactCache
from gatekeeper.client import ChatRequest, LLMClient
from gatekeeper.errors import ConcurrencyLimited, QuotaExhausted
from gatekeeper.limiter import Limiter
from gatekeeper.retry import CallContext, RetryingCaller, RetryPolicy
from metrics import MemorySink
from tests.conftest import FakeProvider, FnProvider, chat_ok, make_settings
from tests.test_errors import REAL_429, real_429

MSG = ({"role": "user", "content": "explain limits"},)
REQ = ChatRequest(ModelClass.COMPACT, MSG)


def caller(vt, transport, **env):
    s = make_settings(**env)
    sink = MemorySink()
    rc = RetryingCaller(LLMClient(s, transport), Limiter(s, vt.clock, vt.sleep), sink,
                        RetryPolicy.from_settings(s), s.input_token_budget, vt.clock, vt.sleep, random.Random(1))
    return rc, sink


def ctx(priority):
    ev = []
    return CallContext(task="hint", priority=priority, on_event=lambda n, d: ev.append(n)), ev


def quota_429(retry_after):
    r = REAL_429["quota"]
    return httpx.Response(429, headers={**r["headers"], "retry-after": str(retry_after)}, content=r["body"].encode())


# ------------------------------------------------------------------------------ quota
async def test_interactive_quota_delay_above_the_cap_is_raised_with_the_delay_and_the_limiter_stays_blocked(vt):
    fake = FakeProvider([real_429("quota")])
    rc, sink = caller(vt, fake)
    c, ev = ctx("INTERACTIVE")
    with pytest.raises(QuotaExhausted) as ei:
        await rc.chat(REQ, c)
    assert ei.value.retry_after_s == 60 and len(fake.requests) == 1          # no second request
    assert [(x.outcome, x.action) for x in sink.records] == [("quota_exhausted", "raise")]
    assert ev[-1] == "gave_up"
    assert rc.limiter.wait_time("chat") == pytest.approx(60, abs=0.01)        # blocked until the delay expires
    t0 = vt.t
    await rc.chat(REQ)                                                        # the next caller waits it out locally
    assert vt.t - t0 == pytest.approx(60, abs=0.01) and len(fake.requests) == 2


async def test_background_waits_a_60_second_quota_delay_and_retries(vt):
    fake = FakeProvider([real_429("quota"), chat_ok("done")])
    rc, sink = caller(vt, fake)
    c, ev = ctx("BACKGROUND")
    t0 = vt.t
    r = await rc.chat(REQ, c)
    assert (r.text, r.attempts) == ("done", 2)
    assert vt.t - t0 == pytest.approx(60, abs=0.01)
    assert [(x.outcome, x.action) for x in sink.records] == [("quota_exhausted", "wait_stated_delay"), ("ok", None)]
    assert "wait_stated_delay" in ev and {q["body"]["model"] for q in fake.requests} == {"compact-a"}


@pytest.mark.parametrize("priority,retry_after,env,waits", [
    ("INTERACTIVE", 30, {}, True),                                  # at the cap: waits
    ("INTERACTIVE", 31, {}, False),
    ("INTERACTIVE", 60, {"MAX_RETRY_WAIT_S": 60}, True),
    ("BACKGROUND", 65, {}, True),                                   # at the background cap: waits
    ("BACKGROUND", 66, {}, False),
    ("BACKGROUND", 70, {"MAX_RETRY_WAIT_S_BACKGROUND": 80}, True),
    (None, 60, {}, False),                                          # no priority behaves as INTERACTIVE
])
async def test_wait_or_raise_by_priority_and_cap(vt, priority, retry_after, env, waits):
    fake = FakeProvider([quota_429(retry_after), chat_ok()])
    rc, _ = caller(vt, fake, **env)
    c, _ = ctx(priority)
    if waits:
        assert (await rc.chat(REQ, c)).attempts == 2
    else:
        with pytest.raises(QuotaExhausted):
            await rc.chat(REQ, c)
        assert len(fake.requests) == 1


async def test_limit_resets_at_in_the_body_is_not_the_delay(vt):
    """The body says the limit resets at an absurd time; only retry-after counts."""
    r = REAL_429["quota"]
    body = r["body"].replace("2026-10-26 17:03:34 UTC", "2099-01-01 00:00:00 UTC").encode()
    fake = FakeProvider([httpx.Response(429, headers={"retry-after": "20"}, content=body), chat_ok()])
    rc, _ = caller(vt, fake)
    t0 = vt.t
    await rc.chat(REQ, ctx("INTERACTIVE")[0])
    assert vt.t - t0 == pytest.approx(20, abs=0.01)


async def test_quota_without_a_retry_after_header_backs_off_instead_of_guessing(vt):
    bare = httpx.Response(429, json={"error": {"message": "Rate limit exceeded", "type": "throttling_error"}})
    fake = FakeProvider([bare, chat_ok()])
    rc, _ = caller(vt, fake)
    r = await rc.chat(REQ, ctx("INTERACTIVE")[0])
    assert r.attempts == 2 and vt.t - 1000.0 <= 0.5                          # first backoff step: uniform(0, 0.5)


# ------------------------------------------------------------------------------ concurrency
@pytest.mark.parametrize("retry_after,env,expected", [
    (60, {}, 2.0),                                                  # the real shape: states 60 s, waits 2 s
    (60, {"CONCURRENCY_RETRY_WAIT_S": 5}, 5.0),
    (1, {}, 1.0),                                                   # a shorter stated delay is honoured
])
async def test_concurrency_limit_waits_min_of_stated_delay_and_cap(vt, retry_after, env, expected):
    r = REAL_429["concurrency"]
    conc = httpx.Response(429, headers={**r["headers"], "retry-after": str(retry_after)}, content=r["body"].encode())
    fake = FakeProvider([conc, chat_ok()])
    rc, sink = caller(vt, fake, **env)
    t0 = vt.t
    res = await rc.chat(REQ, ctx("INTERACTIVE")[0])
    assert res.attempts == 2 and vt.t - t0 == pytest.approx(expected, abs=0.01)
    assert sink.records[0].action == "wait_slot"


async def test_concurrency_refusals_stop_at_the_attempt_limit(vt):
    fake = FakeProvider([real_429("concurrency")] * 10)
    rc, _ = caller(vt, fake, MAX_ATTEMPTS=3)
    with pytest.raises(ConcurrencyLimited):
        await rc.chat(REQ, ctx("INTERACTIVE")[0])
    assert len(fake.requests) == 3


# ------------------------------------------------------------------------------ engine: eta
async def test_engine_degrades_interactive_quota_with_eta_equal_to_the_remaining_delay(vt):
    s = make_settings()
    fake = FnProvider(lambda body: real_429("quota"))
    eng = Engine.create(s, client=LLMClient(s, fake), sink=MemorySink(), cache=ExactCache(":memory:", s.cache_ttl_s),
                        clock=vt.clock, sleep=vt.sleep, rng=random.Random(1))
    r = await eng.run("hint", {"problem": "Calculer la dérivée de f(x) = 7x^3 - 2x + 9", "attempt": "21x"},
                      {"language": "FRENCH"})
    assert r.degraded and r.degrade_reason == "quota_exhausted"
    assert r.eta_seconds == pytest.approx(60, abs=0.1)                       # the delay, not delay + queue estimate
    assert len(fake.requests) == 1
    vt.t += 25                                                                # 25 s later the same refusal is 35 s away
    assert eng.caller.limiter.wait_time("chat") == pytest.approx(35, abs=0.1)
