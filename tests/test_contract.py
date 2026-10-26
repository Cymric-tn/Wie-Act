"""The platform contract table, one test per row, through the whole stack:
RetryingCaller -> Limiter -> LLMClient -> ConstraintSimulator -> fake provider.

  | situation           | service response                 | required handling                     |
  |---------------------|----------------------------------|---------------------------------------|
  | quota exhausted     | quota-exhausted error + delay    | wait the stated delay, then retry     |
  | class saturated     | 503 + Retry-After                | retry at intervals, then fall back    |
  | beyond a ceiling    | 400 naming the ceiling           | fix the request, retry once           |
  | normal              | 200 + quota headers              | read remaining quota, pace            |
"""
import random

import pytest

from config import ModelClass
from gatekeeper.client import ChatRequest, LLMClient
from gatekeeper.errors import CeilingExceeded, FatalError, ModelSaturated
from gatekeeper.limiter import Limiter
from gatekeeper.retry import RetryingCaller, RetryPolicy
from metrics import MemorySink
from simulator.transport import ConstraintSimulator
from tests.conftest import FakeProvider, error, make_settings

MSG = ({"role": "user", "content": "what is a derivative?"},)
# local limiter generous, so the simulated provider is the one that refuses
LOOSE = {"RATE_LIMIT_PER_MIN": 6000, "RATE_BURST": 100, "SIMULATE_CONSTRAINTS": "true"}


def stack(vt, inner=None, **env):
    s = make_settings(**{**LOOSE, **env})
    fake = inner or FakeProvider()
    sim = ConstraintSimulator(fake, s, vt.clock, vt.sleep)
    sink = MemorySink()
    rc = RetryingCaller(LLMClient(s, sim), Limiter(s, vt.clock, vt.sleep), sink,
                        RetryPolicy.from_settings(s), s.input_token_budget, vt.clock, vt.sleep, random.Random(1))
    return rc, sim, fake, sink


async def test_row_normal_200_quota_headers_read_and_paced(vt):
    rc, sim, _, sink = stack(vt, SIM_BURST=5)
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert r.attempts == 1 and r.quota.remaining_requests == 4 and r.quota.limit_requests == 5
    assert sink.records[-1].quota_remaining == 4
    assert rc.limiter.buckets["chat"].tokens <= 4          # local bucket lowered to the server's count


async def test_row_quota_exhausted_waits_stated_delay_then_retries(vt):
    """Another client of the same key spent the provider's quota: our limiter cannot know, so the refusal arrives
    (a client that saw the quota headers would have waited locally instead: see test_limiter)."""
    rc, sim, fake, sink = stack(vt, SIM_RATE_PER_MIN=6, SIM_BURST=1)
    other = RetryingCaller(LLMClient(rc.client.settings, sim), Limiter(rc.client.settings, vt.clock, vt.sleep),
                           MemorySink(), RetryPolicy.from_settings(rc.client.settings),
                           rc.input_token_budget, vt.clock, vt.sleep, random.Random(2))
    await other.chat(ChatRequest(ModelClass.COMPACT, MSG))
    t0 = vt.t
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert r.attempts == 2
    assert vt.t - t0 == pytest.approx(10, abs=0.5)          # 6/min -> provider says 10 s
    assert sim.stats.refusals == {"429:quota_exhausted": 1}
    assert [(x.outcome, x.action) for x in sink.records] == [
        ("quota_exhausted", "wait_stated_delay"), ("ok", None)]
    assert {q["body"]["model"] for q in fake.requests} == {"compact-a"}   # same model, no hopping


@pytest.mark.parametrize("dialect,status", [("annex", 503), ("litellm", 429)])
async def test_row_saturated_retries_at_interval_then_surfaces(vt, dialect, status):
    rc, sim, _, sink = stack(vt, SIM_SATURATION_PROB=1, SIM_SATURATION_DIALECT=dialect, SATURATION_RETRIES=1)
    t0 = vt.t
    with pytest.raises(ModelSaturated) as ei:
        await rc.chat(ChatRequest(ModelClass.MIDSIZE, MSG))
    assert ei.value.attempts == 2
    assert [x.status for x in sink.records] == [status, status]
    assert [x.action for x in sink.records] == ["retry_after_interval", "raise"]
    assert 5 <= vt.t - t0 <= 15                              # waited the stated Retry-After once


async def test_row_ceiling_max_tokens_trimmed_then_retried_once(vt):
    rc, sim, fake, sink = stack(vt, SIM_MAX_TOKENS_CEILING=128, MAX_TOKENS_COMPACT=300)
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert [q["body"]["max_tokens"] for q in fake.requests] == [128]   # only the fixed one reached it
    assert r.attempts == 2 and r.trimmed
    assert [(x.status, x.action) for x in sink.records] == [(400, "fix_request"), (200, None)]


async def test_row_ceiling_prompt_trimmed_then_retried_once(vt):
    rc, sim, fake, sink = stack(vt, SIM_PROMPT_TOKENS_CEILING=100)
    big = ({"role": "system", "content": "be brief"}, {"role": "user", "content": "x" * 1000})
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, big))
    [sent] = fake.requests
    assert sent["body"]["messages"][0]["content"] == "be brief"
    assert sum(len(m["content"]) for m in sent["body"]["messages"]) // 4 <= 100
    assert r.attempts == 2 and r.trimmed


async def test_ceiling_that_cannot_be_fixed_is_not_retried_again(vt):
    rc, sim, fake, sink = stack(vt, SIM_MAX_TOKENS_CEILING=0, MAX_TOKENS_COMPACT=20)
    with pytest.raises(CeilingExceeded):
        await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert fake.requests == [] and [x.action for x in sink.records] == ["raise"]   # 20 -> 10 < minimum


async def test_400_that_names_no_ceiling_is_never_retried(vt):
    inner = FakeProvider([error(400, "invalid_request_error", "unknown model compact-a")])
    rc, sim, fake, sink = stack(vt, inner=inner)
    with pytest.raises(FatalError):
        await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert len(fake.requests) == 1 and sink.records[0].action == "raise"
