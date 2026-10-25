import asyncio
import random

import httpx
import pytest

from config import ModelClass
from gatekeeper.client import ChatRequest, EmbedRequest, LLMClient
from gatekeeper.errors import CeilingExceeded, FatalError, ModelSaturated, QuotaExhausted, TransientError
from gatekeeper.limiter import Limiter
from gatekeeper.retry import CallContext, RetryingCaller, RetryPolicy, default_fix_request
from metrics import MemorySink
from simulator.transport import ConstraintSimulator
from tests.conftest import FakeProvider, chat_ok, error, make_settings

MSG = ({"role": "user", "content": "explain limits"},)


def caller(vt, transport, **env):
    s = make_settings(**env)
    sink = MemorySink()
    rc = RetryingCaller(LLMClient(s, transport), Limiter(s, vt.clock, vt.sleep), sink,
                        RetryPolicy.from_settings(s), s.input_token_budget, vt.clock, vt.sleep, random.Random(1))
    return rc, sink


def events_ctx():
    ev = []
    return CallContext(task="hint", priority="INTERACTIVE", on_event=lambda n, d: ev.append(n)), ev


async def test_normal_call_one_record(vt):
    fake = FakeProvider([chat_ok("hi", 12, 4)])
    rc, sink = caller(vt, fake)
    ctx, ev = events_ctx()
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, MSG), ctx)
    assert (r.text, r.model, r.attempts, r.tokens_in, r.tokens_out) == ("hi", "compact-a", 1, 12, 4)
    [rec] = sink.records
    assert (rec.outcome, rec.task, rec.priority, rec.model_class, rec.status) == (
        "ok", "hint", "INTERACTIVE", "compact", 200)
    assert ev == ["queued", "generating"]


async def test_quota_waits_stated_delay_and_retries_same_model(vt):
    fake = FakeProvider([error(429, "quota_exhausted", "Team quota exhausted", {"Retry-After": "12"}), chat_ok()])
    rc, sink = caller(vt, fake)
    t0 = vt.t
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert r.attempts == 2
    assert vt.t - t0 == pytest.approx(12, abs=0.01)                 # exactly the stated delay
    assert [q["body"]["model"] for q in fake.requests] == ["compact-a", "compact-a"]
    assert [(x.outcome, x.action) for x in sink.records] == [("quota_exhausted", "wait_stated_delay"), ("ok", None)]
    assert sink.records[1].wait_ms == pytest.approx(12000, abs=10)


async def test_quota_delay_too_long_raises_with_delay(vt):
    fake = FakeProvider([error(429, "quota_exhausted", "Team quota exhausted", {"Retry-After": "90"})])
    rc, sink = caller(vt, fake, MAX_RETRY_WAIT_S=30)
    with pytest.raises(QuotaExhausted) as ei:
        await rc.chat(ChatRequest(ModelClass.MIDSIZE, MSG))
    assert ei.value.retry_after_s == 90 and ei.value.attempts == 1
    assert len(fake.requests) == 1                                   # no model hopping on quota
    assert sink.records[0].action == "raise"


async def test_quota_without_delay_uses_jittered_backoff(vt):
    fake = FakeProvider([error(429, "rate_limit", "Rate limit exceeded"), chat_ok()])
    rc, _ = caller(vt, fake)
    t0 = vt.t
    await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert 0 <= vt.t - t0 <= 0.5                                     # uniform(0, base)


async def test_saturation_retries_once_then_raises_for_router(vt):
    sat = lambda: error(503, "model_saturated", "busy", {"Retry-After": "7"})
    fake = FakeProvider([sat(), sat()])
    rc, sink = caller(vt, fake, SATURATION_RETRIES=1)
    ctx, ev = events_ctx()
    t0 = vt.t
    with pytest.raises(ModelSaturated) as ei:
        await rc.chat(ChatRequest(ModelClass.MIDSIZE, MSG), ctx)
    assert ei.value.attempts == 2
    assert vt.t - t0 == pytest.approx(7)
    assert [x.action for x in sink.records] == ["retry_after_interval", "raise"]
    assert "retry_after_interval" in ev and ev[-1] == "gave_up"


async def test_saturation_recovers(vt):
    fake = FakeProvider([error(503, "model_saturated", "busy", {"Retry-After": "5"}), chat_ok("deep")])
    rc, _ = caller(vt, fake)
    assert (await rc.chat(ChatRequest(ModelClass.MIDSIZE, MSG))).text == "deep"


async def test_ceiling_fixed_once_then_retried(vt):
    fake = FakeProvider([error(400, "invalid_request_error", "max_tokens 300 exceeds ceiling 128"), chat_ok()])
    rc, sink = caller(vt, fake, MAX_TOKENS_COMPACT=300)
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert [q["body"]["max_tokens"] for q in fake.requests] == [300, 128]
    assert r.trimmed and r.request.max_tokens == 128
    assert sink.records[0].action == "fix_request"


async def test_ceiling_only_fixed_once(vt):
    ceil = lambda: error(400, "invalid_request_error", "max_tokens exceeds ceiling 128")
    fake = FakeProvider([ceil(), ceil()])
    rc, _ = caller(vt, fake)
    with pytest.raises(CeilingExceeded):
        await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert len(fake.requests) == 2


async def test_input_over_budget_trimmed_before_sending(vt):
    fake = FakeProvider()
    rc, _ = caller(vt, fake, INPUT_TOKEN_BUDGET=200)
    ctx, ev = events_ctx()
    big = ({"role": "system", "content": "be brief"}, {"role": "user", "content": "context " * 500})
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, big), ctx)
    sent = fake.requests[0]["body"]["messages"]
    assert ev[0] == "trimming_input" and r.trimmed
    assert sent[0]["content"] == "be brief"                         # system prompt untouched
    assert sum(len(m["content"]) for m in sent) // 4 < 200


async def test_input_unfixable_raises_without_calling(vt):
    fake = FakeProvider()
    rc, sink = caller(vt, fake, INPUT_TOKEN_BUDGET=20)
    with pytest.raises(CeilingExceeded):
        await rc.chat(ChatRequest(ModelClass.COMPACT, ({"role": "system", "content": "s" * 500},)))
    assert fake.requests == [] and sink.records[0].outcome == "ceiling"


async def test_transient_backoff_then_success(vt):
    fake = FakeProvider([httpx.Response(500), httpx.ConnectError("down"), chat_ok()])
    rc, sink = caller(vt, fake)
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert r.attempts == 3
    assert [x.action for x in sink.records[:2]] == ["backoff", "backoff"]
    assert all(0 <= s <= 8 for s in vt.slept)


async def test_transient_exhausts_attempts(vt):
    fake = FakeProvider([httpx.Response(502) for _ in range(4)])
    rc, _ = caller(vt, fake, MAX_ATTEMPTS=4)
    with pytest.raises(TransientError) as ei:
        await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert ei.value.attempts == 4 and len(fake.requests) == 4


async def test_fatal_never_retried(vt):
    fake = FakeProvider([error(401, "auth_error", "bad key")])
    rc, _ = caller(vt, fake)
    with pytest.raises(FatalError):
        await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert len(fake.requests) == 1


async def test_embed_retries_saturation_and_records_kind(vt):
    fake = FakeProvider([error(503, "model_saturated", "busy", {"Retry-After": "2"})])
    rc, sink = caller(vt, fake)
    r = await rc.embed(EmbedRequest(("a", "b", "c")))
    assert len(r.vectors) == 3 and r.attempts == 2
    assert {x.kind for x in sink.records} == {"embed"}


def test_default_fixer_halves_when_limit_unknown():
    req = ChatRequest(ModelClass.COMPACT, MSG, max_tokens=300, model="m")
    assert default_fix_request(req, CeilingExceeded(ceiling="unknown")).max_tokens == 150
    assert default_fix_request(req.with_(max_tokens=20), CeilingExceeded(ceiling="unknown")) is None


# ------------------------------------------------------------------ end to end through the simulator
async def test_local_limiter_keeps_simulated_provider_from_refusing(vt):
    """Same limits on both sides: 30 requests -> zero quota refusals, paced by our bucket."""
    s = make_settings(SIMULATE_CONSTRAINTS="true")
    sim = ConstraintSimulator(FakeProvider(), s, vt.clock, vt.sleep)
    sink = MemorySink()
    rc = RetryingCaller(LLMClient(s, sim), Limiter(s, vt.clock, vt.sleep), sink, RetryPolicy.from_settings(s),
                        s.input_token_budget, vt.clock, vt.sleep, random.Random(1))
    t0 = vt.t
    rs = await asyncio.gather(*[rc.chat(ChatRequest(ModelClass.COMPACT, MSG)) for _ in range(30)])
    assert len(rs) == 30
    assert not sim.stats.refusals                                    # never tripped the provider
    assert vt.t - t0 == pytest.approx(30, abs=0.5)                   # 20 burst + 10 x 3 s refill


@pytest.mark.parametrize("dialect", ["annex", "litellm"])
async def test_simulated_saturation_surfaces_for_fallback(vt, dialect):
    s = make_settings(SIMULATE_CONSTRAINTS="true", SIM_SATURATION_PROB=1, SIM_SATURATION_DIALECT=dialect)
    sim = ConstraintSimulator(FakeProvider(), s, vt.clock, vt.sleep)
    rc = RetryingCaller(LLMClient(s, sim), Limiter(s, vt.clock, vt.sleep), MemorySink(),
                        RetryPolicy.from_settings(s), s.input_token_budget, vt.clock, vt.sleep)
    with pytest.raises(ModelSaturated):
        await rc.chat(ChatRequest(ModelClass.MIDSIZE, MSG))
    assert (await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))).model == "compact-a"


async def test_compact_saturation_retries_compact_max_retries_honoring_retry_after(vt):
    sat = lambda: error(503, "model_saturated", "busy", {"Retry-After": "2"})
    fake = FakeProvider([sat() for _ in range(10)])
    rc, sink = caller(vt, fake, COMPACT_MAX_RETRIES=3)
    t0 = vt.t
    with pytest.raises(ModelSaturated) as ei:
        await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert ei.value.attempts == 4 and len(fake.requests) == 4        # 1 + COMPACT_MAX_RETRIES
    assert vt.t - t0 == pytest.approx(6)                              # Retry-After honored each time
    assert [x.action for x in sink.records] == ["retry_after_interval"] * 3 + ["raise"]


async def test_compact_saturation_recovers_within_budget(vt):
    sat = lambda: error(429, "all_deployments_in_cooldown", "All deployments are in cooldown", {"Retry-After": "5"})
    fake = FakeProvider([sat(), sat(), chat_ok("back")])
    rc, _ = caller(vt, fake, COMPACT_MAX_RETRIES=3)
    assert (await rc.chat(ChatRequest(ModelClass.COMPACT, MSG))).text == "back"
