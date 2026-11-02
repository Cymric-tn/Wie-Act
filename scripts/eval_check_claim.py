"""Accuracy of check_claim on two sets, reported separately and together:
  original  tests/eval/claims_set.jsonl      30 claims (true, false, partly true, off-course; FR, EN, AR), unchanged since v1.3
  added     tests/eval/claims_set_v14.jsonl  30 claims, 20 of them compound (mixed true / false / off-course parts)

    ENV_FILE=.env.real python -m scripts.eval_check_claim --out reports/eval_check_claim.json
    CLAIM_VERDICT_CLASS=midsize ENV_FILE=.env.real python -m scripts.eval_check_claim --out ...   # the 8B judges each part

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


DEFAULT_SETS = (("original", "tests/eval/claims_set.jsonl"), ("added", "tests/eval/claims_set_v14.jsonl"))


def summarize_sets(rows: list[dict]) -> dict:
    """The summary of every set on its own, of all rows, and of the compound / single claims and the claims a model
    decides (some part checked_by llm), which are the ones a model choice can change."""
    out = {}
    for name in sorted({r["set"] for r in rows}):
        out[name] = summarize([r for r in rows if r["set"] == name])
    out["compound"] = summarize([r for r in rows if r.get("parts")])
    out["single"] = summarize([r for r in rows if not r.get("parts")])
    out["model_decided"] = summarize([r for r in rows if r["checked_by"] in ("llm", "mixed")])
    parts = [(p, e) for r in rows for p, e in zip(r.get("parts") or [], r.get("parts_expected") or [])]
    out["per_part"] = {"n": len(parts), "correct": sum(p["verdict"] == e for p, e in parts),
                       "accuracy": round(sum(p["verdict"] == e for p, e in parts) / len(parts), 3) if parts else None}
    lat = sorted(r["latency_ms"] for r in rows if r["requests"])
    out["latency_ms_requests"] = {"n": len(lat), "p50": lat[len(lat) // 2] if lat else None, "max": lat[-1] if lat else None}
    return out


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


async def run(paths: list[tuple[str, str]], engine: Engine) -> list[dict]:
    items = [{**json.loads(line), "set": name} for name, path in paths for line in open(path, encoding="utf-8") if line.strip()]
    rows = []
    for it in items:
        r = await engine.run("check_claim", {"claim": it["claim"]}, {"language": it["language"]}, Priority.INTERACTIVE)
        d = r.data or {}
        rows.append({"id": it["id"], "set": it["set"], "language": it["language"], "claim": it["claim"], "expected": it["expected"],
                     "got": d.get("verdict"), "checked_by": d.get("checked_by"), "ok": d.get("verdict") == it["expected"],
                     "parts": [{k: p[k] for k in ("claim", "verdict", "checked_by")} for p in d.get("parts") or []] or None,
                     "parts_expected": it.get("parts_expected"),
                     "correction": d.get("correction"), "source_chunk_id": d.get("source_chunk_id"),
                     "degraded": r.degraded, "degrade_reason": r.degrade_reason, "latency_ms": r.latency_ms,
                     "requests": sum(1 for e in r.events if e.name == "generating"),   # not the window delta: it refills while we wait
                     "events": r.event_names})
        mark = "✓" if rows[-1]["ok"] else "✗"
        print(f"{mark} {it['id']:6} expected {it['expected']:13} got {str(d.get('verdict')):13} by {d.get('checked_by')}"
              f"{'  DEGRADED ' + str(r.degrade_reason) if r.degraded else ''}", flush=True)
    return rows


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", action="append", metavar="NAME=PATH", help="a set (repeatable); default: both sets")
    ap.add_argument("--out", default="reports/eval_check_claim.json")
    ap.add_argument("--guard-out", help="write {chat_requests, non_200, server_429, exceptions} here")
    a = ap.parse_args()
    s = load_settings()
    sets = [tuple(x.split("=", 1)) if "=" in x else ("set", x) for x in a.data] if a.data else list(DEFAULT_SETS)
    print(f"provider={s.base_url} compact={s.model_for(next(iter(s.models)))} "
          f"verdict_class={s.claim_verdict_class.value} sets={[n for n, _ in sets]}")
    guard = None
    if a.guard_out:
        from scripts.slot_guard import Guard
        guard = Guard("eval_check_claim")
    rows = []
    try:
        async with Engine.create(s, cache=ExactCache(":memory:", s.cache_ttl_s),
                                 transport=guard.transport if guard else None) as engine:
            rows = await run(sets, engine)
    except Exception as e:
        if guard is None:
            raise
        guard.exception("check_claim", e)
        print(f"EXCEPTION {type(e).__name__}")
    finally:
        if guard:
            print("guard:", json.dumps(guard.write(a.guard_out)))
    summary = {**summarize(rows), "sets": summarize_sets(rows)}      # "overall" etc. over all rows; "sets" = each set, compound, ...
    summary["verdict_class"] = s.claim_verdict_class.value
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    asyncio.run(main())
