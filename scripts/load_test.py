"""Fire a realistic request mix at the engine (real provider behind the constraint simulator).

    ENV_FILE=.env.ollama python -m scripts.load_test --n 50 --mix interactive:0.8,background:0.2 \\
        --sim-profile normal|saturated|quota_low

Profiles (simulator settings layered over the env file; the client side keeps its normal config):
  normal      FelCloud as observed: random 15% mid-size saturation, one mid-size slot, 20 req/min.
  saturated   mid-size saturated 90% of the time, compact 20%: fallbacks, fast mode, breaker trips.
  quota_low   the provider's real quota (10/min, burst 4) is below what the client was told (20/min):
              quota_exhausted refusals with a stated delay, low quota headers -> quota_low routing.
  capacity    normal provider, but LEARNER TURNS instead of independent calls: each turn is classify,
              then the task router.policy.follow_up picks (explain, hint, quiz...; chitchat / off-topic:
              nothing more), like the feature layer. Turns arrive at --rate/2 per minute (default 15/2 = 7.5),
              i.e. an offered load of about 15 engine calls per minute, under the 20/min cap.
              Every record carries a turn_id, so metrics_report can compute requests per turn.

Arrivals are Poisson (--rate per minute). Questions are drawn with replacement from small pools, so
repeats happen naturally (cache + coalescing). Each profile gets a fresh in-memory cache, a fresh
breaker and its own metrics file: metrics/load_<profile>.jsonl (truncated at start).
All texts are synthetic.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from collections import Counter

from dotenv import dotenv_values

from config import load_settings
from engine import Engine, Priority
from gatekeeper.cache import ExactCache
from simulator.profiles import LOAD_TEST_PROFILES, env_overrides

DEFAULT_RATE = {"capacity": 15.0}

TURNS = [   # learner messages; the follow-up task is chosen from classify's intent
    "C'est quoi le nombre dérivé et le taux d'accroissement ?",
    "Comment trouver l'équation de la tangente ?",
    "kifech nderivi polynome terme par terme ?",
    "3lech dérivée mta3 constante hiya 0 ?",
    "How do I know if a function is increasing?",
    "ما هو تعريف العدد المشتق؟",
    "كيفاش نكتب معادلة المماس؟",
    "Je bloque : dériver f(x) = x(x + 1). J'ai trouvé f'(x) = 1, c'est juste ?",
    "Résoudre 2x + 6 = 0 : j'ai trouvé x = 3, c'est bon ?",
    "Find the minimum of f(x) = x² − 4x + 1, I got x = 4. Where is my mistake?",
    "9addech el dérivée mta3 x³ ? ena l9it x²",
    "Donne-moi une piste pour dériver 3x² + 5x − 7, sans la réponse",
    "Merci beaucoup !",
    "ya5i el match chkoun rbe7?",
]


CLASSIFY = [
    "C'est quoi une dérivée ?", "chnowa ya3ni limite? ma fhemt chay fel cours",
    "J'ai trouvé que la dérivée de 3x² est 6x, c'est bon ?", "Merci beaucoup !",
    "Tu peux me poser 3 questions sur les fractions ?", "ما هو الفرق بين المتتالية الحسابية والهندسية؟",
    "My Python loop prints the last item twice, what's wrong?", "عطيني تمرين صغير على المشتقات باش نتدرب",
    "Je bloque sur cet exercice de probabilités, une piste ?", "Can you give me a hint for this integral?",
    "ya5i el match mta3 lbara7 chkoun rbe7?", "Comment on calcule la dérivée d'un produit ?",
]
HINT = [
    ({"problem": "Résoudre 2x + 6 = 0", "attempt": "x = 3"}, {"language": "FRENCH"}),
    ({"problem": "Calculer la dérivée de f(x) = 3x² + 5x − 7"}, {"language": "FRENCH"}),
    ({"problem": "Find the minimum of f(x) = x² − 4x + 1", "attempt": "x = 4"}, {"language": "ENGLISH"}),
    ({"problem": "حل المعادلة 3x − 9 = 0"}, {"language": "ARABIC"}),
    ({"problem": "9addech el dérivée mta3 x³ ?", "attempt": "x²"}, {"language": "ARABIZI"}),
    ({"problem": "Montrer que f(x) = x² est croissante sur [0, +∞["}, {"language": "FRENCH"}),
    ({"problem": "Dériver f(x) = x(x + 1)", "attempt": "f'(x) = 1"}, {"language": "FRENCH"}),
    ({"problem": "What is the slope of the tangent to y = x² at x = 3?"}, {"language": "ENGLISH"}),
]
EXPLAIN = [   # the glossary expands Darija / Arabizi / English terms to French course terms
    ({"question": "C'est quoi le nombre dérivé et le taux d'accroissement ?"}, {"level": "lycée"}),
    ({"question": "Comment trouver l'équation de la tangente ?"}, {"level": "lycée"}),
    ({"question": "kifech nderivi polynome terme par terme ?"}, {"level": "lycée"}),
    ({"question": "3lech dérivée mta3 constante hiya 0 ?"}, {}),
    ({"question": "How do I know if a function is increasing?"}, {}),
    ({"question": "ما هو تعريف العدد المشتق؟"}, {}),
    ({"question": "Pourquoi la dérivée d'un produit n'est pas le produit des dérivées ?"}, {"level": "lycée"}),
    ({"question": "When does a zero of the derivative give a maximum?"}, {}),
]
TASK_MIX = [("classify", 0.4), ("hint", 0.3), ("explain", 0.3)]


def parse_mix(s: str) -> list[tuple[Priority, float]]:
    out = []
    for part in s.split(","):
        name, _, w = part.partition(":")
        out.append((Priority(name.strip().upper()), float(w)))
    return out


def plan(n: int, mix, rate_per_min: float, rng: random.Random) -> list[tuple[float, str, dict, dict, Priority]]:
    t, jobs = 0.0, []
    for _ in range(n):
        t += rng.expovariate(rate_per_min / 60.0)
        task = rng.choices([x for x, _ in TASK_MIX], [w for _, w in TASK_MIX])[0]
        prio = rng.choices([p for p, _ in mix], [w for _, w in mix])[0]
        if task == "classify":
            inputs, context = {"message": rng.choice(CLASSIFY)}, {}
        else:
            inputs, context = rng.choice(HINT if task == "hint" else EXPLAIN)
        jobs.append((t, task, dict(inputs), dict(context), prio))
    return jobs


def plan_turns(n: int, mix, turns_per_min: float, rng: random.Random) -> list[tuple[float, str, str, Priority]]:
    t, jobs = 0.0, []
    for i in range(n):
        t += rng.expovariate(turns_per_min / 60.0)
        prio = rng.choices([p for p, _ in mix], [w for _, w in mix])[0]
        jobs.append((t, f"t{i:03d}", rng.choice(TURNS), prio))
    return jobs


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--mix", default="interactive:0.8,background:0.2")
    ap.add_argument("--sim-profile", choices=LOAD_TEST_PROFILES, default="normal")
    ap.add_argument("--rate", type=float, default=None,
                    help="arrivals per minute (Poisson); capacity: offered engine calls per minute")
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    out = f"metrics/load_{a.sim_profile}.jsonl"
    if os.path.exists(out):
        os.remove(out)
    env = {**dotenv_values(os.environ.get("ENV_FILE", ".env")), **os.environ,
           "SIMULATE_CONSTRAINTS": "true", "METRICS_PATH": out, "SIM_SEED": str(a.seed), **env_overrides(a.sim_profile)}
    s = load_settings({k: v for k, v in env.items() if v is not None})
    rng = random.Random(a.seed)
    a.rate = a.rate or DEFAULT_RATE.get(a.sim_profile, 30.0)
    turns = a.sim_profile == "capacity"
    jobs = plan_turns(a.n, parse_mix(a.mix), a.rate / 2, rng) if turns else plan(a.n, parse_mix(a.mix), a.rate, rng)
    unit = f"turns at {a.rate / 2:g}/min (~{a.rate:g} engine calls/min offered)" if turns else f"rate={a.rate:g}/min"
    print(f"profile={a.sim_profile} n={a.n} mix={a.mix} {unit} -> {out}")
    print(f"  sim: mid saturation {s.sim.saturation_prob}, compact saturation {s.sim.compact_saturation_prob}, "
          f"provider quota {s.sim.rate_per_min:g}/min burst {s.sim.burst} (client believes {s.rate_per_min:g}/min)")

    results, errors = [], []
    t0 = time.monotonic()
    async with Engine.create(s, cache=ExactCache(":memory:", s.cache_ttl_s)) as engine:
        async def fire(at, task, inputs, context, prio):
            await asyncio.sleep(max(0.0, at - (time.monotonic() - t0)))
            try:
                results.append(await engine.run(task, inputs, context, prio))
            except Exception as e:                        # the engine contract says this never happens
                errors.append(f"{task}: {type(e).__name__}: {e}")

        async def turn(at, turn_id, message, prio):
            await asyncio.sleep(max(0.0, at - (time.monotonic() - t0)))
            try:
                c = await engine.run("classify", {"message": message}, {"turn_id": turn_id}, prio)
                results.append(c)
                follow = engine.router.follow_up(c.data or {}).task      # the routing layer's intent -> task
                if follow:
                    ctx = {"turn_id": turn_id, "language": c.data["language"], "level": "lycée",
                           "confusion": c.data.get("confusion")}
                    if follow in ("quiz", "diagnostic"):
                        task, inputs = "quiz", {"message": message, "diagnostic": follow == "diagnostic"}
                    else:
                        task = follow
                        inputs = {"question": message} if follow == "explain" else {"problem": message}
                    results.append(await engine.run(task, inputs, ctx, prio))
            except Exception as e:
                errors.append(f"turn {turn_id}: {type(e).__name__}: {e}")
        await asyncio.gather(*((turn(*j) if turns else fire(*j)) for j in jobs))
    wall = time.monotonic() - t0

    by_reason = Counter(r.degrade_reason for r in results if r.degraded)
    print(f"\ndone in {wall:.0f}s: {len(results)} results, {len(errors)} exceptions raised to the caller")
    print(f"  cache hits {sum(r.from_cache for r in results)}, degraded {sum(r.degraded for r in results)} "
          f"{dict(by_reason)}, fast_extractive {sum(r.mode == 'fast_extractive' for r in results)}")
    for e in errors:
        print("  EXCEPTION", e)
    with open(out.replace(".jsonl", ".summary.json"), "w") as f:
        json.dump({"profile": a.sim_profile, "n": a.n, "unit": "turns" if turns else "calls", "rate": a.rate,
                   "wall_s": round(wall, 1), "exceptions": errors}, f)


if __name__ == "__main__":
    asyncio.run(main())
