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


# Measured on data/docs with llama3.2:3b and qwen2.5:3b (2026-10-24): 3.5 chars/token for English
# prose, 3.0 French, 2.2-2.3 for math-heavy French and for Arabic. Stay below the worst case so the
# estimate is an upper bound: FelCloud silently truncates input near 2048 tokens and will not warn us.
CHARS_PER_TOKEN = 2.0
TOKENS_PER_MESSAGE = 4


def estimate_text_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


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
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "stream": False,                    # hard rule: FelCloud returns whole responses
        }
        if req.json_mode:
            body["response_format"] = {"type": "json_object"}
        if req.frequency_penalty is not None:
            body["frequency_penalty"] = req.frequency_penalty
        return body

    def embed_body(self, req: EmbedRequest) -> dict[str, Any]:
        return {"model": req.model or self.settings.embed_model, "input": list(req.texts)}

    async def chat(self, req: ChatRequest) -> httpx.Response:
        return await self._http.post("/chat/completions", json=self.chat_body(req))

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
