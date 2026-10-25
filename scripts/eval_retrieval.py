"""Retrieval eval: hit@1 / hit@3 on questions with a known expected chunk, plus off-topic rejection.

    ENV_FILE=.env.ollama python -m scripts.eval_retrieval                  # question only
    ENV_FILE=.env.ollama python -m scripts.eval_retrieval --mode glossary  # + data/glossary.yaml expansion
    ENV_FILE=.env.ollama python -m scripts.eval_retrieval --mode policy    # + language plan (= the engine)
    ENV_FILE=.env.ollama python -m scripts.eval_retrieval                  # all three

Two sets by default: the original set (tests/eval/retrieval_set.jsonl, unchanged) and the
cross-language set (Darija / Arabizi questions whose answer is only in a French doc).
No LLM calls: the glossary is deterministic. The index is rebuilt from DOCS_DIR.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from dotenv import dotenv_values

from config import load_settings
from rag.chunker import chunk_dir
from rag.glossary import Glossary
from rag.retriever import language_plan, make_retriever

DATASETS = ["tests/eval/retrieval_set.jsonl", "tests/eval/retrieval_crosslang.jsonl",
            "tests/eval/retrieval_manual.jsonl"]


async def evaluate(r, items, glossary: Glossary | None, policy: bool = False) -> dict:
    """policy = what the engine does: glossary + language_plan (Darija/Arabizi -> French chunks,
    Arabic -> Arabic then French). Items whose answer moves to a French chunk carry expected_french."""
    pos, neg = [x for x in items if x["expected"]], [x for x in items if not x["expected"]]
    h1 = h3 = rejected = 0
    rows = []
    for x in items:
        exp = glossary.expand(x["question"]) if glossary else None
        kws = list(exp.terms) if exp else []
        hits = []
        for f in (language_plan(x["language"]) if policy else [None]):
            hits = await r.search(x["question"], 3, f, expansion=exp)
            if hits:
                break
        if policy and x.get("expected_french") and x["language"] in ("DARIJA", "ARABIZI"):
            x = {**x, "expected": x["expected_french"]}
        ids = [h.chunk.chunk_id for h in hits]
        if x["expected"]:
            want = x["expected"] if isinstance(x["expected"], list) else [x["expected"]]   # any of these
            ok1, ok3 = bool(ids) and ids[0] in want, any(i in want for i in ids)
            h1, h3 = h1 + ok1, h3 + ok3
            mark = ("✓" if ok1 else "✗") + ("✓" if ok3 else "✗")
        else:
            rejected += not hits
            mark = ("✓" if not hits else "✗") + "-"
        rows.append((x, mark, hits, kws))
    return {"rows": rows, "h1": h1, "h3": h3, "rejected": rejected, "pos": len(pos), "neg": len(neg)}


def show(name: str, res: dict, with_kw: bool) -> None:
    print(f"\n=== mode: {name}")
    print(f"{'id':6} {'lang':8} @1@3  top-3 (score / coverage)" + ("   [glossary terms]" if with_kw else ""))
    for x, mark, hits, kws in res["rows"]:
        top = "  ".join(f"{h.chunk.chunk_id.split('#')[1][:26]} ({h.score:.1f}/{h.coverage:.2f})" for h in hits) \
            or "(nothing relevant)"
        print(f"{x['id']:6} {x['language']:8} {mark:4}  {top}" + (f"   {kws}" if with_kw else ""))
        if x["expected"] and mark[0] == "✗":
            want = x["expected"] if isinstance(x["expected"], list) else [x["expected"]]
            print(f"{'':20}expected {' or '.join(w.split('#')[1] for w in want)}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", default=DATASETS)
    ap.add_argument("--mode", choices=["plain", "glossary", "policy", "all"], default="all")
    a = ap.parse_args()
    env = {**dotenv_values(os.environ.get("ENV_FILE", ".env")), **os.environ}
    s = load_settings({k: v for k, v in env.items() if v is not None})
    if s.retriever != "bm25":
        raise SystemExit("this eval runs the default BM25 retriever; set RETRIEVER=bm25")
    r = make_retriever(s.retriever, s.rag_min_coverage, glossary_weight=s.glossary_weight)
    await r.index(chunk_dir(s.docs_dir, s.chunk_max_tokens))
    glossary = Glossary.load(s.glossary_path)
    print(f"retriever={s.retriever} min_coverage={s.rag_min_coverage} glossary={s.glossary_path} "
          f"({glossary.entries} entries, {glossary.variants} variants, weight {s.glossary_weight}) chunks={len(r.chunks)}")

    summary = []
    for path in a.data:
        items = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
        name = os.path.basename(path).removesuffix(".jsonl")
        for mode in (["plain", "glossary", "policy"] if a.mode == "all" else [a.mode]):
            res = await evaluate(r, items, glossary if mode != "plain" else None, policy=mode == "policy")
            show(f"{name} / {mode}", res, mode != "plain")
            summary.append((name, mode, res))

    cell = lambda n, d: f"{n}/{d} {n / d:.0%}" if d else "-"
    print(f"\n{'set':22} {'mode':9} {'hit@1':>10} {'hit@3':>10} {'off-topic rejected':>20}")
    for name, mode, v in summary:
        print(f"{name:22} {mode:9} {cell(v['h1'], v['pos']):>10} {cell(v['h3'], v['pos']):>10} "
              f"{(str(v['rejected']) + '/' + str(v['neg'])) if v['neg'] else '-':>20}")


if __name__ == "__main__":
    asyncio.run(main())
