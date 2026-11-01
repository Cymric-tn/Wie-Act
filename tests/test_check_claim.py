"""Task check_claim: sympy first, then the course (coverage gate), then one compact JSON call."""
import json

import pytest

from engine.result import Result
from rag.chunker import Chunk
from tests.conftest import chat_ok
from tests.test_engine import _index, make_engine, runs  # noqa: F401  (_index: the autouse course index)
from tests.test_retry_v11 import quota_429


def verdict(v="CONTRADICTED", c="La dérivée de x³ est 3x²."):
    return lambda body: chat_ok(json.dumps({"verdict": v, "correction": c}), 400, 30)


PRODUCT = "La dérivée d'un produit est le produit des dérivées."


# ------------------------------------------------------------------ no request at all
@pytest.mark.parametrize("claim,verdict_,by,lang", [
    ("(x²)' = x", "CONTRADICTED", "sympy", "FRENCH"), ("(x³)' = 3x²", "SUPPORTED", "sympy", "FRENCH"),
    ("Pour f(x) = x² + 7, on a f'(x) = 2x + 7.", "CONTRADICTED", "sympy", "FRENCH"),
    ("(x²)' = 2x and (x³)' = 2x²", "PARTLY", "sympy", "ENGLISH"), ("نعلم أن (x²)' = 2x", "SUPPORTED", "sympy", "ARABIC")])
async def test_formula_claims_are_settled_by_sympy_with_no_request(vt, claim, verdict_, by, lang):
    eng, fake, sink = make_engine(vt, verdict())
    r = await eng.run("check_claim", {"claim": claim}, {"language": lang})
    assert isinstance(r, Result) and not r.degraded and r.data["verdict"] == verdict_ and r.data["checked_by"] == by
    assert set(r.data) == {"verdict", "correction", "source_chunk_id", "checked_by"}
    assert fake.requests == [] and r.model_class_used is None and (r.tokens_in, r.tokens_out) == (0, 0)
    assert r.event_names == ["answered_by_rules"]
    assert (verdict_ == "SUPPORTED") == (r.data["correction"] == "") and len(r.data["correction"].split()) <= 25


async def test_the_correct_form_is_stated_in_the_learner_s_language(vt):
    eng, _, _ = make_engine(vt, verdict())
    fr = await eng.run("check_claim", {"claim": "(x³)' = x²"}, {"language": "FRENCH"})
    en = await eng.run("check_claim", {"claim": "(x³)' = x²"}, {"language": "ENGLISH"})
    ar = await eng.run("check_claim", {"claim": "(x³)' = x²"}, {"language": "ARABIC"})
    assert fr.data["correction"] == "Plutôt : (x³)' = 3x²" and en.data["correction"] == "Rather: (x³)' = 3x²"
    assert ar.data["correction"].startswith("الصحيح:") and "3x²" in ar.data["correction"]


async def test_nothing_in_the_course_is_not_in_course_with_no_request(vt):
    eng, fake, _ = make_engine(vt, verdict())
    r = await eng.run("check_claim", {"claim": "La photosynthèse transforme la lumière en énergie chimique."})
    assert r.data == {"verdict": "NOT_IN_COURSE", "correction": "", "source_chunk_id": None, "checked_by": "rules"}
    assert fake.requests == [] and not r.degraded and r.event_names == ["retrieving", "no_relevant_context"]


# ------------------------------------------------------------------ the model path
async def test_one_compact_json_call_with_the_claim_and_the_top_chunk(vt):
    eng, fake, sink = make_engine(vt, verdict("CONTRADICTED", "(u·v)' = u'·v + u·v'."))
    r = await eng.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH", "learner_id": "L1"})
    assert r.data == {"verdict": "CONTRADICTED", "correction": "(u·v)' = u'·v + u·v'.",
                      "source_chunk_id": r.data["source_chunk_id"], "checked_by": "llm"}
    assert r.data["source_chunk_id"].split("#")[0] in ("derivee-erreurs.fr", "derivee-formules.fr")
    assert r.model_class_used == "compact" and len(fake.requests) == 1 and not r.degraded
    body = fake.requests[0]["body"]
    assert body["model"] == "compact-a" and body["response_format"] == {"type": "json_object"}
    user = body["messages"][-1]["content"]
    assert PRODUCT in user and "(u·v)'" in user and "Excerpt:" in user
    assert "Write the correction in French" in body["messages"][0]["content"]
    assert r.event_names == ["retrieving", "expanding_query", "checking_cache", "queued", "generating"] or \
        r.event_names[:1] == ["retrieving"] and r.event_names[-2:] == ["queued", "generating"]
    assert "partial_result" not in r.event_names
    assert runs(sink)[-1].learner_id == "L1"


async def test_the_correction_is_cut_to_25_words_and_cleared_when_nothing_is_wrong(vt):
    eng, _, _ = make_engine(vt, verdict("PARTLY", " ".join(f"mot{i}" for i in range(40))))
    r = await eng.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH"})
    assert r.data["verdict"] == "PARTLY" and len(r.data["correction"].split()) == 25
    eng2, _, _ = make_engine(vt, verdict("SUPPORTED", "Bravo, c'est exact."))
    r2 = await eng2.run("check_claim", {"claim": "La dérivée d'une constante est 0."}, {"language": "FRENCH"})
    assert r2.data["verdict"] == "SUPPORTED" and r2.data["correction"] == ""


async def test_a_correction_whose_formula_sympy_refutes_is_dropped(vt):
    eng, _, _ = make_engine(vt, verdict("CONTRADICTED", "En fait (x²)' = 3x."))
    r = await eng.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH"})
    assert r.data["verdict"] == "CONTRADICTED" and r.data["correction"] == ""
    assert r.notes["correction_dropped"] == "formula refuted by sympy"


async def test_lowercase_and_spaced_verdicts_are_normalized_without_a_repair_call(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok('```json\n{"verdict": "not in course", "correction": ""}\n```'))
    r = await eng.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH"})
    assert r.data["verdict"] == "NOT_IN_COURSE" and not r.repaired and len(fake.requests) == 1


@pytest.mark.parametrize("claim", ["(x²)' = 2x et la dérivée d'une constante est 1", "(x²)' = 2x, donc la dérivée est toujours positive"])
def test_a_formula_with_content_words_around_it_is_never_declared_supported_by_sympy(claim):
    from engine import claims
    assert claims.decide(claims.analyse(claim), "FRENCH") is None


@pytest.mark.parametrize("claim", ["f(x) = x² donc f'(x) = 2x", "so (x²)' = 2x", "(x²)' = 2x", "Exemple : (x²)' = 2x"])
def test_connectives_do_not_stop_sympy_from_deciding(claim):
    from engine import claims
    assert claims.decide(claims.analyse(claim), "FRENCH")["verdict"] == "SUPPORTED"


async def test_a_claim_sympy_only_half_checks_goes_to_the_model_with_what_was_verified(vt):
    eng, fake, _ = make_engine(vt, verdict("PARTLY", "La dérivée n'est pas toujours positive."))
    claim = "(x²)' = 2x et la dérivée d'une constante est 1"          # one true formula, one false claim sympy cannot see
    r = await eng.run("check_claim", {"claim": claim}, {"language": "FRENCH"})
    assert r.data["checked_by"] == "llm" and len(fake.requests) == 1
    assert "Already verified by computation: (x²)' = 2x" in fake.requests[0]["body"]["messages"][-1]["content"]


# ------------------------------------------------------------------ repair ladder, safe default, cache
async def test_garbled_json_is_repaired_once_then_falls_back_to_partly_degraded(vt):
    replies = iter([chat_ok("je pense que c'est faux"), chat_ok(json.dumps({"verdict": "CONTRADICTED", "correction": ""}))])
    eng, fake, _ = make_engine(vt, lambda b: next(replies))
    r = await eng.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH"})
    assert r.repaired and not r.safe_default and r.data["verdict"] == "CONTRADICTED" and len(fake.requests) == 2
    assert "repairing_json" in r.event_names

    eng2, fake2, _ = make_engine(vt, lambda b: chat_ok("aucune idée"))
    bad = await eng2.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH"})
    assert bad.safe_default and bad.degraded and bad.degrade_reason == "json_invalid"
    assert bad.data["verdict"] == "PARTLY" and bad.data["checked_by"] == "llm" and bad.data["correction"] == ""
    assert bad.data["source_chunk_id"] and len(fake2.requests) == 2
    again = await eng2.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH"})
    assert not again.from_cache and len(fake2.requests) == 4                    # a guess is never cached


async def test_the_cache_key_is_the_claim_plus_the_chunk_id(tmp_path, vt):
    from tests.test_retrieval_dynamic import make_engine as dyn_engine
    eng, s = dyn_engine(tmp_path, vt, verdict("CONTRADICTED", "(u·v)' = u'·v + u·v'."))
    fake = eng.caller.client.transport
    a = await eng.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH", "learner_id": "L1"})
    b = await eng.run("check_claim", {"claim": "  " + PRODUCT + " "}, {"language": "FRENCH", "learner_id": "L2"})
    assert b.from_cache and b.data == a.data and len(fake.requests) == 1        # whitespace and learner do not matter
    # the same claim with a different best chunk (a professor's handout wins retrieval) is a new key
    await eng.add_material([Chunk("prof#1", "Polycopié — Produit\nLa dérivée d'un produit n'est pas le produit des "
                                  "dérivées : (u·v)' = u'·v + u·v'. Le produit des dérivées est une erreur classique.",
                                  "derivative", "beginner", "FRENCH", "prof.md", origin="professor",
                                  course_id="analyse", visibility="public", owner="prof-7")])
    c = await eng.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH", "course_id": "analyse"})
    assert c.data["source_chunk_id"] == "prof#1" and not c.from_cache and len(fake.requests) == 2


# ------------------------------------------------------------------ languages, failure
async def test_an_arabic_claim_is_checked_against_the_arabic_course(vt):
    eng, fake, _ = make_engine(vt, verdict("CONTRADICTED", "مشتقة x² هي 2x."))
    r = await eng.run("check_claim", {"claim": "مشتقة الدالة x² هي x."}, {"language": "ARABIC"})
    assert r.data["source_chunk_id"].startswith("mochtaqa.ar#") and r.data["correction"] == "مشتقة x² هي 2x."
    assert "Modern Standard Arabic" in fake.requests[0]["body"]["messages"][0]["content"]


async def test_quota_exhausted_gives_the_safe_default_and_an_eta(vt):
    eng, fake, _ = make_engine(vt, lambda b: quota_429(60))
    r = await eng.run("check_claim", {"claim": PRODUCT}, {"language": "FRENCH"})
    assert r.degraded and r.degrade_reason == "quota_exhausted" and r.eta_seconds == 60
    assert r.data["verdict"] == "PARTLY" and r.data["checked_by"] == "llm" and r.safe_default


# ------------------------------------------------------------------ the eval set
def eval_rows():
    return [json.loads(line) for line in open("tests/eval/claims_set.jsonl", encoding="utf-8") if line.strip()]


def test_the_claim_eval_set_has_30_claims_in_three_languages_and_every_verdict():
    rows = eval_rows()
    assert len(rows) == 30 and len({r["id"] for r in rows}) == 30
    assert {l: sum(r["language"] == l for r in rows) for l in ("FRENCH", "ENGLISH", "ARABIC")} == \
        {"FRENCH": 10, "ENGLISH": 10, "ARABIC": 10}
    assert {r["expected"] for r in rows} == {"SUPPORTED", "CONTRADICTED", "PARTLY", "NOT_IN_COURSE"}
    assert all(r["expected"] in ("SUPPORTED", "CONTRADICTED", "PARTLY", "NOT_IN_COURSE") for r in rows)


async def test_the_claims_routed_to_sympy_or_rules_are_decided_offline_with_no_request(vt):
    eng, fake, _ = make_engine(vt, lambda b: pytest.fail("a request was sent for a claim the rules decide"))
    decided = [r for r in eval_rows() if r["route"] in ("sympy", "rules")]
    assert len(decided) >= 10
    for r in decided:
        got = await eng.run("check_claim", {"claim": r["claim"]}, {"language": r["language"]})
        assert got.data["verdict"] == r["expected"], (r["id"], got.data)
        assert got.data["checked_by"] == r["route"], r["id"]
