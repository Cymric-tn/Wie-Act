import random

import pytest

from config import ModelClass
from engine import Engine
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient
from gatekeeper.errors import ModelSaturated, QuotaExhausted
from metrics import MemorySink
from rag.chunker import chunk_dir
from rag.store import save_chunks
from router.breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker
from router.policy import Router
from tests.conftest import FnProvider, chat_ok, error, make_settings


def breaker(vt, threshold=3, window=60, cooldown=30):
    changes = []
    b = CircuitBreaker(threshold, window, cooldown, vt.clock, lambda o, n, why: changes.append((o, n)))
    return b, changes


# ------------------------------------------------------------------ transitions
def test_closed_to_open_after_threshold_within_window(vt):
    b, changes = breaker(vt)
    b.record_failure(); b.record_failure()
    assert b.state == CLOSED and b.allow()
    b.record_failure()
    assert b.state == OPEN and changes == [(CLOSED, OPEN)] and b.trips == 1


def test_failures_spread_beyond_the_window_do_not_trip(vt):
    b, _ = breaker(vt)
    for _ in range(5):
        b.record_failure()
        vt.t += 31                       # at most 2 failures inside any 60 s window
    assert b.state == CLOSED


def test_successes_do_not_reset_a_closed_window(vt):
    b, _ = breaker(vt)
    b.record_failure(); b.record_success(); b.record_failure(); b.record_success(); b.record_failure()
    assert b.state == OPEN


def test_open_blocks_until_cooldown_then_half_open_lets_exactly_one_probe(vt):
    b, changes = breaker(vt, threshold=1)
    b.record_failure()
    assert not b.allow() and b.retry_in() == pytest.approx(30)
    vt.t += 29.9
    assert not b.allow()
    vt.t += 0.1
    assert b.allow() and b.state == HALF_OPEN          # the probe
    assert not b.allow() and not b.allow()             # everyone else still falls back
    assert changes == [(CLOSED, OPEN), (OPEN, HALF_OPEN)]


def test_half_open_probe_success_closes(vt):
    b, changes = breaker(vt, threshold=1)
    b.record_failure(); vt.t += 30; b.allow()
    b.record_success()
    assert b.state == CLOSED and b.allow() and b.allow()
    assert changes[-1] == (HALF_OPEN, CLOSED)


def test_half_open_probe_failure_reopens_and_restarts_cooldown(vt):
    b, changes = breaker(vt, threshold=1)
    b.record_failure(); vt.t += 30; b.allow()
    b.record_failure()
    assert b.state == OPEN and changes[-1] == (HALF_OPEN, OPEN) and b.trips == 1   # a reopen is not a new trip
    assert b.retry_in() == pytest.approx(30) and not b.allow()


def test_unreported_probe_is_released_or_expires(vt):
    b, _ = breaker(vt, threshold=1)
    b.record_failure(); vt.t += 30
    assert b.allow() and not b.allow()
    b.release_probe()                                  # e.g. the probe was a cache hit
    assert b.allow() and not b.allow()
    vt.t += 30                                         # a probe that never reports expires
    assert b.allow()


def test_late_results_while_open_are_ignored(vt):
    b, changes = breaker(vt, threshold=1)
    b.record_failure()
    b.record_success(); b.record_failure()
    assert b.state == OPEN and changes == [(CLOSED, OPEN)]


# ------------------------------------------------------------------ router: routing + metrics
def test_router_routes_to_fallback_while_open_and_records_state_changes(vt):
    sink = MemorySink()
    r = Router(sink, 2, breaker_threshold=2, breaker_window_s=60, breaker_cooldown_s=30, clock=vt.clock)
    assert set(r.breakers) == {ModelClass.MIDSIZE}                 # compact has nowhere to fall back to
    for _ in range(2):
        r.observe(ModelClass.MIDSIZE, ModelSaturated(status=503))
    r.observe(ModelClass.MIDSIZE, QuotaExhausted(status=429))      # quota is not saturation: ignored
    d = r.decide("explain", {"escalate": True}, quota_tokens=20)
    assert (d.model_class, d.degrade_reason) == (ModelClass.COMPACT, "breaker_open")
    vt.t += 30
    probe = r.decide("explain", {}, 20)
    assert probe.model_class is ModelClass.MIDSIZE and probe.probe
    assert r.decide("explain", {}, 20).degrade_reason == "breaker_open"   # only one probe
    r.observe(ModelClass.MIDSIZE, None)
    assert r.decide("explain", {}, 20).model_class is ModelClass.MIDSIZE
    states = [x.outcome for x in sink.records if x.kind == "breaker"]
    assert states == [OPEN, HALF_OPEN, CLOSED]


# ------------------------------------------------------------------ engine: full cycle
async def test_engine_breaker_cycle_trip_bypass_probe_close(vt, tmp_path):
    index = str(tmp_path / "chunks.json")
    save_chunks(index, chunk_dir("data/docs"))
    healthy = {"mid": False}

    def fn(body):
        if body["model"] == "mid-a" and not healthy["mid"]:
            return error(503, "model_saturated", "busy", {"Retry-After": "2"})
        return chat_ok("Voici le cours :" if body["model"] == "compact-a" else "Explication complète.")

    s = make_settings(RAG_INDEX_PATH=index, BREAKER_THRESHOLD=2, BREAKER_COOLDOWN_S=30, SATURATION_RETRIES=1)
    fake, sink = FnProvider(fn), MemorySink()
    eng = Engine.create(s, client=LLMClient(s, fake), sink=sink, cache=ExactCache(":memory:", s.cache_ttl_s),
                        clock=vt.clock, sleep=vt.sleep, rng=random.Random(1))
    q = lambda i: {"question": f"Comment trouver l'équation de la tangente ? ({i})"}

    r1 = await eng.run("explain", q(1))                                   # 2 saturated attempts -> trips
    assert r1.mode == "fast_extractive" and fake.models().count("mid-a") == 2
    r2 = await eng.run("explain", q(2))                                   # open: straight to fast mode
    assert "breaker_open" in r2.event_names and r2.degrade_reason == "breaker_open"
    assert fake.models().count("mid-a") == 2 and r2.mode == "fast_extractive"
    vt.t += 30
    healthy["mid"] = True
    r3 = await eng.run("explain", q(3))                                   # half-open probe succeeds
    assert (r3.model_class_used, r3.degraded, r3.data["body"]) == ("midsize", False, "Explication complète.")
    assert "probe" in r3.route_reason
    r4 = await eng.run("explain", q(4))
    assert r4.model_class_used == "midsize"
    assert [x.outcome for x in sink.records if x.kind == "breaker"] == [OPEN, HALF_OPEN, CLOSED]
