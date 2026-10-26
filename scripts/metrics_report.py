"""Metrics report from one or more metrics logs (JSONL), side by side.

    python -m scripts.metrics_report metrics/load_normal.jsonl metrics/load_saturated.jsonl \\
        metrics/load_quota_low.jsonl --out reports/load

Writes <out>.md (markdown table) and <out>.csv (section, metric, one column per log), prints the markdown.
Column label = file stem without "load_" (or --labels a,b,c).

Record kinds (metrics/record.py): chat/embed = one provider attempt (or a cache hit, outcome=cache_hit | negative_cache_hit),
route = router decision, run = one engine.run, breaker = breaker state change.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter, defaultdict

Row = tuple[str, str]          # (section, metric)
NOT_SENT = ("cache_hit", "negative_cache_hit")      # chat records that are not provider requests


def load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def pct(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    v = sorted(values)
    return v[max(0, math.ceil(p / 100 * len(v)) - 1)]


def capacity(recs: list[dict], quota_per_min: float, max_concurrent: int,
             learner_rates: list[float]) -> dict[Row, object]:
    """Requests per learner turn (records need a turn_id: load_test --sim-profile capacity) and the active
    learners one key sustains. Every attempt counts, refused or not: that is what the quota is charged."""
    m: dict[Row, object] = {}
    turned = [r for r in recs if r.get("turn_id")]
    if not turned:
        return m
    runs = [r for r in turned if r.get("kind") == "run" and r.get("task") != "compress"]
    attempts = [r for r in turned if r.get("kind") in ("chat", "embed") and r.get("outcome") not in NOT_SENT]
    turns = sorted({r["turn_id"] for r in runs})
    per_turn = Counter(r["turn_id"] for r in attempts)
    ok_per_turn = Counter(r["turn_id"] for r in attempts if r.get("outcome") == "ok")
    runs_per_turn = Counter(r["turn_id"] for r in runs)
    cached = {r["turn_id"] for r in runs if r.get("from_cache")}
    cold = [t for t in turns if t not in cached]
    wait = defaultdict(float)
    for r in runs:
        wait[r["turn_id"]] += r.get("latency_ms") or 0          # classify then follow-up: sequential
    rpt = sum(per_turn[t] for t in turns) / len(turns)
    rpt_cold = sum(per_turn[t] for t in cold) / len(cold) if cold else rpt

    sec = "capacity (per learner turn)"
    m[(sec, "learner turns")] = len(turns)
    m[(sec, "engine calls / turn")] = round(sum(runs_per_turn[t] for t in turns) / len(turns), 2)
    m[(sec, "provider requests / turn (all attempts)")] = round(rpt, 2)
    m[(sec, "ok provider calls / turn")] = round(sum(ok_per_turn[t] for t in turns) / len(turns), 2)
    m[(sec, f"cold turns (no cache hit): n / requests per turn")] = f"{len(cold)} / {rpt_cold:.2f}"
    w = [wait[t] for t in turns]
    m[(sec, "learner wait per turn p50 / p95 (ms)")] = f"{pct(w, 50):.0f} / {pct(w, 95):.0f}"

    ok = [r for r in recs if r.get("kind") == "chat" and r.get("outcome") == "ok"]
    mean_s = (sum(r.get("latency_ms") or 0 for r in ok) / len(ok) / 1000) if ok else 0
    conc_ceiling = max_concurrent * 60 / mean_s if mean_s else float("inf")
    sec = f"capacity estimate (one key, {quota_per_min:g} req/min)"
    m[(sec, "mean provider latency (s)")] = round(mean_s, 1)
    m[(sec, f"concurrency ceiling ({max_concurrent} x 60 / latency, req/min)")] = round(conc_ceiling, 1)
    binding = min(quota_per_min, conc_ceiling)
    m[(sec, "binding limit (req/min)")] = f"{binding:.1f} ({'quota' if binding == quota_per_min else 'concurrency'})"
    for rate in learner_rates:
        label = f"1 turn / {60 / rate:g} s" if rate != 1 else "1 turn / min"
        m[(sec, f"active learners @ {label}: quota-bound (observed / cold)")] = \
            f"{quota_per_min / (rpt * rate):.0f} / {quota_per_min / (rpt_cold * rate):.0f}"
        m[(sec, f"active learners @ {label}: at observed latency (observed / cold)")] = \
            f"{binding / (rpt * rate):.0f} / {binding / (rpt_cold * rate):.0f}"
    return m


def analyze(recs: list[dict]) -> dict[Row, object]:
    m: dict[Row, object] = {}
    attempts = [r for r in recs if r.get("kind") in ("chat", "embed") and r.get("outcome") not in NOT_SENT]
    ok = [r for r in attempts if r.get("outcome") == "ok"]
    runs = [r for r in recs if r.get("kind") == "run"]
    top = [r for r in runs if r.get("task") != "compress"]          # caller-visible runs (compress is nested)
    ts = [r["ts"] for r in recs if "ts" in r]

    sec = "volume"
    m[(sec, "engine.run calls (caller-visible)")] = len(top)
    m[(sec, "nested compress runs")] = len(runs) - len(top)
    m[(sec, "provider requests (all attempts)")] = len(attempts)
    m[(sec, "provider requests ok")] = len(ok)
    m[(sec, "duration (s)")] = round(max(ts) - min(ts), 1) if ts else 0
    m[(sec, "provider requests / min")] = round(len(attempts) / ((max(ts) - min(ts)) / 60), 1) \
        if len(ts) > 1 and max(ts) > min(ts) else 0

    sec = "calls per model"
    per_model = Counter((r.get("model"), r.get("model_class") or "embed") for r in attempts)
    for (model, cls), n in sorted(per_model.items()):
        n_ok = sum(1 for r in ok if r.get("model") == model)
        m[(sec, f"{model} ({cls}) ok / attempts")] = f"{n_ok} / {n}"

    sec = "class split (ok calls)"
    chat_ok = [r for r in ok if r.get("kind") == "chat"]
    by_cls = Counter(r.get("model_class") for r in chat_ok)
    for cls in ("compact", "midsize"):
        m[(sec, f"{cls}")] = f"{by_cls[cls]} ({by_cls[cls] / len(chat_ok):.0%})" if chat_ok else "0"
    by_purpose = Counter(r.get("purpose") or "task" for r in chat_ok)
    for p, n in sorted(by_purpose.items()):
        m[(sec, f"purpose: {p}")] = n

    sec = "tokens"
    m[(sec, "input tokens")] = sum(r.get("tokens_in") or 0 for r in ok)
    m[(sec, "output tokens")] = sum(r.get("tokens_out") or 0 for r in ok)
    m[(sec, "input tokens / ok call")] = round(m[(sec, "input tokens")] / len(ok)) if ok else 0
    m[(sec, "output tokens / ok call")] = round(m[(sec, "output tokens")] / len(ok), 1) if ok else 0
    m[(sec, "outputs cut at the cap (completion_tokens >= cap)")] = sum(1 for r in ok if r.get("truncated_output"))
    sec = "limiter"
    waits = [r.get("wait_ms") or 0 for r in attempts]
    m[(sec, "attempts that waited on the limiter (> 100 ms)")] = sum(1 for w in waits if w > 100)
    m[(sec, "max limiter wait (s)")] = round(max(waits, default=0) / 1000, 1)
    sec = "tokens"

    sec = "cache"
    hits = [r for r in top if r.get("from_cache")]
    m[(sec, "cache hits / runs")] = f"{len(hits)} / {len(top)}"
    m[(sec, "cache hit rate")] = f"{len(hits) / len(top):.0%}" if top else "-"
    m[(sec, "negative cache hits (fast mode, 0 requests)")] = sum(1 for r in top if r.get("from_negative_cache"))
    seen, repeats = set(), 0
    for r in sorted(ok, key=lambda r: r["ts"]):
        h = r.get("request_hash")
        repeats += h in seen
        seen.add(h)
    m[(sec, "repeated identical provider requests")] = repeats

    sec = "refusals -> handling"
    refusals = [r for r in attempts if r.get("outcome") != "ok"]
    m[(sec, "refused attempts (total)")] = len(refusals)
    for (outcome, action), n in sorted(Counter((r.get("outcome"), r.get("action") or "-") for r in refusals).items()):
        m[(sec, f"{outcome} -> {action}")] = n

    sec = "fallbacks / degradation"
    m[(sec, "degraded runs")] = sum(1 for r in top if r.get("degraded"))
    for reason, n in sorted(Counter(r.get("degrade_reason") for r in top if r.get("degraded")).items()):
        m[(sec, f"degraded: {reason}")] = n
    m[(sec, "explain served fast_extractive")] = sum(1 for r in top if r.get("mode") == "fast_extractive")
    m[(sec, "explain served full (generated)")] = sum(1 for r in top if r.get("task") == "explain"
                                                      and r.get("mode") is None and r.get("model_class"))
    for reason, n in sorted(Counter(r.get("degrade_reason") for r in recs
                                    if r.get("kind") == "route" and r.get("degraded")).items()):
        m[(sec, f"route decision: {reason}")] = n

    sec = "circuit breaker"
    br = [r for r in recs if r.get("kind") == "breaker"]
    m[(sec, "trips (closed -> open)")] = sum(1 for r in br if (r.get("detail") or "").startswith("closed -> open"))
    m[(sec, "re-opened after failed probe")] = sum(1 for r in br
                                                   if (r.get("detail") or "").startswith("half_open -> open"))
    m[(sec, "closed after probe")] = sum(1 for r in br if r.get("outcome") == "closed")

    sec = "latency (uncached runs, ms)"
    lat = defaultdict(list)
    for r in top:
        if not r.get("from_cache"):
            lat[r.get("task")].append(r.get("latency_ms") or 0)
            lat["prio: " + str(r.get("priority"))].append(r.get("latency_ms") or 0)
    for key in sorted(lat):
        v = lat[key]
        m[(sec, f"{key} p50 / p95 (n)")] = f"{pct(v, 50):.0f} / {pct(v, 95):.0f} ({len(v)})"
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--labels")
    ap.add_argument("--out", default="reports/metrics")
    ap.add_argument("--quota", type=float, default=20.0, help="requests per minute per key")
    ap.add_argument("--max-concurrent", type=int, default=2)
    ap.add_argument("--learner-rates", default="0.5,1,2", help="turns per learner per minute (assumption)")
    a = ap.parse_args()
    labels = a.labels.split(",") if a.labels else \
        [os.path.splitext(os.path.basename(p))[0].removeprefix("load_") for p in a.logs]
    rates = [float(x) for x in a.learner_rates.split(",")]
    results = []
    for p in a.logs:
        recs = load(p)
        results.append({**analyze(recs), **capacity(recs, a.quota, a.max_concurrent, rates)})
    rows: list[Row] = []
    for res in results:                                   # union of rows, first-seen order...
        rows += [k for k in res if k not in rows]
    sections = list(dict.fromkeys(sec for sec, _ in rows))
    rows.sort(key=lambda r: sections.index(r[0]))         # ...but grouped under their section (stable)

    md = ["| section | metric | " + " | ".join(labels) + " |", "|---|---|" + "---|" * len(labels)]
    last = None
    for row in rows:
        sec, metric = row
        countable = not sec.startswith(("latency", "capacity"))       # absent count = 0; absent estimate = n/a
        cells = [str(r.get(row, "0" if countable else "-")) for r in results]
        md.append(f"| {sec if sec != last else ''} | {metric} | " + " | ".join(cells) + " |")
        last = sec
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    with open(a.out + ".csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["section", "metric", *labels])
        for row in rows:
            w.writerow([*row, *(r.get(row, "") for r in results)])
    print("\n".join(md))
    print(f"\nwrote {a.out}.md and {a.out}.csv")


if __name__ == "__main__":
    main()
