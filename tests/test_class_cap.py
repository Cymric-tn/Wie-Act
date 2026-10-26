"""v1.2 step 6: mid-size may hold at most MIDSIZE_MAX_SLOTS concurrency slots (ghosts included), so compact always
has one. Concurrent timelines, so these run on the virtual-time loop."""
import asyncio

import pytest

from config import ModelClass
from engine import Priority
from gatekeeper.client import ChatRequest
from gatekeeper.retry import CallContext
from tests.outage_harness import CLASSIFIES, EXPLAINS, Timeline, build
from tests.vloop import run_virtual

M, C = ModelClass.MIDSIZE, ModelClass.COMPACT
MSG = ({"role": "user", "content": "hi"},)


async def timed(loop, eng, cls, label, log, at=0.0, priority="INTERACTIVE"):
    await asyncio.sleep(at)
    t0 = loop.time()
    await eng.caller.chat(ChatRequest(cls, MSG), CallContext(task=label, priority=priority))
    log[label] = (t0, loop.time())


def test_midsize_holds_one_slot_and_compact_is_never_blocked_behind_it():
    async def main(loop):
        eng, sim, provider, sink = build(loop, SIM_HANG_MIDSIZE="false", SIM_LATENCY_MIDSIZE_S="30,30")
        async with eng:
            t0, log = loop.time(), {}
            await asyncio.gather(timed(loop, eng, M, "mid1", log), timed(loop, eng, M, "mid2", log),
                                 *(timed(loop, eng, C, f"c{i}", log) for i in (1, 2, 3)))
            took = {k: round(v[1] - t0, 1) for k, v in log.items()}
            assert took["mid1"] == 30 and took["mid2"] == 60            # one at a time: the second waits for the cap
            assert [took[f"c{i}"] for i in (1, 2, 3)] == [2, 4, 6]       # compact: on the reserved slot, in order
            assert sim.stats.max_in_flight <= 2
    run_virtual(main)


def test_without_a_cap_two_midsize_requests_run_together():
    async def main(loop):
        eng, *_ = build(loop, SIM_HANG_MIDSIZE="false", SIM_LATENCY_MIDSIZE_S="30,30", MIDSIZE_MAX_SLOTS=0)
        async with eng:
            t0, log = loop.time(), {}
            await asyncio.gather(timed(loop, eng, M, "mid1", log), timed(loop, eng, M, "mid2", log))
            assert round(log["mid1"][1] - t0) == 30 and round(log["mid2"][1] - t0) == 30
    run_virtual(main)


def test_a_better_priority_compact_waiter_is_served_before_a_blocked_midsize_one():
    async def main(loop):
        eng, *_ = build(loop, SIM_HANG_MIDSIZE="false", SIM_LATENCY_MIDSIZE_S="30,30")
        async with eng:
            t0, log = loop.time(), {}
            await asyncio.gather(
                timed(loop, eng, M, "mid1", log),
                timed(loop, eng, M, "mid2", log, at=1),                              # interactive, but cap is full
                timed(loop, eng, C, "bg_compact", log, at=2, priority="BACKGROUND"))   # eligible: goes first
            assert round(log["bg_compact"][1] - t0) == 4 and round(log["mid2"][1] - t0) == 60
    run_virtual(main)


def test_two_hung_explains_do_not_block_classify():
    """The 26 Oct scenario with the breaker kept closed (BREAKER_TIMEOUT_THRESHOLD=99) so that only the slot cap
    protects compact: each classify costs one compact latency (2 s) plus its place in the queue of classifies."""
    async def main(loop):
        eng, sim, provider, sink = build(loop, BREAKER_TIMEOUT_THRESHOLD=99, INTERACTIVE_DEADLINE_S=1000)
        async with eng:
            tl = Timeline(eng, loop)
            tl.explain("explain1", 0, EXPLAINS[0])
            tl.explain("explain2", 0, EXPLAINS[1])
            for i in range(3):
                tl.classify(f"classify{i + 1}", 0, CLASSIFIES[i])
            tl.classify("classify4", 10, CLASSIFIES[3])
            await asyncio.gather(*tl.tasks)
            compact, queue_ahead = 2.0, {1: 0, 2: 1, 3: 2, 4: 0}          # classifies ahead of it on the compact slot
            took = {i: tl.took(f"classify{i}") for i in (1, 2, 3, 4)}
            for i, t in took.items():
                assert t <= compact * (queue_ahead[i] + 1) + 0.3, tl.table()
            assert max(took.values()) <= 3 * compact + 0.3                # p95 = one compact latency + its queue
            assert not any(tl.calls[f"classify{i}"]["result"].degraded for i in (1, 2, 3, 4))
    run_virtual(main)


def test_a_queued_midsize_explain_is_failed_at_once_when_the_breaker_opens():
    async def main(loop):
        eng, sim, provider, sink = build(loop, INTERACTIVE_DEADLINE_S=1000)
        async with eng:
            tl = Timeline(eng, loop)
            tl.explain("explain1", 0, EXPLAINS[0])      # takes the only mid-size slot, times out at 40 s
            tl.explain("explain2", 1, EXPLAINS[1])      # waits for it
            await asyncio.gather(*tl.tasks)
            r1, r2 = tl.calls["explain1"]["result"], tl.calls["explain2"]["result"]
            assert r1.degrade_reason == r2.degrade_reason == "midsize_unavailable"
            assert tl.calls["explain1"]["end"] == pytest.approx(40, abs=0.5)
            assert tl.calls["explain2"]["end"] == pytest.approx(40, abs=0.5)       # not 40 + 90 (the ghost)
            assert r2.mode == "fast_extractive" and r2.eta_seconds == pytest.approx(30, abs=1)
            assert sim.stats.max_in_flight <= 1               # only explain1 ever reached the server
    run_virtual(main)


def test_the_probe_waits_for_the_ghost_to_leave_the_midsize_slot():
    async def main(loop):
        eng, sim, provider, sink = build(loop, INTERACTIVE_DEADLINE_S=1000)
        async with eng:
            tl = Timeline(eng, loop)
            tl.explain("explain1", 0, EXPLAINS[0])      # timeout at 40, breaker open, ghost until 90
            await tl.tasks[-1]
            tl.explain("e_at_75", 75, EXPLAINS[1])          # cooldown over at 70, but the ghost holds the slot
            await tl.tasks[-1]
            assert not eng._probes and eng.router.breakers[M].state == "open"
            assert eng.caller.limiter.ghost_count(M) == 1
            tl.explain("e_at_95", 95, EXPLAINS[2])
            await tl.tasks[-1]
            assert eng.router.breakers[M].state == "half_open" and len(eng._probes) == 1     # ghost gone: probe sent
            await asyncio.sleep(20)
            probes = [r for r in sink.records if r.kind == "chat" and r.purpose == "probe"]
            assert len(probes) == 1 and probes[0].outcome == "transient"      # still hanging: the probe timed out
            assert eng.router.breakers[M].state == "open"
    run_virtual(main)
