"""Resilience probe for a live mid-size outage on FelCloud (no engine change, just observation).

    ENV_FILE=.env.real python -m scripts.outage_test [--cap 10] [--cap-total 12]

Phase 1, all INTERACTIVE, through engine.run: 2 explain (distinct questions) + 3 classify at t=0, one more
classify at t=10 s. Phase 2: one more explain once phase 1 is over. Every event is logged with a timestamp,
every provider attempt comes from the metrics records. The run is cut (tasks cancelled) once the number of
provider attempts that finished reaches --cap (phase 1) / --cap-total (both); attempts still in flight at that
moment are not counted, so the real total can be up to max_concurrent higher. Writes
metrics/outage_midsize.jsonl and reports/midsize-outage-raw.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time

from dotenv import dotenv_values

from config import load_settings
from engine import Engine, Priority
from gatekeeper.cache import ExactCache
from metrics import JsonlSink

EXPLAIN = ["C'est quoi le taux d'accroissement et comment on obtient le nombre dérivé ?",
           "Comment trouver l'équation de la tangente à une courbe ?"]
EXPLAIN_AFTER = "Quelles sont les erreurs fréquentes avec la dérivée d'un produit ?"
CLASSIFY = ["Je bloque sur la dérivée de x(x + 1), j'ai trouvé 1", "chnowa ya3ni limite? ma fhemt chay",
            "How do I know if a function is increasing?"]
CLASSIFY_LATE = "ما هو تعريف العدد المشتق؟"


class CountingSink:
    """Writes to the JSONL file and keeps every record; counts finished provider attempts."""

    def __init__(self, path: str):
        self.inner, self.records = JsonlSink(path), []

    def write(self, rec) -> None:
        self.inner.write(rec)
        self.records.append(rec)

    @property
    def attempts(self) -> int:
        return sum(1 for r in self.records if r.kind == "chat" and r.outcome not in ("cache_hit", "negative_cache_hit"))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=10)
    ap.add_argument("--cap-total", type=int, default=12)
    a = ap.parse_args()
    out = "metrics/outage_midsize.jsonl"
    if os.path.exists(out):
        os.remove(out)
    env = {**dotenv_values(os.environ.get("ENV_FILE", ".env")), **os.environ, "SIMULATE_CONSTRAINTS": "false"}
    s = load_settings({k: v for k, v in env.items() if v is not None})
    if "felcloud" not in s.base_url:
        raise SystemExit("FelCloud host only")
    sink = CountingSink(out)
    t0 = time.monotonic()
    now = lambda: round(time.monotonic() - t0, 1)
    calls: list[dict] = []

    async def call(engine, label, task, inputs, ctx, at=0.0):
        await asyncio.sleep(max(0.0, at - (time.monotonic() - t0)))
        rec = {"label": label, "task": task, "start_s": now(), "events": [], "end_s": None, "cancelled": False}
        calls.append(rec)

        def on_event(ev):
            rec["events"].append([now(), ev.name, ev.data])
            print(f"  t={now():6.1f}s {label:10} {ev.name} {ev.data or ''}", flush=True)
        try:
            r = await engine.run(task, inputs, ctx, Priority.INTERACTIVE, on_event)
        except asyncio.CancelledError:
            rec["cancelled"] = True
            rec["end_s"] = now()
            raise
        rec.update(end_s=now(), request_id=r.request_id, latency_ms=r.latency_ms, degraded=r.degraded,
                   degrade_reason=r.degrade_reason, mode=r.mode, model_class_used=r.model_class_used,
                   eta_seconds=r.eta_seconds, from_cache=r.from_cache)
        print(f"  t={now():6.1f}s {label:10} RESULT degraded={r.degraded} reason={r.degrade_reason} mode={r.mode} "
              f"class={r.model_class_used}", flush=True)

    async def guard(tasks, cap):
        while not all(t.done() for t in tasks):
            if sink.attempts >= cap:
                print(f"  t={now()}s CAP: {sink.attempts} provider attempts finished, cancelling", flush=True)
                for t in tasks:
                    t.cancel()
                return
            await asyncio.sleep(1)

    async with Engine.create(s, sink=sink, cache=ExactCache(":memory:", s.cache_ttl_s)) as engine:
        print(f"phase 1 at t=0 ({s.request_timeout_s:g}s request timeout, {s.max_attempts} attempts)", flush=True)
        ex = lambda q: {"question": q}
        tasks = [asyncio.create_task(call(engine, f"explain{i + 1}", "explain", ex(q), {"language": "FRENCH"}))
                 for i, q in enumerate(EXPLAIN)]
        tasks += [asyncio.create_task(call(engine, f"classify{i + 1}", "classify", {"message": m}, {}))
                  for i, m in enumerate(CLASSIFY)]
        tasks.append(asyncio.create_task(call(engine, "classify4", "classify", {"message": CLASSIFY_LATE}, {}, 10.0)))
        await asyncio.gather(guard(tasks, a.cap), *tasks, return_exceptions=True)
        p1_end = now()
        print(f"phase 1 over at t={p1_end}s, attempts={sink.attempts}", flush=True)
        t_after = now()
        if sink.attempts < a.cap_total:
            t5 = asyncio.create_task(call(engine, "explain_after", "explain", ex(EXPLAIN_AFTER), {"language": "FRENCH"}))
            await asyncio.gather(guard([t5], a.cap_total), t5, return_exceptions=True)
        breaker = engine.router.breaker_state() if hasattr(engine.router, "breaker_state") else None
    attempts = [{"request_id": r.request_id, "task": r.task, "attempt": r.attempt, "class": r.model_class,
                 "outcome": r.outcome, "action": r.action, "wait_ms": r.wait_ms, "latency_ms": r.latency_ms,
                 "detail": r.detail, "ts": r.ts - (time.time() - (time.monotonic() - t0))}
                for r in sink.records if r.kind == "chat"]
    other = [{"kind": r.kind, "task": r.task, "outcome": r.outcome, "detail": r.detail,
              "ts": round(r.ts - (time.time() - (time.monotonic() - t0)), 1)}
             for r in sink.records if r.kind in ("breaker", "route")]
    json.dump({"phase1_end_s": p1_end, "phase2_start_s": t_after, "calls": calls, "attempts": attempts,
               "route_and_breaker": other, "total_attempts": sink.attempts, "breaker": breaker},
              open("reports/midsize-outage-raw.json", "w"), ensure_ascii=False, indent=1, default=str)
    print(f"done at t={now()}s, finished provider attempts: {sink.attempts}")


if __name__ == "__main__":
    asyncio.run(main())
