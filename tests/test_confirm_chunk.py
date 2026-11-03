"""Engine.confirm_chunk: a professor confirms or fixes a concept tag; the retriever and the saved course index follow."""
import pytest

from tests.test_retrieval_dynamic import ch, make_engine
from rag.store import CourseStore


async def up(tmp_path, vt):
    eng, s = make_engine(tmp_path, vt)
    await eng.add_material([ch("prof#1", "Notes sur les asymptotes obliques.", course_id="analyse", visibility="class",
                               class_id="C1", origin="professor", owner="prof", concept="", confidence=0.4)])
    return eng, s


async def test_the_tag_becomes_certain_and_is_saved_with_the_course(tmp_path, vt):
    eng, s = await up(tmp_path, vt)
    out = await eng.confirm_chunk("prof#1", "tangente")
    assert out["concept"] == "tangente" and out["confidence"] == 1.0
    live = next(c for c in eng.retriever.chunks if c.chunk_id == "prof#1")
    assert (live.concept, live.confidence) == ("tangente", 1.0)
    assert [c.concept for c in CourseStore(s.rag_course_index_dir).load("analyse")] == ["tangente"]     # survives a restart
    assert (await eng.confirm_chunk("prof#1", ""))["concept"] == ""                                       # cleared
    assert (await eng.confirm_chunk("prof#1", None))["concept"] == ""


async def test_unknown_chunk_is_none_and_an_unknown_concept_is_refused(tmp_path, vt):
    eng, _ = await up(tmp_path, vt)
    assert await eng.confirm_chunk("nope", "tangente") is None
    with pytest.raises(ValueError, match="unknown concept"):
        await eng.confirm_chunk("prof#1", "integrales")
    assert next(c for c in eng.retriever.chunks if c.chunk_id == "prof#1").confidence == 0.4              # unchanged


async def test_a_confirmed_chunk_is_no_longer_low_confidence_for_the_next_ingest_view(tmp_path, vt):
    eng, _ = await up(tmp_path, vt)
    before = [c for c in eng.retriever.chunks if c.chunk_id == "prof#1"][0]
    assert before.confidence < 0.6
    await eng.confirm_chunk("prof#1", "puissance")
    assert [c for c in eng.retriever.chunks if c.chunk_id == "prof#1"][0].confidence >= 0.6
    assert len([c for c in eng.retriever.chunks if c.chunk_id == "prof#1"]) == 1                         # replaced, not duplicated
