"""
Thin OpenAI-compatible async client. It only builds requests and sends them:
no retry, no rate limiting, no error interpretation (that is retry.py / limiter.py / errors.py).
Nothing outside gatekeeper/ may use it directly.

The transport is injectable; with SIMULATE_CONSTRAINTS=true create_client() wraps the real
transport in the constraint simulator, so the rest of the gatekeeper sees genuine
httpx.Response objects either way.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from config import ModelClass, Settings


@dataclass(frozen=True)
class ChatRequest:
    model_class: ModelClass
    messages: tuple[dict[str, str], ...]
    max_tokens: int | None = None          # None -> class default from config
    temperature: float = 0.2
    json_mode: bool = False
    model: str | None = None               # None -> first model of the class
    frequency_penalty: float | None = None  # sent only when set (some providers reject it)

    def with_(self, **kw) -> "ChatRequest":
        return replace(self, **kw)


@dataclass(frozen=True)
class EmbedRequest:
    texts: tuple[str, ...]
    model: str | None = None


# Measured on FelCloud (probes 2026-10-26, empty-prompt baseline of 26 tokens removed): French 3.9
# chars/token, Arabic 2.84, Darija in Arabic script 2.54, Arabizi 2.31. The default stays at the worst
# measured case so the estimate is an upper bound: FelCloud silently truncates the START of a prompt
# above about 4096 tokens and will not warn us. ESTIMATE_CHARS_PER_TOKEN sets it (one engine per process).
CHARS_PER_TOKEN = 2.3
TOKENS_PER_MESSAGE = 4


def set_chars_per_token(value: float) -> None:
    global CHARS_PER_TOKEN
    CHARS_PER_TOKEN = float(value)


def chars_per_token() -> float:
    return CHARS_PER_TOKEN


def estimate_text_tokens(text: str, per_token: float | None = None) -> int:
    return math.ceil(len(text) / (per_token or CHARS_PER_TOKEN))


def estimate_tokens(messages) -> int:
    """Conservative (over-)estimate used by the input budget guard and rag/budget.py."""
    return sum(estimate_text_tokens(m.get("content") or "") for m in messages) + TOKENS_PER_MESSAGE * len(messages)


def request_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


@dataclass
class LLMClient:
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None
    _http: httpx.AsyncClient = field(init=False, repr=False)

    def __post_init__(self):
        self._http = httpx.AsyncClient(
            base_url=self.settings.base_url,
            headers={"Authorization": f"Bearer {self.settings.api_key}"},
            timeout=self.settings.request_timeout_s,
            transport=self.transport,
        )

    def resolve(self, req: ChatRequest) -> ChatRequest:
        """Fill model name and max_tokens from config so the body is fully determined."""
        return req.with_(
            model=req.model or self.settings.model_for(req.model_class),
            max_tokens=req.max_tokens or self.settings.max_tokens[req.model_class],
        )

    def chat_body(self, req: ChatRequest) -> dict[str, Any]:
        req = self.resolve(req)
        body: dict[str, Any] = {
            "model": req.model,
            "messages": list(req.messages),
            "temperature": req.temperature,
            "stream": False,                    # hard rule: FelCloud returns whole responses
        }
        # FelCloud ignores max_tokens / max_completion_tokens / stop (probes 2026-10-26) but honours a top-level
        # options.num_predict, so the cap goes out in the form OUTPUT_CAP_MODE selects (default: both).
        mode = self.settings.output_cap_mode
        if mode in ("max_tokens", "both"):
            body["max_tokens"] = req.max_tokens
        if mode in ("options", "both"):
            body["options"] = {"num_predict": req.max_tokens}
        if req.json_mode:
            body["response_format"] = {"type": "json_object"}
        if req.frequency_penalty is not None:
            body["frequency_penalty"] = req.frequency_penalty
        return body

    def embed_body(self, req: EmbedRequest) -> dict[str, Any]:
        return {"model": req.model or self.settings.embed_model, "input": list(req.texts)}

    async def chat(self, req: ChatRequest, timeout: float | None = None) -> httpx.Response:
        """timeout: seconds without an answer before httpx.ReadTimeout (None = the client default)."""
        kw = {} if timeout is None else {"timeout": timeout}
        return await self._http.post("/chat/completions", json=self.chat_body(req), **kw)

    async def embed(self, req: EmbedRequest) -> httpx.Response:
        return await self._http.post("/embeddings", json=self.embed_body(req))

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()


def create_client(settings: Settings, inner: httpx.AsyncBaseTransport | None = None) -> LLMClient:
    """The one place that decides whether the simulator sits between us and the provider."""
    transport = inner
    if settings.simulate:
        from simulator.transport import ConstraintSimulator
        transport = ConstraintSimulator(inner or httpx.AsyncHTTPTransport(), settings)
    return LLMClient(settings, transport)


def parse_chat(resp: httpx.Response) -> tuple[str, int | None, int | None]:
    """(text, tokens_in, tokens_out) from a 200 chat response."""
    data = resp.json()
    usage = data.get("usage") or {}
    text = data["choices"][0]["message"].get("content") or ""
    return text, usage.get("prompt_tokens"), usage.get("completion_tokens")


def parse_embed(resp: httpx.Response) -> tuple[list[list[float]], int | None]:
    data = resp.json()
    vecs = [d["embedding"] for d in sorted(data["data"], key=lambda d: d["index"])]
    return vecs, (data.get("usage") or {}).get("prompt_tokens")
