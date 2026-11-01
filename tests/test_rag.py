"""Retrieval, chunking, budget, compress and grounded explain. No LLM: fake providers only."""
import json
import random

import pytest

from config import ConfigError
from engine import Engine
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient, estimate_tokens
from metrics import MemorySink
from rag.budget import BudgetError, assemble, limit_for
from rag.chunker import Chunk, chunk_dir, chunk_markdown, slug
from rag.normalize import tokenize
from rag.retriever import BM25Retriever, EmbeddingRetriever, make_retriever
from rag.store import load_chunks, save_chunks
from tests.conftest import FnProvider, chat_ok, make_settings


# ====================================================================== normalization, per script
@pytest.mark.parametrize("text,expected", [
    # Latin: lowercased, accents folded, light plural strip, stopwords dropped, x² -> x2
    ("C'est quoi la DÉRIVÉE de x² ?", ["deriv", "x2"]),
    ("Les dérivées des fonctions usuelles", ["deriv", "fonction", "usuell"]),
    ("What are the DERIVATIVES of functions?", ["derivativ", "function"]),
    # Arabizi: digits inside words are letters and are kept; Darija function words are stopwords
    ("chnowa ya3ni dérivée ? ma fhemtech", ["deriv", "fhemtech"]),
    ("3lech el derivee mta3 constante", ["deriv", "constant"]),
])
def test_latin_and_arabizi(text, expected):
    assert tokenize(text) == expected


@pytest.mark.parametrize("variant,canonical", [
    ("مُشْتَقَّةٌ", "مشتقة"),          # diacritics stripped
    ("مشـــتقة", "مشتقة"),             # tatweel stripped
    ("أمثلة", "امثلة"), ("إشارة", "اشارة"), ("آخر", "اخر"),   # alef variants -> ا
    ("مستوى", "مستوي"),                 # alef maksura -> ي
    ("الدالة", "الداله"),                # taa marbuta -> ه
])
def test_arabic_normalization(variant, canonical):
    assert tokenize(variant) == tokenize(canonical) != []


def test_arabic_article_and_conjunction_light_strip():
    assert tokenize("المشتقة والمشتقة بالمشتقة مشتقة") == ["مشتقه"] * 4
    assert tokenize("وإشارة") == ["اشاره"] and tokenize("واحد") == ["واحد"]   # و kept on short words


def test_arabic_is_not_case_folded_and_mixed_text_splits_by_script():
    toks = tokenize("Dérivée = المشتقة, f'(x) = 2X")
    assert toks == ["deriv", "مشتقه", "2x"]
    assert tokenize("٣ أمثلة") == ["امثله"]                                 # Arabic-Indic digit normalized


def test_french_final_e_and_plural_unify_derivee_and_derive():
    assert tokenize("dérivé dérivée dérivées DERIVEE") == ["deriv"] * 4
    assert tokenize("x2 ya3ni 3lech") == ["x2"]                          # digits: never stemmed


def test_darija_arabic_script_stopwords():
    assert tokenize("شنوة مشتقة الدالة الثابتة") == ["مشتقه", "داله", "ثابته"]


# ====================================================================== chunker + store
DOC = """---
topic: derivative
level: beginner
language: FRENCH
title: Titre
---
## Première section
Phrase une. Phrase deux.

## Section longue
""" + "\n\n".join("Paragraphe " + "mot " * 60 for _ in range(6))


def test_chunker_ids_metadata_and_size_limit():
    cs = chunk_markdown(DOC, "data/docs/demo.fr.md", max_tokens=300)
    assert cs[0].chunk_id == "demo.fr#premiere-section" and cs[0].text.startswith("Titre — Première section\n")
    assert (cs[0].topic, cs[0].level, cs[0].language, cs[0].source) == ("derivative", "beginner", "FRENCH",
                                                                        "demo.fr.md")
    longs = [c for c in cs if c.section == "Section longue"]
    assert len(longs) > 1 and [c.chunk_id for c in longs][:2] == ["demo.fr#section-longue", "demo.fr#section-longue-2"]
    assert all(c.tokens <= 300 for c in cs)


def test_sample_docs_chunk_within_limit_and_ids_unique():
    cs = chunk_dir("data/docs", 300)
    assert len(cs) >= 9 and len({c.chunk_id for c in cs}) == len(cs)
    assert all(c.tokens <= 300 for c in cs) and {c.language for c in cs} >= {"FRENCH", "ENGLISH", "ARABIC"}
    assert slug("Interprétation graphique : la tangente") == "interpretation-graphique-la-tangente"


def test_store_roundtrip(tmp_path):
    cs = chunk_dir("data/docs")
    path = str(tmp_path / "i" / "chunks.json")
    save_chunks(path, cs)
    assert load_chunks(path) == cs and load_chunks(str(tmp_path / "missing.json")) == []


# ====================================================================== retrievers
def C(cid, text, **kw):
    return Chunk(cid, text, kw.get("topic", "derivative"), "beginner", kw.get("language", "FRENCH"), "t.md")


CORPUS = [C("fr#tangente", "La tangente à la courbe a pour pente le nombre dérivé."),
          C("fr#constante", "La dérivée d'une constante est nulle : 7 disparaît."),
          C("ar#mochtaqa", "مشتقة الدالة الثابتة تساوي صفرا", language="ARABIC"),
          C("py#boucle", "Une boucle for en Python répète des instructions.", topic="python")]


async def test_bm25_ranks_and_filters():
    r = BM25Retriever()
    await r.index(CORPUS)
    assert (await r.search("équation de la tangente", 1))[0].chunk.chunk_id == "fr#tangente"
    assert (await r.search("مُشتقّة الدّالة الثّابتة", 1))[0].chunk.chunk_id == "ar#mochtaqa"   # diacritics in query
    assert (await r.search("boucle python", 3, {"topic": "derivative"})) == []
    assert [h.chunk.chunk_id for h in await r.search("boucle", 3, {"topic": ["python", "x"]})] == ["py#boucle"]
    assert await r.search("?? !!", 3) == []


async def test_bm25_coverage_gate_rejects_off_topic():
    r = BM25Retriever(min_coverage=0.4)
    await r.index(chunk_dir("data/docs"))
    assert await r.search("c'est quoi une dérivée ?", 3)                    # generic but on topic
    assert await r.search("c'est quoi une fonction en python", 3) == []    # shares one word, off topic
    assert await r.search("Qui a gagné le match hier ?", 3) == []


async def test_embedding_retriever_stub_with_fake_embeddings():
    vocab = ["tangente", "constante", "boucle"]
    async def embed(texts):
        return [[float(w in t.lower()) + 0.01 for w in vocab] for t in texts]
    r = EmbeddingRetriever(embed)
    await r.index(CORPUS)
    assert (await r.search("une tangente", 1))[0].chunk.chunk_id == "fr#tangente"


def test_retriever_selection_from_env():
    assert isinstance(make_retriever("bm25", 0.4), BM25Retriever)
    with pytest.raises(ValueError):
        make_retriever("embedding", 0.4)                                    # needs an embed function
    with pytest.raises(ConfigError):
        make_settings(RETRIEVER="faiss")


# ====================================================================== budget
def render_for(system_chars, question):
    def render(chunks, history):
        return ([{"role": "system", "content": "s" * system_chars}] + list(history)
                + [{"role": "user", "content": "\n".join(c.text for c in chunks) + "\nQ: " + question}])
    return render


def test_budget_never_exceeds_limit_randomized():
    rng = random.Random(7)
    assert limit_for(1800, 0.1) == 1620
    for _ in range(300):
        chunks = [C(f"c{i}", "x" * rng.randint(50, 1400)) for i in range(rng.randint(0, 8))]
        history = [{"role": rng.choice(["user", "assistant"]), "content": "h" * rng.randint(10, 900)}
                   for _ in range(rng.randint(0, 6))]
        a = assemble(render_for(rng.randint(100, 800), "q" * rng.randint(5, 300)), chunks, history, 1620)
        assert estimate_tokens(a.messages) == a.tokens <= 1620 <= 1800


def test_budget_priority_chunks_before_history_newest_turns_kept():
    chunks = [C("top", "a" * 600), C("second", "b" * 600), C("third", "c" * 600)]
    history = [{"role": "user", "content": f"turn{i} " + "h" * 300} for i in range(5)]
    a = assemble(render_for(200, "question"), chunks, history, 1620)
    assert a.chunk_ids == ["top", "second", "third"]
    assert a.history_kept and a.messages[-2]["content"].startswith("turn4")   # newest turns survive
    kinds = [d["kind"] for d in a.dropped]
    assert set(kinds) == {"history"} and [d["id"] for d in a.dropped][0] == "turn-0"   # oldest dropped


def test_budget_skips_a_big_chunk_and_keeps_a_smaller_lower_ranked_one():
    a = assemble(render_for(200, "q"), [C("big", "x" * 3400), C("small", "y" * 300)], [], 1620)
    assert a.chunk_ids == ["small"] and not a.all_chunks_fit
    assert a.dropped == [{"kind": "chunk", "id": "big", "tokens": 1700, "reason": "budget"}]


def test_budget_error_when_question_alone_is_too_big():
    with pytest.raises(BudgetError):
        assemble(render_for(200, "q" * 4000), [], [], 1620)


# ====================================================================== grounded explain + compress
INDEX = None


@pytest.fixture(autouse=True, scope="module")
def _index(tmp_path_factory):
    global INDEX
    INDEX = str(tmp_path_factory.mktemp("idx") / "chunks.json")
    save_chunks(INDEX, chunk_dir("data/docs"))


def is_compress(body):
    return body["messages"][0]["content"].startswith("You select evidence")


def engine_with(vt, fn, **env):
    s = make_settings(**{"RAG_INDEX_PATH": INDEX, **env})
    fake, sink = FnProvider(fn), MemorySink()
    eng = Engine.create(s, client=LLMClient(s, fake), sink=sink, cache=ExactCache(":memory:", s.cache_ttl_s),
                        clock=vt.clock, sleep=vt.sleep, rng=random.Random(1))
    return eng, fake, sink


async def test_explain_grounded_preferred_path(vt):
    eng, fake, sink = engine_with(vt, lambda b: chat_ok("Le nombre dérivé est la pente de la tangente."))
    r = await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?"}, {"level": "lycée"})
    assert (r.model_class_used, r.degraded) == ("midsize", False)
    assert r.event_names == ["retrieving", "checking_cache", "partial_result", "queued", "generating"]
    sent = fake.requests[0]["body"]["messages"]
    assert "Course material:" in sent[-1]["content"] and "y = f'(a)(x − a) + f(a)" in sent[-1]["content"]
    assert "Write the explanation in French." in sent[0]["content"] and "lycée" in sent[0]["content"]
    assert r.notes["sources"][0] == "derivee-definition.fr#interpretation-graphique-la-tangente"
    assert estimate_tokens(sent) <= eng.limit
    r2 = await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?"}, {"level": "lycée"})
    assert r2.from_cache and r2.event_names == ["retrieving", "checking_cache", "cache_hit"]


async def test_explain_nothing_relevant_says_so_without_a_request(vt):
    eng, fake, _ = engine_with(vt, lambda b: chat_ok("invented"))
    r = await eng.run("explain", {"question": "Qui a gagné le match de foot hier ?"})
    assert fake.requests == [] and r.text.startswith("Je n'ai pas trouvé") and r.model_class_used is None
    assert r.notes == {"retrieval": "no_relevant_chunks"} and r.event_names == ["retrieving", "no_relevant_context"]


async def test_explain_cache_key_includes_retrieved_chunk_ids(vt):
    eng, fake, _ = engine_with(vt, lambda b: chat_ok("ok"))
    q = {"question": "Comment trouver l'équation de la tangente ?"}
    await eng.run("explain", q)
    extra = C("new#tangente", "Trouver l'équation de la tangente : équation de la tangente, tangente.")
    await eng.retriever.index([*eng.retriever.chunks, extra])               # same question, other chunks
    r = await eng.run("explain", q)
    assert not r.from_cache and len(fake.requests) == 2


async def test_explain_compresses_when_chunks_do_not_fit(vt):
    def fn(body):
        return chat_ok(json.dumps({"keep": [1, 2]})) if is_compress(body) else chat_ok("Explication courte.")
    eng, fake, sink = engine_with(vt, fn, INPUT_TOKEN_BUDGET=650)          # top chunks no longer fit
    r = await eng.run("explain", {"question": "Quelle est l'erreur avec la dérivée d'une constante, 2x + 7 ?"})
    names = r.event_names
    assert names.index("retrieving") < names.index("compressing") < names.index("generating")
    assert r.notes["compress"]["method"] == "llm" and r.notes["compress"]["sentences"] == 2
    compress_req, explain_req = fake.requests
    assert is_compress(compress_req["body"]) and compress_req["body"]["model"] == "compact-a"
    for req in fake.requests:
        assert estimate_tokens(req["body"]["messages"]) <= eng.limit <= 650
    material = explain_req["body"]["messages"][-1]["content"]
    retrieved = [x["id"] for x in r.notes["retrieved"]]
    assert r.notes["sources"][0] == retrieved[0]                           # sentences 1-2: the top chunk
    assert r.notes["compress"]["restored"] == retrieved[1:]                 # the others keep one sentence each
    assert material.count("Erreurs fréquentes") <= len(retrieved)
    assert r.tokens_in > 0 and {x.task for x in sink.records if x.kind == "run"} == {"compress", "explain"}


async def test_compress_invalid_output_falls_back_to_lexical_selection(vt):
    def fn(body):
        return chat_ok("I think sentences one and two") if is_compress(body) else chat_ok("ok")
    eng, _, _ = engine_with(vt, fn, INPUT_TOKEN_BUDGET=650)
    r = await eng.run("explain", {"question": "Quelle est l'erreur avec la dérivée d'une constante, 2x + 7 ?"})
    assert r.notes["compress"]["method"] == "lexical" and r.data["body"] == "ok"


async def test_compress_task_directly_is_extractive(vt):
    eng, _, _ = engine_with(vt, lambda b: chat_ok('{"keep": ["2", 9, 2]}'))
    chunks = [{"chunk_id": "a", "text": "H — S\nLa dérivée d'une constante est 0. Le ciel est bleu."}]
    r = await eng.run("compress", {"question": "dérivée constante", "chunks": chunks})
    assert r.data["keep"] == [2] and r.data["sentences"] == [{"chunk_id": "a", "text": "Le ciel est bleu."}]


async def test_explain_history_trimmed_before_chunks(vt):
    eng, fake, _ = engine_with(vt, lambda b: chat_ok("ok"))
    history = [{"role": "user", "content": f"old {i} " + "blabla " * 120} for i in range(6)]
    r = await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?", "history": history})
    kinds = {d["kind"] for d in r.notes["dropped"]}
    assert kinds == {"history"} and estimate_tokens(fake.requests[0]["body"]["messages"]) <= eng.limit


async def test_compress_never_drops_a_relevant_chunk_entirely(vt):
    """The model keeps sentences from one chunk only; every other retrieved chunk gets its best one back."""
    def fn(body):
        return chat_ok(json.dumps({"keep": [1]})) if is_compress(body) else chat_ok("ok")
    eng, fake, _ = engine_with(vt, fn, INPUT_TOKEN_BUDGET=700)
    r = await eng.run("explain", {"question": "Quelles sont les erreurs fréquentes quand on dérive une puissance, "
                                              "une constante ou un produit ?"})
    retrieved = [x["id"] for x in r.notes["retrieved"]]
    assert len(retrieved) >= 2 and r.notes["compress"]["restored"]
    assert set(r.notes["sources"]) == set(retrieved)
    assert estimate_tokens(fake.requests[-1]["body"]["messages"]) <= eng.limit


# ====================================================================== glossary expansion
from rag.glossary import Glossary   # noqa: E402

GLOSSARY = Glossary.load("data/glossary.yaml")


def test_glossary_loads_and_rejects_ambiguous_variants(tmp_path):
    assert GLOSSARY.entries >= 55 and GLOSSARY.variants >= 200 and GLOSSARY.topic == "derivative"
    bad = tmp_path / "g.yaml"
    bad.write_text("terms:\n  - fr: [tangente]\n    variants: [mamas]\n  - fr: [pente]\n    variants: [mamas]\n")
    with pytest.raises(ValueError):
        Glossary.load(str(bad))


@pytest.mark.parametrize("question,terms", [
    ("كيفاش نكتب معادلة المماس؟", ("équation", "tangente")),                 # Darija, Arabic letters
    ("ما هو تعريف العدد المشتق؟", ("définition", "dérivée")),               # MSA, multi-word, longest first
    ("kifech nekteb mo3adlet el mamas?", ("équation", "tangente")),          # Arabizi, construct form
    ("chnowa mochtaqa mta3 jather x?", ("dérivée", "racine carrée")),
    ("What is the slope of the tangent line?", ("pente", "coefficient directeur")),   # tangent = tangente (stem)
    ("Comment trouver la tangente ?", ()),                                   # already course words
    ("car je ne comprends pas", ()),                                         # French "car" = because
    ("comment installer python sur windows", ()),
])
def test_glossary_expansion_per_script(question, terms):
    assert GLOSSARY.expand(question).terms == terms


async def test_glossary_terms_weigh_less_than_question_terms():
    r = BM25Retriever(glossary_weight=0.5)
    await r.index(CORPUS)
    w = r.weights("la tangente constant", GLOSSARY.expand("la tangente constant"))
    assert w["tangent"] == 1.0 and w["constant"] == 1.0          # stems: "constant" is now a question term too
    w2 = r.weights("la tangente", GLOSSARY.expand("la tangente slope"))
    assert w2["tangent"] == 1.0 and w2["pent"] == 0.5


async def test_glossary_rescues_a_cross_language_question():
    r = BM25Retriever(min_coverage=0.4)
    await r.index([c for c in chunk_dir("data/docs") if c.language == "FRENCH"])
    q = "كيفاش نكتب معادلة المماس؟"                                 # Darija; the course chunk is French only
    assert await r.search(q, 3) == []
    hits = await r.search(q, 3, expansion=GLOSSARY.expand(q))
    assert hits and hits[0].chunk.chunk_id == "derivee-definition.fr#interpretation-graphique-la-tangente"


async def test_glossary_cannot_open_the_gate_for_an_off_topic_question():
    r = BM25Retriever(min_coverage=0.4)
    await r.index(chunk_dir("data/docs"))
    q = "what is the derivative of my business revenue strategy"
    assert await r.search(q, 3, expansion=GLOSSARY.expand(q)) == []


async def test_glossary_keeps_english_questions_on_the_english_doc():
    r = BM25Retriever(min_coverage=0.4)
    await r.index(chunk_dir("data/docs"))
    q = "When does a zero of the derivative give a local maximum?"
    hits = await r.search(q, 1, expansion=GLOSSARY.expand(q))
    assert hits[0].chunk.chunk_id == "derivative-variations.en#finding-a-maximum-or-a-minimum"


async def test_explain_applies_the_glossary(vt):
    eng, fake, _ = engine_with(vt, lambda b: chat_ok("ok"))
    r = await eng.run("explain", {"question": "كيفاش نكتب معادلة المماس؟"})
    assert r.notes["sources"][0] == "derivee-definition.fr#interpretation-graphique-la-tangente"
    assert r.notes["glossary"] == ["équation", "tangente"]
    assert r.event_names[:2] == ["retrieving", "expanding_query"]
    eng2, _, _ = engine_with(vt, lambda b: chat_ok("ok"), GLOSSARY_PATH="")
    r2 = await eng2.run("explain", {"question": "كيفاش نكتب معادلة المماس؟"})
    assert r2.notes["retrieval"] == "no_relevant_chunks" and "sources" not in r2.notes    # without it: not found


async def test_explain_max_tokens_capped_and_env_configurable(vt):
    eng, fake, _ = engine_with(vt, lambda b: chat_ok("ok"))
    await eng.run("explain", {"question": "Comment trouver l'équation de la tangente ?"})
    assert fake.requests[-1]["body"]["max_tokens"] == 350              # MAX_TOKENS_MIDSIZE is 550
    eng2, fake2, _ = engine_with(vt, lambda b: chat_ok("ok"), EXPLAIN_MAX_TOKENS=200)
    await eng2.run("explain", {"question": "Comment trouver l'équation de la tangente ?"})
    assert fake2.requests[-1]["body"]["max_tokens"] == 200


def test_draft_docs_are_not_indexed_until_reviewed():
    skipped = []
    published = chunk_dir("data/docs", skipped=skipped)
    assert {"derivee-definition.en.md", "limite.fr.md"} <= set(skipped)
    assert not any(c.source in skipped for c in published)
    drafts = [c for c in chunk_dir("data/docs", include_drafts=True) if c.source in skipped]
    assert drafts and all(c.concept and c.tokens <= 300 for c in drafts)
    from engine.exercises import ExerciseBank
    concepts = {e.concept for e in ExerciseBank.load("data/exercises/derivees.yaml").exercises}
    assert {c.concept for c in drafts} - concepts == {"limite"}          # bank needs limite exercises first


def test_draft_formulas_are_mathematically_consistent():
    """The drafts' own derivative claims pass the engine's formula checker (no wrong math)."""
    from engine.grounding import check_formulas
    for c in chunk_dir("data/docs", include_drafts=True):
        if c.source in ("derivee-definition.en.md", "limite.fr.md"):
            assert check_formulas(c.text, [c.text])["wrong"] == [], c.chunk_id
