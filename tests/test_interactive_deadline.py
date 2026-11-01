"""v1.2 step 7: an INTERACTIVE explain without a body after INTERACTIVE_DEADLINE_S is answered in fast mode;
the mid-size generation goes on into the cache."""
import asyncio

import pytest

from config import ModelClass
from engine import Priority
from tests.outage_harness import EXPLAINS, Timeline, build
from tests.vloop import run_virtual

M = ModelClass.MIDSIZE


def test_a_hung_midsize_explain_is_answered_in_fast_mode_at_the_deadline():
    async def main(loop):
        eng, sim, provider, sink = build(loop)                      # hangs forever; interactive timeout 40 s
        async with eng:
            tl = Timeline(eng, loop)
            await tl.explain("e1", 0, EXPLAINS[0])
            r = tl.calls["e1"]["result"]
            assert tl.took("e1") == pytest.approx(25, abs=0.3)
            assert (r.degraded, r.degrade_reason, r.mode, r.followup_available) == (True, "deadline", "fast_extractive", True)
            assert "deadline" in r.event_names and "fast_extractive" in r.event_names
            assert eng.router.breakers[M].closed                    # the request is still out: nothing known yet
            await asyncio.sleep(16)                                 # the abandoned request times out at 40 s
            assert not eng.router.breakers[M].closed                # ... and then the breaker opens
            assert [x for x in sink.records if x.kind == "run"].__len__() == 1       # one run record, not two
    run_virtual(main)


def test_a_slow_answer_that_arrives_after_the_deadline_lands_in_the_cache():
    async def main(loop):
        eng, sim, provider, sink = build(loop, SIM_HANG_S=32)       # arrives at 32 s: after 25, before the 40 s timeout
        async with eng:
            tl = Timeline(eng, loop)
            await tl.explain("first", 0, EXPLAINS[0])
            assert tl.calls["first"]["result"].degrade_reason == "deadline" and tl.took("first") == pytest.approx(25, abs=0.3)
            await asyncio.sleep(10)                                 # t = 35: the full explanation has arrived
            await tl.explain("again", 35, EXPLAINS[0])
            r = tl.calls["again"]["result"]
            assert r.from_cache and not r.degraded and r.model_class_used == "midsize"
            assert "dérivé" in r.data["body"] and tl.took("again") < 1
            assert eng.router.breakers[M].closed
    run_virtual(main)


def test_an_answer_before_the_deadline_is_a_normal_answer():
    async def main(loop):
        eng, *_ = build(loop, SIM_HANG_S=12)
        async with eng:
            tl = Timeline(eng, loop)
            await tl.explain("e", 0, EXPLAINS[0])
            r = tl.calls["e"]["result"]
            assert not r.degraded and r.mode is None and tl.took("e") == pytest.approx(12, abs=0.3)
    run_virtual(main)


def test_background_work_has_no_deadline():
    async def main(loop):
        eng, *_ = build(loop, SIM_HANG_S=60, TIMEOUT_MIDSIZE_BACKGROUND_S=85)
        async with eng:
            tl = Timeline(eng, loop, Priority.BACKGROUND)
            await tl.explain("e", 0, EXPLAINS[0])
            assert not tl.calls["e"]["result"].degraded and tl.took("e") == pytest.approx(60, abs=0.3)
    run_virtual(main)


def test_the_deadline_can_be_switched_off_and_is_configurable():
    async def main(loop):
        eng, *_ = build(loop, SIM_HANG_S=30, INTERACTIVE_DEADLINE_S=0)
        async with eng:
            tl = Timeline(eng, loop)
            await tl.explain("e", 0, EXPLAINS[0])
            assert not tl.calls["e"]["result"].degraded
        eng2, *_ = build(loop, SIM_HANG_S=30, INTERACTIVE_DEADLINE_S=10)
        async with eng2:
            tl2 = Timeline(eng2, loop)
            await tl2.explain("e", 0, EXPLAINS[1])
            assert tl2.calls["e"]["result"].degrade_reason == "deadline" and tl2.took("e") == pytest.approx(10, abs=0.3)
    run_virtual(main)


def test_events_of_the_generation_reach_the_learner_until_the_deadline():
    async def main(loop):
        eng, *_ = build(loop)
        async with eng:
            tl = Timeline(eng, loop)
            await tl.explain("e", 0, EXPLAINS[0])
            names = [n for _, n in tl.calls["e"]["events"]]
            assert names.index("queued") < names.index("generating") < names.index("deadline") < names.index("fast_extractive")
    run_virtual(main)


def test_an_identical_request_waiting_for_a_slow_leader_has_the_same_deadline():
    """The leader is a BACKGROUND generation (a prep page) that takes 60 s. A learner who asks the same thing at t=5 must
    not wait for it: fast mode at 5 + 25 s, and the leader's answer still lands in the cache."""
    async def main(loop):
        eng, sim, provider, sink = build(loop, SIM_HANG_S=60, TIMEOUT_MIDSIZE_BACKGROUND_S=85)
        async with eng:
            tl = Timeline(eng, loop)
            tl.at("leader", 0, "explain", {"question": EXPLAINS[0]}, {"language": "FRENCH"}, Priority.BACKGROUND)
            tl.explain("follower", 5, EXPLAINS[0])
            await asyncio.gather(*tl.tasks)
            lead, fol = tl.calls["leader"]["result"], tl.calls["follower"]["result"]
            assert not lead.degraded and tl.took("leader") == pytest.approx(60, abs=0.5)
            assert fol.degraded and fol.degrade_reason == "deadline" and fol.mode == "fast_extractive"
            assert tl.took("follower") == pytest.approx(25, abs=0.5)                      # not 55 s
            assert "waiting_identical_request" in fol.event_names and "deadline" in fol.event_names
            again = Timeline(eng, loop)
            await again.explain("later", 0, EXPLAINS[0])
            assert again.calls["later"]["result"].from_cache                                 # the leader filled the cache
    run_virtual(main)


def test_a_follower_of_a_quick_leader_is_unchanged_and_a_background_follower_waits_for_it():
    async def main(loop):
        eng, *_ = build(loop, SIM_HANG_S=12)
        async with eng:
            tl = Timeline(eng, loop)
            tl.explain("leader", 0, EXPLAINS[0])
            tl.explain("follower", 3, EXPLAINS[0])
            tl.at("bg", 4, "explain", {"question": EXPLAINS[0]}, {"language": "FRENCH"}, Priority.BACKGROUND)
            await asyncio.gather(*tl.tasks)
            for k in ("follower", "bg"):
                r = tl.calls[k]["result"]
                assert r.from_cache and not r.degraded and "waiting_identical_request" in r.event_names
            assert tl.calls["follower"]["end"] == pytest.approx(12, abs=0.5)
    run_virtual(main)


def test_a_follower_whose_leader_left_nothing_in_the_cache_becomes_a_leader_with_the_same_deadline():
    """With nothing cached (the leader's answer was degraded / not stored), the follower used to start its own generation
    after waiting, with no deadline at all (found by the mixed load: 41 s). It now counts the time it already waited."""
    async def main(loop):
        eng, sim, provider, sink = build(loop, SIM_HANG_S=18, CACHE_TTL_DAYS=0)        # an answer is never cached
        async with eng:
            tl = Timeline(eng, loop)
            tl.explain("leader", 0, EXPLAINS[0])
            tl.explain("follower", 4, EXPLAINS[0])
            await asyncio.gather(*tl.tasks)
            lead, fol = tl.calls["leader"]["result"], tl.calls["follower"]["result"]
            assert not lead.degraded and tl.took("leader") == pytest.approx(18, abs=0.5)
            # the follower woke at 18 s, found nothing, and had 25 - 14 = 11 s left for its own 18 s generation
            assert fol.degrade_reason == "deadline" and fol.mode == "fast_extractive"
            assert tl.took("follower") == pytest.approx(25, abs=0.5)
            assert "waiting_identical_request" in fol.event_names and fol.event_names.count("waiting_identical_request") == 1
    run_virtual(main)
