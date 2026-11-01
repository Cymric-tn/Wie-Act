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
    assert rep["interactive"]["p95_s"] < 25 and v["interactive_p95_under_25s"]
    assert rep["background"]["pages"] >= 1 and rep["interactive"]["n"] > 30
    assert rep["provider"]["by_model"].get("deepseek-ocr", 0) >= 1                 # vision ran in the same window


def test_the_helpers_of_the_report():
    assert pct([1, 2, 3, 4, 5], .5) == 3 and pct([], .5) is None and pct([7], .95) == 7
    assert densest_window([0, 10, 20, 61, 62]) == 4 and densest_window([0, 60, 120]) == 1 and densest_window([]) == 0
