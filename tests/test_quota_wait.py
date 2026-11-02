"""v1.4 step 4: an INTERACTIVE explain that finds the window nearly full waits for the slot when that fits the
deadline, instead of being degraded with quota_low. Virtual clock: nothing really sleeps."""
import json

import pytest

from tests.conftest import chat_ok
from tests.test_engine import _index, make_engine  # noqa: F401  (_index: the autouse course index)

Q = {"question": "C'est quoi le nombre dérivé ?"}
BODY = "Le nombre dérivé f'(a) est la pente de la tangente à la courbe au point d'abscisse a."


def fill(eng, vt, sends: int, age_s: float) -> None:
    """The window holds `sends` sends made `age_s` seconds ago (they leave it at 60 - age_s)."""
    eng.caller.limiter.buckets["chat"]._log = [(vt.clock() - age_s, False)] * sends


async def test_a_slot_freeing_in_5_s_is_waited_for_and_the_explain_is_generated(vt):
    eng, fake, sink = make_engine(vt, lambda b: chat_ok(BODY, 300, 40))
    fill(eng, vt, 19, 55)                                            # 1 free send (< threshold 2); the first expires in 5 s
    assert eng.caller.limiter.buckets["chat"].tokens == 1
    t0 = vt.clock()
    r = await eng.run("explain", Q, {"language": "FRENCH"})
    assert not r.degraded and r.model_class_used == "midsize" and len(fake.requests) == 1
    queued = [e for e in r.events if e.name == "queued" and e.data.get("reason") == "quota_window"]
    assert len(queued) == 1 and queued[0].data["eta_s"] == pytest.approx(5.0, abs=0.6)       # + the window's 0.5 s slack
    assert "falling_back_to_compact" not in r.event_names
    assert vt.clock() - t0 >= 5.0                                    # it did wait
    assert r.event_names.index("queued") < r.event_names.index("generating")


async def test_a_slot_freeing_in_20_s_is_too_far_and_the_explain_degrades_with_quota_low(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 300, 40))
    fill(eng, vt, 19, 40)                                            # the first expires in 20 s > 25 - 10
    r = await eng.run("explain", Q, {"language": "FRENCH"})
    assert r.degraded and r.degrade_reason == "quota_low" and r.mode == "fast_extractive"
    assert not [e for e in r.events if e.name == "queued" and e.data.get("reason") == "quota_window"]
    assert not any(q["body"]["model"] == "mid-a" for q in fake.requests)


@pytest.mark.parametrize("expected_s,waits", [(10, True), (20, False)])
async def test_the_threshold_is_the_deadline_minus_explain_expected_s(vt, expected_s, waits):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 300, 40), EXPLAIN_EXPECTED_S=expected_s)
    fill(eng, vt, 19, 50)                                            # a 10 s wait: <= 25 - 10, > 25 - 20
    r = await eng.run("explain", Q, {"language": "FRENCH"})
    assert (not r.degraded) is waits and (r.degrade_reason == "quota_low") is (not waits)


async def test_a_cache_hit_never_waits(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 300, 40))
    first = await eng.run("explain", Q, {"language": "FRENCH"})
    assert not first.degraded
    fill(eng, vt, 19, 55)
    t0 = vt.clock()
    again = await eng.run("explain", Q, {"language": "FRENCH"})
    assert again.from_cache and vt.clock() == t0 and "queued" not in again.event_names


async def test_background_and_escalated_explains_are_unchanged(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 300, 40))
    fill(eng, vt, 19, 55)
    t0 = vt.clock()
    r = await eng.run("explain", Q, {"language": "FRENCH", "escalate": True})        # escalated: keeps mid-size, no router wait
    assert r.model_class_used == "midsize" and not [e for e in r.events if e.data.get("reason") == "quota_window"]
    eng2, fake2, _ = make_engine(vt, lambda b: chat_ok(BODY, 300, 40))
    fill(eng2, vt, 19, 55)
    b = await eng2.run("explain", Q, {"language": "FRENCH"}, priority="BACKGROUND")
    assert not [e for e in b.events if e.data.get("reason") == "quota_window"]


async def test_a_full_window_with_a_stated_quota_delay_is_not_waited_for_beyond_the_budget(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 300, 40))
    eng.caller.limiter.block("chat", 40)                             # the platform said: wait 40 s
    r = await eng.run("explain", Q, {"language": "FRENCH"})
    assert r.degraded and not [e for e in r.events if e.data.get("reason") == "quota_window"]
