"""Course graph: loader, structural errors, and consistency with data/docs tags and the exercise bank."""
import pytest

from engine.course import CourseError, check_content, load_course, load_courses, parse_course
from engine.exercises import ExerciseBank
from rag.chunker import chunk_dir

COURSE = "data/courses/analyse.yaml"


def titles(x):
    return {"fr": x, "en": x, "ar": x}


def raw(concepts, units_extra=None):
    return {"course": "t", "titles": titles("t"), "units": [
        {"id": "u1", "order": 1, "titles": titles("u"), "concepts": concepts}, *(units_extra or [])]}


def con(i, requires=(), related=(), **kw):
    return {"id": i, "requires": list(requires), "related": list(related), "titles": titles(i), **kw}


def test_the_derivative_unit_loads_with_trilingual_titles():
    c = load_course(COURSE)
    assert c.id == "analyse" and [u.id for u in c.units] == ["derivees"]
    assert len([x for x in c.concepts.values() if not x.draft]) == 11
    for cid in c.concepts:
        assert all(c.title(cid, lang) for lang in ("fr", "en", "ar"))
    assert c.title("produit", "ar") != c.title("produit", "fr") and c.title("derivees", "en") == "Differentiation"
    assert "analyse" in load_courses("data/courses")


def test_graph_matches_docs_tags_and_the_exercise_bank_both_ways():
    c = load_course(COURSE)
    docs = {ch.concept for ch in chunk_dir("data/docs", include_drafts=True) if ch.concept}
    bank = {e.concept for e in ExerciseBank.load("data/exercises/derivees.yaml").exercises}
    assert check_content(c, docs, bank) == []


def test_check_content_reports_every_kind_of_mismatch():
    c = parse_course(raw([con("a"), con("b", ["a"]), con("d", status="draft")]))
    probs = check_content(c, {"a", "x"}, {"a", "b", "y"})
    assert any("'b' has no course text" in p for p in probs)
    assert not any("'d'" in p for p in probs)                  # a draft concept needs neither text nor exercises
    assert any("docs tag 'x'" in p for p in probs) and any("bank concept 'y'" in p for p in probs)
    assert not any("'a' has no" in p for p in probs)


def test_order_puts_prerequisites_first_and_prerequisites_are_transitive():
    c = load_course(COURSE)
    o = c.order()
    for k in c.concepts.values():
        assert all(o.index(r) < o.index(k.id) for r in k.requires)
    assert c.prerequisites("quotient") == [x for x in o if x in {"limite", "nombre dérivé", "puissance", "constante",
                                                                   "polynôme", "produit", "inverse"}]
    assert c.prerequisites("limite") == []
    assert c.order(["extremum", "limite"]) == ["limite", "extremum"]


def test_requires_has_no_cycles_in_the_real_graph_and_cycles_are_rejected():
    load_course(COURSE)                                          # would raise
    with pytest.raises(CourseError, match="cycle in concept requires: a -> b -> a"):
        parse_course(raw([con("a", ["b"]), con("b", ["a"])]))
    with pytest.raises(CourseError, match="cycle"):
        parse_course(raw([con("a", ["a"])]))
    with pytest.raises(CourseError, match="cycle in unit requires"):
        parse_course({**raw([con("a")]), "units": [
            {"id": "u1", "order": 1, "requires": ["u2"], "titles": titles("u"), "concepts": [con("a")]},
            {"id": "u2", "order": 2, "requires": ["u1"], "titles": titles("u"), "concepts": [con("b")]}]})


def test_structural_errors():
    with pytest.raises(CourseError, match="unknown concept 'zz'"):
        parse_course(raw([con("a", ["zz"])]))
    with pytest.raises(CourseError, match="unknown concept 'zz'"):
        parse_course(raw([con("a", related=["zz"])]))
    with pytest.raises(CourseError, match="duplicate concept id 'a'"):
        parse_course(raw([con("a"), con("a")]))
    with pytest.raises(CourseError, match="missing titles"):
        parse_course(raw([{"id": "a", "titles": {"fr": "a"}}]))
    with pytest.raises(CourseError, match="unknown unit"):
        parse_course({**raw([con("a")]), "units": [{"id": "u1", "requires": ["nope"], "titles": titles("u"),
                                                     "concepts": [con("a")]}]})
