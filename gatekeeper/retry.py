"""
Retry policy — what to do with each typed error from errors.py (the platform contract):

  | error             | action                                                              |
  |-------------------|---------------------------------------------------------------------|
  | QuotaExhausted    | the delay is the Retry-After header only (observed: 60 s). The      |
  |                   | window is blocked until it expires, whatever the priority.          |
  |                   | BACKGROUND waits up to MAX_RETRY_WAIT_S_BACKGROUND (65), INTERACTIVE|
  |                   | up to MAX_RETRY_WAIT_S (30); a longer delay -> raise with it (the   |
  |                   | engine degrades with eta = remaining delay). Never hop models       |
  |                   | (quota is per key, another model would be refused too).             |
  | ModelSaturated    | mid-size: retry after Retry-After up to SATURATION_RETRIES, then    |
  |                   | raise so the router can fall back to compact.                       |
  |                   | compact: no fallback exists, so up to COMPACT_MAX_RETRIES, honoring |
  |                   | Retry-After each time, then raise (the engine degrades, never 500s).|
  | ConcurrencyLimited| wait min(Retry-After, CONCURRENCY_RETRY_WAIT_S), retry (the 429     |
  |                   | states 60 s but the slot frees as soon as a request ends).          |
  | CeilingExceeded   | fix the request once (trim max_tokens / context), retry once.       |
  | TransientError    | exponential backoff + full jitter (only when no delay is stated).   |
  |  (mid-size timeout)| no retry: a hung model does not answer a second try; raise at once  |
  |                   | so the engine falls back (compact keeps the retries above).         |
  | FatalError        | raise immediately.                                                  |

Before the first attempt the estimated input is checked against INPUT_TOKEN_BUDGET and against the
context window (estimate + output cap <= CONTEXT_WINDOW * CONTEXT_FILL), because FelCloud silently drops
the START of a prompt above about 4096 tokens (recon and probe C: 200 with prompt_tokens=2050).
After the answer: prompt_tokens < prompt chars / 5 means the input was cut (reported, not cached by the
engine), and completion_tokens >= the cap means the output was cut (finish_reason says "stop" either way).
Every attempt writes one CallRecord and emits progress events.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from config import ModelClass, Settings
from gatekeeper.client import (ChatRequest, EmbedRequest, LLMClient, chars_per_token, estimate_tokens,
                               parse_chat, parse_embed, request_hash, set_chars_per_token)
from gatekeeper.errors import (CeilingExceeded, ClassUnavailable, ConcurrencyLimited, FatalError, LLMError,
                               ModelSaturated,
                               QuotaExhausted, QuotaInfo, TransientError, classify_exception,
                               classify_response, parse_quota)
from gatekeeper.limiter import Clock, Limiter, Sleep
from gatekeeper.queue import PriorityQueue
from metrics import CallRecord, MetricsSink

EventFn = Callable[[str, dict[str, Any]], None]
FixFn = Callable[[ChatRequest, CeilingExceeded], "ChatRequest | None"]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    max_wait_s: float = 30.0              # INTERACTIVE: longest stated delay it will wait for
    max_wait_background_s: float = 65.0   # BACKGROUND: the observed quota delay is 60 s
    concurrency_wait_s: float = 2.0       # cap on a concurrency 429's stated delay
    timeout_midsize_interactive_s: float = 40.0
    timeout_midsize_background_s: float = 85.0
    timeout_compact_s: float = 30.0
    probe_timeout_s: float = 15.0         # the breaker's half-open probe
    server_timeout_s: float = 90.0        # the proxy still counts an abandoned request until then (ghost slot)
    saturation_retries: int = 1           # mid-size
    compact_saturation_retries: int = 3
    backoff_base_s: float = 0.5
    backoff_cap_s: float = 8.0

    @classmethod
    def from_settings(cls, s: Settings) -> "RetryPolicy":
        return cls(max_attempts=s.max_attempts, max_wait_s=s.max_retry_wait_s,
                   max_wait_background_s=s.max_retry_wait_s_background, concurrency_wait_s=s.concurrency_retry_wait_s,
                   saturation_retries=s.saturation_retries, compact_saturation_retries=s.compact_max_retries,
                   timeout_midsize_interactive_s=s.timeout_midsize_interactive_s,
                   timeout_midsize_background_s=s.timeout_midsize_background_s,
                   timeout_compact_s=s.timeout_compact_s, probe_timeout_s=s.breaker_probe_timeout_s,
                   server_timeout_s=s.server_timeout_s)

    def timeout_for(self, model_class: ModelClass, priority: str | None, purpose: str | None = None) -> float:
        """Seconds a chat request may go without an answer: per class and priority, all under the server's 90 s.
        A learner waiting (INTERACTIVE) is given less than background work; the breaker probe the least."""
        if model_class is ModelClass.COMPACT:
            base = self.timeout_compact_s
        elif priority == "BACKGROUND":
            base = self.timeout_midsize_background_s
        else:
            base = self.timeout_midsize_interactive_s
        return min(base, self.probe_timeout_s) if purpose == "probe" else base

    @property
    def hard_cap(self) -> int:
        """Absolute attempt ceiling; each error kind also has its own budget below this."""
        return self.max_attempts + max(self.saturation_retries, self.compact_saturation_retries)

    def backoff(self, attempt: int, rng: random.Random) -> float:
        """Full jitter: uniform(0, min(cap, base * 2^(attempt-1)))."""
        return rng.uniform(0, min(self.backoff_cap_s, self.backoff_base_s * 2 ** (attempt - 1)))


@dataclass
class CallContext:
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    task: str | None = None
    priority: str | None = None
    purpose: str | None = None            # None = the task call itself; "repair" = JSON repair retry
    on_event: EventFn | None = None
    queue_seq: int | None = None          # set by the queue on first admission, reused by retries
    saturation_retries: int | None = None  # override the class budget (0 = fail fast, e.g. a nicety call)
    turn_id: str | None = None            # caller's learner-turn id, copied to every metrics record

    def emit(self, name: str, **data) -> None:
        if self.on_event:
            self.on_event(name, data)


@dataclass
class ChatResult:
    text: str
    model: str
    model_class: str
    tokens_in: int | None
    tokens_out: int | None
    attempts: int
    latency_ms: float
    wait_ms: float
    request: ChatRequest                 # the request actually sent (may have been trimmed)
    trimmed: bool = False
    quota: QuotaInfo = field(default_factory=QuotaInfo)
    truncated: bool = False              # completion_tokens >= the cap: the output was cut (finish_reason is unreliable)
    input_truncated: bool = False        # prompt_tokens far below the prompt size: the provider cut the input


@dataclass
class EmbedResult:
    vectors: list[list[float]]
    model: str
    tokens_in: int | None
    attempts: int
    latency_ms: float
    wait_ms: float


# ---------------------------------------------------------------------- request fixing
MIN_MAX_TOKENS = 16


def _trim_messages(req: ChatRequest, target_tokens: int) -> ChatRequest | None:
    """Shorten the longest non-system message (keep its head) until the estimate fits."""
    msgs = [dict(m) for m in req.messages]
    excess_chars = math.ceil((estimate_tokens(msgs) - target_tokens) * chars_per_token())
    if excess_chars <= 0:
        return req
    idx = [i for i, m in enumerate(msgs) if m.get("role") != "system"]
    if not idx:
        return None
    i = max(idx, key=lambda j: len(msgs[j].get("content") or ""))
    content = msgs[i].get("content") or ""
    keep = len(content) - excess_chars - 8
    if keep < 40:
        return None
    msgs[i]["content"] = content[:keep] + " […]"
    return req.with_(messages=tuple(msgs))


def default_fix_request(req: ChatRequest, err: CeilingExceeded) -> ChatRequest | None:
    """Generic fixer. The engine passes a smarter one later (e.g. drop RAG chunks first)."""
    if err.ceiling == "max_tokens" or err.ceiling == "unknown":
        cur = req.max_tokens or 0
        new = err.limit if err.limit and err.limit < cur else cur // 2
        return req.with_(max_tokens=new) if new >= MIN_MAX_TOKENS else None
    if err.ceiling == "prompt_tokens":
        est = estimate_tokens(req.messages)
        target = int((err.limit or est * 0.75) * 0.9)
        fixed = _trim_messages(req, target)
        return fixed if fixed is not None and estimate_tokens(fixed.messages) <= target else None
    return None


# ---------------------------------------------------------------------- caller
class RetryingCaller:
    """Client + limiter + retry policy + metrics. The only path to the provider."""

    def __init__(self, client: LLMClient, limiter: Limiter, sink: MetricsSink, policy: RetryPolicy,
                 input_token_budget: int, clock: Clock = time.monotonic,
                 sleep: Sleep = asyncio.sleep, rng: random.Random | None = None,
                 queue: PriorityQueue | None = None):
        self.client, self.limiter, self.sink, self.policy = client, limiter, sink, policy
        self.queue = queue or PriorityQueue(limiter, clock)
        # (model_class, error or None) after every chat attempt: feeds the router's circuit breakers
        self.observer: Callable[[ModelClass, LLMError | None], None] | None = None
        # model class -> seconds until it is probed again, or None while it is usable (the router's breaker):
        # a request waiting for a slot of a class that goes down is failed with ClassUnavailable, nothing sent
        self.availability: Callable[[ModelClass], float | None] | None = None
        self.input_token_budget = input_token_budget
        cfg = getattr(client, "settings", None)
        if cfg is not None:
            set_chars_per_token(cfg.estimate_chars_per_token)
        self.context_limit = int(cfg.context_window * cfg.context_fill) if cfg is not None else None
        self.truncation_cpt = cfg.truncation_chars_per_token if cfg is not None else 5.0   # real text packs <= ~4
        self._clock, self._sleep = clock, sleep
        self._rng = rng or random.Random()

    # ------------------------------------------------------------ public
    async def chat(self, req: ChatRequest, ctx: CallContext | None = None,
                   fix_request: FixFn = default_fix_request) -> ChatResult:
        ctx = ctx or CallContext()
        req = self.client.resolve(req)
        trimmed = False

        est = estimate_tokens(req.messages)
        budget = self.input_token_budget
        if self.context_limit is not None:      # the prompt AND the answer must fit the context window
            budget = min(budget, self.context_limit - (req.max_tokens or 0))
        if est > budget:
            local = CeilingExceeded(message=f"input ~{est} tokens > client budget {budget}",
                                    ceiling="prompt_tokens", limit=budget)
            ctx.emit("trimming_input", estimated_tokens=est, budget=budget)
            fixed = fix_request(req, local)
            if fixed is None:
                self._write(ctx, req, attempt=0, outcome="ceiling", action="raise", detail=local.message)
                raise local
            req, trimmed = fixed, True

        state = {"fixed": False, "saturated": 0, "req": req}

        async def send(r: ChatRequest) -> httpx.Response:
            return await self.client.chat(r, timeout=self.policy.timeout_for(r.model_class, ctx.priority, ctx.purpose))

        resp, attempt, latency_ms, wait_ms, total_wait_ms, quota = await self._run(
            "chat", ctx, state, send, fix_request)
        try:
            text, tin, tout = parse_chat(resp)
        except (ValueError, KeyError, IndexError, TypeError) as e:   # malformed 200: give up loudly
            raise TransientError(status=resp.status_code, message=f"malformed response: {e}",
                                 attempts=attempt) from e
        req = state["req"]
        chars = sum(len(m.get("content") or "") for m in req.messages)
        truncated = tout is not None and req.max_tokens is not None and tout >= req.max_tokens
        input_cut = bool(self.truncation_cpt) and tin is not None and tin < chars / self.truncation_cpt
        detail = f"input_truncated: prompt_tokens={tin} for {chars} chars" if input_cut else None
        if truncated:
            ctx.emit("output_truncated", completion_tokens=tout, cap=req.max_tokens)
        if input_cut:
            ctx.emit("input_truncated", prompt_tokens=tin, prompt_chars=chars)
        self._write(ctx, req, attempt=attempt, status=resp.status_code, outcome="ok",
                    latency_ms=latency_ms, wait_ms=wait_ms, tokens_in=tin, tokens_out=tout, quota=quota,
                    detail=detail, truncated_output=truncated)
        return ChatResult(text=text, model=req.model, model_class=req.model_class.value, tokens_in=tin,
                          tokens_out=tout, attempts=attempt, latency_ms=latency_ms, wait_ms=total_wait_ms,
                          request=req, trimmed=trimmed or state["fixed"], quota=quota, truncated=truncated,
                          input_truncated=input_cut)

    async def embed(self, req: EmbedRequest, ctx: CallContext | None = None) -> EmbedResult:
        ctx = ctx or CallContext()
        state = {"fixed": True, "saturated": 0, "req": req}       # nothing to fix on embeddings

        async def send(r: EmbedRequest) -> httpx.Response:
            return await self.client.embed(r)

        resp, attempt, latency_ms, wait_ms, total_wait_ms, quota = await self._run("embed", ctx, state, send, None)
        vecs, tin = parse_embed(resp)
        self._write(ctx, req, attempt=attempt, status=resp.status_code, outcome="ok", latency_ms=latency_ms,
                    wait_ms=wait_ms, tokens_in=tin, quota=quota, kind="embed")
        return EmbedResult(vectors=vecs, model=self.client.embed_body(req)["model"], tokens_in=tin,
                           attempts=attempt, latency_ms=latency_ms, wait_ms=total_wait_ms)

    # ------------------------------------------------------------ loop
    async def _run(self, kind, ctx: CallContext, state: dict, send: Callable[[Any], Awaitable[httpx.Response]],
                   fix_request: FixFn | None):
        state["saturation_budget"] = ctx.saturation_retries
        state["priority"] = ctx.priority
        state["purpose"] = ctx.purpose
        p = self.policy
        extra_wait = total_wait_ms = 0.0
        for attempt in range(1, p.hard_cap + 1):
            req = state["req"]
            cls = req.model_class if kind == "chat" else None
            gate = None
            if cls is not None and self.availability is not None and ctx.purpose != "probe":
                gate = lambda c=cls: self.availability(c)           # a probe is how a down class is tested
            try:
                async with self.queue.slot(kind, ctx.priority, ctx, model_class=cls, unavailable=gate) as slot:
                    ctx.emit("generating" if kind == "chat" else "embedding", attempt=attempt,
                             model_class=getattr(getattr(req, "model_class", None), "value", None))
                    t0 = self._clock()
                    try:
                        resp: httpx.Response | None = await send(req)
                        err = classify_response(resp)
                    except httpx.HTTPError as exc:
                        resp, err = None, classify_exception(exc)
                        if getattr(err, "timeout", False):          # the server still counts it: a ghost slot
                            slot.ghost_for_s = max(0.0, p.server_timeout_s - (self._clock() - t0))
                    except asyncio.CancelledError:                   # abandoned mid-flight: same, the server went on
                        slot.ghost_for_s = max(0.0, p.server_timeout_s - (self._clock() - t0))
                        raise
                    latency_ms = (self._clock() - t0) * 1000
            except ClassUnavailable as cu:                           # the breaker opened while we waited: nothing sent
                ctx.emit("gave_up", error=cu.kind, retry_after_s=cu.retry_after_s)
                cu.attempts = attempt - 1
                raise
            wait_ms = (slot.wait_s + extra_wait) * 1000
            extra_wait = 0.0
            total_wait_ms += wait_ms
            quota = parse_quota(resp.headers) if resp is not None else QuotaInfo()
            if err is None:                # phantom entries only from a 200; a refusal states its own delay
                self.limiter.observe(kind, quota, t0)
            if kind == "chat" and self.observer is not None:
                self.observer(req.model_class, err)
            if err is None:
                if cls is not None:
                    self.limiter.clear_ghosts(cls)      # the class answers again: its old counts are gone
                return resp, attempt, latency_ms, wait_ms, total_wait_ms, quota

            action, delay = self._decide(kind, err, attempt, state, fix_request)
            self._write(ctx, req, attempt=attempt, status=err.status, outcome=err.kind, action=action,
                        latency_ms=latency_ms, wait_ms=wait_ms, quota=quota, detail=err.message[:200],
                        kind=kind)
            if action == "raise":
                err.attempts = attempt
                ctx.emit("gave_up", error=err.kind, retry_after_s=err.retry_after_s)
                raise err
            ctx.emit(action, error=err.kind, seconds=round(delay, 1), attempt=attempt)
            if delay > 0 and action != "wait_stated_delay":   # quota waits happen in the bucket
                await self._sleep(delay)
                extra_wait = delay
        err.attempts = p.hard_cap
        ctx.emit("gave_up", error=err.kind, retry_after_s=err.retry_after_s)
        raise err

    def _decide(self, kind, err: LLMError, attempt: int, state: dict, fix_request) -> tuple[str, float]:
        p = self.policy
        last = attempt >= p.max_attempts
        if state.get("purpose") == "probe":
            return "raise", 0.0                    # a breaker probe is one request: its failure IS the answer
        if isinstance(err, QuotaExhausted):
            delay = err.retry_after_s if err.retry_after_s is not None else p.backoff(attempt, self._rng)
            self.limiter.block(kind, delay)        # in every case nothing is sent before the delay expires
            cap = p.max_wait_background_s if state.get("priority") == "BACKGROUND" else p.max_wait_s
            if delay > cap or last:
                return "raise", 0.0                # the engine degrades with eta = the remaining delay
            return "wait_stated_delay", delay
        if isinstance(err, ModelSaturated):
            state["saturated"] += 1
            delay = err.retry_after_s or 0.0
            # mid-size: the router owns the fallback, so stop early. compact / embed: nowhere to fall back
            if state.get("saturation_budget") is not None:
                budget = state["saturation_budget"]
            elif kind == "chat" and state["req"].model_class is ModelClass.MIDSIZE:
                budget = p.saturation_retries
            elif kind == "chat":
                budget = p.compact_saturation_retries
            else:
                budget = p.max_attempts - 1
            if state["saturated"] > budget or delay > p.max_wait_s:
                return "raise", 0.0
            return "retry_after_interval", delay
        if isinstance(err, ConcurrencyLimited):
            cls = state["req"].model_class if kind == "chat" else None
            if cls is ModelClass.MIDSIZE and self.limiter.ghost_count(cls):
                err.after_timeout = True           # the server still counts our timed-out request: not a retry
                return "raise", 0.0                # case, not a breaker case; the engine falls back
            wait = min(err.retry_after_s or 1.0, p.concurrency_wait_s)
            return ("raise", 0.0) if last else ("wait_slot", wait)
        if isinstance(err, CeilingExceeded):
            if state["fixed"] or fix_request is None or last:
                return "raise", 0.0
            fixed = fix_request(state["req"], err)
            if fixed is None:
                return "raise", 0.0
            state["req"], state["fixed"] = fixed, True
            return "fix_request", 0.0
        if isinstance(err, TransientError):
            if err.timeout and kind == "chat" and state["req"].model_class is ModelClass.MIDSIZE:
                return "raise", 0.0                # a hung mid-size model will not answer a retry: fall back now
            if last:
                return "raise", 0.0
            delay = err.retry_after_s if err.retry_after_s is not None else p.backoff(attempt, self._rng)
            return ("raise", 0.0) if delay > p.max_wait_s else ("backoff", delay)
        return "raise", 0.0                                            # FatalError and unknowns

    # ------------------------------------------------------------ metrics
    def _write(self, ctx: CallContext, req, *, attempt: int, outcome: str, status: int | None = None,
               action: str | None = None, latency_ms: float = 0.0, wait_ms: float = 0.0,
               tokens_in: int | None = None, tokens_out: int | None = None, quota: QuotaInfo | None = None,
               detail: str | None = None, kind: str = "chat", truncated_output: bool = False) -> None:
        if isinstance(req, ChatRequest):
            body, model, cls = self.client.chat_body(req), req.model, req.model_class.value
        else:
            body = self.client.embed_body(req)
            model, cls = body["model"], None
        self.sink.write(CallRecord(
            request_id=ctx.request_id, turn_id=ctx.turn_id, attempt=attempt, kind=kind, task=ctx.task,
            priority=ctx.priority,
            purpose=ctx.purpose,
            model_class=cls, model=model, request_hash=request_hash(body), status=status, outcome=outcome,
            action=action, wait_ms=round(wait_ms, 1), latency_ms=round(latency_ms, 1), tokens_in=tokens_in,
            tokens_out=tokens_out, quota_remaining=quota.remaining_requests if quota else None, detail=detail,
            truncated_output=truncated_output))


def build_caller(settings: Settings, sink: MetricsSink, client: LLMClient | None = None) -> RetryingCaller:
    from gatekeeper.client import create_client
    return RetryingCaller(client or create_client(settings), Limiter(settings), sink,
                          RetryPolicy.from_settings(settings), settings.input_token_budget)
