"""One engine.run per registered task; prints what the judges look at.

    ENV_FILE=.env.ollama python -m scripts.smoke        # run twice: the second run is served from cache
    ENV_FILE=.env.ollama SIM_SATURATION_PROB=1 python -m scripts.smoke --task explain --no-cache
                                                        # mid-size always saturated: the fallback path
"""
from __future__ import annotations

import argparse
import asyncio
import json

from config import load_settings
from engine import Engine, Priority
from gatekeeper.cache import ExactCache

CALLS = [   # synthetic inputs only
    ("classify", {"message": "J'ai trouvé que la dérivée de 3x² est 6x, c'est bon ?"}, {}),
    ("hint", {"problem": "chnowa limite mta3 (x²-1)/(x-1) ki x yemchi l 1 ?", "attempt": "l9it 0/0 donc ma fammech"},
     {"level": "lycée"}),
    ("explain", {"question": "C'est quoi le nombre dérivé, et comment on l'obtient avec le taux d'accroissement ?"},
     {"level": "lycée", "topic": "derivative"}),
    ("check_claim", {"claim": "(x³)' = x²"}, {"language": "FRENCH"}),                              # sympy: 0 requests
    ("check_claim", {"claim": "La dérivée d'un produit est le produit des dérivées."}, {"language": "FRENCH"}),
    ("check_answer", {"problem": "Dérive f(x) = x⁴", "exercise_id": "d01", "steps": ["f(x) = x⁴", "f'(x) = x³"]},
     {"language": "FRENCH"}),
    ("check_answer", {"problem": "Dérive f(x) = x⁴", "steps": ["f(x) = x⁴", "f'(x) = 7x"]}, {"language": "FRENCH"}),
    ("prep_plan", {"scope": {"unit_ids": ["derivees"]}, "deadline": "2099-01-01", "pace_min_per_day": 45,
                   "mastery": {"nombre dérivé": 0.9}, "language": "FRENCH"}, {"course_id": "analyse"}),
    ("ocr_transcribe", {"image": "@sample"}, {"language": "FRENCH"}),      # "@sample": an image drawn with Pillow
]


def sample_image(lines=("Dériver f(x) = 3x² + 5x", "f'(x) = 6x + 5")) -> str:
    """A synthetic 'notebook photo' (1600x1200, resized by the engine to 1 megapixel) as a data URL."""
    import base64
    import io
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1600, 1200), (250, 250, 247))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((80, 100 + i * 120), line, fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def show(r) -> None:
    out = json.dumps(r.data, ensure_ascii=False) if r.data is not None else r.text
    print(f"\n[{r.task}] class={r.model_class_used} model={r.model} latency={r.latency_ms:.0f}ms "
          f"cache={'HIT' if r.from_cache else 'miss'} degraded={r.degraded}"
          + (f" ({r.degrade_reason})" if r.degraded else "")
          + (f" repaired" if r.repaired else "") + (f" SAFE_DEFAULT" if r.safe_default else ""))
    print(f"  tokens in/out: {r.tokens_in}/{r.tokens_out}   route: {r.route_reason}")
    print("  events: " + " -> ".join(f"{e.name}@{e.t_ms:.0f}" for e in r.events))
    if "sources" in r.notes:
        print(f"  sources: {r.notes['sources']}   budget: {r.notes['budget']}"
              + (f"   compress: {r.notes['compress']}" if "compress" in r.notes else ""))
    print(f"  output: {out}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", action="append", help="only these tasks (repeatable)")
    ap.add_argument("--no-cache", action="store_true", help="fresh in-memory cache (e.g. to show a fallback)")
    ap.add_argument("--image", help="a photo of work to transcribe with ocr_transcribe (data URL or base64; default: drawn)")
    a = ap.parse_args()
    s = load_settings()
    models = {c.value: n for c, n in s.models.items()}
    print(f"provider={s.base_url} simulate={s.simulate} saturation_prob={s.sim.saturation_prob} models={models}"
          f" cache={':memory:' if a.no_cache else s.cache_path}")
    cache = ExactCache(":memory:", s.cache_ttl_s) if a.no_cache else None
    async with Engine.create(s, cache=cache) as engine:
        for task, inputs, context in CALLS:
            if not a.task or task in a.task:
                if inputs.get("image") == "@sample":
                    inputs = {**inputs, "image": a.image or sample_image()}
                show(await engine.run(task, inputs, context, Priority.INTERACTIVE))
        if not a.task or "generate_page" in a.task:     # a helper, not a task: one prep page (explain at BACKGROUND)
            page = await engine.generate_page({}, {"concept_id": "puissance", "page_kind": "learn", "mastery": 0.2},
                                              {"language": "FRENCH", "course_id": "analyse"})
            print(f"\n[generate_page] concept={page['concept_id']} degraded={page['degraded']} from_cache={page['from_cache']} "
                  f"quiz={len(page['quiz'])} worked_example={bool(page['worked_example'])}")


if __name__ == "__main__":
    asyncio.run(main())
