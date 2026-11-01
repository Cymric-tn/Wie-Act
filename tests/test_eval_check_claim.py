"""scripts/eval_check_claim.py: the runner and its summary, against a fake provider (no LLM)."""
import json

from scripts.eval_check_claim import run, summarize
from tests.conftest import chat_ok
from tests.test_engine import _index, make_engine  # noqa: F401  (_index: the autouse course index)


def test_summarize_counts_accuracy_per_slice_and_the_confusion_matrix():
    rows = [{"language": "FRENCH", "expected": "SUPPORTED", "got": "SUPPORTED", "checked_by": "llm", "ok": True,
             "requests": 1, "degraded": False},
            {"language": "FRENCH", "expected": "SUPPORTED", "got": "PARTLY", "checked_by": "llm", "ok": False,
             "requests": 1, "degraded": True},
            {"language": "ARABIC", "expected": "NOT_IN_COURSE", "got": "NOT_IN_COURSE", "checked_by": "rules",
             "ok": True, "requests": 0, "degraded": False}]
    s = summarize(rows)
    assert s["overall"] == {"n": 3, "correct": 2, "accuracy": 0.667}
    assert s["by_language"]["FRENCH"]["accuracy"] == 0.5 and s["by_checked_by"]["rules"]["accuracy"] == 1.0
    assert s["confusion"]["SUPPORTED"] == {"SUPPORTED": 1, "PARTLY": 1}
    assert s["requests"] == 2 and s["degraded"] == 1


async def test_the_runner_counts_requests_and_sympy_claims_cost_none(vt, tmp_path):
    p = tmp_path / "set.jsonl"
    p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in [
        {"id": "a", "language": "FRENCH", "claim": "(x²)' = x", "expected": "CONTRADICTED", "route": "sympy"},
        {"id": "b", "language": "FRENCH", "claim": "La dérivée d'une constante est 0.", "expected": "SUPPORTED",
         "route": "llm"},
        {"id": "c", "language": "FRENCH", "claim": "La photosynthèse transforme la lumière.", "expected": "NOT_IN_COURSE",
         "route": "rules"}]), encoding="utf-8")
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(json.dumps({"verdict": "SUPPORTED", "correction": ""})))
    rows = await run(str(p), eng)
    assert [r["got"] for r in rows] == ["CONTRADICTED", "SUPPORTED", "NOT_IN_COURSE"] and all(r["ok"] for r in rows)
    assert [r["requests"] for r in rows] == [0, 1, 0] and len(fake.requests) == 1
    assert [r["checked_by"] for r in rows] == ["sympy", "llm", "rules"]
