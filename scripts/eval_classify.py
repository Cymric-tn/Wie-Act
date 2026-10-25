"""Classify accuracy on the synthetic eval set, through engine.run (same path as production).

    ENV_FILE=.env.ollama python -m scripts.eval_classify --model llama3.2:3b
    ENV_FILE=.env.ollama python -m scripts.eval_classify --model qwen2.5:1.5b

Simulator off by default (we measure the model, not simulated latency); no cache (fresh in-memory).
Reports intent / language accuracy (model raw and after rule reconciliation), JSON repair rate,
safe-default rate, average latency.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics

from dotenv import dotenv_values

from config import load_settings
from engine import Engine, Priority
from gatekeeper.cache import ExactCache
from metrics import MemorySink

DATASET = "tests/eval/classify_set.jsonl"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="compact model to evaluate (overrides MODEL_COMPACT)")
    ap.add_argument("--simulate", action="store_true", help="keep the constraint simulator on")
    ap.add_argument("--data", default=DATASET)
    a = ap.parse_args()

    env = {**dotenv_values(os.environ.get("ENV_FILE", ".env")), **os.environ}
    if a.model:
        env["MODEL_COMPACT"] = a.model
    env["SIMULATE_CONSTRAINTS"] = "true" if a.simulate else "false"
    s = load_settings({k: v for k, v in env.items() if v is not None})
    items = [json.loads(line) for line in open(a.data, encoding="utf-8") if line.strip()]

    sink = MemorySink()
    rows = []
    async with Engine.create(s, sink=sink, cache=ExactCache(":memory:", s.cache_ttl_s)) as engine:
        for it in items:
            r = await engine.run("classify", {"message": it["message"]}, {}, Priority.BACKGROUND)
            d = r.data or {}
            rows.append({**it, "group": it.get("group", "original"), "got_intent": d.get("intent"),
                         "got_lang": d.get("language"), "got_complexity": d.get("complexity"),
                         "got_confidence": d.get("confidence"),
                         "model_lang": r.notes.get("model_language", d.get("language")),
                         "repaired": r.repaired, "default": r.safe_default, "rules": r.model_class_used is None,
                         "degraded": r.degraded, "latency_ms": r.latency_ms})

    model = s.model_for(next(iter(s.models)))
    print(f"\nmodel={model}  provider={s.base_url}  simulate={s.simulate}  n={len(rows)}")
    print(f"{'id':6} {'intent exp':15} {'got':15} {'lang exp':8} {'model':8} {'final':8} {'ms':>6}  flags")
    for x in rows:
        flags = ",".join(f for f, on in (("repair", x["repaired"]), ("DEFAULT", x["default"]),
                                         ("rules", x["rules"]), ("degraded", x["degraded"])) if on)
        mark = lambda ok: " " if ok else "✗"
        print(f"{x['id']:6} {x['intent']:15} {mark(x['got_intent'] == x['intent'])}{str(x['got_intent']):14} "
              f"{x['language']:8} {mark(x['model_lang'] == x['language'])}{str(x['model_lang']):7} "
              f"{mark(x['got_lang'] == x['language'])}{str(x['got_lang']):7} {x['latency_ms']:6.0f}  {flags}")

    for group in dict.fromkeys(x["group"] for x in rows):
        report(f"group: {group}", [x for x in rows if x["group"] == group], sink, group)
    if len({x["group"] for x in rows}) > 1:
        report("all", rows, sink, None)


def report(title: str, rows: list[dict], sink: MemorySink, group: str | None) -> None:
    n = len(rows)
    acc = lambda key, exp: sum(x[key] == x[exp] for x in rows) / n
    llm = [x for x in rows if not x["rules"]]
    lat = [x["latency_ms"] for x in llm] or [0]
    ids = {x["id"] for x in rows}
    repairs = sum(1 for r in sink.records if r.kind == "chat" and r.purpose == "repair")
    banded = [x for x in rows if "complexity_max" in x]
    print(f"\n== {title} (n={n})")
    print(f"intent accuracy            {acc('got_intent', 'intent'):6.0%}")
    print(f"language accuracy (model)  {acc('model_lang', 'language'):6.0%}")
    print(f"language accuracy (final)  {acc('got_lang', 'language'):6.0%}   (after rule reconciliation)")
    print(f"both correct (final)       {sum(x['got_intent'] == x['intent'] and x['got_lang'] == x['language'] for x in rows) / n:6.0%}")
    if banded:
        ok = sum((x["got_complexity"] or 9) <= x["complexity_max"] for x in banded)
        print(f"complexity within band     {ok}/{len(banded)}")
    confs = [x["got_confidence"] for x in llm if x["got_confidence"] is not None]
    if confs:
        print(f"max model confidence       {max(confs):.2f}")
    if group is None:
        print(f"JSON repair rate           {repairs / max(1, len(llm)):6.0%}   ({repairs} repair calls)")
    print(f"safe-default rate          {sum(x['default'] for x in rows) / n:6.0%}")
    print(f"latency avg / p50 / max    {statistics.mean(lat):.0f} / {statistics.median(lat):.0f} / {max(lat):.0f} ms")


if __name__ == "__main__":
    asyncio.run(main())
