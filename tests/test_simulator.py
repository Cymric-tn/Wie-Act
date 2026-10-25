"""The simulator must reproduce the platform contract table, as seen through errors.py."""
import asyncio

import httpx
import pytest

from config import ModelClass
from gatekeeper.client import ChatRequest, EmbedRequest, LLMClient
from gatekeeper.errors import (CeilingExceeded, ConcurrencyLimited, ModelSaturated, QuotaExhausted,
                               classify_response, parse_quota)
from simulator.transport import ConstraintSimulator
from tests.conftest import FakeProvider, make_settings

MSG = ({"role": "user", "content": "hello"},)


def sim_client(vt=None, inner=None, **env):
    s = make_settings(SIMULATE_CONSTRAINTS="true", **env)
    kw = {"clock": vt.clock, "sleep": vt.sleep} if vt else {}
    sim = ConstraintSimulator(inner or FakeProvider(), s, **kw)
    return LLMClient(s, sim), sim


async def test_normal_200_carries_quota_headers(vt):
    c, sim = sim_client(vt)
    r = await c.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert r.status_code == 200 and classify_response(r) is None
    q = parse_quota(r.headers)
    assert q.limit_requests == 20 and q.remaining_requests == 19
    assert r.json()["choices"][0]["message"]["content"] == "reply from compact-a"
    assert sim.stats.calls["compact-a"] == 1


async def test_quota_exhausted_with_stated_delay(vt):
    c, sim = sim_client(vt, SIM_RATE_PER_MIN=6, SIM_BURST=2)
    for _ in range(2):
        assert (await c.chat(ChatRequest(ModelClass.COMPACT, MSG))).status_code == 200
    e = classify_response(await c.chat(ChatRequest(ModelClass.COMPACT, MSG)))
    assert isinstance(e, QuotaExhausted) and e.retry_after_s == pytest.approx(10, abs=1)
    vt.t += e.retry_after_s
    assert (await c.chat(ChatRequest(ModelClass.COMPACT, MSG))).status_code == 200


@pytest.mark.parametrize("dialect,status", [("annex", 503), ("litellm", 429)])
async def test_midsize_saturation_both_dialects(vt, dialect, status):
    c, _ = sim_client(vt, SIM_SATURATION_PROB=1, SIM_SATURATION_DIALECT=dialect)
    r = await c.chat(ChatRequest(ModelClass.MIDSIZE, MSG))
    e = classify_response(r)
    assert r.status_code == status
    assert isinstance(e, ModelSaturated) and 5 <= e.retry_after_s <= 15


async def test_compact_never_saturates(vt):
    c, _ = sim_client(vt, SIM_SATURATION_PROB=1)
    assert (await c.chat(ChatRequest(ModelClass.COMPACT, MSG))).status_code == 200


async def test_midsize_slots_saturate_under_parallel_load():
    c, _ = sim_client(inner=FakeProvider(delay=0.02), SIM_MIDSIZE_SLOTS=1)
    rs = await asyncio.gather(*[c.chat(ChatRequest(ModelClass.MIDSIZE, MSG)) for _ in range(2)])
    assert sorted(r.status_code for r in rs) == [200, 503]


async def test_max_tokens_ceiling_names_the_limit(vt):
    c, _ = sim_client(vt, SIM_MAX_TOKENS_CEILING=256)
    e = classify_response(await c.chat(ChatRequest(ModelClass.COMPACT, MSG, max_tokens=500)))
    assert isinstance(e, CeilingExceeded) and (e.ceiling, e.limit) == ("max_tokens", 256)


async def test_prompt_ceiling_names_the_limit(vt):
    c, _ = sim_client(vt, SIM_PROMPT_TOKENS_CEILING=100)
    big = ({"role": "user", "content": "x" * 1000},)
    e = classify_response(await c.chat(ChatRequest(ModelClass.COMPACT, big)))
    assert isinstance(e, CeilingExceeded) and (e.ceiling, e.limit) == ("prompt_tokens", 100)


async def test_concurrency_cap_enforced_by_provider():
    c, sim = sim_client(inner=FakeProvider(delay=0.02), SIM_MIDSIZE_SLOTS=0)
    rs = await asyncio.gather(*[c.chat(ChatRequest(ModelClass.COMPACT, MSG)) for _ in range(3)])
    errs = [classify_response(r) for r in rs]
    assert sum(e is None for e in errs) == 2
    assert sum(isinstance(e, ConcurrencyLimited) for e in errs) == 1
    assert sim.stats.max_in_flight == 2


async def test_embeddings_have_their_own_quota(vt):
    c, _ = sim_client(vt, SIM_RATE_PER_MIN=1, SIM_BURST=1)
    await c.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert isinstance(classify_response(await c.chat(ChatRequest(ModelClass.COMPACT, MSG))), QuotaExhausted)
    r = await c.embed(EmbedRequest(("a", "b")))
    assert r.status_code == 200 and len(r.json()["data"]) == 2


async def test_per_class_latency_is_added(vt):
    c, _ = sim_client(vt, SIM_LATENCY_SCALE=1, SIM_LATENCY_COMPACT_S="2,6", SIM_LATENCY_MIDSIZE_S="8,30")
    await c.chat(ChatRequest(ModelClass.COMPACT, MSG))
    await c.chat(ChatRequest(ModelClass.MIDSIZE, MSG))
    assert 2 <= vt.slept[0] <= 6 and 8 <= vt.slept[1] <= 30


async def test_forced_faults_for_demo(vt):
    c, sim = sim_client(vt)
    sim.force = "transient"
    assert (await c.chat(ChatRequest(ModelClass.COMPACT, MSG))).status_code == 500
    sim.force = None
    assert (await c.chat(ChatRequest(ModelClass.COMPACT, MSG))).status_code == 200
    assert sim.stats.refusals["500:server_error"] == 1


async def test_gzip_upstream_body_is_not_decoded_twice(vt):
    import gzip, json
    payload = gzip.compress(json.dumps({"choices": [{"message": {"content": "z"}}], "usage": {}}).encode())

    class Gz(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, content=payload, headers={"content-encoding": "gzip"})

    c, _ = sim_client(vt, inner=Gz())
    r = await c.chat(ChatRequest(ModelClass.COMPACT, MSG))
    assert r.json()["choices"][0]["message"]["content"] == "z"
