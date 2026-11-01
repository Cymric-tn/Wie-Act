"""A discrete-event virtual-time event loop for tests that need CONCURRENT timelines.

tests.conftest.VirtualTime adds every sleep to one shared clock, which is right for sequential code but not
when several requests wait at the same time (two 85 s timeouts would add up to 170 s). Here the loop's own
clock is virtual: when nothing is ready, time jumps to the next scheduled wake-up, so asyncio.sleep,
asyncio.wait, Event waits and httpx timeouts all share one honest timeline and the test takes milliseconds.
Pass loop.time as the clock and asyncio.sleep as the sleep of the engine / simulator.

Guards: a loop with nothing scheduled and nothing ready raises (deadlock), and more than MAX_STEPS loop
iterations raise (a wait that does not advance time), so a broken test fails instead of hanging.
"""
from __future__ import annotations

import asyncio

MAX_STEPS = 500_000


class VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self, start: float = 1000.0):
        super().__init__()
        self._vt, self._steps = start, 0
        real_select = self._selector.select

        def select(timeout=None):
            self._steps += 1
            if self._steps > MAX_STEPS:
                raise RuntimeError(f"virtual loop: more than {MAX_STEPS} iterations (a wait that never advances?)")
            if timeout is None:
                raise RuntimeError("virtual loop: deadlock (nothing scheduled, nothing ready)")
            if timeout > 0:
                self._vt += timeout
            return real_select(0)
        self._selector.select = select

    def time(self) -> float:
        return self._vt


def run_virtual(main, limit_s: float = 3600.0):
    """Run `main` (a coroutine function taking the loop) in virtual time; fail if it needs more than limit_s."""
    loop = VirtualLoop()
    try:
        return loop.run_until_complete(asyncio.wait_for(main(loop), limit_s))
    finally:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()
