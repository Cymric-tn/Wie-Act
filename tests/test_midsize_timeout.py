"""v1.2 step 3: a mid-size timeout is not retried; the request falls back at once (explain: fast mode)."""
import random

import httpx
import pytest

from config import ModelClass
from engine import Engine
from gatekeeper.cache import ExactCache
from gatekeeper.client import ChatRequest, LLMClient
from gatekeeper.errors import TransientError
from gatekeeper.limiter import Limiter
from gatekeeper.retry import CallContext, RetryingCaller, RetryPolicy
from metrics import MemorySink
from rag.chunker import chunk_dir
from rag.store import save_chunks
from tests.conftest import FnProvider, chat_ok, error, make_settings

MSG = ({"role": "user", "content": "explain limits"},)


def caller(vt, fn, **env):
    s = make_settings(**env)
    fake = FnProvider(fn)
    rc = RetryingCaller(LLMClient(s, fake), Limiter(s, vt.clock, vt.sleep), MemorySink(),
                        RetryPolicy.from_settings(s), s.input_token_budget, vt.clock, vt.sleep, random.Random(1))
    return rc, fake


def timeout_for(model):
    def fn(body):
        if body["model"] == model:
            raise httpx.ReadTimeout("no answer")
        return chat_ok("ok")
    return fn


async def test_a_midsize_timeout_is_raised_after_one_attempt(vt):
    rc, fake = caller(vt, timeout_for("mid-a"))
    events = []
    ctx = CallContext(task="explain", priority="INTERACTIVE", on_event=lambda n, d: events.append(n))
    with pytest.raises(TransientError) as e:
        await rc.chat(ChatRequest(ModelClass.MIDSIZE, MSG), ctx)
    assert e.value.timeout and e.value.attempts == 1 and fake.models() == ["mid-a"]
    assert "backoff" not in events and events[-1] == "gave_up"
    assert vt.slept == []                                       # not a second of backoff


async def test_a_compact_timeout_keeps_its_retries(vt):
    state = {"n": 0}

    def fn(body):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ReadTimeout("slow")
        return chat_ok("ok")
    rc, fake = caller(vt, fn)
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, MSG), CallContext(task="classify", priority="INTERACTIVE"))
    assert r.attempts == 2 and len(fake.requests) == 2


async def test_midsize_5xx_still_backs_off_and_retries(vt):
    state = {"n": 0}

    def fn(body):
        state["n"] += 1
        return error(502, "bad_gateway", "upstream") if state["n"] == 1 else chat_ok("ok")
    rc, fake = caller(vt, fn)
    r = await rc.chat(ChatRequest(ModelClass.MIDSIZE, MSG), CallContext(task="explain", priority="INTERACTIVE"))
    assert r.attempts == 2 and len(fake.requests) == 2


def engine(vt, tmp_path, fn, **env):
    index = str(tmp_path / "chunks.json")
    save_chunks(index, chunk_dir("data/docs"))
    s = make_settings(RAG_INDEX_PATH=index, **env)
    fake, sink = FnProvider(fn), MemorySink()
    eng = Engine.create(s, client=LLMClient(s, fake), sink=sink, cache=ExactCache(":memory:", s.cache_ttl_s),
                        clock=vt.clock, sleep=vt.sleep, rng=random.Random(1))
    return eng, fake, sink


Q = lambda i: {"question": f"Comment trouver l'équation de la tangente ? ({i})"}


async def test_explain_falls_back_to_fast_mode_at_the_first_timeout(vt, tmp_path):
    eng, fake, sink = engine(vt, tmp_path, timeout_for("mid-a"), BREAKER_COOLDOWN_S=30)
    r = await eng.run("explain", Q(1))
    assert (r.degraded, r.degrade_reason, r.mode) == (True, "midsize_unavailable", "fast_extractive")
    assert r.eta_seconds == pytest.approx(30)                      # = the breaker cooldown remaining
    assert r.followup_available
    for name in ("generating", "gave_up", "falling_back_to_compact", "breaker_open", "fast_extractive"):
        assert name in r.event_names, r.event_names
    assert "backoff" not in r.event_names
    assert fake.models() == ["mid-a"]                              # one request: no retry, no framing call
    r2 = await eng.run("explain", Q(2))                            # breaker open: instant, no request at all
    assert r2.degrade_reason == "breaker_open" and r2.mode == "fast_extractive"
    assert fake.models() == ["mid-a"]


async def test_eta_counts_down_with_the_cooldown(vt, tmp_path):
    eng, fake, _ = engine(vt, tmp_path, timeout_for("mid-a"), BREAKER_COOLDOWN_S=30)
    await eng.run("explain", Q(1))
    vt.t += 12
    r = await eng.run("explain", Q(2))
    assert r.degrade_reason == "breaker_open"
    assert eng.router.retry_in(ModelClass.MIDSIZE) == pytest.approx(18)


async def test_a_non_grounded_midsize_task_falls_back_to_compact_on_a_timeout(vt):
    """The generic path (no fast mode for a task without sources): same fallback as a saturation."""
    from router.policy import ROUTES, Route, Router
    from metrics import MemorySink as MS
    r = Router(MS(), 2, routes={**ROUTES, "summary": Route(ModelClass.MIDSIZE, fallback=ModelClass.COMPACT)})
    d = r.decide("summary", {}, 20)
    fb = r.on_unavailable(d)
    assert (fb.model_class, fb.degrade_reason) == (ModelClass.COMPACT, "midsize_unavailable")
    assert r.on_unavailable(r.decide("classify", {}, 20)) is None   # nowhere to fall back: the caller raises
