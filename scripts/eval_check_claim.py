"""Accuracy of check_claim on tests/eval/claims_set.jsonl (30 claims: true, false, partly true, off-course; FR, EN, AR).

    ENV_FILE=.env.real python -m scripts.eval_check_claim --out reports/eval_check_claim.json

Runs the real engine against whatever provider ENV_FILE names (FelCloud in a slot; never start Ollama on the laptop
for this). Claims decided by sympy or by the retrieval gate cost no request; the others cost one compact request.
Prints accuracy overall, per language, per expected verdict and per `checked_by`, the confusion matrix, and the
requests spent. 20 requests per minute are the team's: the run paces itself through the engine's limiter.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter, defaultdict

from config import load_settings
from engine import Engine, Priority
from gatekeeper.cache import ExactCache

VERDICTS = ("SUPPORTED", "CONTRADICTED", "PARTLY", "NOT_IN_COURSE")


def summarize(rows: list[dict]) -> dict:
    def acc(sel):
        sel = list(sel)
        return {"n": len(sel), "correct": sum(r["ok"] for r in sel),
                "accuracy": round(sum(r["ok"] for r in sel) / len(sel), 3) if sel else None}
    by = lambda key: {k: acc(r for r in rows if r[key] == k) for k in sorted({r[key] for r in rows}, key=str)}
    confusion = defaultdict(Counter)
    for r in rows:
        confusion[r["expected"]][r["got"]] += 1
    return {"overall": acc(rows), "by_language": by("language"), "by_expected": by("expected"),
            "by_checked_by": by("checked_by"), "confusion": {e: dict(c) for e, c in confusion.items()},
            "requests": sum(r["requests"] for r in rows), "degraded": sum(r["degraded"] for r in rows)}


async def run(path: str, engine: Engine) -> list[dict]:
    items = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    rows = []
    for it in items:
        before = engine.caller.limiter.buckets["chat"].tokens
        r = await engine.run("check_claim", {"claim": it["claim"]}, {"language": it["language"]}, Priority.INTERACTIVE)
        d = r.data or {}
        rows.append({"id": it["id"], "language": it["language"], "claim": it["claim"], "expected": it["expected"],
                     "got": d.get("verdict"), "checked_by": d.get("checked_by"), "ok": d.get("verdict") == it["expected"],
                     "correction": d.get("correction"), "source_chunk_id": d.get("source_chunk_id"),
                     "degraded": r.degraded, "degrade_reason": r.degrade_reason, "latency_ms": r.latency_ms,
                     "requests": max(0, round(before - engine.caller.limiter.buckets["chat"].tokens)),
                     "events": r.event_names})
        mark = "✓" if rows[-1]["ok"] else "✗"
        print(f"{mark} {it['id']:6} expected {it['expected']:13} got {str(d.get('verdict')):13} by {d.get('checked_by')}"
              f"{'  DEGRADED ' + str(r.degrade_reason) if r.degraded else ''}", flush=True)
    return rows


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="tests/eval/claims_set.jsonl")
    ap.add_argument("--out", default="reports/eval_check_claim.json")
    ap.add_argument("--guard-out", help="write {chat_requests, non_200, server_429, exceptions} here")
    a = ap.parse_args()
    s = load_settings()
    print(f"provider={s.base_url} compact={s.model_for(next(iter(s.models)))}")
    guard = None
    if a.guard_out:
        from scripts.slot_guard import Guard
        guard = Guard("eval_check_claim")
    rows = []
    try:
        async with Engine.create(s, cache=ExactCache(":memory:", s.cache_ttl_s),
                                 transport=guard.transport if guard else None) as engine:
            rows = await run(a.data, engine)
    except Exception as e:
        if guard is None:
            raise
        guard.exception("check_claim", e)
        print(f"EXCEPTION {type(e).__name__}")
    finally:
        if guard:
            print("guard:", json.dumps(guard.write(a.guard_out)))
    summary = summarize(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
