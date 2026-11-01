"""The share of interactive explain results degraded by `deadline`, from a mixed-load report.

    python -m scripts.explain_deadline reports/felcloud-2026-11-02-load-mixed.json --out reports/felcloud-2026-11-02-explain-deadline

A `deadline` degrade means the explanation was not ready within the engine's 25 s and the learner got fast mode
(course excerpts) while the generation went on into the cache. With a small n the share is coarse: the report says n.
"""
from __future__ import annotations

import argparse
import json
import sys


def share(report: dict) -> dict:
    ex = report["interactive"]["explain"]
    n, k = ex["n"], ex["degraded_by_deadline"]
    by_task = report["interactive"]["by_task"].get("explain", {})
    return {"source": report.get("mode"), "cache": report.get("cache"), "interactive_explain": n,
            "degraded_by_deadline": k, "share": round(k / n, 3) if n else None,
            "explain_latency_s": {q: by_task.get(q) for q in ("p50_s", "p95_s", "max_s")}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--out", required=True, help="path prefix; writes <out>.json and <out>.md")
    a = ap.parse_args(argv)
    out = share(json.load(open(a.report, encoding="utf-8")))
    with open(f"{a.out}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    s = f"{out['share']:.1%}" if out["share"] is not None else "n/a (no explain request)"
    with open(f"{a.out}.md", "w", encoding="utf-8") as f:
        f.write(f"# Explain results degraded by `deadline`\n\nSource: {a.report} ({out['source']}, cache {out['cache']}).\n\n"
                f"- interactive explain requests: **{out['interactive_explain']}**\n"
                f"- degraded by `deadline`: **{out['degraded_by_deadline']}** ({s})\n"
                f"- explain latency: {out['explain_latency_s']}\n\n"
                "A `deadline` degrade is fast mode (course excerpts) after 25 s; the generation kept going into the cache.\n")
    print(open(f"{a.out}.md", encoding="utf-8").read())
    return 0


if __name__ == "__main__":
    sys.exit(main())
