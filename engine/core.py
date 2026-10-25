"""
engine.run(task, inputs, context, priority) -> Result

  rules shortcut? -> route (router.policy) -> [grounded: retrieving -> nothing relevant? say so]
  -> checking_cache -> [grounded: budget -> compressing if chunks do not fit] -> queued -> generating
  -> [mid-size saturated: falling_back_to_compact -> queued -> generating]
  -> JSON tasks: validate -> [repairing_json -> one compact repair] -> [using_safe_default]
  -> text tasks: guards (answer leak, language drift)
  -> cache (clean results only; explain that failed a check -> short-TTL negative marker: a repeat is
     served in fast mode with 0 requests) -> one kind="run" metrics record

Contract with callers: run() NEVER raises a provider error. Busy, out of quota, too large or
broken provider all come back as a well-formed Result with degraded=True and a reason
(plus eta_seconds when retrying later makes sense). Only caller bugs raise (unknown task,
missing required input, bad priority) — as ValueError, before any request is made.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import replace
from typing import Any, Callable, Mapping

from pydantic import ValidationError

from config import ModelClass, Settings, load_settings
from engine.prompts import PromptTemplate, load_prompt
from engine.result import Event, Result
from engine.exercises import ExerciseBank
from engine.grounding import check_formulas
from engine.language import ARABIC_SCRIPT, arabic_ratio, contamination, latin_words
from engine.tasks import (Messages, Registry, TaskContext, TaskSpec, best_sentence, default_registry,
                          fast_excerpts, load_templates, load_terms_ar)

log = logging.getLogger("ednova.engine")
PARTIAL_SENTENCES = 3
UNUSABLE = {"explain_guard:degenerate", "explain_guard:empty"}
# deterministic check failures: the same request would very likely fail again, so mark its key (negative cache)
NEGATIVE = {"ungrounded_formula", "wrong_math", "explain_guard:loop_truncated", "explain_guard:degenerate"}
TEMPLATE_ONLY_LANGUAGES = {"DARIJA", "ARABIZI"}     # one-liners in these come from fixed Tunisian templates
from gatekeeper.cache import ExactCache, cache_key
from gatekeeper.client import ChatRequest, EmbedRequest, LLMClient, create_client
from gatekeeper.errors import (DEFAULT_RETRY_AFTER_S, CeilingExceeded, ConcurrencyLimited, FatalError, LLMError,
                               ModelSaturated, QuotaExhausted, TransientError)
from gatekeeper.limiter import Limiter
from gatekeeper.queue import Priority, PriorityQueue
from gatekeeper.retry import CallContext, ChatResult, RetryingCaller, RetryPolicy
from metrics import CallRecord, JsonlSink, MetricsSink
from rag.budget import BudgetError, assemble, limit_for
from rag.glossary import Glossary
from rag.retriever import Hit, Retriever, language_plan, make_retriever
from rag.store import load_chunks
from router.policy import Decision, Router

EventCallback = Callable[[Event], None]

# how each give-up maps to what the learner sees; retryable ones get an ETA and a "queued" event
_UNAVAILABLE: list[tuple[type[LLMError], str, bool]] = [
    (ModelSaturated, "service_busy", True),
    (ConcurrencyLimited, "service_busy", True),
    (QuotaExhausted, "quota_exhausted", True),
    (TransientError, "service_unavailable", True),
    (CeilingExceeded, "request_too_large", False),
    (FatalError, "provider_error", False),
]


class _Run:
    """Per-call state: events, tokens, degradation."""

    def __init__(self, clock, on_event: EventCallback | None):
        self.clock, self.t0, self.on_event = clock, clock(), on_event
        self.events: list[Event] = []
        self.tokens_in = self.tokens_out = 0
        self.reasons: list[str] = []
        self.model_class: str | None = None
        self.model: str | None = None
        self.decision: Decision | None = None
        self.glossary: list[str] = []
        self.negative_hit = False

    def emit(self, name: str, data: Mapping[str, Any] | None = None) -> None:
        ev = Event(name=name, t_ms=round((self.clock() - self.t0) * 1000, 1), data=dict(data or {}))
        self.events.append(ev)
        if self.on_event:
            self.on_event(ev)

    def degrade(self, reason: str, terminal: bool = False) -> None:
        """terminal = nothing could be generated; that reason wins over earlier ones (e.g. a fallback)."""
        if terminal:
            self.reasons.insert(0, reason)
        else:
            self.reasons.append(reason)

    @property
    def degraded(self) -> bool:
        return bool(self.reasons)

    @property
    def reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None

    def took(self, chat: ChatResult) -> None:
        self.tokens_in += chat.tokens_in or 0
        self.tokens_out += chat.tokens_out or 0
        self.model_class, self.model = chat.model_class, chat.model


class _FastMode(Exception):
    """Internal: a grounded task lost mid-size at runtime; serve it extractively instead."""

    def __init__(self, decision: Decision):
        self.decision = decision


def _extract_json(text: str) -> str:
    """Tolerate ```json fences and chatter around one object (not a repair: no extra request)."""
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    return t[i:j + 1] if 0 <= i < j else t


class Engine:
    def __init__(self, settings: Settings, caller: RetryingCaller, cache: ExactCache, sink: MetricsSink,
                 registry: Registry | None = None, clock: Callable[[], float] = time.monotonic,
                 retriever: Retriever | None = None):
        self.settings, self.caller, self.cache, self.sink = settings, caller, cache, sink
        self.registry = registry or default_registry()
        self.router = Router.from_settings(sink, settings, clock)
        caller.observer = self.router.observe
        self._clock = clock
        self.limit = limit_for(settings.input_token_budget, settings.budget_safety_margin)
        self.retriever = retriever or make_retriever(settings.retriever, settings.rag_min_coverage, self._embed,
                                                     settings.glossary_weight)
        self.glossary = Glossary.load(settings.glossary_path) \
            if settings.glossary_path and os.path.exists(settings.glossary_path) else None
        self.terms_ar = load_terms_ar(settings.terms_ar_path)
        self.templates = load_templates(settings.templates_path)
        self._frequency_penalty_ok = True          # set False (and logged once) if the provider rejects it
        self.exercises = ExerciseBank.load(settings.exercises_path, settings.templates_path) \
            if settings.exercises_path and os.path.exists(settings.exercises_path) else None
        # Latin words an Arabic-script answer may contain: the French course terms of the glossary
        self.course_words = frozenset(w for t in (self.glossary.course_terms if self.glossary else ())
                                      for w in latin_words(t))
        self._indexed = retriever is not None and bool(getattr(retriever, "chunks", None))
        self._index_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Event] = {}     # cache key -> done, for identical concurrent requests
        self.prompts: dict[str, PromptTemplate] = {
            s.name: load_prompt(settings.prompts_dir, s.prompt) for s in self.registry}   # fail fast

    @classmethod
    def create(cls, settings: Settings | None = None, *, transport=None, client: LLMClient | None = None,
               sink: MetricsSink | None = None, cache: ExactCache | None = None, registry: Registry | None = None,
               retriever: Retriever | None = None, clock=time.monotonic, sleep=asyncio.sleep, rng=None) -> "Engine":
        settings = settings or load_settings()
        sink = sink or JsonlSink(settings.metrics_path)
        client = client or create_client(settings, transport)
        limiter = Limiter(settings, clock, sleep)
        caller = RetryingCaller(client, limiter, sink, RetryPolicy.from_settings(settings),
                                settings.input_token_budget, clock, sleep, rng, PriorityQueue(limiter, clock))
        cache = cache or ExactCache(settings.cache_path, settings.cache_ttl_s)
        return cls(settings, caller, cache, sink, registry, clock, retriever)

    async def aclose(self) -> None:
        await self.caller.queue.aclose()
        await self.caller.client.aclose()
        self.cache.close()
        if hasattr(self.sink, "close"):
            self.sink.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    # ================================================================== public
    async def run(self, task: str, inputs: Mapping[str, Any] | None = None,
                  context: Mapping[str, Any] | None = None, priority: Priority | str = Priority.INTERACTIVE,
                  on_event: EventCallback | None = None) -> Result:
        spec = self.registry.get(task)
        inputs = dict(inputs or {})
        missing = [k for k in spec.required_inputs if not str(inputs.get(k) or "").strip()]
        if missing:
            raise ValueError(f"task {task!r} needs inputs {missing}")
        prio = Priority(priority)
        tctx = TaskContext.from_mapping(context or {})
        tctx = replace(tctx, extra={**tctx.extra, "terms_ar": self.terms_ar, "course_words": self.course_words,
                                    "exercise_bank": self.exercises, "templates": self.templates})
        if spec.prepare:
            tctx = spec.prepare(inputs, tctx)
        run = _Run(self._clock, on_event)
        ctx = CallContext(task=task, priority=prio.value, on_event=run.emit, turn_id=tctx.extra.get("turn_id"))
        try:
            return await self._execute(spec, inputs, tctx, prio, run, ctx)
        finally:
            if run.decision is not None:     # a half-open probe that never reached mid-size frees its slot
                self.router.release_probe(run.decision)

    async def _execute(self, spec: TaskSpec, inputs: dict, tctx: TaskContext, prio: Priority, run: "_Run",
                       ctx: CallContext) -> Result:
        task, prompt = spec.name, self.prompts[spec.name]
        if spec.shortcut and (data := spec.shortcut(inputs, tctx)) is not None:
            run.emit("answered_by_rules")
            text = data.pop("_text", None)
            return self._finish(run, ctx, text=text, data=data, notes={"source": data.get("source", "rules")})
        if spec.local is not None:
            return await self._local(spec, prompt, inputs, tctx, ctx, run)

        decision = run.decision = self.router.decide(task, tctx.as_mapping(),
                                                     self.caller.limiter.buckets["chat"].tokens, ctx)
        hits: list[Hit] = []
        if spec.grounded:
            hits = await self._retrieve(inputs, tctx, run)
            if not hits:      # say so instead of letting the model invent an answer; no request spent
                run.emit("no_relevant_context")
                return self._finish(run, ctx, text=spec.not_found_text(prompt, inputs, tctx),
                                    route_reason=decision.reason, notes={"retrieval": "no_relevant_chunks"})
            tctx = replace(tctx, extra={**tctx.extra, "concept": hits[0].chunk.concept})
            # cache identity = question (+ history) + WHICH chunks were retrieved, not the compressed text
            key_messages = spec.render_grounded(prompt, inputs, tctx, [], inputs.get("history") or [])
            messages: Messages = []
        else:
            key_messages = messages = spec.build(prompt, inputs, tctx, self.limit)
        run.emit("checking_cache")
        key = cache_key(task, decision.preferred.value, key_messages, tctx.language, tctx.level, tctx.style,
                        prompt.version, extra=[h.chunk.chunk_id for h in hits])
        if (hit := self.cache.get(key)) is not None:
            return self._cache_hit(run, ctx, key, hit, decision, spec, inputs, tctx, hits)
        if spec.grounded and (reasons := self.cache.get_negative(key)):
            return await self._negative_hit(prompt, inputs, tctx, hits, key, reasons, ctx, run, decision)
        if (leader := self._inflight.get(key)) is not None:
            # the same request is already being generated: wait for it instead of sending a duplicate
            run.emit("waiting_identical_request")
            await leader.wait()
            if (hit := self.cache.get(key)) is not None:
                return self._cache_hit(run, ctx, key, hit, decision, spec, inputs, tctx, hits)
            if spec.grounded and (reasons := self.cache.get_negative(key)):
                return await self._negative_hit(prompt, inputs, tctx, hits, key, reasons, ctx, run, decision)
            # the leader's result was degraded for another reason (never cached): produce our own
            return await self._produce(spec, prompt, inputs, tctx, prio, run, ctx, decision, hits, key, messages)
        if hits:      # the course itself, verbatim, while the full answer is generated (no extra request)
            run.emit("partial_result", {"excerpts": fast_excerpts(str(inputs["question"]),
                                                                  [h.chunk for h in hits], PARTIAL_SENTENCES)})
        done = self._inflight[key] = asyncio.Event()
        try:
            return await self._produce(spec, prompt, inputs, tctx, prio, run, ctx, decision, hits, key, messages)
        finally:
            done.set()
            del self._inflight[key]

    async def _produce(self, spec: TaskSpec, prompt: PromptTemplate, inputs: dict, tctx: TaskContext,
                       prio: Priority, run: "_Run", ctx: CallContext, decision: Decision, hits: list[Hit],
                       key: str, messages: Messages) -> Result:
        task = spec.name
        if decision.degraded:
            run.degrade(decision.degrade_reason)
            run.emit("falling_back_to_compact", {"reason": decision.degrade_reason})

        ground_notes: dict = {}
        pieces: list = []
        if spec.grounded:
            if decision.model_class is not decision.preferred:     # quota_low / breaker_open: no mid-size
                return await self._fast_extractive(prompt, inputs, tctx, [h.chunk for h in hits], {}, ctx, run,
                                                   decision)
            try:
                messages, ground_notes, pieces = await self._ground(spec, prompt, inputs, tctx, hits, prio, run)
                tctx = replace(tctx, extra={**tctx.extra, "material": "\n".join(c.text for c in pieces)})
            except BudgetError as e:
                return self._unavailable(spec, prompt, inputs, tctx, run, ctx, decision,
                                         CeilingExceeded(message=str(e), ceiling="prompt_tokens", limit=self.limit))

        try:
            chat = await self._generate(spec, decision, messages, ctx, run)
        except _FastMode as fm:
            return await self._fast_extractive(prompt, inputs, tctx, pieces, ground_notes, ctx, run, fm.decision)
        except LLMError as e:
            return self._unavailable(spec, prompt, inputs, tctx, run, ctx, decision, e)

        text = data = None
        status, notes = "ok", {}
        if spec.output_schema is not None:
            data, status, notes = await self._json_output(spec, prompt, chat, messages, inputs, tctx, ctx, run)
        else:
            out = spec.finalize_text(chat.text, prompt, inputs, tctx) if spec.finalize_text \
                else (chat.text.strip(), None)
            text, guard, detail = (*out, None)[:3]
            if guard and guard.endswith(("degenerate", "loop_truncated")):
                ground_notes = {**ground_notes, "model_text": chat.text[:3000]}   # kept for review / evals
            if spec.grounded and guard in UNUSABLE:   # nothing worth showing: serve the course verbatim
                run.degrade(guard)
                run.emit("guarded", {"reason": guard})
                log.warning("%s %s guarded: %s, serving extracts", ctx.request_id, task, guard)
                if guard in NEGATIVE:
                    self.cache.mark_negative(key, task, run.reasons, self.settings.negative_cache_ttl_s)
                return await self._fast_extractive(prompt, inputs, tctx, pieces or [h.chunk for h in hits],
                                                   ground_notes, ctx, run, decision)
            if guard:
                run.degrade(guard)
                run.emit("guarded", {"reason": guard, **(detail or {})})
                log.warning("%s %s guarded: %s %s", ctx.request_id, task, guard, detail or "")
                notes = {**notes, **(detail or {})}

        notes = {**ground_notes, **notes}
        body, raw_data = text, data                      # what the model wrote: the only thing cached
        if spec.decorate and text is not None:
            text, data = spec.decorate(body, inputs, tctx, True)
        if spec.check_grounding and body:
            sources = [str(tctx.extra.get("material") or ""), str(inputs.get("question") or "")]
            if (data or {}).get("exercise"):
                sources.append(data["exercise"]["statement"])
            notes = {**notes, **self._formula_verdict(body, sources, run, ctx)}
        if not run.degraded and status != "safe_default":
            self.cache.put(key, task, {"text": body, "data": raw_data, "model_class": run.model_class,
                                       "model": run.model, "notes": notes})
        elif spec.grounded and NEGATIVE & set(run.reasons):
            self.cache.mark_negative(key, task, run.reasons, self.settings.negative_cache_ttl_s)
        return self._finish(run, ctx, text=text, data=data, route_reason=decision.reason, status=status,
                            notes=notes)

    def _formula_verdict(self, body: str, sources: list[str], run: "_Run", ctx: CallContext) -> dict:
        """Correctness over grounding: degrade only on wrong math, or on formulas that are neither
        verifiable nor in the sources. Correct new examples (verified with sympy) pass."""
        c = check_formulas(body, sources)
        notes = {"formulas_verified": len(c["verified"])}
        if c["new_examples"]:
            notes["new_examples"] = c["new_examples"]
        if c["wrong"]:
            run.degrade("wrong_math")
            run.emit("guarded", {"reason": "wrong_math", "spans": c["wrong"]})
            log.warning("%s %s: wrong math %s", ctx.request_id, ctx.task, c["wrong"])
            notes["wrong_math"] = c["wrong"]
        if c["ungrounded"]:
            run.degrade("ungrounded_formula")
            run.emit("guarded", {"reason": "ungrounded_formula", "spans": c["ungrounded"]})
            log.warning("%s %s: ungrounded formula(s) %s", ctx.request_id, ctx.task, c["ungrounded"])
            notes["ungrounded"] = c["ungrounded"]
        return notes

    # ================================================================== fast mode
    async def _negative_hit(self, prompt: PromptTemplate, inputs, tctx: TaskContext, hits: list[Hit], key: str,
                            reasons: list[str], ctx: CallContext, run: "_Run", d: Decision) -> Result:
        """This exact request failed a deterministic check less than NEGATIVE_CACHE_TTL_S ago: serve the course
        verbatim (template framing) instead of sending it again. 0 provider requests; same degrade reasons."""
        run.negative_hit = True
        for r in reasons:
            run.degrade(r)
        run.emit("negative_cache_hit", {"reasons": reasons})
        self.sink.write(CallRecord(request_id=ctx.request_id, turn_id=ctx.turn_id, kind="chat", task=ctx.task,
                                   priority=ctx.priority, model_class=d.preferred.value, request_hash=key[:16],
                                   outcome="negative_cache_hit", from_negative_cache=True, degraded=True,
                                   degrade_reason=run.reason))
        return await self._fast_extractive(prompt, inputs, tctx, [h.chunk for h in hits], {"negative_cache": True},
                                           ctx, run, d, llm_framing=False)

    async def _fast_extractive(self, prompt: PromptTemplate, inputs, tctx: TaskContext, chunks: list,
                               notes: dict, ctx: CallContext, run: "_Run", d: Decision,
                               llm_framing: bool = True) -> Result:
        """Mid-size unavailable: compact does NOT write the explanation. The learner gets the most relevant
        course sentences verbatim (formulas intact), introduced by one line. Never cached.
        llm_framing=False: the framing line comes from the cache or the template, never from a request."""
        run.emit("fast_extractive", {"reason": run.reason})
        excerpts = fast_excerpts(str(inputs["question"]), chunks)
        framing = await self._fast_framing(prompt, str(inputs["question"]), tctx, ctx, run, llm_framing)
        text = framing + "\n\n" + "\n".join(f"- {e['text']}" for e in excerpts)
        extra: dict = {}
        if chunks and getattr(chunks[0], "concept", "") and self.registry.get("explain").decorate:
            tctx = replace(tctx, extra={**tctx.extra, "concept": chunks[0].concept})
            text, extra = self.registry.get("explain").decorate(text, inputs, tctx, False)
            extra.pop("body", None)
        order = [c.chunk_id for c in chunks]
        notes = {**notes, "sources": sorted({e["chunk_id"] for e in excerpts}, key=order.index)}
        return self._finish(run, ctx, text=text, data={"framing": framing, "excerpts": excerpts, **extra},
                            route_reason=d.reason, notes=notes, mode="fast_extractive", followup_available=True)

    async def _fast_framing(self, prompt: PromptTemplate, question: str, tctx: TaskContext, ctx: CallContext,
                            run: "_Run", allow_call: bool = True) -> str:
        return await self._one_liner(prompt, "fast", {"question": question.strip()}, "fast_intro", tctx, ctx, run,
                                     allow_call)

    async def _one_liner(self, prompt: PromptTemplate, kind: str, values: dict, template_prefix: str,
                         tctx: TaskContext, ctx: CallContext, run: "_Run", allow_call: bool = True) -> str:
        """One cheap compact line (fast-mode framing, quiz intro): sections <kind>_system / <kind>_user,
        a localized template on any trouble, never a wait (no saturation retries). The line depends only
        on its inputs + language, so it is cached even when the result around it is not."""
        lang = tctx.language or "FRENCH"
        template = prompt.sections[f"{template_prefix}.{lang}"]
        if lang in TEMPLATE_ONLY_LANGUAGES:      # compact cannot write Tunisian reliably (recon, live replay)
            return template
        rule = prompt.sections.get(f"line.{lang}", prompt.sections.get(f"lang.{lang}", ""))   # one-liner rule
        msgs = [{"role": "system", "content": prompt.render(f"{kind}_system", language_rule=rule)},
                {"role": "user", "content": prompt.render(f"{kind}_user", **values)}]
        cache_task = f"{prompt.task}.{kind}"
        purpose = {"fast": "framing"}.get(kind, kind)          # metrics label (stable across versions)
        key = cache_key(cache_task, ModelClass.COMPACT.value, msgs, lang, None, None, prompt.version)
        if (hit := self.cache.get(key)) is not None:
            self.sink.write(CallRecord(request_id=ctx.request_id, turn_id=ctx.turn_id, kind="chat", task=ctx.task,
                                       priority=ctx.priority, purpose=purpose, model_class=ModelClass.COMPACT.value,
                                       model=hit.get("model"), request_hash=key[:16], outcome="cache_hit",
                                       from_cache=True))
            return hit["text"]
        if not allow_call:
            return template
        fctx = CallContext(request_id=ctx.request_id, turn_id=ctx.turn_id, task=ctx.task, priority=ctx.priority,
                           purpose=purpose, on_event=run.emit, queue_seq=ctx.queue_seq, saturation_retries=0)
        try:
            chat = await self.caller.chat(ChatRequest(ModelClass.COMPACT, tuple(msgs), max_tokens=40,
                                                      temperature=0.2), fctx)
        except LLMError:
            return template
        run.took(chat)
        line = chat.text.strip().splitlines()[0].strip().strip('"“”«»') if chat.text.strip() else ""
        drift = (arabic_ratio(line) >= 0.5) != (lang in ARABIC_SCRIPT)
        if not line or len(line.split()) > 30 or drift or contamination(line, set(self.course_words)):
            return template
        self.cache.put(key, cache_task, {"text": line, "model": chat.model})
        return line

    # ================================================================== local tasks (exercise bank)
    async def _local(self, spec: TaskSpec, prompt: PromptTemplate, inputs: dict, tctx: TaskContext,
                     ctx: CallContext, run: "_Run") -> Result:
        decision = run.decision = self.router.decide(spec.name, tctx.as_mapping(),
                                                     self.caller.limiter.buckets["chat"].tokens, ctx)
        message = str(inputs.get("message") or "")
        terms = self.glossary.expand(message).terms if (self.glossary and message) else ()
        tctx = replace(tctx, extra={**tctx.extra, "expansion_terms": terms})
        run.emit("picking_exercise")
        data = spec.local(inputs, tctx)
        body = data.pop("_text", "")
        lang = tctx.language or "FRENCH"
        intro = None
        if not data.get("exercises"):
            body = prompt.sections[f"empty.{lang}"]
        elif data.get("diagnostic"):
            intro = prompt.sections[f"diagnostic.{lang}"]          # fixed text: no request
        elif inputs.get("intro") == "llm":       # opt-in: the template is the default (see DECISIONS)
            topic = data["exercises"][0]["concept"]
            intro = await self._one_liner(prompt, "intro", {"topic": topic}, "intro_template", tctx, ctx, run)
        elif inputs.get("intro", True):
            intro = prompt.sections[f"intro_template.{lang}"]
        text = f"{intro}\n\n{body}" if intro else body
        return self._finish(run, ctx, text=text, data={**data, "intro": intro}, route_reason=decision.reason,
                            notes={"source": "bank"}, mode="bank")

    # ================================================================== retrieval
    async def _embed(self, texts) -> list[list[float]]:
        r = await self.caller.embed(EmbedRequest(tuple(texts)), CallContext(task="embed", priority="BACKGROUND"))
        return r.vectors

    async def _ensure_index(self) -> None:
        async with self._index_lock:
            if not self._indexed:
                await self.retriever.index(load_chunks(self.settings.rag_index_path))
                self._indexed = True

    async def _retrieve(self, inputs: Mapping, tctx: TaskContext, run: _Run) -> list[Hit]:
        run.emit("retrieving")
        await self._ensure_index()
        filters = {"topic": tctx.extra.get("topic")} if tctx.extra.get("topic") else None
        question = str(inputs["question"])
        expansion = self.glossary.expand(question) if self.glossary else None   # deterministic, no LLM
        if expansion:
            run.emit("expanding_query", {"glossary": list(expansion.terms)})
            run.glossary = list(expansion.terms)
        for lang_filter in language_plan(tctx.language):
            hits = await self.retriever.search(question, self.settings.rag_top_k,
                                               {**(filters or {}), **(lang_filter or {})} or None, expansion)
            if hits:
                return hits
        return []

    async def _ground(self, spec: TaskSpec, prompt, inputs, tctx, hits: list[Hit], prio: Priority,
                      run: _Run) -> tuple[Messages, dict, list]:
        """Assemble within budget; if the top chunks do not fit, compress them (compact) and re-assemble."""
        history = list(inputs.get("history") or [])
        render = lambda chunks, hist: spec.render_grounded(prompt, inputs, tctx, chunks, hist)
        chunks = [h.chunk for h in hits]
        asm = assemble(render, chunks, history, self.limit)
        pieces = chunks
        notes: dict[str, Any] = {"retrieved": [{"id": h.chunk.chunk_id, "score": h.score, "coverage": h.coverage}
                                               for h in hits]}
        if run.glossary:
            notes["glossary"] = run.glossary
        if not asm.all_chunks_fit:
            over = [d["id"] for d in asm.dropped if d["kind"] == "chunk"]
            run.emit("compressing", {"chunks": len(chunks), "over_budget": over})
            comp = await self.run("compress",
                                  {"question": inputs["question"], "chunks": [c.to_dict() for c in chunks]},
                                  {"language": tctx.language, "turn_id": tctx.extra.get("turn_id")}, prio)
            run.tokens_in += comp.tokens_in
            run.tokens_out += comp.tokens_out
            kept: dict[str, list[str]] = {}
            for sent in comp.data["sentences"]:
                kept.setdefault(sent["chunk_id"], []).append(sent["text"])
            # a chunk that passed the relevance gate must not vanish: give it back its best sentence
            restored = []
            for c in chunks:
                if c.chunk_id not in kept and (best := best_sentence(str(inputs["question"]), c)):
                    kept[c.chunk_id] = [best]
                    restored.append(c.chunk_id)
            pieces = [replace(c, text=c.text.split("\n", 1)[0] + "\n" + " ".join(kept[c.chunk_id]))
                      for c in chunks if c.chunk_id in kept]
            notes["compress"] = {"method": comp.data["method"], "sentences": len(comp.data["sentences"]),
                                 "request_id": comp.request_id, "from_cache": comp.from_cache,
                                 "degraded": comp.degraded, "over_budget": over, "restored": restored}
            asm = assemble(render, pieces, history, self.limit)
        notes.update(sources=asm.chunk_ids, budget={"tokens": asm.tokens, "limit": asm.limit},
                     dropped=asm.dropped)
        used = set(asm.chunk_ids)       # what actually made it into the prompt (compressed or whole)
        return asm.messages, notes, [c for c in pieces if c.chunk_id in used]

    # ================================================================== generation
    def _request(self, spec: TaskSpec, cls: ModelClass, messages: Messages, json_mode: bool | None = None,
                 temperature: float | None = None) -> ChatRequest:
        if json_mode is None:
            json_mode = spec.output_schema is not None
        json_mode = json_mode and self.settings.model_for(cls) not in self.settings.json_mode_unsupported
        cap = self.settings.task_max_tokens.get(spec.name, spec.max_tokens)       # e.g. EXPLAIN_MAX_TOKENS
        fp = self.settings.task_frequency_penalty.get(spec.name) if self._frequency_penalty_ok else None
        return ChatRequest(cls, tuple(messages), max_tokens=min(cap, self.settings.max_tokens[cls]),
                           temperature=spec.temperature if temperature is None else temperature,
                           json_mode=json_mode, frequency_penalty=fp or None)

    async def _chat(self, spec: TaskSpec, cls: ModelClass, messages: Messages, ctx: CallContext) -> ChatResult:
        """caller.chat, but a provider that rejects frequency_penalty gets the request again without it,
        and never sees it again (logged once)."""
        req = self._request(spec, cls, messages)
        try:
            return await self.caller.chat(req, ctx)
        except FatalError as e:
            if req.frequency_penalty is None or "frequency_penalty" not in (e.message or "").lower():
                raise
            self._frequency_penalty_ok = False
            log.warning("provider rejected frequency_penalty (%s); sending requests without it", e.message[:120])
            return await self.caller.chat(req.with_(frequency_penalty=None), ctx)

    async def _generate(self, spec, d: Decision, messages, ctx, run: _Run) -> ChatResult:
        run.model_class = d.model_class.value
        try:
            chat = await self._chat(spec, d.model_class, messages, ctx)
        except ModelSaturated:
            fb = self.router.on_saturated(d, ctx)
            if fb is None:
                raise
            run.degrade(fb.degrade_reason)
            run.emit("falling_back_to_compact", {"reason": fb.degrade_reason})
            if spec.grounded:
                raise _FastMode(fb) from None
            run.model_class = fb.model_class.value
            chat = await self._chat(spec, fb.model_class, messages, ctx)
        run.took(chat)
        return chat

    def _parse(self, spec: TaskSpec, text: str) -> tuple[dict | None, str | None]:
        try:
            obj = json.loads(_extract_json(text))
        except ValueError as e:
            return None, f"invalid JSON: {getattr(e, 'msg', e)}"
        if not isinstance(obj, dict):
            return None, "not a JSON object"
        try:
            return spec.output_schema.model_validate(obj).model_dump(), None
        except ValidationError as e:
            errs = "; ".join(f"{'.'.join(map(str, x['loc'])) or 'object'}: {x['msg']}" for x in e.errors()[:3])
            return None, f"schema: {errs}"

    async def _json_output(self, spec, prompt, chat: ChatResult, messages, inputs, tctx, ctx, run):
        """(data, status, notes). status: ok | repaired | safe_default."""
        data, err = self._parse(spec, chat.text)
        status = "ok"
        if data is None and prompt.has("repair"):
            run.emit("repairing_json", {"error": err})
            rctx = CallContext(request_id=ctx.request_id, turn_id=ctx.turn_id, task=ctx.task, priority=ctx.priority,
                               purpose="repair",
                               on_event=run.emit, queue_seq=ctx.queue_seq)
            repair = [*messages, {"role": "assistant", "content": chat.text[:1500]},
                      {"role": "user", "content": prompt.render("repair", error=err)}]
            try:   # repair is always compact (the cheap class), deterministic
                rchat = await self.caller.chat(self._request(spec, ModelClass.COMPACT, repair, temperature=0.0), rctx)
                run.took(rchat)
                data, err = self._parse(spec, rchat.text)
                status = "repaired"
            except LLMError as e:
                err = f"repair call failed: {e.kind}"
        if data is None:
            run.emit("using_safe_default", {"error": err})
            return spec.safe_default(inputs, tctx), "safe_default", {"error": err}
        if spec.finalize_data:
            data, notes = spec.finalize_data(data, inputs, tctx)
            return data, status, notes
        return data, status, {}

    # ================================================================== outcomes
    def _cache_hit(self, run: _Run, ctx: CallContext, key: str, hit: dict, d: Decision, spec: TaskSpec,
                   inputs: Mapping, tctx: TaskContext, hits: list | None = None) -> Result:
        run.emit("cache_hit")
        self.sink.write(CallRecord(request_id=ctx.request_id, turn_id=ctx.turn_id, kind="chat", task=ctx.task,
                                       priority=ctx.priority,
                                   model_class=hit.get("model_class"), model=hit.get("model"),
                                   request_hash=key[:16], outcome="cache_hit", from_cache=True))
        run.model_class, run.model = hit.get("model_class"), hit.get("model")
        text, data = hit.get("text"), hit.get("data")
        body, notes = text, dict(hit.get("notes") or {})
        if spec.rules_on_hit and spec.finalize_data and data is not None:   # today's rules on yesterday's entry
            data, fresh = spec.finalize_data(data, inputs, tctx)
            notes.update({k: v for k, v in fresh.items() if k not in notes})
        if spec.decorate and text is not None:           # cached = the model's body; scaffolding is rebuilt
            text, data = spec.decorate(text, inputs, tctx, True)
        if spec.check_grounding and body:                # also on hits: an entry cached before a rule existed
            sources = [h.chunk.text for h in (hits or [])] + [str(inputs.get("question") or "")]
            if (data or {}).get("exercise"):
                sources.append(data["exercise"]["statement"])
            verdict = self._formula_verdict(body, sources, run, ctx)
            notes.update(verdict)
            if verdict.get("wrong_math") or verdict.get("ungrounded"):
                self.cache.delete(key)                  # an entry stored before today's rules: drop it
        return self._finish(run, ctx, text=text, data=data, from_cache=True, route_reason=d.reason, notes=notes)

    def _unavailable(self, spec: TaskSpec, prompt, inputs, tctx, run: _Run, ctx, d: Decision,
                     e: LLMError) -> Result:
        reason, retryable = next((r, rt) for t, r, rt in _UNAVAILABLE if isinstance(e, t))
        eta = None
        if retryable:
            wait = e.retry_after_s if e.retry_after_s is not None else DEFAULT_RETRY_AFTER_S
            eta = round(wait + self.caller.queue.eta_s(self.caller.queue.depth()), 1)
        run.degrade(reason, terminal=True)
        run.emit("queued" if retryable else "unavailable", {"reason": reason, "eta_s": eta, "error": e.kind})
        data = spec.safe_default(inputs, tctx) if spec.safe_default else None
        text = spec.fallback_text(prompt, inputs, tctx) if spec.fallback_text else None
        return self._finish(run, ctx, text=text, data=data, route_reason=d.reason, eta=eta,
                            status="safe_default" if data is not None else "ok", notes={"error": str(e)[:200]})

    def _finish(self, run: _Run, ctx: CallContext, *, text=None, data=None, from_cache=False, route_reason=None,
                eta=None, status="ok", notes=None, mode=None, followup_available=False) -> Result:
        notes = dict(notes or {})
        if len(run.reasons) > 1:
            notes["degrade_reasons"] = run.reasons
        res = Result(task=ctx.task, request_id=ctx.request_id, text=text, data=data,
                     model_class_used=run.model_class, model=run.model, from_cache=from_cache,
                     degraded=run.degraded, degrade_reason=run.reason, eta_seconds=eta, events=run.events,
                     latency_ms=round((self._clock() - run.t0) * 1000, 1), tokens_in=run.tokens_in,
                     tokens_out=run.tokens_out, route_reason=route_reason, repaired=status == "repaired",
                     safe_default=status == "safe_default", notes=notes, mode=mode,
                     followup_available=followup_available)
        outcome = "degraded" if res.degraded else "cache_hit" if from_cache else status
        self.sink.write(CallRecord(
            request_id=ctx.request_id, turn_id=ctx.turn_id, kind="run", task=ctx.task, priority=ctx.priority,
            model_class=res.model_class_used, model=res.model, outcome=outcome, from_cache=from_cache,
            from_negative_cache=run.negative_hit,
            degraded=res.degraded, degrade_reason=res.degrade_reason, latency_ms=res.latency_ms,
            tokens_in=res.tokens_in, tokens_out=res.tokens_out, eta_s=eta, mode=mode, detail=route_reason,
            graded_by=(data or {}).get("graded_by") if ctx.task == "grade" else None))
        return res
