"""Explain on every on-topic question of the retrieval eval sets: formula checks, loops, salvage.

    ENV_FILE=.env.ollama python -m scripts.eval_explain [--runs 2]

Real models, simulator off, a fresh in-memory cache per run (so a second run generates again; explain
runs at temperature 0.3). Per question: outcome, wrong_math / ungrounded spans, new examples.
Summary: loop rate, salvage rate (loops kept as truncated text vs sent to fast mode), how many answers
contain new examples, wrong_math and ungrounded_formula counts. Every answer is saved (--out) so the
formula check can be recomputed offline without regenerating.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter

from dotenv import dotenv_values

from config import load_settings
from engine import Engine, Priority
from gatekeeper.cache import ExactCache
from metrics import MemorySink

DATASETS = ["tests/eval/retrieval_set.jsonl", "tests/eval/retrieval_crosslang.jsonl",
            "tests/eval/retrieval_manual.jsonl"]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1, help="repeat the whole set (fresh cache each run)")
    ap.add_argument("--out", default="reports/eval_explain.json")
    a = ap.parse_args()
    env = {**dotenv_values(os.environ.get("ENV_FILE", ".env")), **os.environ, "SIMULATE_CONSTRAINTS": "false"}
    s = load_settings({k: v for k, v in env.items() if v is not None})
    items = [json.loads(line) for p in DATASETS for line in open(p, encoding="utf-8") if line.strip()]
    items = [x for x in items if x["expected"]]

    outcomes, counts, saved = Counter(), Counter(), []
    for run_no in range(1, a.runs + 1):
        async with Engine.create(s, sink=MemorySink(), cache=ExactCache(":memory:", s.cache_ttl_s)) as engine:
            for x in items:
                r = await engine.run("explain", {"question": x["question"]}, {"language": x["language"]},
                                     Priority.BACKGROUND)
                reasons = r.notes.get("degrade_reasons") or ([r.degrade_reason] if r.degrade_reason else [])
                found = r.notes.get("retrieval") != "no_relevant_chunks"
                kind = (r.degrade_reason or "ok") if found else "not_found"
                outcomes[kind] += 1
                if found:
                    counts["answers"] += 1
                    counts["loop_truncated"] += "explain_guard:loop_truncated" in reasons
                    counts["loop_fast_mode"] += "explain_guard:degenerate" in reasons
                    for key in ("wrong_math", "ungrounded", "new_examples"):
                        counts[key] += bool(r.notes.get(key))
                spans = {k: r.notes[k] for k in ("wrong_math", "ungrounded", "new_examples") if r.notes.get(k)}
                print(f"run{run_no} {x['id']:7} {x['language']:8} {kind:30} {r.latency_ms / 1000:5.1f}s  {spans}")
                saved.append({"run": run_no, "id": x["id"], "question": x["question"], "language": x["language"],
                              "outcome": kind, "reasons": reasons, "body": (r.data or {}).get("body"),
                              **{k: r.notes.get(k, []) for k in ("wrong_math", "ungrounded", "new_examples")},
                              "sources": r.notes.get("sources", [])})

    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(saved, f, ensure_ascii=False, indent=1)
    n = counts["answers"]
    loops = counts["loop_truncated"] + counts["loop_fast_mode"]
    pct = lambda k, d: f"{k}/{d} ({k / max(1, d):.0%})"
    print(f"\nexplain answers: {n} (+{outcomes['not_found']} 'not in the course') over {a.runs} run(s)")
    print(f"loop rate:        {pct(loops, n)}")
    print(f"salvage rate:     {pct(counts['loop_truncated'], loops)} kept as truncated text; "
          f"{counts['loop_fast_mode']} to fast mode")
    print(f"new examples:     {pct(counts['new_examples'], n)} answers")
    print(f"wrong_math:       {pct(counts['wrong_math'], n)}")
    print(f"ungrounded:       {pct(counts['ungrounded'], n)}")
    print(f"outcomes: {dict(outcomes)}")


if __name__ == "__main__":
    asyncio.run(main())
