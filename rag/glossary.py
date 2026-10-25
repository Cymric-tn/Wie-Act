"""
Deterministic query expansion from a curated glossary (data/glossary.yaml), replacing LLM keywords.

Matching runs on rag.normalize tokens (the exact BM25 representation), longest variant first, so
"العدد المشتق" maps as one concept before "مشتق" could. Each match yields a concept group:
(the question tokens it covers, the French course tokens they mean). The retriever scores the French
tokens at GLOSSARY_WEIGHT and counts each group ONCE in the coverage gate (matched if either the
learner's word or its course term is in the chunk), so an expansion can only help a question whose
own words were in the glossary; it can never turn an off-topic question into an on-topic one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from rag.normalize import tokenize


@dataclass(frozen=True)
class Expansion:
    groups: tuple[tuple[frozenset[str], frozenset[str]], ...] = ()   # (question tokens, course tokens)
    terms: tuple[str, ...] = ()                                      # the course terms, for notes/logs

    def __bool__(self) -> bool:
        return bool(self.groups)

    @property
    def course_tokens(self) -> frozenset[str]:
        return frozenset(t for _, ts in self.groups for t in ts)


@dataclass
class Glossary:
    topic: str = ""
    # first token -> [(variant tokens, course terms)], longest variant first
    _index: dict[str, list[tuple[tuple[str, ...], tuple[str, ...]]]] = field(default_factory=dict)
    entries: int = 0
    variants: int = 0
    course_terms: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: str) -> "Glossary":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        g = cls(topic=str(data.get("topic", "")))
        seen: dict[tuple[str, ...], tuple[str, ...]] = {}
        for i, e in enumerate(data.get("terms") or []):
            targets = tuple(str(t) for t in (e.get("fr") or []))
            if not targets or not all(tokenize(t) for t in targets):
                raise ValueError(f"{path}: entry {i} needs French course terms that survive tokenization")
            g.entries += 1
            g.course_terms += targets
            for v in e.get("variants") or []:
                toks = tuple(tokenize(str(v)))
                if not toks:
                    raise ValueError(f"{path}: variant {v!r} (entry {targets}) is only stopwords / too short")
                if toks in seen and seen[toks] != targets:
                    raise ValueError(f"{path}: variant {v!r} maps to both {seen[toks]} and {targets}")
                if toks not in seen:
                    seen[toks] = targets
                    g._index.setdefault(toks[0], []).append((toks, targets))
                    g.variants += 1
        for lst in g._index.values():
            lst.sort(key=lambda x: -len(x[0]))
        return g

    def expand(self, question: str) -> Expansion:
        toks = tokenize(question)
        groups, terms, i = [], [], 0
        while i < len(toks):
            for variant, targets in self._index.get(toks[i], ()):
                if tuple(toks[i:i + len(variant)]) == variant:
                    course = frozenset(t for tg in targets for t in tokenize(tg))
                    if not course <= set(toks):          # already in course words: nothing to add
                        groups.append((frozenset(variant), course))
                        terms.extend(t for t in targets if t not in terms)
                    i += len(variant)
                    break
            else:
                i += 1
        return Expansion(tuple(groups), tuple(terms))
