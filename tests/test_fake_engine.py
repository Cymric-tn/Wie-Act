"""FakeEngine: same run() signature as Engine, every state reachable, realistic events and timing."""
import asyncio
import inspect

import pytest

from engine import Engine, Priority
from engine.fake import INTERACTIVE_DEADLINE_S, FakeEngine
from engine.result import Event, Result
from tests.vloop import run_virtual

INPUTS = {"classify": {"message": "c'est quoi une dérivée ?"}, "hint": {"problem": "dérive x^4"},
          "explain": {"question": "c'est quoi le nombre dérivé ?"}, "quiz": {"message": "un exercice"},
          "grade": {"problem": "p", "solution": "s", "attempt": "a"},
          "compress": {"question": "q", "chunks": [{"chunk_id": "c", "text": "t"}]},
          "ocr_transcribe": {"image": "data:image/png;base64,AAAA"}, "check_claim": {"claim": "(x²)' = x"},
          "check_answer": {"problem": "dérive x⁴", "steps": ["4x³", "4x²"]},
          "prep_plan": {"scope": {"unit_ids": ["derivees"]}, "deadline": "2026-11-20", "pace_min_per_day": 30}}


def test_run_has_the_same_signature_as_the_engine():
    real, fake = inspect.signature(Engine.run), inspect.signature(FakeEngine.run)
    assert list(real.parameters) == list(fake.parameters)
    assert [p.default for p in real.parameters.values()] == [p.default for p in fake.parameters.values()]


def all_cases():
    return [(t, o) for t, outs in FakeEngine.OUTCOMES.items() for o in outs]


@pytest.mark.parametrize("task,outcome", all_cases())
async def test_every_outcome_of_every_task_is_a_valid_result(task, outcome):
    fake = FakeEngine(speed=0)
    seen = []
    r = await fake.run(task, INPUTS[task], {"fake_outcome": outcome, "language": "FRENCH"}, on_event=seen.append)
    assert isinstance(r, Result) and r.task == task and r.request_id
    assert r.events and [e.name for e in r.events] == [e.name for e in seen]
    ts = [e.t_ms for e in r.events]
    assert ts == sorted(ts) and r.latency_ms >= ts[-1]
    assert r.text is not None or r.data is not None or r.degraded
    if r.degraded:
        assert r.degrade_reason
    if r.degrade_reason in ("quota_exhausted", "room_busy"):
        assert r.eta_seconds and r.eta_seconds > 0
    if r.degrade_reason == "superseded":
        assert r.eta_seconds is None


async def test_modes_select_the_default_outcome():
    normal = await FakeEngine("normal", speed=0).run("explain", INPUTS["explain"])
    assert not normal.degraded and normal.model_class_used == "midsize"
    degraded = await FakeEngine("degraded", speed=0).run("explain", INPUTS["explain"])
    assert degraded.degraded and degraded.mode == "fast_extractive" and degraded.followup_available
    claim = await FakeEngine("degraded", speed=0).run("check_claim", INPUTS["check_claim"])
    assert claim.data["verdict"] == "NOT_IN_COURSE" and claim.model_class_used is None
    slow = await FakeEngine("slow", speed=0).run("explain", INPUTS["explain"])
    assert slow.degrade_reason == "deadline" and slow.latency_ms == INTERACTIVE_DEADLINE_S * 1000


async def test_slow_mode_is_slower_and_timing_is_engine_time_not_wall_time():
    n = await FakeEngine("normal", speed=0).run("hint", INPUTS["hint"])
    s = await FakeEngine("slow", speed=0).run("hint", INPUTS["hint"])
    assert s.latency_ms == pytest.approx(3 * n.latency_ms)


def test_real_time_playback_sleeps_the_engine_timeline_scaled_by_speed():
    async def main(loop):
        fake = FakeEngine("normal", speed=0.5, sleep=asyncio.sleep)
        t0 = loop.time()
        r = await fake.run("explain", INPUTS["explain"])
        return r, loop.time() - t0
    r, took = run_virtual(main)
    assert took == pytest.approx(r.latency_ms / 1000 * 0.5, rel=0.01)


async def test_room_busy_is_enforced_like_the_engine():
    fake = FakeEngine("normal", speed=0.001)
    first = asyncio.create_task(fake.run("explain", INPUTS["explain"], {"room_id": "R1"}))
    await asyncio.sleep(0)
    second = await fake.run("check_claim", INPUTS["check_claim"], {"room_id": "R1"})
    assert second.degrade_reason == "room_busy" and second.eta_seconds
    assert not (await first).degraded
    assert not (await fake.run("check_claim", INPUTS["check_claim"], {"room_id": "R1"})).degraded


async def test_unknown_task_unknown_outcome_and_missing_input_are_caller_bugs():
    fake = FakeEngine(speed=0)
    with pytest.raises(ValueError):
        await fake.run("nope", {})
    with pytest.raises(ValueError):
        await fake.run("explain", {})
    with pytest.raises(ValueError):
        await fake.run("explain", INPUTS["explain"], {"fake_outcome": "bogus"})
    with pytest.raises(ValueError):
        FakeEngine("weird")


async def test_cache_invalidate_and_generate_page_exist():
    fake = FakeEngine(speed=0)
    assert fake.cache.invalidate("abc") == 1 and fake.cache.invalidated == ["abc"]
    page = await fake.generate_page({}, {"concept_id": "puissance", "page_kind": "learn"})
    assert page["concept_id"] == "puissance" and len(page["quiz"]) == 3 and page["worked_example"]


@pytest.mark.parametrize("outcome", ["quota_exhausted", "superseded", "room_busy"])
async def test_generic_outcomes_match_the_real_engine_examples(outcome):
    """docs/examples/classify.<outcome>.json was produced by the real Engine (scripts/gen_api_examples)."""
    import json
    real = json.load(open(f"docs/examples/classify.{outcome}.json", encoding="utf-8"))
    assert real["_source"] == "real"
    fake = await FakeEngine(speed=0).run("classify", INPUTS["classify"], {"fake_outcome": outcome})
    assert [e.name for e in fake.events] == [e["name"] for e in real["events"]]
    assert (fake.degraded, fake.degrade_reason) == (real["degraded"], real["degrade_reason"])
    assert (fake.eta_seconds is None) == (real["eta_seconds"] is None)
    assert set(fake.data or {}) == set(real["data"] or {})
