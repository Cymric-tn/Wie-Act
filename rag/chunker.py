"""
Markdown docs -> chunks of at most CHUNK_MAX_TOKENS (estimated, conservative), with metadata.

    ---                      front matter: topic, level, language, title
    topic: derivative
    ...
    ---
    ## Section heading       one section = one chunk when it fits (docs are written ~250-300 tokens
    <!-- concept: tangente -->  the section's concept, same ids as data/exercises/*.yaml (stripped from the text)
    paragraphs...            per section); longer sections are packed by paragraph, then by sentence

chunk_id = "<file stem>#<section slug>[-<part>]": stable across re-indexing and small edits elsewhere
in the doc, so eval sets and cache keys that name chunk ids keep working.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import asdict, dataclass

from gatekeeper.client import estimate_text_tokens
from rag.normalize import fold_latin, strip_arabic_diacritics

_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_HEAD = re.compile(r"^## +(.+?) *$", re.M)
_AR = re.compile("[\u0600-\u06FF]")
_CONCEPT = re.compile(r"<!--\s*concept:\s*(.+?)\s*-->\s*\n?")
_SENT = re.compile(r"(?<=[.!?؟])\s+")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    topic: str
    level: str
    language: str
    source: str
    title: str = ""
    section: str = ""
    concept: str = ""            # exercise-bank concept id: explain appends a practice exercise on it

    @property
    def tokens(self) -> int:
        return estimate_text_tokens(self.text)

    def to_dict(self) -> dict:
        return asdict(self)


def slug(s: str) -> str:
    s = strip_arabic_diacritics(s) if _AR.search(s) else fold_latin(s)
    return re.sub(r"[^\w]+", "-", s).strip("-")


def _pack(units: list[str], max_tokens: int, sep: str) -> list[str]:
    parts, cur = [], ""
    for u in units:
        cand = f"{cur}{sep}{u}" if cur else u
        if cur and estimate_text_tokens(cand) > max_tokens:
            parts.append(cur)
            cur = u
        else:
            cur = cand
    return parts + ([cur] if cur else [])


def _split_section(body: str, max_tokens: int) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    units = []
    for p in paras:   # a paragraph longer than a chunk is split by sentence
        units.extend(_pack(_SENT.split(p), max_tokens, " ") if estimate_text_tokens(p) > max_tokens else [p])
    return _pack(units, max_tokens, "\n\n")


def chunk_markdown(text: str, source: str, max_tokens: int = 300) -> list[Chunk]:
    m = _FRONT.match(text)
    meta = {}
    if m:
        meta = {k.strip(): v.strip() for k, _, v in (l.partition(":") for l in m.group(1).splitlines()) if k.strip()}
        text = text[m.end():]
    stem = os.path.splitext(os.path.basename(source))[0]
    title = meta.get("title", stem)
    heads = list(_HEAD.finditer(text))
    sections = [(h.group(1), text[h.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)])
                for i, h in enumerate(heads)] or [(title, text)]
    chunks = []
    for heading, body in sections:
        m = _CONCEPT.search(body)
        concept = m.group(1).strip() if m else meta.get("concept", "")
        body = _CONCEPT.sub("", body)
        header = f"{title} — {heading}\n"
        for i, part in enumerate(_split_section(body, max_tokens - estimate_text_tokens(header))):
            cid = f"{stem}#{slug(heading)}" + (f"-{i + 1}" if i else "")
            chunks.append(Chunk(cid, header + part, meta.get("topic", ""), meta.get("level", ""),
                                meta.get("language", ""), os.path.basename(source), title, heading, concept))
    return chunks


def doc_status(text: str) -> str:
    m = _FRONT.match(text)
    meta = {k.strip(): v.strip() for k, _, v in (l.partition(":") for l in m.group(1).splitlines())} if m else {}
    return meta.get("status", "")


def chunk_dir(docs_dir: str, max_tokens: int = 300, include_drafts: bool = False,
              skipped: list[str] | None = None) -> list[Chunk]:
    """Docs whose header says `status: draft` are skipped (they wait for human review) unless asked."""
    out = []
    for path in sorted(glob.glob(os.path.join(docs_dir, "*.md"))):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if doc_status(text) == "draft" and not include_drafts:
            if skipped is not None:
                skipped.append(os.path.basename(path))
            continue
        out.extend(chunk_markdown(text, path, max_tokens))
    ids = [c.chunk_id for c in out]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate chunk ids (two sections with the same heading in one doc?)")
    return out
