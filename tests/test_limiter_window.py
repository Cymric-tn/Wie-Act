"""Sliding-window limiter (v1.1): at most N sends in ANY rolling window, whatever the arrival pattern."""
import asyncio
import random

import pytest

from gatekeeper.errors import QuotaInfo
from gatekeeper.limiter import Limiter, SlidingWindow
from tests.conftest import make_settings

LIMIT, WINDOW = 20, 60.0


def serve(vt, take, arrivals):
    """One stream: each request arrives at its time, waits for the window if needed, and is sent.
    `take` is called to wait/record; returns the send times relative to the start."""
    t0, sends = vt.t, []
    for a in sorted(arrivals):
        vt.t = max(vt.t, t0 + a)
        while (w := take()) > 0:
            vt.t += w
        sends.append(vt.t - t0)
    return sends


def densest_window(sends, window=WINDOW):
    return max(sum(t <= x < t + window for x in sends) for t in sends)


def patterns():
    rng = random.Random(11)
    poisson, t = [], 0.0
    while t < 300:
        t += rng.expovariate(90 / 60)                       # 90 arrivals/min, far above the 20/min limit
        poisson.append(t)
    return {
        "20 at t=0 then steady 20/min": [0.0] * 20 + [3.0 * i for i in range(1, 80)],
        "20 at t=0 then steady 30/min": [0.0] * 20 + [2.0 * i for i in range(1, 120)],
        "10 every 15 s": [15.0 * k for k in range(8) for _ in range(10)],
        "poisson 90/min + a burst of 25": poisson + [100.0] * 25,
        "100 at once": [0.0] * 100,
        "slow trickle": [10.0 * i for i in range(40)],
    }


@pytest.mark.parametrize("slack", [0.0, 0.5])
@pytest.mark.parametrize("name", list(patterns()))
def test_no_window_ever_holds_more_than_the_limit(vt, name, slack):
    w = SlidingWindow(LIMIT, WINDOW, vt.clock, slack)
    arrivals = patterns()[name]
    sends = serve(vt, w.try_take, arrivals)
    assert len(sends) == len(arrivals)                       # everything is eventually served
    assert densest_window(sends) <= LIMIT


@pytest.mark.parametrize("name", list(patterns()))
async def test_same_guarantee_through_the_limiter(vt, name):
    """Same patterns through Limiter.slot (concurrency slot + window), one request at a time."""
    lim = Limiter(make_settings(RATE_LIMIT_PER_MIN=LIMIT, RATE_WINDOW_S=WINDOW), vt.clock, vt.sleep)
    t0, sends = vt.t, []
    for a in sorted(patterns()[name]):
        vt.t = max(vt.t, t0 + a)
        async with lim.slot("chat"):
            sends.append(vt.t - t0)
    assert densest_window(sends) <= LIMIT


def test_twenty_at_once_then_the_21st_waits_one_full_window(vt):
    w = SlidingWindow(LIMIT, WINDOW, vt.clock)
    assert all(w.try_take() == 0 for _ in range(LIMIT))
    assert w.try_take() == pytest.approx(60.0)
    vt.t += 59.99
    assert w.try_take() > 0
    vt.t += 0.01
    assert w.try_take() == 0                                 # right when the first send leaves the window


def test_slack_keeps_a_slot_busy_a_little_longer(vt):
    w = SlidingWindow(1, WINDOW, vt.clock, slack_s=0.5)
    w.try_take()
    assert w.try_take() == pytest.approx(60.5)


def test_slots_free_one_at_a_time_as_sends_leave_the_window(vt):
    w = SlidingWindow(3, 60.0, vt.clock)
    for dt in (0, 10, 10):                                   # sends at 0, 10, 20
        vt.t += dt
        assert w.try_take() == 0
    assert w.try_take() == pytest.approx(40.0)               # first send leaves at t=60
    vt.t += 40
    assert w.try_take() == 0
    assert w.try_take() == pytest.approx(10.0)               # next one frees at t=70, not a whole new window


def test_float_rounding_does_not_spin(vt):
    w = SlidingWindow(1, 60.0, vt.clock)
    vt.t = 1000.1
    w.try_take()
    vt.t = 1000.1 + 60.0                                     # lands a hair below the expiry in float
    assert w.wait_time() == 0 and w.try_take() == 0


# ------------------------------------------------------------------------------ server feedback
def test_server_remaining_below_ours_adds_phantom_entries(vt):
    w = SlidingWindow(LIMIT, WINDOW, vt.clock)
    w.try_take()                                             # we count 1 send, 19 free
    w.sync_remaining(14)                                     # the server has 5 more that are not ours
    assert w.tokens == 14


def test_server_remaining_above_ours_changes_nothing(vt):
    w = SlidingWindow(LIMIT, WINDOW, vt.clock)
    w.try_take()
    w.sync_remaining(19)
    w.sync_remaining(20)
    w.sync_remaining(None)
    assert w.tokens == 19


def test_phantoms_expire_with_the_send_that_the_server_counted(vt):
    w = SlidingWindow(LIMIT, WINDOW, vt.clock)
    w.try_take()
    vt.t += 30
    w.sync_remaining(10, at=vt.t - 30)                       # counted when the request was sent, 30 s ago
    assert w.tokens == 10
    vt.t += 30.0
    assert w.tokens == LIMIT - 0                             # all of it, ours and theirs, left the window


def test_phantom_entries_are_never_refunded(vt):
    w = SlidingWindow(3, WINDOW, vt.clock)
    w.try_take()
    vt.t += 5
    w.try_take()
    w.sync_remaining(0, at=vt.t)                             # 1 phantom
    w.refund()                                               # a slot taken but not used: drops our OLDEST real send
    assert w.tokens == 1 and [p for _, p in w._log] == [False, True]


def test_stated_delay_blocks_and_zeroes_free_slots(vt):
    w = SlidingWindow(LIMIT, WINDOW, vt.clock)
    w.block_for(45)
    assert w.tokens == 0 and w.try_take() == pytest.approx(45)
    vt.t += 45
    assert w.tokens == LIMIT and w.try_take() == 0


def test_server_never_refuses_a_client_that_reads_the_headers(vt):
    """Another client of the same key spent 7 of the server's 20 slots. We cannot see it, but the first
    response tells us (remaining), and from then on we never send more than the server will take."""
    server, refused = [], 0

    def serve_request(t):
        nonlocal refused
        server[:] = [x for x in server if x > t - WINDOW]
        if len(server) >= LIMIT:
            refused += 1
            return None
        server.append(t)
        return LIMIT - len(server)                           # the quota header

    server.extend([vt.t] * 7)                                # the other client, all at the start
    w = SlidingWindow(LIMIT, WINDOW, vt.clock, 0.5)
    t0 = vt.t
    for _ in range(60):
        while (wait := w.try_take()) > 0:
            vt.t += wait
        remaining = serve_request(vt.t)
        assert remaining is not None
        w.sync_remaining(remaining, at=vt.t)
        vt.t += 1.0
    assert refused == 0 and vt.t - t0 > 60                   # 60 requests really were paced over more than a window


# ------------------------------------------------------------------------------ limiter wiring
async def test_a_window_slot_is_only_taken_with_a_concurrency_slot(vt):
    lim = Limiter(make_settings(RATE_LIMIT_PER_MIN=20, MAX_CONCURRENT=1), vt.clock, vt.sleep)
    release = asyncio.Event()

    async def holder():
        async with lim.slot("chat"):
            await release.wait()

    async def follower():
        async with lim.slot("chat"):
            pass

    a = asyncio.create_task(holder())
    await asyncio.sleep(0)
    b = asyncio.create_task(follower())
    for _ in range(5):
        await asyncio.sleep(0)
    assert lim.concurrency.locked() and len(lim.buckets["chat"]._log) == 1    # b waits and has not spent a slot
    release.set()
    await asyncio.gather(a, b)
    assert len(lim.buckets["chat"]._log) == 2


async def test_chat_and_embeddings_have_their_own_windows(vt):
    lim = Limiter(make_settings(RATE_LIMIT_PER_MIN=20, EMBED_RATE_LIMIT_PER_MIN=3, EMBED_RATE_WINDOW_S=10),
                  vt.clock, vt.sleep)
    for _ in range(3):
        async with lim.slot("embed"):
            pass
    assert lim.buckets["embed"].wait_time() == pytest.approx(10.5)   # its own 10 s window (+ 0.5 s slack)
    assert lim.buckets["chat"].wait_time() == 0 and lim.buckets["chat"].limit == 20
    async with lim.slot("chat") as info:
        assert info.rate_wait_s == 0


def test_limiter_observe_uses_the_send_time(vt):
    lim = Limiter(make_settings(RATE_LIMIT_PER_MIN=20), vt.clock, vt.sleep)
    vt.t += 40
    lim.observe("chat", QuotaInfo(remaining_requests=15), sent_at=vt.t - 35)
    assert lim.buckets["chat"].tokens == 15
    vt.t += 25.6                                             # 60.6 s after the send: all phantoms are gone
    assert lim.buckets["chat"].tokens == 20


def test_window_settings_come_from_env():
    s = make_settings(RATE_LIMIT_PER_MIN=12, RATE_WINDOW_S=30, EMBED_RATE_LIMIT_PER_MIN=7, EMBED_RATE_WINDOW_S=20,
                      RATE_WINDOW_SLACK_S=1)
    lim = Limiter(s)
    assert (lim.buckets["chat"].limit, lim.buckets["chat"].window_s, lim.buckets["chat"].slack_s) == (12, 30, 1)
    assert (lim.buckets["embed"].limit, lim.buckets["embed"].window_s) == (7, 20)
