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
EXTRA = "tests/eval/explain_extra.jsonl"       # 10 more FRENCH / ENGLISH / ARABIC questions (--extra)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=1, help="repeat the whole set (fresh cache each run)")
    ap.add_argument("--out", default="reports/eval_explain.json")
    ap.add_argument("--extra", action="store_true", help=f"also ask the questions of {EXTRA}")
    a = ap.parse_args()
    env = {**dotenv_values(os.environ.get("ENV_FILE", ".env")), **os.environ, "SIMULATE_CONSTRAINTS": "false"}
    s = load_settings({k: v for k, v in env.items() if v is not None})
    items = [json.loads(line) for p in DATASETS + ([EXTRA] if a.extra else []) for line in open(p, encoding="utf-8") if line.strip()]
    items = [x for x in items if x["expected"]]

    outcomes, counts, saved = Counter(), Counter(), []
    lat, tout = [], []
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
                    counts["language_drift"] += "explain_guard:language_drift" in reasons
                    counts["contamination"] += any(x.endswith(":contamination") for x in reasons)
                    counts["truncated_output"] += bool(r.notes.get("truncated_output"))
                    counts["formulas_verified"] += r.notes.get("formulas_verified") or 0
                    counts["answers_with_verified"] += bool(r.notes.get("formulas_verified"))
                    if not r.from_cache:
                        lat.append(r.latency_ms / 1000)
                        tout.append(r.tokens_out)
                spans = {k: r.notes[k] for k in ("wrong_math", "ungrounded", "new_examples") if r.notes.get(k)}
                print(f"run{run_no} {x['id']:7} {x['language']:8} {kind:30} {r.latency_ms / 1000:5.1f}s  {spans}")
                saved.append({"run": run_no, "id": x["id"], "question": x["question"], "language": x["language"],
                              "outcome": kind, "reasons": reasons, "latency_s": round(r.latency_ms / 1000, 1),
                              "tokens_out": r.tokens_out, "from_cache": r.from_cache,
                              "truncated_output": bool(r.notes.get("truncated_output")), "body": (r.data or {}).get("body"),
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
    print(f"language_drift:   {pct(counts['language_drift'], n)}")
    print(f"contamination:    {pct(counts['contamination'], n)}")
    print(f"truncated_output: {pct(counts['truncated_output'], n)}")
    print(f"formulas verified (sympy): {counts['formulas_verified']} in {counts['answers_with_verified']} answers")
    if tout:
        q = lambda v, f: sorted(v)[min(len(v) - 1, round(f * (len(v) - 1)))]
        print(f"output tokens / generated answer: mean {sum(tout) / len(tout):.1f} (n={len(tout)}); "
              f"latency p50 {q(lat, .5):.1f}s p95 {q(lat, .95):.1f}s")
    print(f"outcomes: {dict(outcomes)}")


if __name__ == "__main__":
    asyncio.run(main())
