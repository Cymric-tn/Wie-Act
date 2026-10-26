"""
Constraint simulator: an httpx transport that sits in front of the real provider
(Ollama locally) and reproduces the FelCloud platform contract:

  | situation                 | simulated response                                         |
  |---------------------------|------------------------------------------------------------|
  | quota exhausted           | 429 quota_exhausted + Retry-After (+ "retry in Ns" in msg)  |
  | mid-size saturated        | annex:   503 model_saturated + Retry-After                  |
  |                           | litellm: 429 all_deployments_in_cooldown + Retry-After     |
  | too many in flight        | 429 concurrency_limit + Retry-After                         |
  | beyond a ceiling          | 400 naming the ceiling (max_tokens / prompt tokens)         |
  | class hangs (outage)      | no answer: the client times out (httpx.ReadTimeout at its    |
  |                           | read timeout) while the "server" keeps counting the request  |
  |                           | in flight until SERVER_TIMEOUT_S (or hang_s) after arrival   |
  | normal                    | provider's 200 + quota headers, after extra per-class delay |

Ported from mock_server.py (which stays usable as a standalone HTTP mock).
Because responses are real httpx.Response objects, the gatekeeper code exercised here is
exactly the code that runs against FelCloud.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from collections import Counter
from dataclasses import dataclass, field

import httpx

from config import ModelClass, Settings
from gatekeeper import errors as E
from gatekeeper.limiter import Clock, Sleep, TokenBucket


@dataclass
class SimStats:
    calls: Counter = field(default_factory=Counter)        # model -> successful calls
    refusals: Counter = field(default_factory=Counter)     # "status:type" -> count
    max_in_flight: int = 0


class ConstraintSimulator(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport, settings: Settings,
                 clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep):
        self.inner, self.settings, self.cfg = inner, settings, settings.sim
        self._sleep, self._clock = sleep, clock
        self.enabled, self.profile = True, "env"
        self.rng = random.Random(self.cfg.seed)
        self.chat_bucket = TokenBucket(self.cfg.rate_per_min, self.cfg.burst, clock)
        self.embed_bucket = TokenBucket(self.cfg.embed_rate_per_min, self.cfg.embed_burst, clock)
        self.in_flight = 0
        self.midsize_in_flight = 0
        self.stats = SimStats()
        self._bg: set[asyncio.Task] = set()     # server-side bookkeeping of hung requests
        self.force: str | None = None    # tests/demo: "quota" | "saturated" | "ceiling" | "transient"

    def apply_profile(self, name: str) -> None:
        """Switch at runtime (chat.py /sim): a named profile over the env settings, or "off" = pass-through."""
        from dataclasses import replace
        from simulator.profiles import PROFILES
        if name == "off":
            self.enabled, self.profile = False, "off"
            return
        self.cfg = replace(self.settings.sim, **PROFILES[name])
        self.chat_bucket = TokenBucket(self.cfg.rate_per_min, self.cfg.burst, self._clock)
        self.embed_bucket = TokenBucket(self.cfg.embed_rate_per_min, self.cfg.embed_burst, self._clock)
        self.enabled, self.profile = True, name

    # ------------------------------------------------------------------ dispatch
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not self.enabled:
            return await self._forward(request)
        path = request.url.path
        if path.endswith("/chat/completions"):
            return await self._chat(request)
        if path.endswith("/embeddings"):
            return await self._embed(request)
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        for t in list(self._bg):
            t.cancel()
        await self.inner.aclose()

    # ------------------------------------------------------------------ helpers
    def _quota_headers(self, b: TokenBucket) -> dict[str, str]:
        return {E.H_LIMIT_REQUESTS: str(int(b.capacity)),
                E.H_REMAINING_REQUESTS: str(max(0, int(b.tokens))),
                E.H_LIMIT_PARALLEL: str(self.cfg.max_concurrent),
                E.H_REMAINING_PARALLEL: str(max(0, self.cfg.max_concurrent - self.in_flight))}

    def _refuse(self, request, status: int, etype: str, msg: str, headers=None) -> httpx.Response:
        self.stats.refusals[f"{status}:{etype}"] += 1
        return httpx.Response(status, json={"error": {"message": msg, "type": etype, "code": str(status)}},
                              headers=headers or {}, request=request)

    def _forced(self, what: str) -> bool:
        return self.force == what

    async def _forward(self, request: httpx.Request) -> httpx.Response:
        resp = await self.inner.handle_async_request(request)
        content = await resp.aread()
        await resp.aclose()
        # body is already decoded: drop encoding/length headers so it is not decoded twice
        headers = [(k, v) for k, v in resp.headers.items()
                   if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(resp.status_code, headers=headers, content=content, request=request)

    # ------------------------------------------------------------------ chat
    async def _chat(self, request: httpx.Request) -> httpx.Response:
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            return self._refuse(request, 400, "invalid_request", "body is not JSON")
        model = body.get("model", "")
        cls = self.settings.class_of(model)

        wait = self.chat_bucket.try_take()
        if wait > 0 or self._forced("quota"):
            wait = max(wait, 1.0)
            # ASSUMPTION(felcloud): quota-exhausted shape (status, type, header) is unconfirmed.
            return self._refuse(request, 429, "quota_exhausted",
                                f"Team quota exhausted. Retry in {wait:.1f}s",
                                {E.H_RETRY_AFTER: f"{wait:.0f}" if wait >= 1 else "1",
                                 **self._quota_headers(self.chat_bucket)})
        if self.in_flight >= self.cfg.max_concurrent:
            return self._refuse(request, 429, "concurrency_limit",
                                "Max parallel requests reached for this key", {E.H_RETRY_AFTER: "1"})

        mt = body.get("max_tokens") or 0
        if mt > self.cfg.max_tokens_ceiling or self._forced("ceiling"):
            return self._refuse(request, 400, "ceiling_exceeded",
                                f"max_tokens {mt} exceeds ceiling {self.cfg.max_tokens_ceiling}")
        # the provider counts with its real tokenizer: ~2.5 chars/token on average (measured on data/docs)
        prompt_tokens = int(sum(len(str(m.get("content", ""))) for m in body.get("messages", [])) / 2.5)
        if prompt_tokens > self.cfg.prompt_tokens_ceiling:
            return self._refuse(request, 400, "ceiling_exceeded",
                                f"prompt is {prompt_tokens} tokens, exceeds ceiling "
                                f"{self.cfg.prompt_tokens_ceiling}")

        if cls is ModelClass.MIDSIZE and (
                self._forced("saturated")
                or (self.cfg.midsize_slots and self.midsize_in_flight >= self.cfg.midsize_slots)
                or self.rng.random() < self.cfg.saturation_prob):
            ra = str(self.rng.randint(*self.cfg.saturation_retry_after_s))
            if self.cfg.saturation_dialect == "litellm":   # exactly what recon observed
                return self._refuse(
                    request, 429, "all_deployments_in_cooldown",
                    f"No deployments available for selected model, Try again in {ra} seconds. "
                    f"All deployments for selected model are in cooldown. Passed model={model}",
                    {E.H_RETRY_AFTER: ra})
            return self._refuse(request, 503, "model_saturated", "Mid-size model class saturated",
                                {E.H_RETRY_AFTER: ra})

        if cls is ModelClass.COMPACT and self.cfg.compact_saturation_prob \
                and self.rng.random() < self.cfg.compact_saturation_prob:
            ra = str(self.rng.randint(*self.cfg.saturation_retry_after_s))
            return self._refuse(request, 503, "model_saturated", "Compact model class saturated",
                                {E.H_RETRY_AFTER: ra})

        if self._forced("transient") or self.rng.random() < self.cfg.transient_error_prob:
            return self._refuse(request, 500, "server_error", "upstream error")

        is_mid = cls is ModelClass.MIDSIZE
        if (is_mid and self.cfg.hang_midsize) or (cls is ModelClass.COMPACT and self.cfg.hang_compact):
            return await self._hang(request)
        self.in_flight += 1
        self.midsize_in_flight += is_mid
        self.stats.max_in_flight = max(self.stats.max_in_flight, self.in_flight)
        try:
            lo, hi = self.cfg.latency_midsize_s if is_mid else self.cfg.latency_compact_s
            delay = self.cfg.latency_scale * self.rng.uniform(lo, hi)
            resp, _ = await asyncio.gather(self._forward(request), self._sleep(delay))
        finally:
            self.in_flight -= 1
            self.midsize_in_flight -= is_mid
        if resp.status_code == 200:
            self.stats.calls[model] += 1
            for k, v in self._quota_headers(self.chat_bucket).items():
                resp.headers[k] = v
        return resp

    async def _hang(self, request: httpx.Request) -> httpx.Response:
        """The class accepts the request and never answers (hang_s=None) or answers after hang_s seconds.
        The client gives up at its read timeout (httpx.ReadTimeout, as the real client does); the "server"
        keeps the request in flight until it ends by itself (hang_s, or the proxy's SERVER_TIMEOUT_S), whether
        or not the client is still there: this is why a retry right after a timeout is refused with a 429."""
        hang_s, server_s = self.cfg.hang_s, self.settings.server_timeout_s
        self.in_flight += 1
        self.stats.max_in_flight = max(self.stats.max_in_flight, self.in_flight)

        async def server_side() -> None:
            try:
                await self._sleep(min(hang_s, server_s) if hang_s is not None else server_s)
            finally:
                self.in_flight -= 1
        task = asyncio.get_running_loop().create_task(server_side())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

        read_timeout = (request.extensions.get("timeout") or {}).get("read")
        if hang_s is not None and (read_timeout is None or hang_s <= read_timeout):
            await self._sleep(hang_s)
            return await self._forward(request)
        await self._sleep(read_timeout if read_timeout is not None else server_s)
        if read_timeout is None:
            return self._refuse(request, 504, "gateway_timeout", "upstream timed out")
        raise httpx.ReadTimeout("simulated hang: no answer", request=request)

    # ------------------------------------------------------------------ embeddings
    async def _embed(self, request: httpx.Request) -> httpx.Response:
        wait = self.embed_bucket.try_take()
        if wait > 0:
            return self._refuse(request, 429, "quota_exhausted",
                                f"Embeddings quota exhausted. Retry in {wait:.1f}s",
                                {E.H_RETRY_AFTER: f"{max(1.0, wait):.0f}",
                                 **self._quota_headers(self.embed_bucket)})
        if self.in_flight >= self.cfg.max_concurrent:
            return self._refuse(request, 429, "concurrency_limit",
                                "Max parallel requests reached for this key", {E.H_RETRY_AFTER: "1"})
        self.in_flight += 1
        self.stats.max_in_flight = max(self.stats.max_in_flight, self.in_flight)
        try:
            resp = await self._forward(request)
        finally:
            self.in_flight -= 1
        if resp.status_code == 200:
            self.stats.calls["embeddings"] += 1
            for k, v in self._quota_headers(self.embed_bucket).items():
                resp.headers[k] = v
        return resp
