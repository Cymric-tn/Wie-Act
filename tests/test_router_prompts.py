import pytest

from config import ModelClass
from engine.prompts import PromptError, parse_prompt
from engine.tasks import Classification, Registry, TaskSpec, default_registry, reveals_answer
from metrics import MemorySink
from router.policy import ROUTES, Router, escalated

C, M, V = ModelClass.COMPACT, ModelClass.MIDSIZE, ModelClass.VISION


# ------------------------------------------------------------------ router
def test_routing_table_matches_claude_md():
    assert {t: r.default for t, r in ROUTES.items()} == {
        "classify": C, "hint": C, "grade": C, "compress": C, "summary": C, "quiz": C, "explain": M,
        "ocr_transcribe": V, "check_claim": C, "check_answer": C, "prep_plan": C}
    assert [t for t, r in ROUTES.items() if r.fallback] == ["explain"]


@pytest.mark.parametrize("ctx,exp", [({}, False), ({"escalate": True}, True), ({"complexity": 4}, True),
                                     ({"complexity": 3}, False), ({"complexity": "5"}, False)])
def test_escalation_rule(ctx, exp):
    assert escalated(ctx) is exp


def test_every_decision_logged_with_reason():
    sink = MemorySink()
    r = Router(sink, quota_low_threshold=2)
    d = r.decide("hint", {}, quota_tokens=20)
    assert (d.model_class, d.degraded) == (C, False)
    d2 = r.decide("explain", {}, quota_tokens=1)
    assert (d2.preferred, d2.model_class, d2.degrade_reason) == (M, C, "quota_low")
    d3 = r.decide("explain", {"complexity": 5}, quota_tokens=1)
    assert d3.model_class is M and "escalated" in d3.reason
    assert r.on_saturated(r.decide("hint", {}, 20)) is None
    fb = r.on_saturated(r.decide("explain", {}, 20))
    assert (fb.model_class, fb.degraded, fb.degrade_reason) == (C, True, "midsize_saturated")
    assert all(x.kind == "route" and x.detail for x in sink.records)
    assert len(sink.records) == 7                 # 5 decide + 2 on_saturated (incl. "no fallback")


def test_unknown_task_has_no_route():
    with pytest.raises(ValueError):
        Router(MemorySink(), 2).decide("translate", {}, 20)


# ------------------------------------------------------------------ registry
def test_registry_model_class_comes_from_the_routing_table():
    reg = default_registry()
    assert {s.name: s.model_class for s in reg} == {"classify": C, "hint": C, "compress": C, "explain": M,
                                                    "quiz": C, "grade": C, "ocr_transcribe": V, "check_claim": C,
                                                        "check_answer": C, "prep_plan": C}
    assert reg.get("classify").output_schema is Classification and reg.get("classify").max_tokens == 80
    with pytest.raises(ValueError):
        Registry([TaskSpec(name="translate", prompt="x", max_tokens=10)])


# ------------------------------------------------------------------ prompts
def test_prompt_needs_version_header_and_system():
    with pytest.raises(PromptError):
        parse_prompt("## system\nhi")
    with pytest.raises(PromptError):
        parse_prompt("---\ntask: t\n---\n## system\nhi")
    with pytest.raises(PromptError):
        parse_prompt("---\ntask: t\nversion: 1\n---\n## user\nhi")


def test_prompt_sections_render_and_fields():
    p = parse_prompt("---\ntask: t\nversion: 3\n---\n## system\nLevel $level {json}\n## example.FR\n"
                     "problem: a: b\nhint: c\n")
    assert p.id == "t@v3" and p.render("system", level="x") == "Level x {json}"
    assert p.fields("example.FR") == {"problem": "a: b", "hint": "c"}
    with pytest.raises(PromptError):
        p.render("system")


# ------------------------------------------------------------------ hint answer guard
@pytest.mark.parametrize("hint,solution,leak", [
    ("Que se passe-t-il si x = 3 ?", "x = -3", False),
    ("La réponse est x = -3.", "x = -3", True),
    ("Donc x=-3", "-3", True),
    ("Remplace x par 3 dans 2x + 6.", "-3", False),
    ("Pense au discriminant.", None, False),
])
def test_reveals_answer(hint, solution, leak):
    assert reveals_answer(hint, solution) is leak
