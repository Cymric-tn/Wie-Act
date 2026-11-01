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
import hashlib
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
from engine.course import load_courses
from engine.exercises import ExerciseBank, phrase_language
from engine.grounding import check_formulas
from engine import answers
from engine.vision import ImageError, prepare_image
from engine import language as L
from engine.language import ARABIC_SCRIPT, arabic_ratio, contamination, latin_words
from engine.tasks import (Messages, Registry, TaskContext, TaskSpec, best_sentence, default_registry,
                          fast_excerpts, load_templates, load_terms_ar, trim_to_last_sentence)

log = logging.getLogger("ednova.engine")
PARTIAL_SENTENCES = 3
UNUSABLE = {"explain_guard:degenerate", "explain_guard:empty"}
# deterministic check failures: the same request would very likely fail again, so mark its key (negative cache)
NEGATIVE = {"ungrounded_formula", "wrong_math", "explain_guard:loop_truncated", "explain_guard:degenerate"}
TEMPLATE_ONLY_LANGUAGES = {"DARIJA", "ARABIZI"}     # one-liners in these come from fixed Tunisian templates
from gatekeeper.cache import ExactCache, cache_key
from gatekeeper.client import ChatRequest, EmbedRequest, LLMClient, create_client
from gatekeeper.errors import (DEFAULT_RETRY_AFTER_S, CeilingExceeded, ClassUnavailable, ConcurrencyLimited, FatalError, LLMError,
                               ModelSaturated, QuotaExhausted, Superseded, TransientError)
from gatekeeper.limiter import Limiter
from gatekeeper.queue import Priority, PriorityQueue
from gatekeeper.retry import CallContext, ChatResult, RetryingCaller, RetryPolicy
from metrics import IDENTITY_KEYS, CallRecord, JsonlSink, MetricsSink, identity
from rag.budget import BudgetError, assemble, limit_for
from rag.glossary import Glossary
from rag.retriever import VIEWER, Hit, Retriever, language_plan, make_retriever
from rag.store import CourseStore, load_chunks
from router.policy import Decision, Router

EventCallback = Callable[[Event], None]

# how each give-up maps to what the learner sees; retryable ones get an ETA and a "queued" event
_UNAVAILABLE: list[tuple[type[LLMError], str, bool]] = [
    (ModelSaturated, "service_busy", True),
    (ConcurrencyLimited, "service_busy", True),
    (QuotaExhausted, "quota_exhausted", True),
    (TransientError, "service_unavailable", True),
    (ClassUnavailable, "service_unavailable", True),
    (Superseded, "superseded", False),
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
        self.retrieval_on: str | None = None      # which input found the course chunks: passage | question | board_text
        self.eta: float | None = None             # when a helper run (grade) was refused with a delay
        self.silent = False                      # a run that finishes after its caller left: no "run" metrics record
        self.started = asyncio.get_running_loop().time()    # the deadline is measured on the event loop's clock

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

    def __init__(self, decision: Decision, eta: float | None = None):
        self.decision = decision
        self.eta = eta                       # when the unavailable class is probed again


def trim_to_tokens(text: str, max_tokens: int) -> str:
    """Keep the head of `text` within about max_tokens (the estimator's own chars-per-token), ending on a line or
    sentence boundary when one is near, marked with an ellipsis. Text that fits is returned as it is."""
    from gatekeeper.client import chars_per_token
    limit = max(40, int(max_tokens * chars_per_token()))
    if len(text) <= limit:
        return text
    cut = text[:limit]
    best = max(cut.rfind("\n"), *(cut.rfind(m) for m in (". ", "? ", "! ", "。", "؟ ")))
    if best >= limit * 0.6:
        cut = cut[:best + 1]
    else:
        cut = cut[:max(cut.rfind(" "), int(limit * 0.8))]
    return cut.rstrip() + " […]"


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
        caller.availability = self.router.unavailable_for
        self._clock = clock
        # prompt assembly must also leave room for the longest answer inside the context window
        # (the OCR cap is not an answer the text prompts have to leave room for: its prompt is an image, sized separately)
        window_room = int(settings.context_window * settings.context_fill) - max(
            v for k, v in settings.task_max_tokens.items() if k != "ocr_transcribe")
        self.limit = min(limit_for(settings.input_token_budget, settings.budget_safety_margin), window_room)
        self.retriever = retriever or make_retriever(settings.retriever, settings.rag_min_coverage, self._embed,
                                                     settings.glossary_weight, settings.source_boost_professor)
        self.course_store = CourseStore(settings.rag_course_index_dir) if settings.rag_course_index_dir else None
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
        self._rooms: dict[str, float] = {}                # room_id -> loop time its in-flight run started
        self._room_ewma_s = 8.0                           # typical duration of a room's run (for room_busy eta)
        self._probes: set[asyncio.Task] = set()           # half-open breaker probes in flight
        self._background: set[asyncio.Task] = set()       # generations that outlived the interactive deadline
        self.courses = load_courses(settings.courses_dir) \
            if settings.courses_dir and os.path.isdir(settings.courses_dir) else {}
        self.prompts: dict[str, PromptTemplate] = {
            s.name: load_prompt(settings.prompts_dir, s.prompt) for s in self.registry if s.prompt}   # fail fast

    @classmethod
    def create(cls, settings: Settings | None = None, *, transport=None, client: LLMClient | None = None,
               sink: MetricsSink | None = None, cache: ExactCache | None = None, registry: Registry | None = None,
               retriever: Retriever | None = None, clock=time.monotonic, sleep=asyncio.sleep, rng=None) -> "Engine":
        settings = settings or load_settings()
        sink = sink or JsonlSink(settings.metrics_path)
        client = client or create_client(settings, transport)
        limiter = Limiter(settings, clock, sleep)
        caller = RetryingCaller(client, limiter, sink, RetryPolicy.from_settings(settings),
                                settings.input_token_budget, clock, sleep, rng,
                                PriorityQueue(limiter, clock, background_reserve=settings.background_reserve))
        cache = cache or ExactCache(settings.cache_path, settings.cache_ttl_s)
        return cls(settings, caller, cache, sink, registry, clock, retriever)

    async def aclose(self) -> None:
        for t in list(self._probes) + list(self._background):
            t.cancel()
        await self.caller.queue.aclose()
        await self.caller.client.aclose()
        self.cache.close()
        if hasattr(self.sink, "close"):
            self.sink.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    def status(self) -> dict:
        """A snapshot for a health check: breaker per class, the chat window, the queue. No request, no model."""
        queue = self.caller.queue
        lim = queue.limiter
        chat = lim.buckets["chat"]
        return {
            "breaker": {cls.value: {"state": br.state, "retry_in_s": round(br.retry_in(), 1), "trips": br.trips}
                        for cls, br in self.router.breakers.items()},
            "window": {"limit": chat.limit, "window_s": chat.window_s, "remaining": int(chat.tokens),
                       "in_flight": lim.in_flight, "max_concurrent": lim.max_concurrent},
            "queue": {"depth": queue.depth("chat")},
        }

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
        ids = {k: str(context[k]) for k in IDENTITY_KEYS if context and context.get(k) is not None}
        token = identity.set(ids)
        try:
            return await self._run(spec, inputs, context, prio, on_event)
        finally:
            identity.reset(token)

    async def _run(self, spec: TaskSpec, inputs: dict, context: Mapping[str, Any] | None, prio: Priority,
                   on_event: EventCallback | None) -> Result:
        task = spec.name
        if spec.query_input != "question":          # grounded tasks search with their own input ("claim")
            inputs = {**inputs, "question": inputs[spec.query_input]}
        tctx = TaskContext.from_mapping(context or {})
        tctx = replace(tctx, extra={**tctx.extra, "terms_ar": self.terms_ar, "course_words": self.course_words,
                                    "exercise_bank": self.exercises, "templates": self.templates,
                                    "courses": self.courses, "settings": self.settings,
                                    "chunks_fn": lambda: getattr(self.retriever, "chunks", [])})
        if spec.prepare:
            tctx = spec.prepare(inputs, tctx)
        run = _Run(self._clock, on_event)
        ctx = CallContext(task=task, priority=prio.value, on_event=run.emit, turn_id=tctx.extra.get("turn_id"))
        self._maybe_probe()
        key_extra: list[str] = []
        for name, share in spec.budgeted_inputs.items():      # passage, board_text: a share of the prompt budget
            raw = str(inputs.get(name) or "").strip()
            if not raw:
                continue
            key_extra.append(f"{name}:{hashlib.sha256(raw.encode()).hexdigest()[:12]}")   # the FULL original is the identity
            kept = trim_to_tokens(raw, int(self.limit * getattr(self.settings, share)))
            if kept != raw:
                run.emit("trimming_input", {"input": name, "from_chars": len(raw), "to_chars": len(kept)})
            inputs = {**inputs, name: kept}
        if key_extra:
            tctx = replace(tctx, extra={**tctx.extra, "key_extra": key_extra})
        room = str(context["room_id"]) if context and context.get("room_id") is not None else None
        if room is not None:
            now = asyncio.get_running_loop().time()
            if room in self._rooms:       # one AI request in flight per room: say so, with when to come back
                eta = round(max(1.0, self._room_ewma_s - (now - self._rooms[room])), 1)
                run.degrade("room_busy", terminal=True)
                run.emit("room_busy", {"room_id": room, "eta_s": eta})
                return self._finish(run, ctx, eta=eta, route_reason="room_busy", notes={"room_id": room})
            self._rooms[room] = now
        try:
            return await self._execute(spec, inputs, tctx, prio, run, ctx)
        finally:
            if room is not None:
                took = asyncio.get_running_loop().time() - self._rooms.pop(room)
                self._room_ewma_s = 0.7 * self._room_ewma_s + 0.3 * took
            if run.decision is not None:     # a half-open probe that never reached mid-size frees its slot
                self.router.release_probe(run.decision)

    # ================================================================== breaker probe
    def _maybe_probe(self) -> None:
        """A class whose breaker is open past its cooldown gets one probe: a tiny BACKGROUND request with a short
        timeout and no retries, never a learner's request. Traffic triggers it (no timer): an idle engine
        spends nothing. The breaker closes if it answers and re-opens (cooldown restarts) if it does not."""
        for cls in self.router.probe_due(self.caller.limiter.class_free):
            t = asyncio.get_running_loop().create_task(self._probe(cls))
            self._probes.add(t)
            t.add_done_callback(self._probes.discard)

    async def _probe(self, cls: ModelClass) -> None:
        identity.set({})                           # the task copied the triggering learner's ids: a probe has none
        ctx = CallContext(task="probe", priority=Priority.BACKGROUND.value, purpose="probe", saturation_retries=0)
        req = ChatRequest(cls, ({"role": "user", "content": "Reply with the single word: ok"},), max_tokens=8,
                          temperature=0.0)
        try:
            await self.caller.chat(req, ctx)       # the breaker learns the outcome through caller.observer
        except LLMError as e:
            log.warning("probe of %s failed: %s", cls.value, e.kind)

    async def _execute(self, spec: TaskSpec, inputs: dict, tctx: TaskContext, prio: Priority, run: "_Run",
                       ctx: CallContext) -> Result:
        task, prompt = spec.name, self.prompts.get(spec.name)
        if spec.vision:
            try:
                img = prepare_image(inputs["image"], self.settings.vision_max_pixels,
                                    self.settings.image_tokens_per_megapixel, self.settings.vision_max_bytes)
            except ImageError as e:     # the learner's photo, not a caller bug: answered, degraded, nothing sent
                run.degrade("invalid_image", terminal=True)
                run.emit("unavailable", {"reason": "invalid_image", "eta_s": None, "error": "image"})
                return self._finish(run, ctx, route_reason="invalid image", notes={"error": str(e)})
            if img.resized:
                run.emit("resizing_image", {"from": f"{img.from_size[0]}x{img.from_size[1]}",
                                            "to": f"{img.width}x{img.height}"})
            tctx = replace(tctx, extra={**tctx.extra, "image": img})
        if spec.needs_index:
            await self._ensure_index()
        if spec.shortcut and (data := spec.shortcut(inputs, tctx)) is not None:
            run.emit("answered_by_rules")
            text = data.pop("_text", None)
            notes = {"source": data.get("source", "rules"), **data.pop("_notes", {})}
            return self._finish(run, ctx, text=text, data=data, notes=notes,
                                mode="rules" if spec.name == "prep_plan" else None)
        if spec.local is not None:
            return await self._local(spec, prompt, inputs, tctx, ctx, run)
        if spec.name == "check_answer":
            return await self._check_answer(prompt, inputs, tctx, prio, run, ctx)

        decision = run.decision = self.router.decide(task, tctx.as_mapping(),
                                                     self.caller.limiter.buckets["chat"].tokens, ctx)
        hits: list[Hit] = []
        if spec.grounded:
            hits = await self._retrieve(inputs, tctx, run)
            if not hits:      # say so instead of letting the model invent an answer; no request spent
                run.emit("no_relevant_context")
                nf = spec.not_found_data(prompt, inputs, tctx) if spec.not_found_data else None
                return self._finish(run, ctx, text=None if nf else spec.not_found_text(prompt, inputs, tctx), data=nf,
                                    route_reason=decision.reason, notes={"retrieval": "no_relevant_chunks"})
            if spec.max_chunks:
                hits = hits[:spec.max_chunks]
            tctx = replace(tctx, extra={**tctx.extra, "concept": hits[0].chunk.concept,
                                        "top_chunk": hits[0].chunk.chunk_id})
            # cache identity = question (+ history) + WHICH chunks were retrieved, not the compressed text
            key_messages = spec.render_grounded(prompt, inputs, tctx, [], inputs.get("history") or [])
            messages: Messages = []
        else:
            key_messages = messages = spec.build(prompt, inputs, tctx, self.limit)
        run.emit("checking_cache")
        key = cache_key(task, decision.preferred.value, key_messages, tctx.language, tctx.level, tctx.style,
                        prompt.version, extra=[h.chunk.chunk_id for h in hits] + list(tctx.extra.get("key_extra", ())))
        if (hit := self.cache.get(key)) is not None:
            return self._cache_hit(run, ctx, key, hit, decision, spec, inputs, tctx, hits)
        if spec.grounded and (reasons := self.cache.get_negative(key)):
            return await self._negative_hit(prompt, inputs, tctx, hits, key, reasons, ctx, run, decision)
        announced = False
        while (leader := self._inflight.get(key)) is not None:
            # the same request is already being generated: wait for it instead of sending a duplicate
            if not announced:
                run.emit("waiting_identical_request")
                announced = True
            if self._has_deadline(spec, prio, decision):
                # a learner waiting for someone else's identical request has the same deadline as any learner: the leader
                # may be a slow generation (or a BACKGROUND page) that takes far longer than INTERACTIVE_DEADLINE_S
                left = max(0.0, self.settings.interactive_deadline_s - (asyncio.get_running_loop().time() - run.started))
                try:
                    await asyncio.wait_for(leader.wait(), timeout=left)
                except asyncio.TimeoutError:
                    run.degrade("deadline")
                    run.emit("deadline", {"deadline_s": self.settings.interactive_deadline_s, "generation_continues": True})
                    log.warning("%s %s: the identical request in flight has no body after %.0f s, serving extracts",
                                ctx.request_id, ctx.task, self.settings.interactive_deadline_s)
                    return await self._fast_extractive(prompt, inputs, tctx, [h.chunk for h in hits], {}, ctx, run,
                                                       replace(decision, reason=decision.reason + "; interactive deadline"),
                                                       llm_framing=False)
            else:
                await leader.wait()
            if (hit := self.cache.get(key)) is not None:
                return self._cache_hit(run, ctx, key, hit, decision, spec, inputs, tctx, hits)
            if spec.grounded and (reasons := self.cache.get_negative(key)):
                return await self._negative_hit(prompt, inputs, tctx, hits, key, reasons, ctx, run, decision)
            # the leader's result was degraded (never cached): nobody has it. Whoever wakes first becomes the leader
            # (below, with the same deadline as any request); the others wait for that one in this loop.
        if hits and spec.partial_result:    # the course itself, verbatim, while the full answer is generated
            run.emit("partial_result", {"excerpts": fast_excerpts(self._query_text(inputs),
                                                                  [h.chunk for h in hits], PARTIAL_SENTENCES)})
        done = self._inflight[key] = asyncio.Event()
        if self._has_deadline(spec, prio, decision):
            return await self._produce_by_deadline(spec, prompt, inputs, tctx, prio, run, ctx, decision, hits, key,
                                                   messages, done)
        try:
            return await self._produce(spec, prompt, inputs, tctx, prio, run, ctx, decision, hits, key, messages)
        finally:
            done.set()
            del self._inflight[key]

    def _has_deadline(self, spec: TaskSpec, prio: Priority, d: Decision) -> bool:
        """Only a learner waiting for a full explanation from mid-size has a deadline (INTERACTIVE_DEADLINE_S)."""
        return (prio is Priority.INTERACTIVE and spec.grounded and d.model_class is d.preferred
                and d.fallback is not None and self.settings.interactive_deadline_s > 0)

    async def _produce_by_deadline(self, spec, prompt, inputs, tctx, prio, run: "_Run", ctx, decision, hits, key,
                                   messages, done: asyncio.Event) -> Result:
        """The generation runs as a task of its own. If it has not produced a body by INTERACTIVE_DEADLINE_S, the
        learner gets fast mode now (degrade_reason "deadline", followup_available) and the task goes on: when
        mid-size answers, the full explanation lands in the cache and the learner's next ask is a cache hit."""
        loop = asyncio.get_running_loop()
        attached = [True]

        def forward(ev: Event) -> None:           # until the learner is answered, its events show the generation
            if attached[0]:
                run.emit(ev.name, ev.data)
        bg = _Run(self._clock, forward)
        bg.t0, bg.started = run.t0, run.started
        bg.glossary = list(run.glossary)         # state the retrieval already put on the learner's run
        bg.retrieval_on = run.retrieval_on
        bg_ctx = CallContext(request_id=ctx.request_id, task=ctx.task, priority=ctx.priority, purpose=ctx.purpose,
                             on_event=bg.emit, queue_seq=ctx.queue_seq, turn_id=ctx.turn_id,
                             owner=None)       # the learner was answered: a newer question must not kill this fill
        d = replace(decision, reason=decision.reason + "; completes after the learner's deadline")
        bg.decision = d
        task = loop.create_task(self._produce(spec, prompt, inputs, tctx, prio, bg, bg_ctx, d, hits, key, messages))
        self._background.add(task)

        def finished(t: asyncio.Task) -> None:
            self._background.discard(t)
            done.set()
            self._inflight.pop(key, None)
            if not t.cancelled() and t.exception() is not None:
                log.error("%s %s: generation failed after the deadline: %r", ctx.request_id, ctx.task, t.exception())
        task.add_done_callback(finished)
        remaining = max(0.0, self.settings.interactive_deadline_s - (loop.time() - run.started))
        await asyncio.wait({task}, timeout=remaining)
        attached[0] = False
        if task.done():
            res = task.result()             # its run record (same request) was written by the generation itself
            res.events = run.events         # the learner's timeline: retrieval + everything forwarded from the task
            return res
        bg.silent = True                    # the learner is answered below: the late finish writes no second record
        run.degrade("deadline")
        run.emit("deadline", {"deadline_s": self.settings.interactive_deadline_s, "generation_continues": True})
        log.warning("%s %s: no body after %.0f s, serving extracts; the generation continues into the cache",
                    ctx.request_id, ctx.task, self.settings.interactive_deadline_s)
        return await self._fast_extractive(prompt, inputs, tctx, [h.chunk for h in hits], {}, ctx, run,
                                           replace(decision, reason=decision.reason + "; interactive deadline"),
                                           llm_framing=False)

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
                # a class known to be down gets no nicety calls either: the framing line is cached or a template
                return await self._fast_extractive(prompt, inputs, tctx, [h.chunk for h in hits], {}, ctx, run,
                                                   decision, llm_framing=decision.degrade_reason != "breaker_open")
            try:
                messages, ground_notes, pieces = await self._ground(spec, prompt, inputs, tctx, hits, prio, run)
                tctx = replace(tctx, extra={**tctx.extra, "material": "\n".join(c.text for c in pieces)})
            except BudgetError as e:
                return self._unavailable(spec, prompt, inputs, tctx, run, ctx, decision,
                                         CeilingExceeded(message=str(e), ceiling="prompt_tokens", limit=self.limit))

        try:
            chat = await self._generate(spec, decision, messages, ctx, run)
        except _FastMode as fm:
            return await self._fast_extractive(prompt, inputs, tctx, pieces, ground_notes, ctx, run, fm.decision,
                                               llm_framing=fm.eta is None, eta=fm.eta)
        except LLMError as e:
            return self._unavailable(spec, prompt, inputs, tctx, run, ctx, decision, e)

        text = data = None
        status, notes = "ok", {}
        if spec.output_schema is not None:      # a cut JSON reply is invalid: the repair ladder handles it
            data, status, notes = await self._json_output(spec, prompt, chat, messages, inputs, tctx, ctx, run)
            if status == "safe_default" and spec.safe_default_reason:
                run.degrade(spec.safe_default_reason)
        else:
            reply = chat.text
            if chat.truncated:                   # hit the output cap: keep whole sentences only
                reply = trim_to_last_sentence(reply)
                notes = {"truncated_output": True}
                run.emit("output_trimmed", {"completion_tokens": chat.tokens_out})
            out = spec.finalize_text(reply, prompt, inputs, tctx) if spec.finalize_text \
                else (reply.strip(), None)
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
        if spec.vision and (img := tctx.extra.get("image")) is not None:
            notes["image"] = {"sha": img.sha[:12], "size": f"{img.width}x{img.height}", "resized": img.resized,
                              "est_tokens": img.est_tokens}
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
            self.cache.link(ctx.request_id, key)
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
                               llm_framing: bool = True, eta: float | None = None) -> Result:
        """Mid-size unavailable: compact does NOT write the explanation. The learner gets the most relevant
        course sentences verbatim (formulas intact), introduced by one line. Never cached.
        llm_framing=False: the framing line comes from the cache or the template, never from a request."""
        run.emit("fast_extractive", {"reason": run.reason})
        excerpts = fast_excerpts(self._query_text(inputs), chunks)
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
                            route_reason=d.reason, notes=notes, mode="fast_extractive", followup_available=True,
                            eta=eta)

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
            cap = self.settings.task_max_tokens[{"intro": "quiz_intro", "guide": "check_answer"}.get(kind, "framing")]
            chat = await self.caller.chat(ChatRequest(ModelClass.COMPACT, tuple(msgs), max_tokens=cap,
                                                      temperature=0.2), fctx)
        except LLMError:
            return template
        run.took(chat)
        if self._input_cut(run, ctx, chat):
            return template                      # never cached
        text = trim_to_last_sentence(chat.text) if chat.truncated else chat.text
        line = text.strip().splitlines()[0].strip().strip('"“”«»') if text.strip() else ""
        drift = (arabic_ratio(line) >= 0.5) != (lang in ARABIC_SCRIPT)
        if not line or len(line.split()) > 30 or drift or contamination(line, set(self.course_words)):
            return template
        self.cache.put(key, cache_task, {"text": line, "model": chat.model})
        return line

    # ================================================================== ingestion
    async def ingest_file(self, file: str, course_id: str, unit_id: str = "", owner: str = "",
                          visibility: str = "public", source: str = "professor", *, class_id: str = "",
                          ocr: bool = True, add: bool = False, **kw):
        """rag.ingest.ingest + OCR of the scanned pages: each is read by the `ocr_transcribe` task at BACKGROUND, one at
        a time, at most INGEST_MAX_OCR_PAGES per file. The result is an IngestResult (see rag/ingest.py); add=True also
        puts its chunks in the live index (add_material). Each OCR run carries course_id and class_id, and the
        learner_id of a learner's own upload, never a room."""
        from rag import ingest as I
        ids = {"course_id": course_id, **({"class_id": class_id} if class_id else {}),
               **({"learner_id": owner} if source == "learner" and owner else {})}

        async def read(page: int, png: bytes) -> I.OcrPage:
            r = await self.run("ocr_transcribe", {"image": png}, {"language": kw.get("language"), **ids},
                               Priority.BACKGROUND)
            if r.degraded and r.degrade_reason != "low_confidence":
                return I.OcrPage(ok=False, reason=r.degrade_reason)
            return I.OcrPage((r.data or {}).get("text", ""), True, r.degrade_reason == "low_confidence")

        kw.setdefault("min_confidence", self.settings.ingest_min_tag_confidence)
        res = await I.ingest_with_ocr(file, course_id, unit_id, owner, visibility, source, class_id=class_id,
                                      ocr=read if ocr else None, max_ocr_pages=self.settings.ingest_max_ocr_pages,
                                      course=self.courses.get(course_id), glossary=self.glossary,
                                      max_tokens=self.settings.chunk_max_tokens, **kw)
        if add and res.chunks:
            await self.add_material(res.chunks)
        return res

    # ================================================================== prep pages
    async def generate_page(self, plan, focus_point: Mapping[str, Any], context: Mapping[str, Any] | None = None,
                            on_event: EventCallback | None = None) -> dict:
        """One prep page for a focus point of a prep_plan: the explanation (the `explain` task at BACKGROUND, through
        the same cache as the tutor's: an identical question is a hit), a worked example from the bank and three
        mini-quiz items (bank exercises of the concept, then of its prerequisites, then deterministic variants).
        `plan` is the prep_plan Result or its data; only focus_point is read from it, so it may be {}.
        Never raises a provider error: a degraded explanation comes back marked, the exercises are still there."""
        from engine import prep
        context = dict(context or {})
        courses = self.courses
        cid = context.get("course_id") or (next(iter(courses)) if len(courses) == 1 else None)
        course = courses.get(cid)
        concept = str(focus_point["concept_id"])
        kind = focus_point.get("page_kind", "learn")
        lang = L.normalize_label(context.get("language")) or "FRENCH"
        pl = phrase_language(lang)
        p = focus_point.get("mastery")
        if p is None:
            p = 0.9 if kind == "review" else 0.2 if focus_point.get("reason_code") == "low_mastery" else 0.5
        title = course.title(concept, pl) if course and concept in course.concepts else concept
        worked, quiz = prep.page_exercises(self.exercises, course, concept, float(p), lang)
        taken = [x["exercise_id"] for x in [worked] if x] + [q["id"] for q in quiz if q["source"] == "bank"]
        question = {"fr": f"C'est quoi : {title} ?", "en": f"What is: {title}?", "ar": f"ما هو: {title}؟"}[pl]
        ex = await self.run("explain", {"question": question, "exclude_exercises": taken},
                            {**context, "language": lang}, Priority.BACKGROUND, on_event)
        return {"concept_id": concept, "page_kind": kind, "title": title, "language": lang,
                "explanation": ex.text, "explanation_sections": ex.data, "explain_request_id": ex.request_id,
                "from_cache": ex.from_cache, "degraded": ex.degraded, "degrade_reason": ex.degrade_reason,
                "eta_seconds": ex.eta_seconds, "worked_example": worked, "quiz": quiz}

    # ================================================================== check_answer
    @staticmethod
    def _sub_context(tctx: TaskContext) -> dict:
        """The context of a run this engine starts for itself (compress, grade): language, turn and the learner's
        ids, but never room_id: the room is held by the run that is calling, it must not refuse its own helper."""
        keep = ("turn_id", "learner_id", "course_id", "class_id")
        return {"language": tctx.language, **{k: tctx.extra[k] for k in keep if tctx.extra.get(k) is not None}}

    async def _check_answer(self, prompt: PromptTemplate, inputs: dict, tctx: TaskContext, prio: Priority,
                            run: "_Run", ctx: CallContext) -> Result:
        """Line by line (engine.answers): safe parse -> OK / WRONG / UNCHECKED; the first WRONG line is explained by
        the exercise's stored mistakes, then by the buggy rules, else by one compact guiding question that is never
        allowed to state the answer; the last line goes through grade."""
        steps = inputs["steps"]
        steps = steps.splitlines() if isinstance(steps, str) else steps
        if not isinstance(steps, (list, tuple)):
            raise ValueError("task 'check_answer': steps must be a list of lines")
        lines = [str(s).strip() for s in steps if str(s).strip()][:answers.MAX_LINES]
        if not lines:
            raise ValueError("task 'check_answer' needs at least one non-empty step")
        exercise = None
        if inputs.get("exercise_id"):
            exercise = self.exercises.get(str(inputs["exercise_id"])) if self.exercises else None
            if exercise is None:
                raise ValueError(f"unknown exercise_id {inputs['exercise_id']!r}")
        run.decision = self.router.decide("check_answer", tctx.as_mapping(),
                                          self.caller.limiter.buckets["chat"].tokens, ctx)
        lang, problem = tctx.language or "FRENCH", str(inputs["problem"])
        run.emit("parsing_steps", {"steps": len(lines)})
        target = answers.target_for(problem, exercise)
        results = answers.check_steps(lines, target)
        run.emit("checking_steps")
        notes: dict[str, Any] = {"target": target.kind if target else None}
        solution = str(inputs.get("solution") or (exercise.solution if exercise else "") or
                       (target.solution_text() if target else "") or "").strip() or None
        used_llm = used_rules = False

        first = next((r.index for r in results if r.verdict == "WRONG"), None)
        feedback = ""
        if first is not None:
            w = results[first]
            diag = answers.diagnose(w.value, target, exercise, lang) if w.value is not None and w.note == "wrong" else None
            if diag is not None:
                feedback, notes["diagnosis"] = diag
                used_rules = True
            else:
                feedback, asked = await self._guiding_question(prompt, problem, w, target, solution, tctx, ctx, run)
                used_llm = asked
                notes["diagnosis"] = "llm" if asked else "template"

        final, final_fb, graded_llm = await self._final_verdict(lines, results, exercise, problem, solution, tctx, prio,
                                                                run)
        used_llm = used_llm or graded_llm
        if not feedback and final == "INCORRECT" and final_fb:
            feedback = final_fb
            used_rules = used_rules or not graded_llm
        data = {"steps": [{"index": r.index, "verdict": r.verdict, "note": answers.note(r.note, lang)} for r in results],
                "first_wrong": first, "final_verdict": final, "feedback": feedback,
                "graded_by": "llm" if used_llm else "rules" if used_rules else "sympy"}
        return self._finish(run, ctx, data=data, route_reason=run.decision.reason, notes=notes,
                            mode=None if used_llm else "rules", eta=getattr(run, "eta", None))

    async def _guiding_question(self, prompt, problem, wrong, target, solution, tctx, ctx, run) -> tuple[str, bool]:
        """One compact line asking the learner to look at their first wrong line. Never the answer: a line that
        leaks (the hint guard + the derivative written out) is replaced by the fixed question."""
        lang = tctx.language or "FRENCH"
        template = prompt.sections[f"guide_template.{lang}"]
        line = await self._one_liner(prompt, "guide", {"problem": problem, "n": wrong.index + 1, "step": wrong.line},
                                     "guide_template", tctx, ctx, run)
        if answers.leaks(line, target, solution):
            run.emit("guarded", {"reason": "hint_guard:answer_leak"})
            return template, False
        return line, line != template

    async def _final_verdict(self, lines, results, exercise, problem, solution, tctx, prio, run) -> tuple[str, str, bool]:
        """(verdict, feedback, used_llm): the last line against the solution. The exercise's stored answers are
        compared first (text included: variations, extrema); everything else goes through the grade task, which
        is sympy first and the compact grader only for text it cannot compare."""
        if not solution:
            return "UNKNOWN", "", False
        last = results[-1].line
        if answers.normalize_answer(last) and answers.normalize_answer(last) == answers.normalize_answer(solution):
            return "CORRECT", "", False                       # the stated solution, written the same way: no request
        if exercise is not None:
            for expr, fb in exercise.common_mistakes:
                if answers.normalize_answer(last) == answers.normalize_answer(expr):
                    return "INCORRECT", fb, False
        attempt = last.split("=")[-1].strip() if results[-1].value is not None and "=" in last else last
        mistakes = [{"expr": e, "feedback": f} for e, f in (exercise.common_mistakes if exercise else ())]
        res = await self.run("grade", {"problem": problem, "solution": solution, "attempt": attempt,
                                       "common_mistakes": mistakes}, self._sub_context(tctx), prio,
                             on_event=lambda ev: run.emit(ev.name, ev.data))
        run.tokens_in += res.tokens_in
        run.tokens_out += res.tokens_out
        if res.model_class_used and not res.from_cache:
            run.model_class, run.model = res.model_class_used, res.model
        for reason in ([res.degrade_reason] if res.degraded else []):
            run.degrade(reason)
        if res.degraded and res.eta_seconds is not None:
            run.eta = res.eta_seconds
        d = res.data or {}
        verdict = d.get("verdict") if d.get("verdict") in ("CORRECT", "INCORRECT", "PARTIAL") else "UNKNOWN"
        return verdict, d.get("feedback") or "", d.get("graded_by") == "llm" and not res.from_cache

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
                base = load_chunks(self.settings.rag_index_path)
                extra = self.course_store.load_all() if self.course_store else []   # runtime material of every course
                await self.retriever.index(base + extra)
                self._indexed = True

    # ================================================================== runtime material
    async def add_material(self, chunks: list) -> int:
        """Add chunks to the live index (professor documents, learner uploads) and persist them per course, so they
        survive a restart. A chunk with an existing id replaces the old one. Returns the number added."""
        if not hasattr(self.retriever, "add"):
            raise NotImplementedError(f"{type(self.retriever).__name__} cannot add chunks at runtime")
        await self._ensure_index()
        async with self._index_lock:
            n = self.retriever.add(chunks)
            self._persist({c.course_id for c in chunks})
        return n

    async def remove_material(self, chunk_ids=(), *, source: str | None = None, course_id: str | None = None,
                              owner: str | None = None) -> int:
        """Remove chunks by id, and/or all chunks of one uploaded `source` file (optionally within a course /
        of an owner). Course docs of the base index can be removed too, but they come back at the next restart
        unless the index is rebuilt. Returns how many were removed."""
        await self._ensure_index()

        def where(c) -> bool:
            return (source is not None and c.source == source and (course_id is None or c.course_id == course_id)
                    and (owner is None or c.owner == owner))
        async with self._index_lock:
            gone = self.retriever.remove(list(chunk_ids), where=where)
            self._persist({c.course_id for c in gone})
        return len(gone)

    def _persist(self, course_ids) -> None:
        if not self.course_store:
            return
        base = {c.chunk_id for c in load_chunks(self.settings.rag_index_path)}
        for cid in course_ids:
            if cid:
                self.course_store.save(cid, [c for c in self.retriever.chunks
                                             if c.course_id == cid and c.chunk_id not in base])

    @staticmethod
    def _query_text(inputs: Mapping) -> str:
        """What the lexical parts (fast-mode extracts) match against: the question and the selected passage."""
        return " ".join(x for x in (str(inputs.get("question") or ""), str(inputs.get("passage") or "")) if x.strip())

    async def _retrieve(self, inputs: Mapping, tctx: TaskContext, run: _Run) -> list[Hit]:
        """The searches, in order, until one passes the coverage gate: the selected passage (it IS the topic, when the
        learner selected one), the question, and the whiteboard text (when the question alone names no topic)."""
        run.emit("retrieving")
        await self._ensure_index()
        filters = {"topic": tctx.extra.get("topic")} if tctx.extra.get("topic") else None
        viewer = {k: tctx.extra.get(k) for k in ("course_id", "class_id", "learner_id")}
        filters = {**(filters or {}), VIEWER: viewer}      # public course docs, this class's material, own uploads
        limit = self.settings.retrieval_query_chars
        passage, board = str(inputs.get("passage") or "").strip(), str(inputs.get("board_text") or "").strip()
        queries = [(n, q) for n, q in (("passage", passage[:limit]), ("question", str(inputs["question"])),
                                       ("board_text", board[:limit])) if q.strip()]
        for source, query in queries:
            expansion = self.glossary.expand(query) if self.glossary else None   # deterministic, no LLM
            if expansion:
                run.emit("expanding_query", {"glossary": list(expansion.terms)})
                run.glossary = list(expansion.terms)
            for lang_filter in language_plan(tctx.language):
                hits = await self.retriever.search(query, self.settings.rag_top_k,
                                                   {**(filters or {}), **(lang_filter or {})} or None, expansion)
                if hits:
                    run.retrieval_on = source
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
        if run.retrieval_on and run.retrieval_on != "question" and ("passage" in inputs or "board_text" in inputs):
            notes["retrieval_on"] = run.retrieval_on
        if not asm.all_chunks_fit:
            over = [d["id"] for d in asm.dropped if d["kind"] == "chunk"]
            run.emit("compressing", {"chunks": len(chunks), "over_budget": over})
            comp = await self.run("compress",
                                  {"question": inputs["question"], "chunks": [c.to_dict() for c in chunks]},
                                  self._sub_context(tctx), prio)
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

    def _input_cut(self, run: "_Run", ctx: CallContext, chat: ChatResult) -> bool:
        """The provider silently dropped the start of the prompt: the answer is unreliable, so the result is
        degraded (which keeps it out of the cache) and the case is logged."""
        if not chat.input_truncated:
            return False
        if "input_truncated" not in run.reasons:
            run.degrade("input_truncated")
        log.warning("%s %s: provider truncated the input (prompt_tokens=%s)", ctx.request_id, ctx.task,
                    chat.tokens_in)
        return True

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
        except (TransientError, ClassUnavailable, ConcurrencyLimited) as e:
            down = (isinstance(e, ClassUnavailable) or (isinstance(e, TransientError) and e.timeout)
                    or (isinstance(e, ConcurrencyLimited) and e.after_timeout))
            if not down or d.fallback is None or d.model_class is d.fallback:
                raise
            # the class hung (no retry was spent: retry.py), went down while we waited, or the server still counts
            # our timed-out request: the breaker is told where it matters; answer from the fallback now
            fb = self.router.on_unavailable(d, ctx)
            eta = self.router.retry_in(d.model_class) or self.settings.breaker_cooldown_s
            run.degrade(fb.degrade_reason)
            run.emit("falling_back_to_compact", {"reason": fb.degrade_reason})
            if not self.router.breakers[d.model_class].closed:
                run.emit("breaker_open", {"model_class": d.model_class.value, "retry_in_s": round(eta, 1)})
            if spec.grounded:
                raise _FastMode(fb, round(eta, 1)) from None
            run.model_class = fb.model_class.value
            chat = await self._chat(spec, fb.model_class, messages, ctx)
        run.took(chat)
        self._input_cut(run, ctx, chat)
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
                self._input_cut(run, ctx, rchat)
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
        self.cache.link(ctx.request_id, key)
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
        if retryable and isinstance(e, QuotaExhausted):
            # the limiter is blocked until the stated delay expires: that remaining delay is when to come back
            wait = self.caller.limiter.wait_time("chat") or (e.retry_after_s or DEFAULT_RETRY_AFTER_S)
            eta = round(wait, 1)
        elif retryable:
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
        if run.silent:
            return res
        self.sink.write(CallRecord(
            request_id=ctx.request_id, turn_id=ctx.turn_id, kind="run", task=ctx.task, priority=ctx.priority,
            model_class=res.model_class_used, model=res.model, outcome=outcome, from_cache=from_cache,
            from_negative_cache=run.negative_hit,
            degraded=res.degraded, degrade_reason=res.degrade_reason, latency_ms=res.latency_ms,
            tokens_in=res.tokens_in, tokens_out=res.tokens_out, eta_s=eta, mode=mode, detail=route_reason,
            graded_by=(data or {}).get("graded_by") if ctx.task == "grade" else None))
        return res
