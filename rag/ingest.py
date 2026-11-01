"""
Ingestion: a file (PDF, DOCX, Markdown/text) -> chunks ready for Engine.add_material. No model call.

    res = ingest("polycopie.pdf", course_id="analyse", unit_id="derivees", owner="prof-7",
                 visibility="class", source="professor", class_id="C1", course=course, glossary=glossary)
    res.chunks          # Chunk objects: split by headings into pieces of about CHUNK_MAX_TOKENS
    res.low_confidence  # chunks whose concept tag a professor should confirm (confidence < min_confidence)
    res.needs_ocr       # page numbers with no extractable text (scans): rag/ingest sends them to ocr_transcribe

  * Readers. PDF: PyMuPDF (`pip install pymupdf`, imported lazily; IngestError says so when missing). DOCX: the
    standard library (a .docx is a zip of XML), headings from the paragraph styles. Markdown / text: `#`..`###`
    headings; an explicit `<!-- concept: id -->` comment keeps the author's tag (confidence 1.0).
  * Chunks. One heading section = one chunk when it fits, else packed by paragraph then sentence (the same
    packer as the course docs). PDF headings are a heuristic on the text (numbered or short capitalised lines
    without a final full stop); a PDF with none yields one section per page group, never nothing.
  * Concept tag. Each chunk is scored with BM25 against every concept of the unit (all concepts of the course when
    the unit is unknown): the "query" is the concept's trilingual title plus the glossary expansion of it, the
    "document" is the chunk. confidence = idf-weighted coverage of the concept's terms in the chunk, halved by how
    close the runner-up scored (a chunk that fits two concepts equally is not a confident tag). No candidate with
    coverage >= MIN_TAG_COVERAGE: concept "" and confidence 0.
  * Chunk ids are `<source>/<owner>/<file stem>#<heading slug>[-n]`: stable, so ingesting the same file again
    replaces its chunks, and one learner's `notes.pdf` never collides with another's.
  * Scanned pages: a PDF page with fewer than MIN_PAGE_CHARS characters of text is listed in `needs_ocr`
    (`render_page(n)` gives a PNG); its text is simply absent from `chunks` until it has been transcribed.
    ingest_with_ocr() transcribes them through a callback (the engine passes ocr_transcribe at BACKGROUND): at most
    MAX_OCR_PAGES per file (20), one at a time, stopping after two failures in a row; what was not read stays in
    `needs_ocr` (a second call reads only that: transcriptions are cached by image). A page the reader flagged as
    unreliable keeps its text but its chunks get confidence < 0.5, so a professor is asked to confirm them.
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field, replace
from typing import Awaitable, Callable
from xml.etree import ElementTree as ET

from engine import language as L
from engine.course import Course
from rag.chunker import Chunk, chunk_markdown, estimate_text_tokens, slug, _split_section
from rag.glossary import Glossary
from rag.retriever import BM25Retriever

VISIBILITIES = ("public", "class", "private")
SOURCES = ("course", "professor", "learner")
MIN_PAGE_CHARS = 20
MIN_TAG_COVERAGE = 0.3
LOW_CONFIDENCE = 0.5
SUPPORTED = (".pdf", ".docx", ".md", ".markdown", ".txt")
MAX_OCR_PAGES = 20
OCR_STOP_AFTER_FAILURES = 2
OCR_LOW_CONFIDENCE = 0.49


class IngestError(ValueError):
    pass


@dataclass
class Section:
    heading: str
    body: str
    page: int = 0


@dataclass
class IngestResult:
    chunks: list[Chunk]
    needs_ocr: list[int] = field(default_factory=list)         # 1-based page numbers without extractable text
    pages: int = 0
    warnings: list[str] = field(default_factory=list)
    min_confidence: float = LOW_CONFIDENCE
    ocr: dict = field(default_factory=dict)       # after ingest_with_ocr: transcribed / low_confidence / failed / pending
    _render: Callable[[int], bytes] | None = None

    @property
    def low_confidence(self) -> list[Chunk]:
        return [c for c in self.chunks if c.confidence < self.min_confidence]

    def render_page(self, number: int) -> bytes:
        if self._render is None:
            raise IngestError("this file has no pages to render")
        return self._render(number)


# ------------------------------------------------------------------ readers
def _read_markdown(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    return re.sub(r"^#{1,3} +", "## ", text, flags=re.M)         # every heading level splits a section


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _read_docx(path: str) -> list[Section]:
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
        raise IngestError(f"{os.path.basename(path)}: not a readable .docx ({e})") from None
    sections: list[Section] = []
    heading, body = "", []

    def flush() -> None:
        if body:
            sections.append(Section(heading, "\n\n".join(body)))
        body.clear()
    for p in root.iter(f"{_W}p"):
        text = "".join(t.text or "" for t in p.iter(f"{_W}t")).strip()
        if not text:
            continue
        style = p.find(f"{_W}pPr/{_W}pStyle")
        name = (style.get(f"{_W}val") if style is not None else "") or ""
        if re.match(r"(?i)^(heading\s*\d|titre\s*\d|title)", name):
            flush()
            heading = text
        else:
            body.append(text)
    flush()
    return sections


_NUMBERED = re.compile(r"^(\d+(\.\d+)*[.)]?|[IVX]+[.)]|chapitre|section|partie|chapter|part)\s+\S", re.I)


def _is_heading(line: str) -> bool:
    """Heuristic on a text line (PDF text has no styles): a numbered line ("2.1 Limites", "Chapitre 3"), an
    ALL-CAPS line, or a short line that starts with a capital, has no sentence punctuation at the end and no maths."""
    s = line.strip()
    if not s or len(s) > 80 or s.endswith((".", ",", ";", ":", "!", "?", "؟")):
        return False
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 4:
        return False
    if _NUMBERED.match(s) or (s.isupper() and len(letters) >= 4):
        return True
    return len(s.split()) <= 6 and s[0].isupper() and not re.search(r"[()=+<>≠≤≥/\d]", s)


def _read_pdf(path: str) -> tuple[list[Section], list[int], int, Callable[[int], bytes]]:
    try:
        import fitz                                       # PyMuPDF (the name it had before 1.24; still shipped)
    except ImportError:
        try:
            import pymupdf as fitz
        except ImportError:
            raise IngestError("PDF ingestion needs PyMuPDF: pip install pymupdf") from None
    try:
        doc = fitz.open(path)
    except Exception as e:                                # corrupt / encrypted: the caller gets one clear error
        raise IngestError(f"{os.path.basename(path)}: cannot open as a PDF ({e})") from None
    texts, scanned = [], []
    try:
        n = len(doc)
        for i in range(n):
            t = (doc[i].get_text("text") or "").strip()
            if len(t) < MIN_PAGE_CHARS:
                scanned.append(i + 1)
                texts.append("")
            else:
                texts.append(t)
    finally:
        doc.close()

    def render(number: int) -> bytes:                     # reopened on demand: nothing is held open between calls
        d = fitz.open(path)
        try:
            return d[number - 1].get_pixmap(dpi=150).tobytes("png")
        finally:
            d.close()
    sections: list[Section] = []
    heading, body, start = "", [], 1

    def flush() -> None:
        if body:
            sections.append(Section(heading, "\n".join(body).strip(), start))
        body.clear()
    for pno, t in enumerate(texts, 1):
        for line in t.splitlines():
            if _is_heading(line):
                flush()
                heading, start = line.strip(), pno
            else:
                if not body and not heading:
                    start = pno                      # text before the first heading: the page it begins on
                body.append(line)
        if body:
            body.append("")                          # a page break ends a paragraph, not a section
    flush()
    return [s for s in sections if s.body.strip()], scanned, n, render


# ------------------------------------------------------------------ concept tags
def _tag(chunks: list[Chunk], course: Course | None, unit_id: str, glossary: Glossary | None) -> list[Chunk]:
    """Concept + confidence of every chunk without an explicit tag, by BM25 against the concept titles."""
    if course is None or not chunks:
        return chunks
    ids = course.concepts_of(unit_id) if unit_id in {u.id for u in course.units} else list(course.concepts)
    r = BM25Retriever()
    r.chunks = list(chunks)
    r._rebuild()
    queries: dict[str, list] = {}
    for cid in ids:      # the titles are translations of each other: alternatives, not one long query
        c = course.concepts[cid]
        texts = list(dict.fromkeys([cid, *c.titles.values()]))
        queries[cid] = [(t, glossary.expand(t) if glossary else None) for t in texts]
    out = []
    for i, ch in enumerate(chunks):
        if ch.concept:                                    # the author tagged it: keep it
            out.append(replace(ch, confidence=1.0))
            continue
        scored = []
        for cid, variants in queries.items():
            best_v = max(((r.coverage(r.units(t, e), i, translated=True), r.score(r.weights(t, e), i))
                          for t, e in variants), default=(0.0, 0.0))
            scored.append((best_v[1], best_v[0], cid))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        best = scored[0] if scored else None
        if best is None or best[0] <= 0 or best[1] < MIN_TAG_COVERAGE:
            out.append(replace(ch, concept="", confidence=0.0))
            continue
        second = scored[1][0] if len(scored) > 1 else 0.0
        conf = best[1] * (1.0 - 0.5 * min(1.0, second / best[0]))
        out.append(replace(ch, concept=best[2], confidence=round(conf, 3)))
    return out


# ------------------------------------------------------------------ main
def _language_of(text: str, front: str | None = None) -> str:
    if front:
        return front.upper()
    if L.arabic_ratio(text) >= 0.3:
        return "ARABIC"
    return "ENGLISH" if L.detect(text[:2000])[0] == "ENGLISH" else "FRENCH"


def ingest(file: str, course_id: str, unit_id: str = "", owner: str = "", visibility: str = "public",
           source: str = "professor", *, class_id: str = "", title: str | None = None, language: str | None = None,
           course: Course | None = None, glossary: Glossary | None = None, max_tokens: int = 300,
           min_confidence: float = LOW_CONFIDENCE) -> IngestResult:
    if visibility not in VISIBILITIES:
        raise IngestError(f"visibility must be one of {VISIBILITIES}, got {visibility!r}")
    if source not in SOURCES:
        raise IngestError(f"source must be one of {SOURCES}, got {source!r}")
    if not course_id:
        raise IngestError("course_id is required")
    if visibility == "class" and not class_id:
        raise IngestError("visibility 'class' needs a class_id")
    if visibility == "private" and not owner:
        raise IngestError("visibility 'private' needs an owner (the learner_id)")
    if not os.path.exists(file):
        raise IngestError(f"{file}: no such file")
    ext = os.path.splitext(file)[1].lower()
    if ext not in SUPPORTED:
        raise IngestError(f"{os.path.basename(file)}: unsupported type {ext!r}; supported: {', '.join(SUPPORTED)}")
    stem = os.path.splitext(os.path.basename(file))[0]
    prefix = f"{source}/{slug(owner) or 'anon'}/{slug(stem) or 'file'}"
    scope = dict(course_id=course_id, unit_id=unit_id, visibility=visibility, owner=owner, class_id=class_id,
                 origin=source)
    res = IngestResult([], min_confidence=min_confidence)
    if ext in (".md", ".markdown", ".txt"):
        text = _read_markdown(file)
        if not text.lstrip().startswith("---"):
            text = f"---\ntitle: {title or stem}\n---\n{text}"
        elif title:
            text = re.sub(r"^title:.*$", f"title: {title}", text, count=1, flags=re.M)
        chunks = [replace(c, chunk_id=f"{prefix}#{c.chunk_id.split('#', 1)[1]}", source=os.path.basename(file))
                  for c in chunk_markdown(text, file, max_tokens, **scope)]
        chunks = [replace(c, language=(language or c.language or _language_of(c.text)).upper()) for c in chunks]
    else:
        if ext == ".docx":
            sections = _read_docx(file)
        else:
            sections, res.needs_ocr, res.pages, res._render = _read_pdf(file)
            if res.needs_ocr:
                res.warnings.append(f"{len(res.needs_ocr)} page(s) without extractable text: {res.needs_ocr[:10]}"
                                    + ("..." if len(res.needs_ocr) > 10 else "") + " (needs_ocr)")
        chunks = _sections_to_chunks(sections, prefix, os.path.basename(file), title or stem, language, max_tokens,
                                     scope)
    if not chunks:
        res.warnings.append("no text could be extracted" + (" (all pages need OCR)" if res.needs_ocr else ""))
    res.chunks = _tag(_unique(chunks), course, unit_id, glossary)
    return res


def _sections_to_chunks(sections: list[Section], prefix: str, source_name: str, title: str, language: str | None,
                        max_tokens: int, scope: dict) -> list[Chunk]:
    chunks: list[Chunk] = []
    for sec in sections:
        heading = sec.heading or title
        header = f"{title} — {heading}\n"
        lang = (language or _language_of(sec.body)).upper()
        room = max(40, max_tokens - estimate_text_tokens(header))
        for i, part in enumerate(_split_section(sec.body, room)):
            cid = f"{prefix}#{slug(heading) or 'section'}" + (f"-{i + 1}" if i else "")
            chunks.append(Chunk(cid, header + part, "", "", lang, source_name, title, heading, "", page=sec.page,
                                **scope))
    return chunks


def _unique(chunks: list[Chunk]) -> list[Chunk]:
    """Two sections with the same heading in one file: number the later ones instead of losing one."""
    seen: dict[str, int] = {}
    out = []
    for c in chunks:
        n = seen.get(c.chunk_id, 0)
        seen[c.chunk_id] = n + 1
        out.append(c if n == 0 else replace(c, chunk_id=f"{c.chunk_id}~{n + 1}"))
    return out


# ------------------------------------------------------------------ scanned pages
@dataclass
class OcrPage:
    """What an OCR callback returns for one page."""
    text: str = ""
    ok: bool = True                  # False: the page could not be read now (refused, busy): it stays pending
    low_confidence: bool = False     # read, but with visible problems
    reason: str | None = None


OcrFn = Callable[[int, bytes], Awaitable[OcrPage]]


async def ingest_with_ocr(file: str, course_id: str, unit_id: str = "", owner: str = "", visibility: str = "public",
                          source: str = "professor", *, ocr: OcrFn | None = None, max_ocr_pages: int = MAX_OCR_PAGES,
                          **kw) -> IngestResult:
    """ingest(), then the scanned pages through `ocr(page_number, png_bytes)`, in page order, at most max_ocr_pages."""
    res = ingest(file, course_id, unit_id, owner, visibility, source, **kw)
    if not res.needs_ocr or ocr is None:
        return res
    flagged = list(res.needs_ocr)
    todo, skipped = flagged[:max(0, max_ocr_pages)], flagged[max(0, max_ocr_pages):]
    done: dict[int, OcrPage] = {}
    failed: dict[int, str] = {}
    pending: list[int] = list(skipped)
    streak = 0
    for i, n in enumerate(todo):
        try:
            got = await ocr(n, res.render_page(n))
        except Exception as e:               # a page that cannot even be rendered is not worth stopping the file for
            got = OcrPage(ok=False, reason=type(e).__name__)
        if not got.ok:
            failed[n] = got.reason or "failed"
            streak += 1
            if streak >= OCR_STOP_AFTER_FAILURES:
                pending = list(todo[i + 1:]) + pending      # the refusal will repeat: stop, keep the rest for later
                break
            continue
        streak = 0
        done[n] = got
    stem = os.path.splitext(os.path.basename(file))[0]
    prefix = f"{source}/{slug(owner) or 'anon'}/{slug(stem) or 'file'}"
    scope = dict(course_id=course_id, unit_id=unit_id, visibility=visibility, owner=owner, class_id=kw.get("class_id", ""),
                 origin=source)
    title = kw.get("title") or stem
    new: list[Chunk] = []
    low_pages = []
    for n in sorted(done):
        page = done[n]
        text = page.text.strip()
        if not text:
            failed[n] = "no text read"
            continue
        sections = [replace(sec, page=n) for sec in _page_sections(text, n)]
        part = _sections_to_chunks(sections, prefix, os.path.basename(file), title, kw.get("language"),
                                   kw.get("max_tokens", 300), scope)
        part = _tag(_unique(part), kw.get("course"), unit_id, kw.get("glossary"))
        if page.low_confidence:
            low_pages.append(n)
            part = [replace(c, confidence=min(c.confidence, OCR_LOW_CONFIDENCE)) for c in part]
        new += part
    merged = sorted([*res.chunks, *new], key=lambda c: c.page)        # stable: text pages keep their order
    res.chunks = _unique(merged)
    res.needs_ocr = sorted(set(failed) | set(pending))
    res.ocr = {"flagged": flagged, "transcribed": sorted(n for n in done if n not in failed), "low_confidence": low_pages,
               "failed": failed, "pending": sorted(pending), "cap": max_ocr_pages}
    if skipped:
        res.warnings.append(f"{len(skipped)} scanned page(s) over the cap of {max_ocr_pages} were not read: "
                            f"{skipped[:10]}{'...' if len(skipped) > 10 else ''}")
    if failed:
        res.warnings.append(f"{len(failed)} page(s) could not be read now: {sorted(failed)[:10]}")
    res.warnings = [w for w in res.warnings if "no text could be extracted" not in w or not res.chunks]
    return res


def _page_sections(text: str, page: int) -> list[Section]:
    """One transcribed page -> sections (headings by the PDF heuristic); a page without one is "Page n"."""
    sections, heading, body = [], "", []

    def flush() -> None:
        if body:
            sections.append(Section(heading or f"Page {page}", "\n".join(body).strip(), page))
        body.clear()
    for line in text.splitlines():
        if _is_heading(line):
            flush()
            heading = line.strip()
        else:
            body.append(line)
    flush()
    return [s for s in sections if s.body.strip()]
