"""Mixed load: 3 learners in tutor sessions, 1 room doing checks, 1 prep plan generating pages in the background.

    python -m scripts.load_mixed --virtual                       # simulated FelCloud in VIRTUAL time: offline, seconds
    ENV_FILE=.env.real python -m scripts.load_mixed --real      # FelCloud, in a slot, in real time (--duration 240)
    ENV_FILE=.env.real python -m scripts.load_mixed --real --probe-vision   # first: does a vision request use the window?

What it checks (the v1.3 acceptance numbers):
  * interactive p95 latency stays under 25 s while the prep plan generates in the background;
  * 0 server 429s (the engine's own window and reserve keep us under the platform's 20 / min and 2 in flight);
  * 0 exceptions out of engine.run (it never raises a provider error);
  * the densest 60 s window never holds more than 20 chat sends (vision included).
--virtual drives the real engine through the constraint simulator (FelCloud's latencies, quota, concurrency 429s,
random saturation) with canned model replies and a virtual clock: it tests the engine's scheduling, not the models,
and says so in the report. --real does the same against the live API: every request spends the team's quota.
Both write reports/load-mixed-<mode>-v13.{json,md}.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
from collections import Counter, defaultdict

import httpx

from engine import Engine, Priority
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient
from metrics import MemorySink

LEARNERS = ("L1", "L2", "L3")
CTX = {"course_id": "analyse", "class_id": "C1", "language": "FRENCH"}
EXPLAIN_Q = ["C'est quoi le taux d'accroissement et le nombre dérivé ?", "Explique-moi la dérivée d'une constante",
             "Comment dériver une puissance ?", "C'est quoi la tangente à une courbe ?",
             "Pourquoi la dérivée d'un produit n'est pas le produit des dérivées ?",
             "Comment étudier les variations d'une fonction avec la dérivée ?"]
HINT_Q = [("Dérive f(x) = x⁴", "x³"), ("Dérive f(x) = x² + 7", "2x + 7"), ("Dérive f(x) = x(x + 3)", "1"),
          ("Dérive f(x) = 3x² + 5x", "6x")]
CLAIMS = ["La dérivée d'une constante est 0.", "La dérivée d'un produit est le produit des dérivées.", "(x³)' = x²",
          "Si f'(a) = 0, alors f a forcément un extremum en a.", "La tangente en a a pour équation y = f'(a)(x − a) + f(a).",
          "(x²)' = 2x"]


class SpyTransport(httpx.AsyncBaseTransport):
    """Records every request the engine sends: time, model, status and the platform's rate headers."""

    def __init__(self, inner: httpx.AsyncBaseTransport, clock):
        self.inner, self.clock = inner, clock
        self.log: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        t0 = self.clock()
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        resp = await self.inner.handle_async_request(request)
        h = {k.lower(): v for k, v in resp.headers.items()}
        self.log.append({"t0": t0, "t1": self.clock(), "path": request.url.path, "model": body.get("model"),
                         "status": resp.status_code,
                         "remaining": h.get("x-ratelimit-api_key-remaining-requests"),
                         "remaining_parallel": h.get("x-ratelimit-api_key-remaining-max_parallel_requests"),
                         "rate_limit_type": h.get("rate_limit_type")})
        return resp

    async def aclose(self) -> None:
        await self.inner.aclose()


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))], 2)


def densest_window(times: list[float], window: float = 60.0) -> int:
    ts = sorted(times)
    best, j = 0, 0
    for i, t in enumerate(ts):
        while ts[j] <= t - window:
            j += 1
        best = max(best, i - j + 1)
    return best


class Recorder:
    def __init__(self, clock):
        self.clock, self.runs, self.exceptions = clock, [], []

    async def run(self, engine: Engine, who: str, task: str, inputs: dict, context: dict,
                  priority: Priority = Priority.INTERACTIVE):
        t0 = self.clock()
        try:
            r = await engine.run(task, inputs, {**CTX, **context}, priority)
        except Exception as e:           # engine.run must never raise a provider error
            self.exceptions.append({"who": who, "task": task, "error": f"{type(e).__name__}: {e}"})
            return None
        self.runs.append({"who": who, "task": task, "priority": priority.value, "t": round(t0, 2),
                          "latency_s": round(self.clock() - t0, 2), "degraded": r.degraded,
                          "degrade_reason": r.degrade_reason, "from_cache": r.from_cache, "mode": r.mode,
                          "model_class": r.model_class_used, "waited_reserve": "waiting_reserve" in r.event_names})
        return r


async def learner(engine, rec, sleep, now, end, rng, who, offset):
    await sleep(offset)
    while now() < end:
        msg = rng.choice(EXPLAIN_Q + [h[0] for h in HINT_Q])
        c = await rec.run(engine, who, "classify", {"message": msg}, {"learner_id": who})
        if rng.random() < 0.55:
            await rec.run(engine, who, "explain", {"question": rng.choice(EXPLAIN_Q)}, {"learner_id": who})
        else:
            problem, attempt = rng.choice(HINT_Q)
            await rec.run(engine, who, "hint", {"problem": problem, "attempt": attempt}, {"learner_id": who})
        await sleep(rng.uniform(8, 20))                 # the learner reads and thinks


async def room(engine, rec, sleep, now, end, rng, image_fn):
    n = 0
    await sleep(5)
    while now() < end:
        n += 1
        ctx = {"room_id": "R1", "learner_id": "L1"}
        kind = n % 3
        if kind == 1:
            await rec.run(engine, "R1", "check_claim", {"claim": rng.choice(CLAIMS)}, ctx)
        elif kind == 2:
            await rec.run(engine, "R1", "check_answer", {"problem": "Dérive f(x) = x⁴",
                                                         "steps": ["f(x) = x⁴", rng.choice(["f'(x) = 4x³", "f'(x) = x³", "f'(x) = 7x"])]}, ctx)
        else:
            await rec.run(engine, "R1", "ocr_transcribe", {"image": image_fn(n)}, ctx)
        await sleep(rng.uniform(6, 12))


async def prep(engine, rec, sleep, now, end, max_pages):
    r = await rec.run(engine, "P1", "prep_plan",
                      {"scope": {"unit_ids": ["derivees"]}, "deadline": "2099-01-01", "pace_min_per_day": 45,
                       "mastery": {"nombre dérivé": 0.9}}, {"learner_id": "P1"}, Priority.BACKGROUND)
    if r is None or not r.data:
        return
    for fp in r.data["focus_points"][:max_pages]:
        if now() >= end:
            break
        t0, seen = now(), []
        try:
            page = await engine.generate_page({}, fp, {**CTX, "learner_id": "P1"}, on_event=lambda ev: seen.append(ev.name))
            rec.runs.append({"who": "P1", "task": "generate_page", "priority": "BACKGROUND", "t": round(t0, 2),
                             "latency_s": round(now() - t0, 2), "degraded": page["degraded"],
                             "degrade_reason": page["degrade_reason"], "from_cache": page["from_cache"], "mode": None,
                             "model_class": None, "waited_reserve": "waiting_reserve" in seen})
        except Exception as e:
            rec.exceptions.append({"who": "P1", "task": "generate_page", "error": f"{type(e).__name__}: {e}"})


def summarize(rec: Recorder, spy: SpyTransport, sink: MemorySink, duration: float, mode: str, extra: dict) -> dict:
    inter = [r for r in rec.runs if r["priority"] == "INTERACTIVE" and r["task"] != "generate_page"]
    lat = [r["latency_s"] for r in inter]
    chat = [x for x in spy.log if x["path"].endswith("/chat/completions")]
    by_task = defaultdict(list)
    for r in inter:
        by_task[r["task"]].append(r["latency_s"])
    sends = [x["t0"] for x in chat]
    refusals = Counter(f"{x['status']}:{x['rate_limit_type'] or '-'}" for x in chat if x["status"] != 200)
    events = [r for r in sink.records if r.kind == "run"]
    return {
        "mode": mode, "duration_s": duration, **extra,
        "interactive": {"n": len(inter), "p50_s": pct(lat, .5), "p95_s": pct(lat, .95), "max_s": max(lat, default=None),
                        "degraded": sum(r["degraded"] for r in inter),
                        "degrade_reasons": dict(Counter(r["degrade_reason"] for r in inter if r["degraded"])),
                        "by_task": {t: {"n": len(v), "p50_s": pct(v, .5), "p95_s": pct(v, .95)} for t, v in sorted(by_task.items())}},
        "background": {"n": len([r for r in rec.runs if r["priority"] == "BACKGROUND"]),
                       "pages": len([r for r in rec.runs if r["task"] == "generate_page"]),
                       "pages_degraded": len([r for r in rec.runs if r["task"] == "generate_page" and r["degraded"]]),
                       "waited_for_reserve": sum(r["waited_reserve"] for r in rec.runs)},
        "provider": {"chat_requests": len(chat), "by_model": dict(Counter(x["model"] for x in chat)),
                     "non_200": dict(refusals), "server_429": sum(1 for x in chat if x["status"] == 429),
                     "densest_60s_window": densest_window(sends)},
        "exceptions": rec.exceptions,
        "records": {"runs": len(events), "by_outcome": dict(Counter(r.outcome for r in events))},
        "verdict": {"interactive_p95_under_25s": (pct(lat, .95) or 0) < 25, "zero_server_429": not any(x["status"] == 429 for x in chat),
                    "zero_exceptions": not rec.exceptions, "window_at_most_20": densest_window(sends) <= 20}}


def write(report: dict, prefix: str) -> None:
    os.makedirs("reports", exist_ok=True)
    with open(f"{prefix}.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    i, p, v = report["interactive"], report["provider"], report["verdict"]
    lines = [f"# Mixed load, {report['mode']} (engine v1.3), cache {report['cache']}", "",
             report.get("note", ""), "",
             f"- duration {report['duration_s']:.0f} s; {i['n']} interactive requests, {report['background']['pages']} prep pages "
             f"({report['background']['pages_degraded']} degraded), {report['background']['waited_for_reserve']} requests held for the reserve",
             f"- interactive latency: p50 {i['p50_s']} s, **p95 {i['p95_s']} s**, max {i['max_s']} s; degraded {i['degraded']} {i['degrade_reasons']}",
             f"- provider: {p['chat_requests']} chat requests {p['by_model']}; non-200 {p['non_200'] or 'none'}; "
             f"**server 429s {p['server_429']}**; densest 60 s window **{p['densest_60s_window']}** sends",
             f"- exceptions out of engine.run: **{len(report['exceptions'])}**", "",
             "| task | n | p50 s | p95 s |", "|---|---|---|---|"]
    lines += [f"| {t} | {d['n']} | {d['p50_s']} | {d['p95_s']} |" for t, d in i["by_task"].items()]
    lines += ["", "| check | result |", "|---|---|"] + [f"| {k} | {'PASS' if val else 'FAIL'} |" for k, val in v.items()]
    if report.get("vision_probe"):
        lines += ["", "## Does a vision request use the chat window?", "", "```json", json.dumps(report["vision_probe"], indent=1), "```"]
    with open(f"{prefix}.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ------------------------------------------------------------------ virtual: the real engine, simulated FelCloud
def canned(body: dict) -> httpx.Response:
    """The provider behind the simulator: plausible replies by task, nothing more."""
    from tests.conftest import chat_ok
    msgs = body["messages"]
    first, last = str(msgs[0].get("content")), msgs[-1].get("content")
    text = last if isinstance(last, str) else json.dumps(last)
    if body.get("model") == "deepseek-ocr":
        return chat_ok("Dériver f(x) = 3x² + 5x\nf'(x) = 6x + 5", 820, 40)
    if body.get("response_format"):
        if "Reply with ONE JSON object" in first and "verdict" in first and "SUPPORTED" in first:
            return chat_ok(json.dumps({"verdict": "CONTRADICTED", "correction": "(u·v)' = u'·v + u·v'."}), 600, 30)
        if "student's answer" in first:
            return chat_ok(json.dumps({"verdict": "INCORRECT", "mistake": None, "feedback": "Vérifie l'exposant."}), 300, 30)
        intent = "EXPLAIN" if any(w in text.lower() for w in ("explique", "c'est quoi", "pourquoi", "comment")) else "HINT"
        return chat_ok(json.dumps({"intent": intent, "language": "FRENCH", "confusion": "MEDIUM", "complexity": 2,
                                   "confidence": 0.9}), 310, 40)
    if body.get("model") == "mid-a":
        return chat_ok("La dérivée mesure la vitesse à laquelle une fonction change : on regarde le taux d'accroissement "
                       "entre deux points, puis on fait tendre l'écart vers zéro pour obtenir la pente de la tangente.", 1100, 60)
    return chat_ok("Que devient l'exposant de x quand on dérive ?", 260, 22)


def run_virtual(args) -> dict:
    from simulator.transport import ConstraintSimulator
    from tests.conftest import FnProvider, make_settings
    from tests.vloop import run_virtual as vrun
    from rag.chunker import chunk_dir
    from rag.store import save_chunks
    import tempfile

    async def main(loop):
        with tempfile.TemporaryDirectory() as d:
            index = os.path.join(d, "chunks.json")
            save_chunks(index, chunk_dir("data/docs"))
            s = make_settings(RAG_INDEX_PATH=index, SIMULATE_CONSTRAINTS="true", SIM_LATENCY_SCALE=1, SIM_SEED=args.seed,
                              SIM_LATENCY_COMPACT_S="2,5", SIM_LATENCY_MIDSIZE_S="8,15", SIM_LATENCY_VISION_S="10,18",
                              SIM_SATURATION_PROB=args.saturation, SIM_MIDSIZE_SLOTS=1, EXPLAIN_MAX_TOKENS=350,
                              COURSES_DIR="data/courses")
            spy = SpyTransport(FnProvider(canned), loop.time)
            sim = ConstraintSimulator(spy, s, clock=loop.time, sleep=asyncio.sleep)
            sink = MemorySink()
            engine = Engine.create(s, client=LLMClient(s, sim), sink=sink,
                                   cache=ExactCache(":memory:", 0 if args.no_cache else s.cache_ttl_s),
                                   clock=loop.time, sleep=asyncio.sleep, rng=random.Random(args.seed))
            rec, rng = Recorder(loop.time), random.Random(args.seed)
            t0 = loop.time()
            end = t0 + args.duration
            from tests.test_ocr_task import data_url, photo
            image_fn = lambda n: data_url(photo(1400, 1000, lines=[f"Dériver f(x) = {n}x² + 5x", f"f'(x) = {2 * n}x + 5"]))
            tasks = [asyncio.ensure_future(learner(engine, rec, asyncio.sleep, loop.time, end, rng, w, i * 4))
                     for i, w in enumerate(LEARNERS)]
            tasks += [asyncio.ensure_future(room(engine, rec, asyncio.sleep, loop.time, end, rng, image_fn)),
                      asyncio.ensure_future(prep(engine, rec, asyncio.sleep, loop.time, end, args.pages))]
            await asyncio.gather(*tasks)
            await asyncio.sleep(0)
            rep = summarize(rec, spy, sink, loop.time() - t0, "virtual (simulated FelCloud)", {
                "seed": args.seed, "saturation_prob": args.saturation, "cache": "off" if args.no_cache else "on",
                "note": "Simulated: the real engine through the constraint simulator (compact 2-5 s, mid-size 8-15 s, vision "
                        "10-18 s, 20 / min, 2 in flight, one mid-size slot, random mid-size saturation) with canned model "
                        "replies and a virtual clock. It checks the engine's scheduling, not the models, and not FelCloud."})
            await engine.aclose()
            return rep
    return vrun(main, limit_s=args.duration * 20)


# ------------------------------------------------------------------ real: FelCloud
async def run_real(args) -> dict:
    from config import load_settings
    import time
    s = load_settings()
    if s.simulate:
        raise SystemExit("SIMULATE_CONSTRAINTS must be false for --real (ENV_FILE=.env.real)")
    clock = time.monotonic
    spy = SpyTransport(httpx.AsyncHTTPTransport(), clock)
    sink = MemorySink()
    engine = Engine.create(s, transport=spy, sink=sink, cache=ExactCache(":memory:", 0 if args.no_cache else s.cache_ttl_s))
    rec, rng = Recorder(clock), random.Random(args.seed)
    from scripts.smoke import sample_image
    probe = None
    if args.probe_vision:
        probe = await vision_probe(engine, spy, rec)
    t0 = clock()
    end = t0 + args.duration
    image_fn = lambda n: sample_image((f"Dériver f(x) = {n}x² + 5x", f"f'(x) = {2 * n}x + 5"))
    tasks = [asyncio.ensure_future(learner(engine, rec, asyncio.sleep, clock, end, rng, w, i * 4))
             for i, w in enumerate(LEARNERS)]
    tasks += [asyncio.ensure_future(room(engine, rec, asyncio.sleep, clock, end, rng, image_fn)),
              asyncio.ensure_future(prep(engine, rec, asyncio.sleep, clock, end, args.pages))]
    await asyncio.gather(*tasks)
    rep = summarize(rec, spy, sink, clock() - t0, "real (FelCloud)", {"seed": args.seed, "cache": "off" if args.no_cache else "on",
                    "note": "Live run against FelCloud: every request spent the team's quota."})
    if probe:
        rep["vision_probe"] = probe
    await engine.aclose()
    return rep


async def vision_probe(engine: Engine, spy: SpyTransport, rec: Recorder) -> dict:
    """compact, vision, compact, one after the other with nothing else running: the platform's remaining-requests
    header after each tells whether the vision request was counted in the 20-per-minute window."""
    from scripts.smoke import sample_image
    seq = []
    for label, task, inputs in (("compact-1", "classify", {"message": "probe un"}),
                                ("vision", "ocr_transcribe", {"image": sample_image(("probe vision",))}),
                                ("compact-2", "classify", {"message": "probe deux"})):
        n0 = len(spy.log)
        await rec.run(engine, "probe", task, inputs, {})
        new = [x for x in spy.log[n0:] if x["path"].endswith("/chat/completions")]
        seq.append({"step": label, "requests": len(new), "remaining_after": new[-1]["remaining"] if new else None,
                    "model": new[-1]["model"] if new else None})
    rem = [int(x["remaining_after"]) for x in seq if x["remaining_after"] is not None]
    counted = len(rem) == 3 and (rem[0] - rem[1] == 1) and (rem[1] - rem[2] == 1)
    return {"sequence": seq, "vision_request_uses_the_chat_window": counted if len(rem) == 3 else "unknown (no rate headers)",
            "reading": "remaining must fall by 1 after the vision request exactly as after a compact one"}


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--virtual", action="store_true")
    mode.add_argument("--real", action="store_true")
    ap.add_argument("--duration", type=float, default=240.0)
    ap.add_argument("--pages", type=int, default=6, help="prep pages to generate in the background")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--saturation", type=float, default=0.05, help="--virtual: random mid-size saturation probability")
    ap.add_argument("--probe-vision", action="store_true", help="--real: first measure whether vision counts in the window")
    ap.add_argument("--no-cache", action="store_true", help="every request reaches the provider (the harshest mix)")
    ap.add_argument("--out", help="report path prefix (default reports/load-mixed-<mode>-v13)")
    a = ap.parse_args()
    report = run_virtual(a) if a.virtual else asyncio.run(run_real(a))
    prefix = a.out or f"reports/load-mixed-{'virtual' if a.virtual else 'felcloud'}-v13"
    write(report, prefix)
    print(open(f"{prefix}.md", encoding="utf-8").read())
    return 0 if all(report["verdict"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
