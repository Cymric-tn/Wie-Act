"""Scanned PDF pages -> ocr_transcribe at BACKGROUND, capped per file (rag.ingest.ingest_with_ocr, Engine.ingest_file)."""
import asyncio
import io
import sys
import types

import pytest
from PIL import Image, ImageDraw

from engine.course import load_course
from rag.glossary import Glossary
from rag.ingest import MAX_OCR_PAGES, IngestError, OcrPage, ingest_with_ocr
from tests.conftest import chat_ok
from tests.test_check_claim import _index  # noqa: F401  (autouse course index for make_engine)
from tests.test_engine import make_engine
from tests.test_ingest import KW, PRODUIT, TANGENTE

TEXT_PAGE = "1. La tangente\n" + TANGENTE
SCAN_TANGENT = "2. La tangente\n" + TANGENTE
SCAN_PRODUCT = "3. Le produit\n" + PRODUIT


def png(n: int) -> bytes:
    img = Image.new("RGB", (800, 1100), (250, 250, 247))
    ImageDraw.Draw(img).text((50, 50), f"page {n}", fill=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakePage:
    def __init__(self, n, text):
        self.n, self.text = n, text

    def get_text(self, kind="text"):
        return self.text

    def get_pixmap(self, dpi=150):
        return types.SimpleNamespace(tobytes=lambda fmt: png(self.n))


class FakeDoc(list):
    def close(self):
        pass


@pytest.fixture
def pdf(monkeypatch, tmp_path):
    pages = {}
    mod = types.ModuleType("fitz")
    mod.open = lambda path: FakeDoc(FakePage(i + 1, t) for i, t in enumerate(pages[path]))
    monkeypatch.setitem(sys.modules, "fitz", mod)

    def make(texts, name="scan.pdf"):
        f = tmp_path / name
        f.write_bytes(b"%PDF-fake")
        pages[str(f)] = texts
        return str(f)
    return make


def reader(texts, calls=None):
    async def ocr(page, data):
        if calls is not None:
            calls.append((page, len(data)))
        t = texts(page) if callable(texts) else texts[page]
        return t if isinstance(t, OcrPage) else OcrPage(t)
    return ocr


async def test_scanned_pages_are_read_in_page_order_merged_and_tagged(pdf):
    f = pdf([TEXT_PAGE, "", ""])
    calls = []
    res = await ingest_with_ocr(f, ocr=reader({2: SCAN_TANGENT, 3: SCAN_PRODUCT}, calls), **KW)
    assert [c[0] for c in calls] == [2, 3] and all(c[1] > 100 for c in calls)           # page images, in order
    assert [c.page for c in res.chunks] == [1, 2, 3] and [c.section for c in res.chunks] == \
        ["1. La tangente", "2. La tangente", "3. Le produit"]
    assert [c.concept for c in res.chunks] == ["tangente", "tangente", "produit"]
    assert res.needs_ocr == [] and res.ocr["transcribed"] == [2, 3] and res.ocr["pending"] == []
    assert len({c.chunk_id for c in res.chunks}) == 3 and all(c.origin == "professor" for c in res.chunks)


async def test_at_most_twenty_pages_per_file_and_the_rest_stays_pending(pdf):
    assert MAX_OCR_PAGES == 20
    f = pdf([TEXT_PAGE] + [""] * 24)
    calls = []
    res = await ingest_with_ocr(f, ocr=reader(lambda n: f"Page de cours numéro {n}\n{TANGENTE}", calls), **KW)
    assert len(calls) == 20 and [c[0] for c in calls] == list(range(2, 22))
    assert res.needs_ocr == [22, 23, 24, 25] and res.ocr["pending"] == [22, 23, 24, 25] and res.ocr["cap"] == 20
    assert any("over the cap of 20" in w for w in res.warnings)
    assert len(res.chunks) == 21
    again = await ingest_with_ocr(f, ocr=reader(lambda n: "x", []), max_ocr_pages=3, **KW)       # the cap is a parameter
    assert len(again.ocr["transcribed"]) == 3 and len(again.ocr["pending"]) == 21


async def test_two_failures_in_a_row_stop_the_file_and_keep_the_rest_for_later(pdf):
    f = pdf([TEXT_PAGE, "", "", "", "", ""])
    calls = []
    res = await ingest_with_ocr(f, ocr=reader(lambda n: OcrPage(ok=False, reason="quota_exhausted") if n in (3, 4) else
                                              SCAN_TANGENT, calls), **KW)
    assert [c[0] for c in calls] == [2, 3, 4]                           # 5 and 6 were not even tried
    assert res.ocr["transcribed"] == [2] and set(res.ocr["failed"]) == {3, 4} and res.ocr["pending"] == [5, 6]
    assert res.needs_ocr == [3, 4, 5, 6] and any("could not be read now" in w for w in res.warnings)
    # a single failure does not stop it
    r2 = await ingest_with_ocr(f, ocr=reader(lambda n: OcrPage(ok=False, reason="busy") if n == 3 else SCAN_TANGENT), **KW)
    assert r2.ocr["transcribed"] == [2, 4, 5, 6] and r2.ocr["failed"] == {3: "busy"} and r2.ocr["pending"] == []


async def test_an_unreliable_page_keeps_its_text_and_asks_for_confirmation(pdf):
    f = pdf([TEXT_PAGE, ""])
    res = await ingest_with_ocr(f, ocr=reader({2: OcrPage(SCAN_TANGENT, True, True)}), **KW)
    ocr_chunk = [c for c in res.chunks if c.page == 2][0]
    assert ocr_chunk.confidence < 0.5 and ocr_chunk in res.low_confidence and res.ocr["low_confidence"] == [2]
    assert [c for c in res.chunks if c.page == 1][0] not in res.low_confidence


async def test_an_empty_reading_is_a_failure_and_a_renderer_error_does_not_crash(pdf):
    f = pdf([TEXT_PAGE, "", ""])

    async def ocr(page, data):
        if page == 2:
            return OcrPage("   ")
        raise RuntimeError("boom")
    res = await ingest_with_ocr(f, ocr=ocr, **KW)
    assert res.ocr["failed"] == {2: "no text read", 3: "RuntimeError"} and len(res.chunks) == 1


async def test_without_an_ocr_function_or_pages_nothing_changes(pdf):
    f = pdf([TEXT_PAGE, ""])
    res = await ingest_with_ocr(f, ocr=None, **KW)
    assert res.needs_ocr == [2] and res.ocr == {} and len(res.chunks) == 1
    ok = pdf([TEXT_PAGE], "ok.pdf")
    called = []
    res2 = await ingest_with_ocr(ok, ocr=reader({}, called), **KW)
    assert called == [] and res2.needs_ocr == []
    with pytest.raises(IngestError):
        await ingest_with_ocr(f, ocr=None, **{**KW, "visibility": "secret"})


# ------------------------------------------------------------------ through the engine
OCR_TEXT = "2. La tangente\n" + TANGENTE


async def test_engine_reads_scanned_pages_at_background_one_at_a_time_and_caches_them(pdf, vt, tmp_path):
    eng, fake, sink = make_engine(vt, lambda b: chat_ok(OCR_TEXT, 820, 60))
    f = pdf([TEXT_PAGE, "", "", ""])
    res = await eng.ingest_file(f, "analyse", "derivees", "prof-7", "class", "professor", class_id="C1", add=True)
    assert res.ocr["transcribed"] == [2, 3, 4] and res.needs_ocr == []
    ocr_calls = [x for x in sink.records if x.kind == "chat" and x.task == "ocr_transcribe"]
    assert len(ocr_calls) == 3 and {x.priority for x in ocr_calls} == {"BACKGROUND"} and {x.model_class for x in ocr_calls} == {"vision"}
    assert {(x.course_id, x.class_id, x.learner_id, x.room_id) for x in ocr_calls} == {("analyse", "C1", None, None)}
    assert [x.request_hash for x in ocr_calls] == [x.request_hash for x in sorted(ocr_calls, key=lambda r: r.ts)]
    assert any(c.chunk_id in {k.chunk_id for k in eng.retriever.chunks} for c in res.chunks)    # add=True: indexed
    n = len(fake.requests)
    again = await eng.ingest_file(f, "analyse", "derivees", "prof-7", "class", "professor", class_id="C1")
    assert again.ocr["transcribed"] == [2, 3, 4] and len(fake.requests) == n         # the same pages: all cache hits


async def test_a_learner_upload_carries_the_learner_id_and_ocr_can_be_switched_off(pdf, vt):
    eng, fake, sink = make_engine(vt, lambda b: chat_ok(OCR_TEXT, 820, 60))
    f = pdf([TEXT_PAGE, ""], "mine.pdf")
    res = await eng.ingest_file(f, "analyse", "derivees", "L1", "private", "learner")
    ocr_calls = [x for x in sink.records if x.task == "ocr_transcribe" and x.kind == "run"]
    assert [x.learner_id for x in ocr_calls] == ["L1"] and res.chunks[-1].visibility == "private"
    n = len(fake.requests)
    off = await eng.ingest_file(pdf([TEXT_PAGE, ""], "off.pdf"), "analyse", "derivees", "L1", "private", "learner", ocr=False)
    assert off.needs_ocr == [2] and len(fake.requests) == n


async def test_the_engine_cap_comes_from_the_setting_and_a_refusal_keeps_pages_for_later(pdf, vt):
    from tests.test_retry_v11 import quota_429
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(OCR_TEXT, 820, 60), INGEST_MAX_OCR_PAGES=2)
    res = await eng.ingest_file(pdf([TEXT_PAGE, "", "", "", ""]), "analyse", "derivees", "prof-7", "public", "professor")
    assert res.ocr["transcribed"] == [2, 3] and res.ocr["pending"] == [4, 5] and res.ocr["cap"] == 2
    eng2, fake2, _ = make_engine(vt, lambda b: quota_429(60))
    r2 = await eng2.ingest_file(pdf([TEXT_PAGE, "", "", ""], "q.pdf"), "analyse", "derivees", "prof-7", "public", "professor")
    assert r2.ocr["transcribed"] == [] and set(r2.ocr["failed"]) == {2, 3} and r2.ocr["pending"] == [4]
    assert r2.needs_ocr == [2, 3, 4] and len(r2.chunks) == 1


async def test_tests_never_write_runtime_material_into_the_repo(pdf, vt):
    import os
    eng, _, _ = make_engine(vt, lambda b: chat_ok(OCR_TEXT, 820, 60))
    await eng.ingest_file(pdf([TEXT_PAGE, ""], "iso.pdf"), "analyse", "derivees", "prof-7", "public", "professor", add=True)
    assert not os.path.exists("data/index/courses") and os.path.isdir(eng.settings.rag_course_index_dir)
    assert "ednova-courses-" in eng.settings.rag_course_index_dir
