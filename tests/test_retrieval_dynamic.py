"""v1.3 step 8: runtime chunks, per-course persistence, visibility scoping, professor boost, no eval regression."""
import json
import os
import random

import pytest

from engine import Engine
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient
from metrics import MemorySink
from rag.chunker import Chunk, chunk_dir
from rag.glossary import Glossary
from rag.retriever import VIEWER, BM25Retriever, visible
from rag.store import CourseStore, load_chunks, save_chunks
from tests.conftest import FnProvider, chat_ok, make_settings


def ch(cid, text, **kw):
    return Chunk(cid, text, "derivative", "beginner", kw.pop("language", "FRENCH"), "f.md", **kw)


TANGENT = "La tangente à la courbe au point d'abscisse a a pour coefficient directeur le nombre dérivé f'(a)."


async def bm25(chunks, **kw):
    r = BM25Retriever(min_coverage=0.4, **kw)
    await r.index(chunks)
    return r


# ---------------------------------------------------------------- visibility
def test_visibility_rules():
    pub, legacy = ch("a", "x", course_id="analyse"), ch("b", "x")
    cls = ch("c", "x", course_id="analyse", visibility="class", class_id="C1", owner="prof")
    priv = ch("d", "x", course_id="analyse", visibility="private", owner="L1")
    me = {"course_id": "analyse", "class_id": "C1", "learner_id": "L1"}
    assert all(visible(c, me) for c in (pub, legacy, cls, priv))
    assert not visible(cls, {**me, "class_id": "C2"}) and not visible(priv, {**me, "learner_id": "L2"})
    assert not visible(pub, {**me, "course_id": "other"}) and visible(legacy, {**me, "course_id": "other"})
    # nobody identified: public material only
    assert visible(pub, None) and not visible(cls, None) and not visible(priv, {}) and not visible(priv, {"learner_id": ""})
    assert not visible(ch("e", "x", visibility="class"), {"class_id": ""})       # a class chunk with no class_id: nobody


async def test_search_applies_the_viewer_and_a_search_without_one_sees_public_only():
    r = await bm25([ch("pub", TANGENT, course_id="analyse"),
                    ch("cls", TANGENT + " (polycopié)", course_id="analyse", visibility="class", class_id="C1"),
                    ch("priv", TANGENT + " (mes notes)", course_id="analyse", visibility="private", owner="L1"),
                    ch("othercourse", TANGENT, course_id="physique")])
    ids = lambda hits: {h.chunk.chunk_id for h in hits}
    q = "tangente et nombre dérivé"
    assert ids(await r.search(q, 9)) == {"pub", "othercourse"}                     # no viewer: public only
    me = {VIEWER: {"course_id": "analyse", "class_id": "C1", "learner_id": "L1"}}
    assert ids(await r.search(q, 9, me)) == {"pub", "cls", "priv"}
    other = {VIEWER: {"course_id": "analyse", "class_id": "C2", "learner_id": "L2"}}
    assert ids(await r.search(q, 9, other)) == {"pub"}


# ---------------------------------------------------------------- runtime add / remove
async def test_add_remove_and_replace_change_the_results():
    r = await bm25([ch("t", TANGENT)])
    q = "limite du taux d'accroissement"
    assert await r.search(q) == []
    assert r.add([ch("l", "Le nombre dérivé est la limite du taux d'accroissement quand h tend vers 0.")]) == 1
    assert [h.chunk.chunk_id for h in await r.search(q)] == ["l"]
    r.add([ch("l", "Texte remplacé sur la tangente à la courbe.")])                  # same id: replaced, not duplicated
    assert [c.chunk_id for c in r.chunks] == ["t", "l"] and await r.search(q) == []
    gone = r.remove(["t"])
    assert [c.chunk_id for c in gone] == ["t"] and [c.chunk_id for c in r.chunks] == ["l"]
    assert [c.chunk_id for c in r.remove(where=lambda c: c.chunk_id == "l")] == ["l"] and r.chunks == []
    assert await r.search(q) == [] and r.remove(["nothing"]) == []


# ---------------------------------------------------------------- professor boost
async def test_professor_boost_reorders_wins_ties_and_does_not_open_the_gate():
    text = "Le nombre dérivé est la limite du taux d'accroissement quand h tend vers 0."
    official, prof = ch("a-official", text), ch("b-prof", text, origin="professor")
    # identical text: a tie without the boost; the professor wins it whatever the ids say
    r = await bm25([official, prof], professor_boost=1.0)
    assert [h.chunk.chunk_id for h in await r.search("nombre dérivé limite taux accroissement")][0] == "b-prof"
    # a slightly weaker professor chunk overtakes the official one only with the boost
    weaker = ch("b-prof", text + " Voir aussi la tangente et la courbe et la fonction.", origin="professor")
    q = "nombre dérivé limite taux accroissement"
    r1 = await bm25([official, weaker], professor_boost=1.0)
    r2 = await bm25([official, weaker], professor_boost=1.3)
    s1 = {h.chunk.chunk_id: h.score for h in await r1.search(q)}
    top2 = await r2.search(q)
    assert s1["b-prof"] < s1["a-official"] and top2[0].chunk.chunk_id == "b-prof"
    assert top2[0].score == pytest.approx(s1["b-prof"] * 1.3, abs=0.01)
    # the gate looks at coverage, which the boost does not change: an off-topic professor chunk stays out
    r3 = await bm25([ch("off", "Python est un langage de programmation avec des fonctions lentes.", origin="professor"),
                     official], professor_boost=50.0)
    got = [h.chunk.chunk_id for h in await r3.search("c'est quoi le nombre dérivé en python ?")]
    assert got == ["a-official"]                            # the boosted off-topic chunk ("python") is not let in


# ---------------------------------------------------------------- persistence + engine
def make_engine(tmp_path, vt, fn=lambda b: chat_ok("ok"), **env):
    base = str(tmp_path / "base.json")
    if not os.path.exists(base):
        save_chunks(base, chunk_dir("data/docs"))
    s = make_settings(RAG_INDEX_PATH=base, RAG_COURSE_INDEX_DIR=str(tmp_path / "courses"), **env)
    return Engine.create(s, client=LLMClient(s, FnProvider(fn)), sink=MemorySink(),
                         cache=ExactCache(":memory:", s.cache_ttl_s), clock=vt.clock, sleep=vt.sleep,
                         rng=random.Random(1)), s


async def test_runtime_material_is_persisted_per_course_and_reloaded(tmp_path, vt):
    eng, s = make_engine(tmp_path, vt)
    mine = [ch("up#1", "Mes notes sur les asymptotes obliques et les branches infinies.", course_id="analyse",
               visibility="private", owner="L1", origin="learner"),
            ch("prof#1", "Polycopié du professeur sur les asymptotes obliques et les branches infinies.",
               course_id="analyse", visibility="class", class_id="C1", origin="professor", owner="prof")]
    other = [ch("phys#1", "Cours de physique sur la vitesse.", course_id="physique")]
    assert await eng.add_material(mine + other) == 3
    store = CourseStore(s.rag_course_index_dir)
    assert store.courses() == ["analyse", "physique"]
    assert {c.chunk_id for c in store.load("analyse")} == {"up#1", "prof#1"}           # base docs are not copied
    assert [c.origin for c in store.load("analyse")] == ["learner", "professor"]

    eng2, _ = make_engine(tmp_path, vt)                                                   # a restart
    await eng2._ensure_index()
    assert {"up#1", "prof#1", "phys#1"} <= {c.chunk_id for c in eng2.retriever.chunks}
    assert len(eng2.retriever.chunks) == len(load_chunks(s.rag_index_path)) + 3

    assert await eng2.remove_material(source="f.md", course_id="analyse", owner="L1") == 1   # one uploaded file
    assert [c.chunk_id for c in store.load("analyse")] == ["prof#1"]
    assert await eng2.remove_material(["prof#1", "phys#1"]) == 2
    assert store.courses() == []                                                          # empty course: file removed


async def test_explain_sees_a_learner_s_private_upload_only_for_that_learner(tmp_path, vt):
    seen = []

    def model(body):
        seen.append(body["messages"][-1]["content"])
        return chat_ok("Une asymptote oblique est une droite dont la courbe se rapproche à l'infini.")
    eng, _ = make_engine(tmp_path, vt, model)
    await eng.add_material([ch("up#1", "Asymptote oblique : la droite y = ax + b dont la courbe se rapproche quand x "
                                       "tend vers l'infini. Mes notes de cours sur les asymptotes obliques.",
                               course_id="analyse", visibility="private", owner="L1", origin="learner")])
    q = {"question": "c'est quoi une asymptote oblique ?"}
    nobody = await eng.run("explain", q, {"language": "FRENCH", "course_id": "analyse"})
    stranger = await eng.run("explain", q, {"language": "FRENCH", "course_id": "analyse", "learner_id": "L2"})
    owner = await eng.run("explain", q, {"language": "FRENCH", "course_id": "analyse", "learner_id": "L1"})
    assert nobody.notes.get("retrieval") == "no_relevant_chunks" and stranger.notes.get("retrieval") == "no_relevant_chunks"
    assert not owner.degraded and "up#1" in owner.notes["sources"]
    again = await eng.run("explain", q, {"language": "FRENCH", "course_id": "analyse", "learner_id": "L1"})
    assert again.from_cache                                   # the owner's key includes the chunk id: no cross-talk
    assert not (await eng.run("explain", q, {"language": "FRENCH", "course_id": "analyse"})).from_cache


# ---------------------------------------------------------------- the existing evals do not regress
async def test_existing_retrieval_evals_are_unchanged():
    from scripts.eval_retrieval import DATASETS, evaluate
    r = BM25Retriever(min_coverage=0.4, glossary_weight=0.5, professor_boost=1.3)
    await r.index(chunk_dir("data/docs"))
    g = Glossary.load("data/glossary.yaml")
    got = {}
    for path in DATASETS:
        items = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
        for mode in ("plain", "glossary", "policy"):
            v = await evaluate(r, items, g if mode != "plain" else None, policy=mode == "policy")
            got[(os.path.basename(path), mode)] = (v["h1"], v["h3"], v["rejected"])
    # baseline measured before v1.3 (scripts.eval_retrieval, 2026-11-01): hit@1, hit@3, off-topic rejected
    assert got[("retrieval_set.jsonl", "policy")] == (13, 14, 4)
    assert got[("retrieval_crosslang.jsonl", "policy")][:2] == (6, 7)
    assert got[("retrieval_manual.jsonl", "policy")][:2] == (2, 2)
    assert got[("retrieval_set.jsonl", "plain")] == (13, 14, 4)
    assert got[("retrieval_crosslang.jsonl", "glossary")][:2] == (6, 7)
