"""
Course graph: course -> units (order, requires) -> concepts (requires, related), titles in fr / en / ar.

    course = load_course("data/courses/analyse.yaml")
    course.order()                  # every concept, prerequisites first
    course.prerequisites("produit") # transitive
    course.title("produit", "ar")

Structural errors (duplicate ids, a reference to an unknown id, a cycle in `requires`) raise CourseError at load
time. check_content() compares the graph with the course text and the exercise bank and returns the mismatches
(tests/test_course_graph.py fails on any). Concept ids are the ids of the bank and of the `<!-- concept: id -->`
tags of data/docs. status: draft marks a planned concept (its text is a draft awaiting review, or the bank has no
exercise yet): it is exempt from the "must exist in docs and bank" rule, and prep plans skip it.
"""
from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import yaml

LANGS = ("fr", "en", "ar")


class CourseError(ValueError):
    pass


@dataclass(frozen=True)
class Concept:
    id: str
    unit: str
    requires: tuple[str, ...]
    related: tuple[str, ...]
    titles: Mapping[str, str]
    status: str = "published"

    @property
    def draft(self) -> bool:
        return self.status == "draft"


@dataclass(frozen=True)
class Unit:
    id: str
    order: int
    requires: tuple[str, ...]
    titles: Mapping[str, str]
    concepts: tuple[str, ...]


@dataclass
class Course:
    id: str
    titles: Mapping[str, str]
    units: list[Unit]
    concepts: dict[str, Concept] = field(default_factory=dict)

    def unit(self, unit_id: str) -> Unit:
        for u in self.units:
            if u.id == unit_id:
                return u
        raise KeyError(f"unknown unit {unit_id!r}")

    def concept(self, concept_id: str) -> Concept:
        try:
            return self.concepts[concept_id]
        except KeyError:
            raise KeyError(f"unknown concept {concept_id!r}") from None

    def title(self, node_id: str, lang: str = "fr") -> str:
        node = self.concepts.get(node_id) or next((u for u in self.units if u.id == node_id), None)
        if node is None:
            raise KeyError(f"unknown id {node_id!r}")
        return node.titles.get(lang.lower()) or node.titles["fr"]

    def order(self, concept_ids: Iterable[str] | None = None) -> list[str]:
        """The concepts (default: all) prerequisites first. Units by `order`, then the file order of concepts;
        a concept always follows everything it requires, whichever unit that is in."""
        wanted = set(concept_ids) if concept_ids is not None else set(self.concepts)
        base = [c for u in sorted(self.units, key=lambda u: u.order) for c in u.concepts]
        out: list[str] = []
        seen: set[str] = set()

        def visit(c: str) -> None:
            if c in seen:
                return
            seen.add(c)
            for r in self.concepts[c].requires:
                visit(r)
            out.append(c)
        for c in base:
            visit(c)
        return [c for c in out if c in wanted]

    def prerequisites(self, concept_id: str) -> list[str]:
        """Every concept that must be known first (transitive), in teaching order."""
        need: set[str] = set()
        stack = list(self.concept(concept_id).requires)
        while stack:
            c = stack.pop()
            if c not in need:
                need.add(c)
                stack.extend(self.concepts[c].requires)
        return [c for c in self.order() if c in need]

    def concepts_of(self, unit_id: str) -> list[str]:
        return list(self.unit(unit_id).concepts)


def _cycle(edges: Mapping[str, Iterable[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    path: list[str] = []

    def dfs(n: str) -> list[str] | None:
        state[n] = 1
        path.append(n)
        for m in edges.get(n, ()):
            if state.get(m) == 1:
                return path[path.index(m):] + [m]
            if state.get(m) is None and (c := dfs(m)):
                return c
        state[n] = 2
        path.pop()
        return None
    for n in edges:
        if state.get(n) is None and (c := dfs(n)):
            return c
    return None


def _titles(raw: Mapping | None, where: str) -> dict[str, str]:
    t = {k: str(v) for k, v in (raw or {}).items()}
    missing = [lang for lang in LANGS if not t.get(lang)]
    if missing:
        raise CourseError(f"{where}: missing titles {missing}")
    return t


def parse_course(data: Mapping) -> Course:
    cid = str(data.get("course") or "")
    if not cid:
        raise CourseError("missing `course`")
    units: list[Unit] = []
    concepts: dict[str, Concept] = {}
    for u in data.get("units") or []:
        uid = str(u["id"])
        ids = []
        for c in u.get("concepts") or []:
            cc = str(c["id"])
            if cc in concepts:
                raise CourseError(f"duplicate concept id {cc!r}")
            concepts[cc] = Concept(cc, uid, tuple(map(str, c.get("requires") or ())),
                                   tuple(map(str, c.get("related") or ())), _titles(c.get("titles"), f"concept {cc}"),
                                   str(c.get("status") or "published"))
            ids.append(cc)
        if any(x.id == uid for x in units):
            raise CourseError(f"duplicate unit id {uid!r}")
        units.append(Unit(uid, int(u.get("order", len(units) + 1)), tuple(map(str, u.get("requires") or ())),
                          _titles(u.get("titles"), f"unit {uid}"), tuple(ids)))
    unit_ids = {u.id for u in units}
    for u in units:
        for r in u.requires:
            if r not in unit_ids:
                raise CourseError(f"unit {u.id}: requires unknown unit {r!r}")
    for c in concepts.values():
        for kind, refs in (("requires", c.requires), ("related", c.related)):
            for r in refs:
                if r not in concepts:
                    raise CourseError(f"concept {c.id}: {kind} unknown concept {r!r}")
    if cyc := _cycle({c.id: c.requires for c in concepts.values()}):
        raise CourseError("cycle in concept requires: " + " -> ".join(cyc))
    if cyc := _cycle({u.id: u.requires for u in units}):
        raise CourseError("cycle in unit requires: " + " -> ".join(cyc))
    return Course(cid, _titles(data.get("titles"), "course"), units, concepts)


def load_course(path: str) -> Course:
    with open(path, encoding="utf-8") as f:
        try:
            return parse_course(yaml.safe_load(f) or {})
        except CourseError as e:
            raise CourseError(f"{path}: {e}") from None


def load_courses(directory: str) -> dict[str, Course]:
    out: dict[str, Course] = {}
    for p in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
        c = load_course(p)
        if c.id in out:
            raise CourseError(f"{p}: course id {c.id!r} defined twice")
        out[c.id] = c
    return out


def check_content(course: Course, doc_concepts: Iterable[str], bank_concepts: Iterable[str]) -> list[str]:
    """Mismatches between the graph and the course text / bank. Empty = consistent.
    Both ways: every non-draft concept must have text and exercises; every tag and every bank concept must be
    in the graph."""
    docs, bank = set(doc_concepts), set(bank_concepts)
    graph = set(course.concepts)
    live = {c for c in graph if not course.concepts[c].draft}
    problems = [f"concept {c!r} has no course text (no `<!-- concept: {c} -->` in data/docs)"
                for c in sorted(live - docs)]
    problems += [f"concept {c!r} has no exercise in the bank" for c in sorted(live - bank)]
    problems += [f"docs tag {c!r} is not in the course graph" for c in sorted(docs - graph)]
    problems += [f"bank concept {c!r} is not in the course graph" for c in sorted(bank - graph)]
    return problems
