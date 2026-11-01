"""Replay of the 26 Oct FelCloud mid-size outage (reports/midsize-outage-2026-10-26.md) in virtual time:
mid-size hangs, compact is healthy. 2 explains and 3 classifies at t=0, one more classify at t=10, then explains
while the breaker should be open, the outage ends, and mid-size must come back through a probe."""
import asyncio

import pytest

from engine import Priority
from tests.outage_harness import EXPLAINS, CLASSIFIES, Timeline, build, end_outage
from tests.vloop import run_virtual


async def scenario(loop, **env):
    eng, sim, provider, sink = build(loop, **env)
    async with eng:
        tl = Timeline(eng, loop)
        tl.explain("explain1", 0, EXPLAINS[0])
        tl.explain("explain2", 0, EXPLAINS[1])
        for i in range(3):
            tl.classify(f"classify{i + 1}", 0, CLASSIFIES[i])
        tl.classify("classify4", 10, CLASSIFIES[3])
        tl.explain("explain3", 50, EXPLAINS[2])           # breaker should be open: fast, no request
        tl.explain("explain4", 51, EXPLAINS[3])
        tl.explain("explain5", 100, EXPLAINS[4])          # a probe is due about now (outage still on)
        loop.call_at(tl.t0 + 120, end_outage, sim)        # mid-size comes back at t=120
        tl.explain("explain6", 200, EXPLAINS[5])          # triggers the next probe, which succeeds
        tl.explain("explain7", 260, EXPLAINS[6])          # mid-size again
        await asyncio.gather(*tl.tasks)
        return tl, eng, sim, provider, sink


def test_replay_of_the_midsize_outage():
    tl, eng, *_ = run_virtual(scenario)
    print("\n" + tl.table())
    # the first explains degrade within the interactive timeout (about 40 s), not after 4 x 85 s
    for k in ("explain1", "explain2"):
        assert tl.calls[k]["result"].degraded and tl.took(k) <= 45, tl.table()
    # once the breaker is open every explain is answered at once
    for k in ("explain3", "explain4"):
        r = tl.calls[k]["result"]
        assert r.degraded and r.mode == "fast_extractive" and tl.took(k) <= 1.5, tl.table()
    # compact is never blocked by the hung explains: each classify waits at most for the classifies before it
    for i, k in enumerate(("classify1", "classify2", "classify3", "classify4")):
        assert tl.took(k) <= 2.0 * (i + 1) + 0.5 and not tl.calls[k]["result"].degraded, tl.table()
    # the breaker closes through a probe and mid-size serves again
    assert eng.router.breakers[next(iter(eng.router.breakers))].state == "closed", tl.table()
    last = tl.calls["explain7"]["result"]
    assert not last.degraded and last.model_class_used == "midsize", tl.table()
