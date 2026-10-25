"""Real provider (Ollama by default). Run:  ENV_FILE=.env.ollama pytest -q -m integration"""
import json
import os

import pytest
from dotenv import dotenv_values

from config import ModelClass, load_settings
from gatekeeper.client import ChatRequest, EmbedRequest, create_client
from gatekeeper.errors import classify_response
from gatekeeper.retry import CallContext, build_caller
from metrics import JsonlSink, MemorySink

pytestmark = pytest.mark.integration


def settings(**over):
    env = {**dotenv_values(os.environ.get("ENV_FILE", ".env.ollama")), **over}
    return load_settings({k: v for k, v in env.items() if v is not None})


@pytest.fixture
def fast_sim():
    # real provider + simulator, but no extra latency and no random saturation
    return settings(SIMULATE_CONSTRAINTS="true", SIM_LATENCY_SCALE="0", SIM_SATURATION_PROB="0")


async def test_compact_chat_raw():
    async with create_client(settings(SIMULATE_CONSTRAINTS="false")) as c:
        r = await c.chat(ChatRequest(ModelClass.COMPACT, ({"role": "user", "content": "Say just: ok"},),
                                     max_tokens=8, temperature=0))
    assert classify_response(r) is None
    assert r.json()["choices"][0]["message"]["content"].strip()


async def test_compact_json_mode_through_gatekeeper(fast_sim):
    sink = MemorySink()
    rc = build_caller(fast_sim, sink)
    msgs = ({"role": "system", "content": 'Reply with ONE JSON object: {"language": "<english|french>"}'},
            {"role": "user", "content": "Bonjour, c'est quoi une dérivée ?"})
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, msgs, max_tokens=40, temperature=0, json_mode=True))
    assert "language" in json.loads(r.text)
    assert sink.records[-1].tokens_in and sink.records[-1].quota_remaining is not None
    await rc.client.aclose()


async def test_midsize_chat_through_gatekeeper(fast_sim):
    rc = build_caller(fast_sim, MemorySink())
    r = await rc.chat(ChatRequest(ModelClass.MIDSIZE, ({"role": "user", "content": "2+2=? one word"},),
                                  max_tokens=8, temperature=0))
    assert r.model == fast_sim.model_for(ModelClass.MIDSIZE) and r.text.strip()
    await rc.client.aclose()


async def test_embeddings(fast_sim):
    if not fast_sim.embed_model:
        pytest.skip("MODEL_EMBED not set")
    rc = build_caller(fast_sim, MemorySink())
    r = await rc.embed(EmbedRequest(("dérivée", "derivative", "قطة")))
    assert len(r.vectors) == 3 and len(r.vectors[0]) > 100
    await rc.client.aclose()


async def test_full_stack_real_call_writes_metrics_record(tmp_path):
    """One real Ollama call: RetryingCaller -> Limiter -> client -> simulator (real latency) -> Ollama,
    and one JSONL metrics record on disk."""
    s = settings(SIMULATE_CONSTRAINTS="true", SIM_SATURATION_PROB="0")
    path = tmp_path / "calls.jsonl"
    sink = JsonlSink(str(path))
    rc = build_caller(s, sink)
    r = await rc.chat(ChatRequest(ModelClass.COMPACT, ({"role": "user", "content": "Say just: ok"},),
                                  max_tokens=8, temperature=0),
                      CallContext(task="smoke", priority="INTERACTIVE"))
    await rc.client.aclose()
    sink.close()

    [line] = path.read_text().splitlines()
    rec = json.loads(line)
    print("\nmetrics record:", line)
    assert r.text.strip() and rc.client.transport.__class__.__name__ == "ConstraintSimulator"
    assert (rec["kind"], rec["task"], rec["model_class"], rec["model"], rec["status"], rec["outcome"]) == (
        "chat", "smoke", "compact", s.model_for(ModelClass.COMPACT), 200, "ok")
    assert rec["tokens_in"] > 0 and rec["tokens_out"] > 0 and rec["request_hash"]
    assert rec["quota_remaining"] is not None                  # quota headers from the simulator were read
    assert rec["latency_ms"] >= s.sim.latency_compact_s[0] * 1000   # simulated CPU latency applied
