"""Writes docs/examples/<task>.<outcome>.json: one Result per outcome, for docs/ENGINE_API.md.

Examples marked "real" come out of the real Engine (the gatekeeper, the guards, the cache and the exercise bank run
for real) with a scripted provider standing in for the model: the shapes, events, degrade reasons and eta values are
what engine.run returns; only the model's words are canned. Outcomes of tasks the real engine does not implement yet,
and the ones that need real minutes (the 25 s deadline), come from FakeEngine and are marked "canned".

    python -m scripts.gen_api_examples            # rewrites docs/examples/
    python -m scripts.gen_api_examples --check    # exits 1 if the files on disk are out of date
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
import tempfile

import httpx

from engine import Engine
from engine.fake import FakeEngine
from engine.result import Result
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient
from metrics import MemorySink
from rag.chunker import chunk_dir
from rag.store import save_chunks
from tests.conftest import FnProvider, VirtualTime, chat_ok, error, make_settings

OUT = "docs/examples"
CLASSIFY_OK = {"intent": "EXPLAIN", "language": "FRENCH", "confusion": "MEDIUM", "complexity": 2, "confidence": 0.9}
Q_EXPLAIN = {"question": "C'est quoi le taux d'accroissement et le nombre dérivé ?"}
BODY = ("Le taux d'accroissement mesure la pente de la sécante entre deux points ; quand h tend vers 0, il tend vers "
        "le nombre dérivé, c'est-à-dire la pente de la tangente.")
REAL_429 = lambda: httpx.Response(429, headers={"retry-after": "60", "rate_limit_type": "requests"}, json={
    "error": {"message": "Rate limit exceeded for api_key: <key-hash>. Limit type: requests. Current limit: 20, "
                         "Remaining: 0.", "type": "throttling_error", "param": None, "code": "429"}})
SAT = lambda: error(503, "model_saturated", "busy", {"Retry-After": "4"})


class Rig:
    """One real Engine over a scripted provider, in virtual time."""

    def __init__(self, fn, index: str, **env):
        self.vt = VirtualTime()
        s = make_settings(**{"RAG_INDEX_PATH": index, **env})
        self.fake = FnProvider(fn)
        self.engine = Engine.create(s, client=LLMClient(s, self.fake), sink=MemorySink(),
                                    cache=ExactCache(":memory:", s.cache_ttl_s), clock=self.vt.clock,
                                    sleep=self.vt.sleep, rng=random.Random(1))


def reply(body):
    """The scripted model: JSON for JSON tasks, a short sentence otherwise."""
    sys_prompt = (body["messages"][0]["content"] if body.get("messages") else "")
    if body.get("response_format"):
        user = body["messages"][-1]["content"]
        if "Problem" in user or "solution" in user.lower():
            return chat_ok(json.dumps({"verdict": "INCORRECT", "mistake": None,
                                       "feedback": "Que devient l'exposant quand tu dérives ?"}), 300, 30)
        return chat_ok(json.dumps(CLASSIFY_OK), 310, 40)
    if "1 sentence" in sys_prompt or "one line" in sys_prompt.lower() or body.get("max_tokens", 999) <= 60:
        return chat_ok("Voici ce que dit le cours :", 40, 8)
    if body["model"] == "mid-a":
        return chat_ok(BODY, 1100, 60)
    return chat_ok("Quelle règle donne la dérivée d'une puissance ? Regarde l'exposant.", 260, 22)


async def real_examples(index: str) -> dict[str, tuple[str, Result]]:
    out: dict[str, tuple[str, Result]] = {}

    async def go(name, rig, task, inputs, context=None, prio="INTERACTIVE"):
        out[name] = ("real", await rig.engine.run(task, inputs, context or {}, prio))

    r = Rig(lambda b: reply(b), index)
    msg = {"message": "je bloque sur la dérivée de x⁴, tu peux m'expliquer ?"}
    await go("classify.ok", r, "classify", msg, {"learner_id": "L1", "course_id": "calc1"})
    await go("classify.cache_hit", r, "classify", msg, {"learner_id": "L1", "course_id": "calc1"})
    await go("classify.rules", r, "classify", {"message": "merci"})
    await go("hint.ok", r, "hint", {"problem": "Dérive f(x) = x⁴", "attempt": "x³"}, {"language": "FRENCH"})
    await go("hint.stored", r, "hint", {"problem": "Dérive f(x) = x⁴", "stored_hints": [
        "Quelle règle donne la dérivée de xⁿ ?", "(xⁿ)' = n·xⁿ⁻¹ : ici, que vaut n ?"], "hint_level": 2})
    await go("explain.ok", r, "explain", Q_EXPLAIN, {"language": "FRENCH", "topic": "derivative"})
    await go("explain.cache_hit", r, "explain", Q_EXPLAIN, {"language": "FRENCH", "topic": "derivative"})
    await go("explain.not_in_course", r, "explain", {"question": "Comment fonctionne la photosynthèse ?"},
             {"language": "FRENCH"})
    await go("quiz.ok", r, "quiz", {"message": "un exercice sur la dérivée d'une puissance", "concept": "puissance"})
    await go("quiz.diagnostic", r, "quiz", {"message": "teste mon niveau", "diagnostic": True})
    await go("grade.correct", r, "grade", {"problem": "Dérive f(x) = x⁴", "solution": "f'(x) = 4x³",
                                           "attempt": "4x^3"})
    await go("grade.incorrect", r, "grade", {"problem": "Dérive f(x) = x⁴", "solution": "f'(x) = 4x³",
                                             "attempt": "x³", "common_mistakes": [
        {"expr": "x³", "feedback": "Tu as baissé l'exposant mais oublié de multiplier par l'exposant."}]})
    await go("grade.llm", r, "grade", {"problem": "Explique pourquoi la dérivée d'une constante est nulle.",
                                       "solution": "Une constante ne varie pas : sa pente est 0.",
                                       "attempt": "parce que ça ne bouge pas"})

    junk = Rig(lambda b: chat_ok("je ne sais pas, désolé", 300, 12), index)
    await go("classify.safe_default", junk, "classify", {"message": "explique-moi les limites"})

    sat = Rig(lambda b: SAT(), index, SATURATION_RETRIES=1, COMPACT_MAX_RETRIES=3)
    await go("explain.fast_extractive", sat, "explain", Q_EXPLAIN, {"language": "FRENCH"})

    q = Rig(lambda b: REAL_429(), index)
    await go("classify.quota_exhausted", q, "classify", {"message": "explique-moi la tangente"})

    # superseded / room_busy: the one waiting INTERACTIVE request of a learner is replaced; one run per room
    s = Rig(lambda b: reply(b), index, MAX_CONCURRENT=1)
    await s.engine.caller.limiter.acquire("chat")                       # the only slot is taken
    a = asyncio.create_task(s.engine.run("classify", {"message": "première question sur les limites"},
                                         {"learner_id": "L1", "room_id": "R1"}))
    await asyncio.sleep(0.01)
    out["classify.room_busy"] = ("real", await s.engine.run("classify", {"message": "encore une question"},
                                                            {"room_id": "R1"}))          # R1 still has `a` in flight
    b = asyncio.create_task(s.engine.run("classify", {"message": "deuxième question sur les limites"},
                                         {"learner_id": "L1"}))                        # replaces `a` in the queue
    await asyncio.sleep(0.01)
    out["classify.superseded"] = ("real", await asyncio.wait_for(a, 5))
    s.engine.caller.limiter.release()
    await b
    return out


async def canned_examples() -> dict[str, tuple[str, Result]]:
    f = FakeEngine(speed=0)
    out = {}
    for task, outcomes in FakeEngine.OUTCOMES.items():
        for o in outcomes:
            from tests.test_fake_engine import INPUTS
            out[f"{task}.{o}"] = ("canned", await f.run(task, INPUTS[task], {"fake_outcome": o}))
    return out


async def build() -> dict[str, tuple[str, Result]]:
    with tempfile.TemporaryDirectory() as d:
        index = os.path.join(d, "chunks.json")
        save_chunks(index, chunk_dir("data/docs"))
        real = await real_examples(index)
    canned = await canned_examples()
    return {**canned, **{k: v for k, v in real.items() if v[1] is not None}}   # real wins over canned


def render(source: str, r: Result) -> str:
    d = r.model_dump()
    d["request_id"] = "a1b2c3d4e5f6"
    return json.dumps({"_source": source, **d}, ensure_ascii=False, indent=2) + "\n"


DOC_SRC, DOC_OUT = "docs/ENGINE_API.src.md", "docs/ENGINE_API.md"
_MARK = re.compile(r"^\{\{example ([\w.]+)\}\}$", re.M)


def render_doc(examples: dict[str, tuple[str, Result]]) -> str:
    src = open(DOC_SRC, encoding="utf-8").read()

    def block(m: re.Match) -> str:
        name = m.group(1)
        if name not in examples:
            raise SystemExit(f"{DOC_SRC}: unknown example {name!r}")
        source, r = examples[name]
        body = json.dumps(r.model_dump() | {"request_id": "a1b2c3d4e5f6"}, ensure_ascii=False, indent=2)
        return f"*{name}: {'real engine output' if source == 'real' else 'canned (FakeEngine)'}*\n\n```json\n{body}\n```"
    return _MARK.sub(block, src)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--doc", action="store_true", help="also write docs/ENGINE_API.md from docs/ENGINE_API.src.md")
    a = ap.parse_args()
    ex = asyncio.run(build())
    doc = render_doc(ex)
    if a.check and (not os.path.exists(DOC_OUT) or open(DOC_OUT, encoding="utf-8").read() != doc):
        print("stale: ENGINE_API.md")
        return 1
    if a.doc and not a.check:
        with open(DOC_OUT, "w", encoding="utf-8") as fh:
            fh.write(doc)
    os.makedirs(OUT, exist_ok=True)
    stale = []
    for name, (source, r) in sorted(ex.items()):
        path = os.path.join(OUT, f"{name}.json")
        text = render(source, r)
        if a.check:
            if not os.path.exists(path) or open(path, encoding="utf-8").read() != text:
                stale.append(name)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
    if a.check:
        print("stale:", stale or "none")
        return 1 if stale else 0
    print(f"wrote {len(ex)} examples to {OUT} ({sum(1 for s, _ in ex.values() if s == 'real')} real)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
