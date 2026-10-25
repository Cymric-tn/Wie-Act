import json

import pytest
from dotenv import dotenv_values

from config import ConfigError, ModelClass, load_settings
from gatekeeper.client import ChatRequest, EmbedRequest, LLMClient, create_client, estimate_tokens
from metrics import CallRecord, JsonlSink
from simulator.transport import ConstraintSimulator
from tests.conftest import BASE_ENV, FakeProvider, make_settings


# ------------------------------------------------------------------ config
def test_classes_map_to_names_from_env():
    s = make_settings()
    assert s.models[ModelClass.COMPACT] == ("compact-a", "compact-b")
    assert s.model_for(ModelClass.COMPACT) == "compact-a"
    assert s.model_for(ModelClass.MIDSIZE) == "mid-a"
    assert s.class_of("compact-b") is ModelClass.COMPACT
    assert s.class_of("nope") is None


def test_key_never_in_repr():
    s = make_settings()
    assert "test-key-not-real" not in repr(s)
    assert "test-key-not-real" not in str(s)


def test_missing_required_setting():
    env = dict(BASE_ENV)
    del env["MODEL_MIDSIZE"]
    with pytest.raises(ConfigError, match="MODEL_MIDSIZE"):
        load_settings(env)


def test_simulator_settings_and_validation():
    s = make_settings(SIMULATE_CONSTRAINTS="true", SIM_LATENCY_MIDSIZE_S="1,2", RATE_LIMIT_PER_MIN=12)
    assert s.simulate and s.sim.latency_midsize_s == (1.0, 2.0)
    assert s.sim.rate_per_min == 12          # simulated provider defaults to the real limit
    with pytest.raises(ConfigError):
        make_settings(SIM_SATURATION_DIALECT="bogus")


@pytest.mark.parametrize("path", [".env.example", ".env.ollama"])
def test_shipped_env_files_parse(path):
    env = {k: v for k, v in dotenv_values(path).items() if v is not None}
    s = load_settings(env)
    assert s.model_for(ModelClass.COMPACT) == "llama3.2:3b"   # inline comments are stripped
    assert s.simulate is True


def test_env_example_has_no_real_key():
    assert dotenv_values(".env.example")["LLM_API_KEY"] == "changeme"


# ------------------------------------------------------------------ metrics
def test_jsonl_sink(tmp_path):
    p = tmp_path / "m" / "calls.jsonl"
    sink = JsonlSink(str(p))
    sink.write(CallRecord(request_id="r1", model_class="compact", tokens_in=5))
    sink.write(CallRecord(request_id="r1", attempt=2, outcome="saturated", action="raise"))
    sink.close()
    rows = [json.loads(line) for line in p.read_text().splitlines()]
    assert [r["attempt"] for r in rows] == [1, 2]
    assert "tokens_out" not in rows[0]                        # None fields omitted
    assert rows[1]["outcome"] == "saturated"


# ------------------------------------------------------------------ client
async def test_chat_body_resolves_class_and_never_streams():
    fake = FakeProvider()
    async with LLMClient(make_settings(MAX_TOKENS_COMPACT=111), fake) as c:
        r = await c.chat(ChatRequest(ModelClass.COMPACT, ({"role": "user", "content": "hi"},), json_mode=True))
    assert r.status_code == 200
    sent = fake.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["body"]["model"] == "compact-a"
    assert sent["body"]["max_tokens"] == 111
    assert sent["body"]["stream"] is False
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert sent["headers"]["authorization"] == "Bearer test-key-not-real"


async def test_embed_body_uses_embed_model():
    fake = FakeProvider()
    async with LLMClient(make_settings(), fake) as c:
        await c.embed(EmbedRequest(("a", "b")))
    assert fake.requests[0]["path"] == "/v1/embeddings"
    assert fake.requests[0]["body"] == {"model": "embed-a", "input": ["a", "b"]}


def test_create_client_wraps_simulator_only_when_enabled():
    assert not isinstance(create_client(make_settings(), FakeProvider()).transport, ConstraintSimulator)
    c = create_client(make_settings(SIMULATE_CONSTRAINTS="true"), FakeProvider())
    assert isinstance(c.transport, ConstraintSimulator)


def test_estimate_tokens_grows_with_content():
    assert estimate_tokens([{"role": "user", "content": "x" * 4000}]) > 1000
