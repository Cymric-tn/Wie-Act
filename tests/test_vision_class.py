"""v1.3 step 10: the vision model class: config, image parts in the client, resizing, input budget, timeout,
slots shared with the other classes."""
import asyncio
import base64
import io
import json
import random

import httpx
import pytest
from PIL import Image

from config import HEAVY_CLASSES, ConfigError, ModelClass, load_settings
from engine.vision import ImageError, prepare_image
from gatekeeper.client import ChatRequest, LLMClient, estimate_tokens, image_part
from gatekeeper.errors import CeilingExceeded, TransientError
from gatekeeper.limiter import Limiter
from gatekeeper.queue import PriorityQueue
from gatekeeper.retry import CallContext, RetryingCaller, RetryPolicy
from metrics import MemorySink
from tests.conftest import BASE_ENV, FakeProvider, FnProvider, chat_ok, make_settings
from tests.vloop import run_virtual

V, M, C = ModelClass.VISION, ModelClass.MIDSIZE, ModelClass.COMPACT


def png(w=200, h=100, color=(255, 255, 255), mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def vision_req(est=1000, text="Free OCR."):
    url = "data:image/jpeg;base64," + base64.b64encode(b"x" * 100).decode()
    return ChatRequest(V, ({"role": "user", "content": [image_part(url, est), {"type": "text", "text": text}]},))


# ------------------------------------------------------------------ config
def test_vision_class_defaults_and_overrides():
    s = make_settings()
    assert s.model_for(V) == "deepseek-ocr" and s.timeout_vision_s == 40.0 and s.vision_max_pixels == 1_000_000
    assert s.class_of("deepseek-ocr") is V and s.max_tokens[V] == 700
    s2 = make_settings(MODEL_VISION="ocr-x,ocr-y", TIMEOUT_VISION_S=25, VISION_MAX_PIXELS=500000, OCR_MAX_TOKENS=300)
    assert s2.models[V] == ("ocr-x", "ocr-y") and s2.timeout_vision_s == 25 and s2.task_max_tokens["ocr_transcribe"] == 300
    with pytest.raises(ConfigError, match="TIMEOUT_VISION_S"):
        make_settings(TIMEOUT_VISION_S=90)                       # not under the server's 90 s
    assert V in HEAVY_CLASSES and M in HEAVY_CLASSES and C not in HEAVY_CLASSES


# ------------------------------------------------------------------ images
def test_resize_to_at_most_one_megapixel_keeps_the_aspect_and_hashes_the_pixels():
    big = prepare_image(png(2000, 1500))
    assert big.resized and big.from_size == (2000, 1500) and big.width * big.height <= 1_000_000
    assert abs(big.width / big.height - 2000 / 1500) < 0.01
    small = prepare_image(png(300, 200))
    assert not small.resized and (small.width, small.height) == (300, 200)
    assert big.data_url.startswith("data:image/jpeg;base64,") and big.bytes_out < big.bytes_in * 2
    # the same picture sent as a data URL, as bare base64 or re-encoded has the same identity
    again = prepare_image("data:image/png;base64," + base64.b64encode(png(2000, 1500)).decode())
    bare = prepare_image(base64.b64encode(png(2000, 1500)).decode())
    assert again.sha == bare.sha == big.sha
    other = Image.new("RGB", (2000, 1500), (255, 255, 255))
    other.putpixel((5, 5), (0, 0, 0))
    for x in range(100):
        other.putpixel((100 + x, 700), (0, 0, 0))
    buf = io.BytesIO()
    other.save(buf, "PNG")
    assert prepare_image(buf.getvalue()).sha != big.sha                        # different pixels, different identity


def test_token_estimate_follows_the_pixels_with_a_floor_and_transparency_becomes_white():
    assert prepare_image(png(1000, 1000)).est_tokens == 1000
    assert prepare_image(png(2000, 2000)).est_tokens <= 1000                    # capped by the resize
    assert prepare_image(png(10, 10)).est_tokens == 256
    assert prepare_image(png(1000, 1000), tokens_per_megapixel=2000).est_tokens == 2000
    t = prepare_image(png(50, 50, (0, 0, 0, 0), "RGBA"))
    px = Image.open(io.BytesIO(base64.b64decode(t.data_url.split(",", 1)[1]))).getpixel((10, 10))
    assert min(px) > 240


@pytest.mark.parametrize("bad,msg", [("", "empty"), ("not base64 !!!", "readable|base64"), (b"hello", "readable"),
                                     ("https://x.test/a.png", "not fetched"), (42, "string or bytes")])
def test_unreadable_images_raise_image_error(bad, msg):
    with pytest.raises(ImageError, match=msg):
        prepare_image(bad)
    with pytest.raises(ImageError, match="MB limit"):
        prepare_image(png(100, 100), max_bytes=100)


# ------------------------------------------------------------------ client + budget
def test_chat_body_carries_the_image_part_without_our_bookkeeping_key():
    s = make_settings()
    body = LLMClient(s, FakeProvider()).chat_body(vision_req())
    parts = body["messages"][0]["content"]
    assert parts[0] == {"type": "image_url", "image_url": {"url": parts[0]["image_url"]["url"]}}
    assert parts[1] == {"type": "text", "text": "Free OCR."} and body["model"] == "deepseek-ocr"
    assert "x_est_tokens" not in json.dumps(body) and body["stream"] is False


def test_the_budget_counts_an_image_by_its_estimate():
    assert estimate_tokens(vision_req(est=1000).messages) > 1000
    assert estimate_tokens(vision_req(est=3000).messages) - estimate_tokens(vision_req(est=1000).messages) == 2000


def caller(fn, **env):
    s = make_settings(**env)
    fake = FnProvider(fn)
    rc = RetryingCaller(LLMClient(s, fake), Limiter(s), MemorySink(), RetryPolicy.from_settings(s),
                        s.input_token_budget, rng=random.Random(1))
    return rc, fake


async def test_an_image_over_the_input_budget_is_refused_locally_and_cannot_be_trimmed():
    rc, fake = caller(lambda b: chat_ok("x"))
    with pytest.raises(CeilingExceeded) as e:
        await rc.chat(vision_req(est=3000), CallContext())
    assert e.value.ceiling == "prompt_tokens" and fake.requests == []          # nothing sent
    r = await rc.chat(vision_req(est=1000), CallContext())                     # a 1 MP image fits
    assert r.text == "x" and r.model_class == "vision" and len(fake.requests) == 1


async def test_a_vision_request_is_not_flagged_as_truncated_input():
    rc, _ = caller(lambda b: chat_ok("texte", tin=1100, tout=5), INPUT_TRUNCATION_CHARS_PER_TOKEN=5.0)
    assert not (await rc.chat(vision_req(), CallContext())).input_truncated


# ------------------------------------------------------------------ timeout, hang, slots
def test_vision_has_its_own_timeout_and_a_hang_is_not_retried():
    p = RetryPolicy.from_settings(make_settings(TIMEOUT_VISION_S=33))
    assert p.timeout_for(V, "INTERACTIVE") == 33 and p.timeout_for(V, "BACKGROUND") == 33
    assert p.timeout_for(C, "INTERACTIVE") == 30

    async def main(loop):
        s = make_settings()
        fake = FnProvider(lambda b: (_ for _ in ()).throw(httpx.ReadTimeout("hung")))
        lim = Limiter(s, loop.time, asyncio.sleep)
        rc = RetryingCaller(LLMClient(s, fake), lim, MemorySink(), RetryPolicy.from_settings(s), s.input_token_budget,
                            loop.time, asyncio.sleep, random.Random(1), PriorityQueue(lim, loop.time))
        with pytest.raises(TransientError) as e:
            await rc.chat(vision_req(), CallContext(priority="INTERACTIVE"))
        assert e.value.timeout and len(fake.requests) == 1                     # no second request
        assert lim.ghost_count(V) == 1                                         # the server still counts it
    run_virtual(main)


async def test_vision_and_midsize_together_never_take_the_slot_compact_needs():
    s = make_settings(MAX_CONCURRENT=2)
    lim = Limiter(s)
    assert lim.class_free(V) and lim.class_free(M)
    lim.claim(M)
    assert not lim.class_free(V) and not lim.class_free(M) and lim.class_free(C)   # one slot left, for compact
    lim.class_in_use[M] = 0
    lim.claim(V)
    assert not lim.class_free(M) and not lim.class_free(V)
    # the reservation is off with MIDSIZE_MAX_SLOTS=0
    lim0 = Limiter(make_settings(MIDSIZE_MAX_SLOTS=0, VISION_MAX_SLOTS=0))
    lim0.claim(M)
    assert lim0.class_free(V) and lim0.class_free(M)
    # one slot only: nothing to reserve, the cap alone applies
    lim1 = Limiter(make_settings(MAX_CONCURRENT=1))
    assert lim1.class_free(V)


# ------------------------------------------------------------------ simulator
async def test_simulator_serves_vision_with_its_latency_and_counts_the_image_as_tokens():
    from simulator.transport import ConstraintSimulator
    s = make_settings(SIMULATE_CONSTRAINTS="true", SIM_LATENCY_SCALE=1, SIM_LATENCY_VISION_S="10,10",
                      SIM_LATENCY_COMPACT_S="1,1", SIM_PROMPT_TOKENS_CEILING=2048, SIM_VISION_IMAGE_TOKENS=800)
    slept = []

    async def sleep(x):
        slept.append(x)
    sim = ConstraintSimulator(FnProvider(lambda b: chat_ok("ocr text")), s, sleep=sleep) \
        if "sleep" in ConstraintSimulator.__init__.__code__.co_varnames else None
    if sim is None:
        pytest.skip("simulator sleep is not injectable here")
    async with httpx.AsyncClient(base_url="http://p.test/v1", transport=sim) as http:
        body = LLMClient(s, None).chat_body(vision_req(est=800))
        r = await http.post("/chat/completions", json=body)
    assert r.status_code == 200 and 10 in slept
