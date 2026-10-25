"""Interactive console over engine.run: one learner turn per line, with live progress events.

    ENV_FILE=.env.ollama python -m scripts.chat [--lang FRENCH] [--level lycée] [--sim calm]

A plain line is a learner turn, handled like the feature layer would:
  * classify, then EXPLAIN -> explain (the course excerpt is printed first, then the full answer; its
    closing bank exercise becomes the open problem);
    HINT / ANSWER_ATTEMPT -> hint; QUIZ -> an exercise from the bank; DIAGNOSTIC -> 3 bank exercises of
    increasing level; chitchat and off-topic stop after classify.
  * while an exercise is open, a line that looks like an answer (digits, "=", math symbols) is graded
    against it directly, without classify; any other line is classified first (ANSWER_ATTEMPT -> graded,
    HINT -> stored hint, QUIZ -> a new exercise...). /hint serves the stored hints, one level at a time.
Commands:

    /explain <question>   /hint [<problem> [:: <attempt>]]   /quiz [<concept>]   /classify <message>
    /skip (close the open exercise)   /lang <DARIJA|ARABIZI|FRENCH|ARABIC|ENGLISH|auto>   /level <text>
    /sim <normal|calm|saturated|quota_low|off>   /bg (toggle BACKGROUND)   /stats   /help   /quit

The simulator starts in "calm" (no saturation, no added latency) for manual testing.
Every call goes through the engine (and so the gatekeeper); metrics go to METRICS_PATH as usual.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

from dotenv import dotenv_values

from config import load_settings
from engine import Engine, Event, Priority, Result
from engine.exercises import Exercise
from engine.language import LANGUAGES
from metrics import CallRecord, JsonlSink, MetricsSink
from simulator.profiles import CHAT_PROFILES

@dataclass
class _TurnRef:                     # what Router.follow_up reads to attribute its route record
    request_id: str
    turn_id: str | None
HELP = __doc__.split("Commands:", 1)[1].split("The simulator", 1)[0].rstrip()
VERDICT = {"CORRECT": "correct", "INCORRECT": "not yet", "PARTIAL": "partly", "UNKNOWN": "could not check"}


_ANSWER_SYMBOLS = re.compile(r"[=²³⁴√^/+*×·]|[−-]\s*\d")
_ANSWER_TOKEN = re.compile(r"[−-]?\(?[\d.,]*x?[\d²³⁴]*\)?")   # 6, 4x, x, 2x², -3 ... not "ya3ni"


def looks_like_answer(line: str) -> bool:
    """A cheap test (no request): math content means the learner is answering the open exercise.
    Digits INSIDE words are Arabizi letters ("ya3ni", "3lech"), not math."""
    if _ANSWER_SYMBOLS.search(line):
        return True
    return any(t and any(c.isdigit() or c == "x" for c in t) and _ANSWER_TOKEN.fullmatch(t)
               for t in line.replace("?", " ").replace(",", " ").split())


class TeeSink:
    """Write-through to the real sink, plus in-memory counters for /stats."""

    def __init__(self, inner: MetricsSink):
        self.inner, self.records = inner, []

    def write(self, record: CallRecord) -> None:
        self.records.append(record)
        self.inner.write(record)

    def close(self) -> None:
        if hasattr(self.inner, "close"):
            self.inner.close()


@dataclass
class Practice:
    """The open exercise (and the rest of a diagnostic series)."""
    exercise: Exercise
    queue: list[Exercise] = field(default_factory=list)
    hints_used: int = 0
    last_attempt: str | None = None
    score: list[bool] = field(default_factory=list)
    diagnostic: bool = False


class Console:
    def __init__(self, engine: Engine, sink: TeeSink | None = None, out=print,
                 language: str | None = None, level: str | None = None):
        self.engine, self.sink, self.out = engine, sink, out
        self.language, self.level = language, level
        self.priority = Priority.INTERACTIVE
        self.turns = 0
        self.practice: Practice | None = None
        self.served: list[str] = []                     # exercise ids already given (no repeats)
        self._line_turn: str | None = None

    # ------------------------------------------------------------ rendering
    def _event(self, e: Event) -> None:
        if e.name == "partial_result":                  # the course itself, while the answer is generated
            self.out("   course excerpt (full answer coming):")
            for ex in e.data.get("excerpts", []):
                self.out(f"   > {ex['text']}")
            return
        extra = {k: v for k, v in e.data.items() if v not in (None, [], {})}
        self.out(f"   · {e.name}" + (f" {json.dumps(extra, ensure_ascii=False)}" if extra else "")
                 + f"  [{e.t_ms:.0f} ms]")

    def _result(self, r: Result) -> None:
        if r.task == "classify" and r.data:
            d = r.data
            self.out(f"   {d['intent']} · {d['language']} · confusion {d['confusion']} · complexity {d['complexity']}"
                     f" · confidence {d['confidence']}")
        if r.task == "grade" and r.data:
            d = r.data
            self.out(f"\n  {VERDICT.get(d['verdict'], d['verdict'])}" + (f" — {d['feedback']}" if d.get("feedback") else "")
                     + f"   [graded_by={d.get('graded_by')}]")
        if r.text:
            self.out("")
            for line in r.text.splitlines():
                self.out(f"  {line}")
            self.out("")
        flags = [f"class={r.model_class_used or 'rules'}", f"{r.latency_ms:.0f} ms",
                 "cache HIT" if r.from_cache else "cache miss", f"tokens {r.tokens_in}/{r.tokens_out}"]
        if r.degraded:
            flags.append(f"DEGRADED ({r.degrade_reason})")
        if r.mode:
            flags.append(f"mode={r.mode}" + (" (ask again later for a full explanation)" if r.followup_available else ""))
        if r.eta_seconds:
            flags.append(f"retry in ~{r.eta_seconds:.0f}s")
        for key in ("sources", "glossary", "contamination"):
            if r.notes.get(key):
                flags.append(f"{key}: " + ", ".join(map(str, r.notes[key])))
        self.out(f"   [{r.task}] " + " | ".join(flags))

    async def _run(self, task: str, inputs: dict, turn_id: str, language: str | None = None,
                   extra: dict | None = None) -> Result:
        if self._line_turn is None:                  # only lines that reach the engine are learner turns
            self.turns += 1
            self._line_turn = f"chat-{os.getpid()}-{self.turns}"
        turn_id = self._line_turn
        ctx = {"turn_id": turn_id, "level": self.level, "language": language or self.language, **(extra or {})}
        self.out(f"-> {task}")
        r = await self.engine.run(task, inputs, {k: v for k, v in ctx.items() if v}, self.priority, self._event)
        self._result(r)
        return r

    # ------------------------------------------------------------ practice (exercise bank)
    async def _start_quiz(self, turn: str, message: str = "", concept: str | None = None, diagnostic=False,
                          language: str | None = None) -> None:
        inputs = {"message": message, "concept": concept, "diagnostic": diagnostic, "exclude": self.served}
        r = await self._run("quiz", {k: v for k, v in inputs.items() if v}, turn, language)
        exs = [self.engine.exercises.get(e["id"]) for e in (r.data or {}).get("exercises", [])]
        if exs:
            self.served += [e.id for e in exs]
            self.practice = Practice(exs[0], exs[1:], diagnostic=diagnostic)
            self.out(f"   (exercise {exs[0].id} is open: type your answer, /hint for a hint, /skip to close it)")

    async def _explain(self, question: str, turn: str, language: str | None = None,
                       confusion: str | None = None) -> None:
        r = await self._run("explain", {"question": question, "exclude_exercises": self.served}, turn, language,
                            {"confusion": confusion})
        ex = (r.data or {}).get("exercise")
        if ex and self.engine.exercises:              # the closing practice item becomes the open problem
            e = self.engine.exercises.get(ex["id"])
            self.served.append(e.id)
            self.practice = Practice(e)
            self.out(f"   (exercise {e.id} is open: type your answer, /hint for a hint, /skip to close it)")

    async def _grade(self, answer: str, turn: str) -> None:
        p = self.practice
        e = p.exercise
        p.last_attempt = answer
        r = await self._run("grade", {"problem": e.statement, "solution": e.solution, "attempt": answer,
                                      "common_mistakes": [list(m) for m in e.common_mistakes]}, turn)
        verdict = (r.data or {}).get("verdict")
        if p.diagnostic:
            p.score.append(verdict == "CORRECT")
            if p.queue:
                p.exercise, p.queue, p.hints_used = p.queue[0], p.queue[1:], 0
                self.out(f"\n  next ({p.exercise.id}, level {p.exercise.level}): {p.exercise.statement}")
            else:
                self.out(f"   diagnostic score: {sum(p.score)}/{len(p.score)}")
                self.practice = None
        elif verdict == "CORRECT":
            self.practice = None
        else:
            self.out("   (/hint for a hint, or try again)")

    async def _stored_hint(self, turn: str) -> None:
        p = self.practice
        p.hints_used += 1
        e = p.exercise
        await self._run("hint", {"problem": e.statement, "attempt": p.last_attempt, "solution": e.solution,
                                 "stored_hints": list(e.hints), "hint_level": p.hints_used}, turn)
        if p.hints_used >= len(e.hints):
            self.out("   (that was the last hint for this exercise)")

    # ------------------------------------------------------------ simulator
    def set_sim(self, name: str) -> str:
        sim = getattr(self.engine.caller.client, "transport", None)
        if not hasattr(sim, "apply_profile"):
            return "   simulator not active (started without it)"
        if name not in CHAT_PROFILES:
            return f"   profiles: {', '.join(CHAT_PROFILES)}"
        sim.apply_profile(name)
        return f"   simulator = {name}"

    # ------------------------------------------------------------ input
    async def handle(self, line: str) -> bool:
        """Process one input line. Returns False when the user asked to quit."""
        line = line.strip()
        if not line:
            return True
        cmd, _, arg = line.partition(" ") if line.startswith("/") else ("", "", line)
        arg = arg.strip()
        self._line_turn = None                       # assigned by the first engine call of this line
        turn = ""
        try:
            if cmd in ("/quit", "/exit"):
                return False
            if cmd == "/help":
                self.out(HELP)
            elif cmd == "/lang":
                v = arg.upper()
                if v == "AUTO":
                    self.language = None
                elif v in LANGUAGES:
                    self.language = v
                else:
                    self.out(f"   languages: {', '.join(LANGUAGES)} or auto")
                self.out(f"   language = {self.language or 'auto (detected per message)'}")
            elif cmd == "/level":
                self.level = arg or None
                self.out(f"   level = {self.level or 'not specified'}")
            elif cmd == "/bg":
                self.priority = Priority.INTERACTIVE if self.priority is Priority.BACKGROUND else Priority.BACKGROUND
                self.out(f"   priority = {self.priority.value}")
            elif cmd == "/sim":
                self.out(self.set_sim(arg.lower()))
            elif cmd == "/stats":
                self.out(self.stats())
            elif cmd == "/skip":
                self.practice = None
                self.out("   exercise closed")
            elif cmd == "/explain" and arg:
                await self._explain(arg, turn)
            elif cmd == "/hint" and not arg and self.practice:
                await self._stored_hint(turn)
            elif cmd == "/hint" and arg:
                problem, _, attempt = arg.partition("::")
                await self._run("hint", {"problem": problem.strip(), "attempt": attempt.strip() or None}, turn)
            elif cmd == "/quiz":
                await self._start_quiz(turn, concept=arg or None)
            elif cmd == "/classify" and arg:
                await self._run("classify", {"message": arg}, turn)
            elif cmd:
                self.out(f"   unknown or incomplete command {cmd!r}; /help")
            elif self.practice and looks_like_answer(arg):   # the answer to the open exercise: no classify
                await self._grade(arg, turn)
            else:
                await self._turn(arg, turn)
        except ValueError as e:                      # caller-side mistakes only; provider errors never raise
            self.out(f"   error: {e}")
        return True

    async def _turn(self, message: str, turn: str) -> None:
        c = await self._run("classify", {"message": message}, turn)
        d = c.data or {}
        f = self.engine.router.follow_up(d, _TurnRef(c.request_id, self._line_turn))
        follow = f.task
        if f.overridden:
            self.out(f"   · route: {f.reason}")
        lang = self.language or d.get("language")
        if self.practice and d.get("intent") == "ANSWER_ATTEMPT":
            await self._grade(message, turn)
        elif self.practice and d.get("intent") == "HINT":
            await self._stored_hint(turn)
        elif follow is None:
            self.out("   (no tutoring follow-up for chitchat / off-topic)")
        elif follow == "explain":
            await self._explain(message, turn, lang, d.get("confusion"))
        elif follow == "hint":
            await self._run("hint", {"problem": message}, turn, lang)
        else:
            await self._start_quiz(turn, message=message, diagnostic=follow == "diagnostic", language=lang)

    def stats(self) -> str:
        if self.sink is None:
            return "   (no stats)"
        recs = self.sink.records
        att = [r for r in recs if r.kind in ("chat", "embed") and r.outcome != "cache_hit"]
        ok = [r for r in att if r.outcome == "ok"]
        runs = [r for r in recs if r.kind == "run" and r.task != "compress"]
        by_cls = Counter(r.model_class for r in ok)
        graded = Counter(r.graded_by for r in runs if r.task == "grade")
        return (f"   turns {self.turns} | engine calls {len(runs)} | provider requests {len(att)} (ok {len(ok)}) | "
                f"compact {by_cls['compact']} / midsize {by_cls['midsize']} | "
                f"cache hits {sum(r.from_cache for r in runs)} | degraded {sum(r.degraded for r in runs)} | "
                f"tokens {sum(r.tokens_in or 0 for r in ok)}/{sum(r.tokens_out or 0 for r in ok)} | "
                f"graded_by sympy {graded['sympy']} / llm {graded['llm']}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", help="fix the learner language (default: detected per message)")
    ap.add_argument("--level")
    ap.add_argument("--sim", default="calm", choices=CHAT_PROFILES, help="simulator profile (default: calm)")
    a = ap.parse_args()
    env = {**dotenv_values(os.environ.get("ENV_FILE", ".env")), **os.environ}
    env["SIMULATE_CONSTRAINTS"] = "true"            # always wrapped, so /sim can switch at runtime
    s = load_settings({k: v for k, v in env.items() if v is not None})
    sink = TeeSink(JsonlSink(s.metrics_path))
    async with Engine.create(s, sink=sink) as engine:
        console = Console(engine, sink, language=(a.lang or "").upper() or None, level=a.level)
        print(f"EdNova console — provider {s.base_url}. {console.set_sim(a.sim).strip()}. /help for commands.")
        while True:
            try:
                line = await asyncio.to_thread(input, "\nyou> ")
            except (EOFError, KeyboardInterrupt):
                break
            if not await console.handle(line):
                break
        print(console.stats())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
