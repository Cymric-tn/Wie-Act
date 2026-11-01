"""The numbers the team shows judges, from the metrics records (kind=chat attempts, kind=run results)."""
from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from typing import Iterable, Mapping

from metrics.review import iso


def read_jsonl(path: str) -> list[dict]:
    """Every record of a metrics JSONL file, as dicts. A half-written last line is skipped."""
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def percentile(sorted_values: list[float], q: float) -> float | None:
    """Nearest rank."""
    if not sorted_values:
        return None
    return sorted_values[max(0, math.ceil(q * len(sorted_values)) - 1)]


def _share(n: int, total: int) -> float:
    return round(n / total, 4) if total else 0.0


def summarize(records: Iterable[Mapping], since: float | None = None, class_id: str | None = None,
              course_id: str | None = None) -> dict:
    runs, attempts = [], []
    for r in records:
        if since is not None and r.get("ts", 0) < since:
            continue
        if class_id and r.get("class_id") != class_id:
            continue
        if course_id and r.get("course_id") != course_id:
            continue
        kind = r.get("kind", "chat")
        (runs if kind == "run" else attempts if kind == "chat" else []).append(r)

    sent = [a for a in attempts if a.get("outcome") not in ("cache_hit", "negative_cache_hit") and not a.get("from_cache")]
    by_class, by_model = Counter(a.get("model_class") or "unknown" for a in sent), Counter(a.get("model") or "unknown" for a in sent)
    hits = sum(1 for r in runs if r.get("from_cache"))
    degraded = [r for r in runs if r.get("degraded")]
    reasons = Counter(r.get("degrade_reason") or "unknown" for r in degraded)
    per_task: dict[str, list[float]] = defaultdict(list)
    for r in runs:
        per_task[r.get("task") or "unknown"].append(float(r.get("latency_ms") or 0.0))
    s429 = [a for a in attempts if a.get("status") == 429]
    return {
        "since": iso(since) if since is not None else None,
        "filters": {"class_id": class_id, "course_id": course_id},
        "runs": {"total": len(runs), "by_task": dict(sorted(Counter(r.get("task") or "unknown" for r in runs).items()))},
        "provider_requests": {"total": len(sent), "by_class": dict(sorted(by_class.items())),
                              "by_model": dict(sorted(by_model.items()))},
        "cache": {"hits": hits, "runs": len(runs), "hit_rate": _share(hits, len(runs))},
        "degraded": {"count": len(degraded), "share": _share(len(degraded), len(runs)),
                     "by_reason": {k: {"count": n, "share": _share(n, len(runs))} for k, n in sorted(reasons.items())}},
        "latency_ms": {t: {"count": len(v), "p50": percentile(sorted(v), 0.5), "p95": percentile(sorted(v), 0.95)}
                       for t, v in sorted(per_task.items())},
        "server_429": {"count": len(s429), "by_outcome": dict(sorted(Counter(a.get("outcome") or "unknown" for a in s429).items()))},
    }
