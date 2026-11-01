"""The mixed load (3 learners, 1 room, 1 prep plan in the background) in virtual time through the constraint simulator:
the v1.3 acceptance numbers, held by a test. It checks the engine's scheduling, not the models."""
import argparse

import pytest

from scripts.load_mixed import densest_window, pct, run_virtual


def args(**kw):
    return argparse.Namespace(**{"duration": 150.0, "pages": 3, "seed": 1, "saturation": 0.05, "no_cache": True, **kw})


@pytest.mark.parametrize("seed", [1, 2])
def test_interactive_latency_stays_bounded_while_a_prep_plan_generates(seed):
    rep = run_virtual(args(seed=seed))
    v = rep["verdict"]
    assert v["zero_exceptions"] and v["zero_server_429"] and v["window_at_most_20"], rep["provider"]
    # Explain is bounded by the engine's own 25 s deadline (a request not ready by then gets fast mode), also when it waited
    # for an identical request in flight; a p95 AT 25 means one learner in twenty got fast mode (reported as "deadline").
    by = rep["interactive"]["by_task"]
    assert rep["interactive"]["p95_s"] <= 25.5
    assert all(d["max_s"] <= 25.5 for t, d in by.items() if t != "ocr_transcribe"), by
    # A photo can wait for the single heavy slot (mid-size and vision share max_concurrent - 1) behind a generation that
    # keeps going into the cache; bounded by one such generation plus the vision call itself.
    assert by.get("ocr_transcribe", {"max_s": 0})["max_s"] < 60
    assert rep["background"]["pages"] >= 1 and rep["interactive"]["n"] > 30
    assert rep["provider"]["by_model"].get("deepseek-ocr", 0) >= 1                 # vision ran in the same window


def test_the_helpers_of_the_report():
    assert pct([1, 2, 3, 4, 5], .5) == 3 and pct([], .5) is None and pct([7], .95) == 7
    assert densest_window([0, 10, 20, 61, 62]) == 4 and densest_window([0, 60, 120]) == 1 and densest_window([]) == 0
