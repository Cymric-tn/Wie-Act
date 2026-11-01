"""Simulator hang mode: a model class that accepts a request and never answers (or answers after N s)."""
import asyncio

import httpx
import pytest

from config import ModelClass
from gatekeeper.client import ChatRequest, LLMClient
from gatekeeper.errors import ConcurrencyLimited, classify_response
from simulator.profiles import PROFILES, env_overrides
from simulator.transport import ConstraintSimulator
from tests.conftest import FakeProvider, make_settings
from tests.vloop import run_virtual

MSG = ({"role": "user", "content": "hello"},)


def rig(loop, **env):
    s = make_settings(SIMULATE_CONSTRAINTS="true", SIM_LATENCY_SCALE=0, **env)
    sim = ConstraintSimulator(FakeProvider(), s, clock=loop.time, sleep=asyncio.sleep)
    return LLMClient(s, sim), sim


def test_a_hung_class_times_out_at_the_client_read_timeout_and_compact_is_unaffected():
    async def main(loop):
        c, sim = rig(loop, SIM_HANG_MIDSIZE="true", REQUEST_TIMEOUT_S=40)
        t0 = loop.time()
        with pytest.raises(httpx.ReadTimeout):
            await c.chat(ChatRequest(ModelClass.MIDSIZE, MSG))
        assert loop.time() - t0 == pytest.approx(40)
        r = await c.chat(ChatRequest(ModelClass.COMPACT, MSG))
        assert r.status_code == 200
        await c.aclose()
    run_virtual(main)


def test_the_server_keeps_counting_a_hung_request_until_the_server_timeout():
    async def main(loop):
        c, sim = rig(loop, SIM_HANG_MIDSIZE="true", REQUEST_TIMEOUT_S=40, MAX_CONCURRENT=2)
        for _ in range(2):
            with pytest.raises(httpx.ReadTimeout):
                await c.chat(ChatRequest(ModelClass.MIDSIZE, MSG))
        assert sim.in_flight == 2                     # the client left at 40 s, the server counts until 90 s
        e = classify_response(await c.chat(ChatRequest(ModelClass.COMPACT, MSG)))
        assert isinstance(e, ConcurrencyLimited)      # ... so even compact is refused: finding d of the outage report
        await asyncio.sleep(90)
        assert sim.in_flight == 0
        assert (await c.chat(ChatRequest(ModelClass.COMPACT, MSG))).status_code == 200
        await c.aclose()
    run_virtual(main)


def test_hang_s_makes_the_answer_arrive_late_or_not_at_all():
    async def main(loop):
        c, sim = rig(loop, SIM_HANG_MIDSIZE="true", SIM_HANG_S=25, REQUEST_TIMEOUT_S=40)
        t0 = loop.time()
        r = await c.chat(ChatRequest(ModelClass.MIDSIZE, MSG))        # 25 s < 40 s: it arrives
        assert r.status_code == 200 and loop.time() - t0 == pytest.approx(25)
        c2, _ = rig(loop, SIM_HANG_MIDSIZE="true", SIM_HANG_S=25, REQUEST_TIMEOUT_S=10)
        with pytest.raises(httpx.ReadTimeout):                        # 25 s > 10 s: timeout
            await c2.chat(ChatRequest(ModelClass.MIDSIZE, MSG))
        await c.aclose()
        await c2.aclose()
    run_virtual(main)


def test_hang_compact_and_the_midsize_down_profile():
    async def main(loop):
        c, sim = rig(loop, SIM_HANG_COMPACT="true", REQUEST_TIMEOUT_S=5)
        with pytest.raises(httpx.ReadTimeout):
            await c.chat(ChatRequest(ModelClass.COMPACT, MSG))
        assert (await c.chat(ChatRequest(ModelClass.MIDSIZE, MSG))).status_code == 200
        await c.aclose()
    run_virtual(main)
    p = PROFILES["midsize_down"]
    assert p["hang_midsize"] is True and p["saturation_prob"] == 0 and "hang_s" not in p
    s = make_settings(**env_overrides("midsize_down"))
    assert s.sim.hang_midsize is True and s.sim.hang_s is None and not s.sim.hang_compact


def test_apply_profile_switches_the_simulator_to_midsize_down():
    async def main(loop):
        c, sim = rig(loop, REQUEST_TIMEOUT_S=5)
        assert (await c.chat(ChatRequest(ModelClass.MIDSIZE, MSG))).status_code == 200
        sim.apply_profile("midsize_down")
        with pytest.raises(httpx.ReadTimeout):
            await c.chat(ChatRequest(ModelClass.MIDSIZE, MSG))
        await c.aclose()
    run_virtual(main)
