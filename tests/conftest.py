"""Shared test helpers. Nothing here talks to a real LLM."""
from __future__ import annotations

import asyncio
import json
from collections import deque

import httpx
import pytest

from config import load_settings

BASE_ENV = {
    "LLM_BASE_URL": "http://provider.test/v1",
    "LLM_API_KEY": "test-key-not-real",
    "MODEL_COMPACT": "compact-a,compact-b",
    "MODEL_MIDSIZE": "mid-a",
    "MODEL_EMBED": "embed-a",
    "SIM_LATENCY_SCALE": "0",
    "SIM_SATURATION_PROB": "0",
    "SIM_SEED": "7",
}


def make_settings(**overrides):
    return load_settings({**BASE_ENV, **{k: str(v) for k, v in overrides.items()}})


class VirtualTime:
    """clock + sleep pair: sleeping advances time instantly."""

    def __init__(self, start: float = 1000.0):
        self.t = start
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        s = max(0.0, s)
        self.slept.append(s)
        self.t += s
        await asyncio.sleep(0)


def chat_ok(text="ok", tin=10, tout=3, headers=None):
    return httpx.Response(200, json={
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": tin, "completion_tokens": tout, "total_tokens": tin + tout}},
        headers=headers or {})


def embed_ok(n=1, dim=4):
    return httpx.Response(200, json={
        "data": [{"index": i, "embedding": [0.1 * (i + 1)] * dim} for i in range(n)],
        "usage": {"prompt_tokens": 5 * n}})


def error(status, etype, msg, headers=None):
    return httpx.Response(status, json={"error": {"message": msg, "type": etype, "code": str(status)}},
                          headers=headers or {})


class FakeProvider(httpx.AsyncBaseTransport):
    """OpenAI-shaped stand-in for Ollama/FelCloud. Scripted responses first, then a default 200.
    A script item may be an httpx.Response or an Exception to raise."""

    def __init__(self, script=None, delay: float = 0.0):
        self.script = deque(script or [])
        self.delay = delay
        self.requests: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append({"path": request.url.path, "body": body, "headers": dict(request.headers)})
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.script:
                item = self.script.popleft()
                if isinstance(item, BaseException):
                    raise item
                item.request = request
                return item
            if request.url.path.endswith("/embeddings"):
                r = embed_ok(len(body.get("input") or []))
            else:
                r = chat_ok(text=f"reply from {body.get('model')}")
            r.request = request
            return r
        finally:
            self.in_flight -= 1


@pytest.fixture
def vt():
    return VirtualTime()


class FnProvider(FakeProvider):
    """Answers with fn(body) -> httpx.Response (or raises). Records every request like FakeProvider."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append({"path": request.url.path, "body": body, "headers": dict(request.headers)})
        r = self.fn(body)
        r.request = request
        return r

    def models(self) -> list[str]:
        return [r["body"].get("model") for r in self.requests]
