"""Task ocr_transcribe: the image goes to the vision class, the transcription comes back as {text, latex,
confidence_note}; nothing is graded. Images are generated (Pillow): there are no sample images in reports/."""
import base64
import io
import json

from PIL import Image, ImageDraw

from engine.result import Result
from tests.conftest import chat_ok
from tests.test_engine import make_engine, runs

LINES = ["Dériver f(x) = 3x² + 5x", "f'(x) = 6x + 5"]


def photo(w=1600, h=1200, lines=LINES, fmt="PNG", extra=None):
    """A white 'page' with the work written on it, like a phone photo of a notebook."""
    img = Image.new("RGB", (w, h), (250, 250, 247))
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((60, 80 + i * 90), line, fill=(20, 20, 20))
    if extra:
        d.line(extra, fill=(0, 0, 0), width=5)
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def data_url(raw, mime="png"):
    return f"data:image/{mime};base64," + base64.b64encode(raw).decode()


def reader(text="\n".join(LINES)):
    return lambda body: chat_ok(text, 820, 40)


async def test_transcription_goes_to_the_vision_class_and_comes_back_structured(vt):
    eng, fake, sink = make_engine(vt, reader())
    r = await eng.run("ocr_transcribe", {"image": data_url(photo())})
    assert isinstance(r, Result) and not r.degraded and r.model_class_used == "vision" and r.model == "deepseek-ocr"
    assert r.data == {"text": "\n".join(LINES), "latex": "Dériver f(x) = 3x^{2} + 5x \\\\ f'(x) = 6x + 5",
                      "confidence_note": ""}
    assert r.text == r.data["text"] and set(r.data) == {"text", "latex", "confidence_note"}   # nothing graded
    assert r.event_names == ["resizing_image", "checking_cache", "queued", "generating"]
    body = fake.requests[0]["body"]
    assert body["model"] == "deepseek-ocr" and "response_format" not in body and body["stream"] is False
    parts = body["messages"][0]["content"]
    assert [p["type"] for p in parts] == ["image_url", "text"] and "x_est_tokens" not in json.dumps(body)
    sent = Image.open(io.BytesIO(base64.b64decode(parts[0]["image_url"]["url"].split(",", 1)[1])))
    assert sent.width * sent.height <= 1_000_000 and sent.width > sent.height          # resized, aspect kept
    assert "Do not solve" in parts[1]["text"]
    assert r.notes["image"]["resized"] and r.notes["image"]["size"] == f"{sent.width}x{sent.height}"
    rec = runs(sink)[-1]
    assert rec.priority == "INTERACTIVE" and rec.model_class == "vision"                 # interactive by default


async def test_a_small_image_is_not_resized_and_has_no_resizing_event(vt):
    eng, fake, _ = make_engine(vt, reader())
    r = await eng.run("ocr_transcribe", {"image": data_url(photo(600, 300))})
    assert "resizing_image" not in r.event_names and not r.notes["image"]["resized"]


async def test_cache_is_by_image_content_not_by_encoding(vt):
    eng, fake, _ = make_engine(vt, reader())
    raw = photo(1200, 900)
    first = await eng.run("ocr_transcribe", {"image": data_url(raw)})
    again = await eng.run("ocr_transcribe", {"image": base64.b64encode(raw).decode()})    # bare base64, same photo
    assert again.from_cache and again.data == first.data and again.tokens_in == 0 and len(fake.requests) == 1
    assert again.event_names == ["resizing_image", "checking_cache", "cache_hit"]
    other = await eng.run("ocr_transcribe", {"image": data_url(photo(1200, 900, extra=[(0, 0), (900, 700)]))})
    assert not other.from_cache and len(fake.requests) == 2                               # another photo
    assert again.request_id != first.request_id


async def test_a_reading_with_visible_problems_is_low_confidence_returned_and_not_cached(vt):
    eng, fake, _ = make_engine(vt, reader("f(x) = (3x + [?]\nf'(x) = 6x + 5"))
    img = data_url(photo(800, 600))
    r = await eng.run("ocr_transcribe", {"image": img}, {"language": "FRENCH"})
    assert r.degraded and r.degrade_reason == "low_confidence" and "guarded" in r.event_names
    assert "Ligne 1" in r.data["confidence_note"] and r.data["text"].startswith("f(x) = (3x + [?]")
    assert r.notes["issues"] == [{"kind": "unreadable", "line": 1}]
    again = await eng.run("ocr_transcribe", {"image": img})
    assert not again.from_cache and len(fake.requests) == 2                               # a bad reading is retried


async def test_an_empty_reading_asks_for_a_new_photo(vt):
    eng, _, _ = make_engine(vt, reader("   "))
    r = await eng.run("ocr_transcribe", {"image": data_url(photo(400, 300, lines=[]))}, {"language": "ENGLISH"})
    assert r.degrade_reason == "low_confidence" and r.data["text"] == "" and "retake the photo" in r.data["confidence_note"]


async def test_the_model_s_wrong_math_is_transcribed_not_corrected(vt):
    eng, _, _ = make_engine(vt, reader("f'(x) = 5x"))                    # the learner's (wrong) line: copied as is
    r = await eng.run("ocr_transcribe", {"image": data_url(photo(500, 300, lines=["f'(x) = 5x"]))})
    assert r.data["text"] == "f'(x) = 5x" and not r.degraded


async def test_unreadable_image_input_is_answered_degraded_without_a_request(vt):
    eng, fake, sink = make_engine(vt, reader())
    for bad in ("data:image/png;base64,AAAA", b"not an image", "https://x.test/a.png"):
        r = await eng.run("ocr_transcribe", {"image": bad})
        assert r.degraded and r.degrade_reason == "invalid_image" and r.data is None and r.text is None
        assert r.event_names[-1] == "unavailable" and r.eta_seconds is None
    assert fake.requests == []
    import pytest
    with pytest.raises(ValueError, match="needs inputs"):
        await eng.run("ocr_transcribe", {})


async def test_an_image_that_cannot_fit_the_input_budget_is_refused_locally(vt):
    eng, fake, _ = make_engine(vt, reader(), IMAGE_TOKENS_PER_MEGAPIXEL=8000)
    r = await eng.run("ocr_transcribe", {"image": data_url(photo(1200, 900))})
    assert r.degraded and r.degrade_reason == "request_too_large" and fake.requests == []


async def test_vision_requests_use_the_shared_chat_window_and_background_priority_is_allowed(vt):
    eng, fake, sink = make_engine(vt, reader())
    before = eng.caller.limiter.buckets["chat"].tokens
    await eng.run("ocr_transcribe", {"image": data_url(photo(700, 500))}, priority="BACKGROUND")
    assert eng.caller.limiter.buckets["chat"].tokens == before - 1
    assert [x.priority for x in sink.records if x.kind == "chat"] == ["BACKGROUND"]


async def test_quota_exhausted_is_reported_like_any_chat_task(vt):
    from tests.test_retry_v11 import quota_429
    eng, fake, _ = make_engine(vt, lambda b: quota_429(60))
    r = await eng.run("ocr_transcribe", {"image": data_url(photo(700, 500))})
    assert r.degrade_reason == "quota_exhausted" and r.eta_seconds == 60 and r.model_class_used == "vision"
