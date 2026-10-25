"""
Retriever interface + implementations, selected by RETRIEVER=bm25|embedding.

  BM25Retriever       default. Pure Python (Okapi BM25, k1=1.5, b=0.75), no model, no quota.
                      Recon: FelCloud lists no embedding model, so this is the production path.
                      Relevance gate = query coverage, not raw score: on a one-topic corpus the
                      topic word has a low idf, so "c'est quoi une dérivée ?" scores below
                      "the function in my code is slow". Coverage weights query terms by idf, with
                      terms unseen in the corpus ("python") at the median idf.
  EmbeddingRetriever  stub for when an embedding model exists (local Ollama bge-m3 works).
                      It embeds through the gatekeeper (separate embeddings quota) and ranks by cosine.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from rag.chunker import Chunk
from rag.glossary import Expansion
from rag.normalize import tokenize

_AR = re.compile("[\u0600-\u06FF]")

Filters = Mapping[str, Any] | None


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float
    coverage: float = 1.0          # BM25: idf-weighted share of the query terms this chunk contains


class Retriever(Protocol):
    async def index(self, chunks: Sequence[Chunk]) -> None: ...

    async def search(self, query: str, k: int = 4, filters: Filters = None,
                     expansion: Expansion | None = None) -> list[Hit]: ...


def _match(c: Chunk, filters: Filters) -> bool:
    for key, want in (filters or {}).items():
        if want is None:
            continue
        have = getattr(c, key, None)
        if isinstance(want, (list, tuple, set, frozenset)):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


class BM25Retriever:
    def __init__(self, min_coverage: float = 0.0, k1: float = 1.5, b: float = 0.75, glossary_weight: float = 0.5):
        self.min_coverage, self.k1, self.b, self.glossary_weight = min_coverage, k1, b, glossary_weight
        self.chunks: list[Chunk] = []
        self._tf: list[Counter] = []
        self._len: list[int] = []
        self._idf: dict[str, float] = {}
        self._avgdl = 1.0
        self._scripts: list[set[str]] = []

    async def index(self, chunks: Sequence[Chunk]) -> None:
        self.chunks = list(chunks)
        docs = [tokenize(c.text) for c in self.chunks]
        self._tf = [Counter(d) for d in docs]
        self._scripts = [{_script(t) for t in d} for d in docs]
        self._len = [len(d) for d in docs]
        self._avgdl = (sum(self._len) / len(docs)) if docs else 1.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}   # always > 0
        # a query word absent from the corpus ("python") weighs like a TYPICAL corpus word (median idf),
        # not the rarest one: measured, this keeps on-topic >= 0.49 and off-topic <= 0.28 coverage
        self._unseen_idf = statistics.median(self._idf.values()) if self._idf else 1.0

    def weights(self, query: str, expansion: Expansion | None = None) -> dict[str, float]:
        """Question terms weigh 1; course terms added by the glossary GLOSSARY_WEIGHT (unless also asked)."""
        w = {t: self.glossary_weight for t in (expansion.course_tokens if expansion else ())}
        w.update({t: 1.0 for t in tokenize(query)})
        return w

    @staticmethod
    def units(query: str, expansion: Expansion | None = None) -> list[tuple[frozenset[str], bool]]:
        """Coverage units: one per glossary concept (learner words + course words, matched if any is in
        the chunk) and one per remaining question term. bool = untranslated question term."""
        units, covered = [], set()
        for qtoks, ctoks in (expansion.groups if expansion else ()):
            units.append((qtoks | ctoks, False))
            covered |= qtoks
        units += [(frozenset([t]), True) for t in dict.fromkeys(tokenize(query)) if t not in covered]
        return units

    def coverage(self, units: list[tuple[frozenset[str], bool]], i: int, translated: bool = False) -> float:
        """idf-weighted share of the question's concepts found in chunk i. translated (the glossary
        matched something): an untranslated question term written in a script the chunk does not use
        at all (a Darija word vs a French chunk) cannot match by construction and leaves the
        denominator. Same-script misses still count (an off-topic "python" stays unmatched)."""
        scripts, tf = self._scripts[i], self._tf[i]
        total = got = 0.0
        for members, plain in units:
            if translated and plain and _script(next(iter(members))) not in scripts:
                continue
            w = max(self._idf.get(t, self._unseen_idf) for t in members)
            total += w
            got += w if any(t in tf for t in members) else 0.0
        return got / total if total else 0.0

    def score(self, weights: Mapping[str, float], i: int) -> float:
        tf, dl, s = self._tf[i], self._len[i], 0.0
        for t, wt in weights.items():
            f = tf.get(t)
            if f:
                s += wt * self._idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
        return s

    async def search(self, query: str, k: int = 4, filters: Filters = None,
                     expansion: Expansion | None = None) -> list[Hit]:
        q = self.weights(query, expansion)
        if not q:
            return []
        units = self.units(query, expansion)
        hits = [Hit(c, round(self.score(q, i), 3), round(self.coverage(units, i, bool(expansion)), 3))
                for i, c in enumerate(self.chunks) if _match(c, filters)]
        hits = [h for h in hits if h.score > 0 and h.coverage >= self.min_coverage]
        return sorted(hits, key=lambda h: (-h.score, h.chunk.chunk_id))[:k]


def _script(token: str) -> str:
    return "arabic" if _AR.search(token) else "latin"


EmbedFn = Callable[[Sequence[str]], Awaitable[list[list[float]]]]


class EmbeddingRetriever:
    """Stub: in-memory vectors, cosine ranking. Not the default: FelCloud has no embedding model."""

    def __init__(self, embed: EmbedFn, min_score: float = 0.0, batch: int = 16):
        self.embed, self.min_score, self.batch = embed, min_score, batch
        self.chunks: list[Chunk] = []
        self._vecs: list[list[float]] = []

    @staticmethod
    def _unit(v: list[float]) -> list[float]:
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    async def index(self, chunks: Sequence[Chunk]) -> None:
        self.chunks, self._vecs = list(chunks), []
        for i in range(0, len(self.chunks), self.batch):
            vecs = await self.embed([c.text for c in self.chunks[i:i + self.batch]])
            self._vecs.extend(self._unit(v) for v in vecs)

    async def search(self, query: str, k: int = 4, filters: Filters = None,
                     expansion: Expansion | None = None) -> list[Hit]:
        if not self.chunks:
            return []
        q = self._unit((await self.embed([" ".join([query, *(expansion.terms if expansion else ())])]))[0])
        hits = [Hit(c, round(sum(a * b for a, b in zip(q, v)), 4))
                for c, v in zip(self.chunks, self._vecs) if _match(c, filters)]
        return sorted([h for h in hits if h.score >= self.min_score], key=lambda h: -h.score)[:k]


def language_plan(learner_language: str | None) -> list[dict | None]:
    """Which course chunks a learner's question searches, in order (first non-empty wins).
    DARIJA / ARABIZI: French chunks only (the course language; the glossary carries the terms over).
    ARABIC: Arabic chunks first, French when nothing Arabic passes the gate. ENGLISH: English first, then
    French (the glossary maps English terms to French ones). FRENCH / unknown: everything."""
    if learner_language in ("DARIJA", "ARABIZI"):
        return [{"language": "FRENCH"}]
    if learner_language == "ARABIC":
        return [{"language": "ARABIC"}, {"language": "FRENCH"}]
    if learner_language == "ENGLISH":
        return [{"language": "ENGLISH"}, {"language": "FRENCH"}]
    return [None]


def make_retriever(kind: str, min_coverage: float, embed: EmbedFn | None = None,
                   glossary_weight: float = 0.5) -> Retriever:
    if kind == "bm25":
        return BM25Retriever(min_coverage=min_coverage, glossary_weight=glossary_weight)
    if kind == "embedding":
        if embed is None:
            raise ValueError("RETRIEVER=embedding needs an embed function (and MODEL_EMBED)")
        return EmbeddingRetriever(embed)
    raise ValueError(f"unknown RETRIEVER {kind!r}")
