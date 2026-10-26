"""The 26 Oct mid-size outage, offline: engine + simulator (mid-size hangs, compact normal) + a fake provider, all in
virtual time (tests/vloop.py). The scenario is the one of reports/midsize-outage-2026-10-26.md."""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import replace

import httpx

from engine import Engine, Priority
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient
from metrics import MemorySink
from simulator.transport import ConstraintSimulator
from tests.conftest import chat_ok, make_settings

CLASSIFY_JSON = '{"intent":"EXPLAIN","language":"FRENCH","confusion":"LOW","complexity":2,"confidence":0.9}'
ANSWER = "Le nombre dérivé est la limite du taux d'accroissement quand h tend vers zéro."

EXPLAINS = ["C'est quoi le taux d'accroissement et comment on obtient le nombre dérivé ?",
            "Comment trouver l'équation de la tangente à une courbe ?",
            "Quelles sont les erreurs fréquentes avec la dérivée d'un produit ?",
            "Comment dériver un polynôme terme par terme ?",
            "À quoi sert une dérivée, intuitivement, avec la vitesse d'une voiture ?",
            "Comment dériver un quotient u/v ?",
            "Pourquoi la dérivée d'une constante est-elle nulle ?"]
CLASSIFIES = ["Je bloque sur la dérivée de x(x + 1), j'ai trouvé 1", "chnowa ya3ni limite? ma fhemt chay",
              "How do I know if a function is increasing?", "ما هو تعريف العدد المشتق؟"]


class Provider(httpx.AsyncBaseTransport):
    """The healthy provider behind the simulator: JSON for json_mode requests, prose otherwise."""

    def __init__(self):
        self.requests: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append(body)
        r = chat_ok(CLASSIFY_JSON if body.get("response_format") else ANSWER, 100, 20)
        r.request = request
        return r


def build(loop, **env):
    """(engine, simulator, provider, sink). Compact answers in 2 s, mid-size in 10 s when it is not hanging."""
    base = dict(SIM_HANG_MIDSIZE="true", SIM_LATENCY_SCALE=1, SIM_LATENCY_COMPACT_S="2,2",
                SIM_LATENCY_MIDSIZE_S="10,10", SIM_MIDSIZE_SLOTS=0, SIM_SATURATION_PROB=0,
                SIM_RATE_PER_MIN=1000, SIM_BURST=1000, RATE_LIMIT_PER_MIN=100, SIMULATE_CONSTRAINTS="true")
    s = make_settings(**{**base, **env})
    provider, sink = Provider(), MemorySink()
    sim = ConstraintSimulator(provider, s, clock=loop.time, sleep=asyncio.sleep)
    eng = Engine.create(s, client=LLMClient(s, sim), sink=sink, cache=ExactCache(":memory:", s.cache_ttl_s),
                        clock=loop.time, sleep=asyncio.sleep, rng=random.Random(1))
    return eng, sim, provider, sink


def end_outage(sim: ConstraintSimulator) -> None:
    sim.cfg = replace(sim.cfg, hang_midsize=False)


class Timeline:
    """Runs engine calls at given virtual times and records start, end and the Result of each."""

    def __init__(self, eng, loop, priority=Priority.INTERACTIVE):
        self.eng, self.loop, self.t0, self.priority = eng, loop, loop.time(), priority
        self.calls: dict[str, dict] = {}
        self.tasks: list[asyncio.Task] = []

    def at(self, label: str, t: float, task: str, inputs: dict, context: dict | None = None,
           priority=None) -> asyncio.Task:
        async def go():
            await asyncio.sleep(max(0.0, self.t0 + t - self.loop.time()))
            rec = self.calls[label] = {"start": self.loop.time() - self.t0, "events": []}
            r = await self.eng.run(task, inputs, context or {}, priority or self.priority,
                                   lambda ev: rec["events"].append((round(self.loop.time() - self.t0, 1), ev.name)))
            rec.update(end=self.loop.time() - self.t0, result=r)
        t_ = asyncio.create_task(go())
        self.tasks.append(t_)
        return t_

    def explain(self, label, t, question):
        return self.at(label, t, "explain", {"question": question}, {"language": "FRENCH"})

    def classify(self, label, t, message):
        return self.at(label, t, "classify", {"message": message})

    def took(self, label) -> float:
        c = self.calls[label]
        return c["end"] - c["start"]

    def table(self) -> str:
        rows = ["call           start    took  degraded  reason               mode"]
        for k, c in sorted(self.calls.items(), key=lambda kv: kv[1]["start"]):
            r = c["result"]
            rows.append(f"{k:14} {c['start']:6.1f} {c['end'] - c['start']:7.1f}  {str(r.degraded):8}  "
                        f"{str(r.degrade_reason):20} {r.mode}")
        return "\n".join(rows)
