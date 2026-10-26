"""Load run against the REAL provider (simulator off). Same question pools and Poisson arrivals as load_test.

    ENV_FILE=<felcloud profile> python -m scripts.load_felcloud --n 30 [--rate 30] [--seed 1]

Writes metrics/load_felcloud.jsonl (truncated at start) and metrics/load_felcloud.summary.json.
Refuses to run if SIMULATE_CONSTRAINTS is on or the base URL is not the FelCloud host.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from collections import Counter

from dotenv import dotenv_values

from config import load_settings
from engine import Engine
from gatekeeper.cache import ExactCache
from scripts.load_test import parse_mix, plan


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--mix", default="interactive:0.8,background:0.2")
    ap.add_argument("--rate", type=float, default=30.0, help="arrivals per minute (Poisson)")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    out = "metrics/load_felcloud.jsonl"
    if os.path.exists(out):
        os.remove(out)
    env = {**dotenv_values(os.environ.get("ENV_FILE", ".env")), **os.environ,
           "SIMULATE_CONSTRAINTS": "false", "METRICS_PATH": out}
    s = load_settings({k: v for k, v in env.items() if v is not None})
    if "felcloud" not in s.base_url:
        raise SystemExit("not the FelCloud host: refusing to run")
    jobs = plan(a.n, parse_mix(a.mix), a.rate, random.Random(a.seed))
    print(f"FelCloud n={a.n} rate={a.rate:g}/min mix={a.mix} -> {out}")

    results, errors = [], []
    t0 = time.monotonic()
    async with Engine.create(s, cache=ExactCache(":memory:", s.cache_ttl_s)) as engine:
        async def fire(at, task, inputs, context, prio):
            await asyncio.sleep(max(0.0, at - (time.monotonic() - t0)))
            try:
                results.append(await engine.run(task, inputs, context, prio))
            except Exception as e:                        # the engine contract says this never happens
                errors.append(f"{task}: {type(e).__name__}: {e}")
        await asyncio.gather(*(fire(*j) for j in jobs))
    wall = time.monotonic() - t0
    by_reason = Counter(r.degrade_reason for r in results if r.degraded)
    print(f"done in {wall:.0f}s: {len(results)} results, {len(errors)} exceptions raised to the caller")
    print(f"  cache hits {sum(r.from_cache for r in results)}, degraded {sum(r.degraded for r in results)} "
          f"{dict(by_reason)}, fast_extractive {sum(r.mode == 'fast_extractive' for r in results)}")
    for e in errors:
        print("  EXCEPTION", e)
    with open(out.replace(".jsonl", ".summary.json"), "w") as f:
        json.dump({"profile": "felcloud", "n": a.n, "unit": "calls", "rate": a.rate, "wall_s": round(wall, 1),
                   "exceptions": errors}, f)


if __name__ == "__main__":
    asyncio.run(main())
