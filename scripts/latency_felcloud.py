"""Step 5 of the FelCloud check: sequential engine.run calls with distinct synthetic questions.

    ENV_FILE=<felcloud profile> python -m scripts.latency_felcloud

Prints per-call and per-class latency (p50 / max), flags explain > 60 s, and checks that an exact repeat
is served from the cache with no provider request (counted in the metrics file).
"""
from __future__ import annotations

import asyncio
import json
import statistics

from config import load_settings
from engine import Engine, Priority

CLASSIFY = [("FRENCH", "Pourquoi la dérivée de x² vaut 2x ? Je ne comprends pas d'où ça vient."),
            ("ENGLISH", "Can you tell me what a limit is and why we need it for derivatives?"),
            ("ARABIC", "كيف أحسب مشتقة الدالة س تكعيب زائد خمسة س؟"),
            ("DARIJA-AR", "شنوا معناها المماس؟ ما فهمتش كيفاش نلقاه"),
            ("ARABIZI", "kifech nel9a el derivee mta3 x carré plus 3 ?")]
EXPLAIN = [("FRENCH", "C'est quoi la dérivée d'un produit de deux fonctions ?"),
           ("ENGLISH", "How do I differentiate a quotient of two functions?"),
           ("ARABIC", "ما هي قاعدة المشتقة المركبة؟")]


def chat_requests(path: str) -> int:
    n = 0
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        n += r.get("kind") == "chat" and r.get("outcome") != "cache_hit"
    return n


async def main() -> None:
    s = load_settings()
    mpath = s.metrics_path
    rows: list[tuple[str, str, float, bool, bool, str | None]] = []
    async with Engine.create() as e:
        for i, (lang, msg) in enumerate(CLASSIFY):
            r = await e.run("classify", {"message": msg}, {"turn_id": f"lat-c{i}"}, Priority.INTERACTIVE)
            rows.append(("classify", lang, r.latency_ms, r.from_cache, r.degraded, r.degrade_reason))
            print(f"classify {lang:9} {r.latency_ms:8.0f} ms degraded={r.degraded} {r.data}", flush=True)
        first = None
        for i, (lang, q) in enumerate(EXPLAIN):
            r = await e.run("explain", {"question": q}, {"language": lang, "turn_id": f"lat-e{i}"})
            first = first or (lang, q)
            rows.append(("explain", lang, r.latency_ms, r.from_cache, r.degraded, r.degrade_reason))
            print(f"explain  {lang:9} {r.latency_ms:8.0f} ms class={r.model_class_used} degraded={r.degraded}"
                  f" {r.degrade_reason or ''} tokens={r.tokens_in}/{r.tokens_out}", flush=True)
        before = chat_requests(mpath)
        r = await e.run("explain", {"question": first[1]}, {"language": first[0], "turn_id": "lat-rep"})
        after = chat_requests(mpath)
        print(f"repeat explain: from_cache={r.from_cache} latency={r.latency_ms:.1f} ms "
              f"provider requests during repeat={after - before}")
    out = {}
    for cls in ("classify", "explain"):
        lat = sorted(x[2] for x in rows if x[0] == cls and not x[3])
        out[cls] = {"n": len(lat), "p50_ms": statistics.median(lat), "max_ms": lat[-1],
                    "over_60s": [round(x) for x in lat if x > 60000]}
        print(f"{cls}: n={len(lat)} p50={out[cls]['p50_ms']:.0f} ms max={lat[-1]:.0f} ms over60s={out[cls]['over_60s']}")
    out["repeat"] = {"from_cache": r.from_cache, "provider_requests": after - before}
    out["calls"] = [dict(zip(("task", "lang", "latency_ms", "cache", "degraded", "reason"), x)) for x in rows]
    json.dump(out, open("reports/felcloud-latency.json", "w"), ensure_ascii=False, indent=1)


asyncio.run(main())
