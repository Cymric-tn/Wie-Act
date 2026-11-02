"""Offline: run the formula check again on every saved explain answer (no LLM, no network).

    python -m scripts.recheck_formulas [--out reports/recheck-formulas.json] [files...]

Each saved answer is checked against the chunks it was generated from (`sources` ids, looked up in the chunk index) plus
the question, as the engine does. Prints, per file and in total, how many answers have an `ungrounded` formula, a
`wrong_math` formula, or (v1.4) an unchecked span. Run it on two checkouts to get a before / after.
"""
from __future__ import annotations

import argparse
import ast
import json
import os

from engine.grounding import check_formulas
from rag.store import load_chunks

FILES = ("reports/eval_explain_felcloud.json", "reports/eval_explain.json", "reports/eval_explain.run-a.json",
         "reports/eval_explain.before-v1.json")
INDEX = "data/index/chunks.json"


def _list(v):
    if isinstance(v, str):
        try:
            return ast.literal_eval(v)
        except (ValueError, SyntaxError):
            return []
    return v or []


def recheck(files, index: str = INDEX) -> dict:
    chunks = {c.chunk_id: c.text for c in load_chunks(index)}
    out = {"files": {}, "answers": 0, "ungrounded": 0, "wrong_math": 0, "unchecked": 0, "spans": {}}
    for path in files:
        if not os.path.exists(path):
            continue
        rows = [r for r in json.load(open(path, encoding="utf-8")) if r.get("body")]
        f = {"answers": len(rows), "ungrounded": 0, "wrong_math": 0, "unchecked": 0}
        for r in rows:
            sources = [chunks[i] for i in _list(r.get("sources")) if i in chunks] + [str(r.get("question") or "")]
            c = check_formulas(r["body"], sources)
            for key, res in (("ungrounded", c["ungrounded"]), ("wrong_math", c["wrong"]), ("unchecked", c.get("unchecked", []))):
                if res:
                    f[key] += 1
                    out["spans"].setdefault(key, []).append({"file": os.path.basename(path), "id": r.get("id"),
                                                             "run": r.get("run"), "spans": res})
        out["files"][os.path.basename(path)] = f
        for k in ("answers", "ungrounded", "wrong_math", "unchecked"):
            out[k] += f[k]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=list(FILES))
    ap.add_argument("--out")
    a = ap.parse_args()
    res = recheck(a.files)
    print(json.dumps({k: v for k, v in res.items() if k != "spans"}, indent=1))
    for key, items in res["spans"].items():
        print(f"\n{key}:")
        for it in items:
            print(f"  {it['file']} {it['id']} run {it['run']}: {it['spans']}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
