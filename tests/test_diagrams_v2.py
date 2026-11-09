import copy
import sys
from pathlib import Path
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "app"))
from diagrams import (Diagram, build_concept_map, build_from_text, build_function_graph, build_variation_table,
                      check_table, solution_of)

COURSE = Path(__file__).resolve().parent / "fixtures" / "diagrams_course.yaml"
REFS = ["x²", "x³ − 3x", "1/x", "(x² − 1)/(x − 1)", "(x + 1)/(x − 2)", "√x", "ln x", "x·e⁻ˣ"]


def test_version_2_and_v1_still_valid():
    g = build_function_graph("x³ − 3x")
    assert g["version"] == 2
    old = {k: v for k, v in g.items() if k != "version"}
    old["version"] = 1
    for k in ("derivative_samples", "tangent_at"):
        old["payload"].pop(k, None)
    for q in old["payload"]["points"]:
        q.pop("kind", None)
    Diagram.model_validate(old)  # a version 1 spec keeps validating


def test_auto_range_shows_extrema():
    p = build_function_graph("x³ − 3x")["payload"]
    assert p["x_range"] == [-2.5, 2.5] and p["y_range"][1] <= 20
    kinds = {q["kind"]: (q["x"], q["y"]) for q in p["points"] if q.get("kind") in ("max", "min")}
    assert kinds == {"max": (-1.0, 2.0), "min": (1.0, -2.0)}


def test_samples_contain_critical_points_exactly():
    s = build_function_graph("x³ − 3x")["payload"]["derivative_samples"]
    for c in (-1.0, 1.0):
        i = s["x"].index(c)
        assert s["f"][i] == pytest.approx(c ** 3 - 3 * c)
        assert s["df"][i] == pytest.approx(0, abs=1e-9)


def test_samples_null_at_asymptote_and_non_differentiable():
    s = build_function_graph("1/x")["payload"]["derivative_samples"]
    assert any(v is None for v in s["f"])
    r = build_function_graph("√x")["payload"]["derivative_samples"]
    i = r["x"].index(0.0) if 0.0 in r["x"] else None
    assert all(r["df"][j] is None for j in range(len(r["x"])) if r["x"][j] < 0)


def test_focus_applies():
    nodes = {n["id"] for n in build_from_text("carte des notions autour de produit", COURSE)["payload"]["nodes"]}
    full = {n["id"] for n in build_concept_map(COURSE)["payload"]["nodes"]}
    assert "produit" in nodes and nodes < full and "tangente" not in nodes
    assert build_from_text("concept map around product rule", COURSE)["payload"]["focus"] == "produit"


def test_no_mastery_means_no_mastery_field():
    assert all("mastery" not in n for n in build_concept_map(COURSE)["payload"]["nodes"])


@pytest.mark.parametrize("e", REFS)
def test_practice_correct_filling_passes(e):
    s = build_variation_table(e, mode="practice")
    assert s["payload"]["mode"] == "practice"
    assert check_table(s, solution_of(s)) == {"correct": True, "mistakes": []}


def test_equivalent_notations_pass():
    s = build_variation_table("x³ − 3x", mode="practice")
    a = solution_of(s)
    a["vals"][1]["f"], a["vals"][2]["f"] = "2.0", "−2.0"
    a["columns"][1] = "-1.0"
    assert check_table(s, a)["correct"]


def _mutations(s):
    a = solution_of(s)
    for i in range(len(a["columns"])):
        b = copy.deepcopy(a); b["columns"][i] = "12345"; yield b, "x", i
    for k, v in enumerate(a["signs"]):
        b = copy.deepcopy(a); b["signs"][k] = {"+": "-", "-": "+", "0": "+", "||": "0"}[v]; yield b, "f_prime", k
    for j, v in enumerate(a["trends"]):
        b = copy.deepcopy(a); b["trends"][j] = "down" if v == "up" else "up"; yield b, "arrows", j
    for i, v in enumerate(s["payload"]["values"]):
        b = copy.deepcopy(a); b["vals"][i]["f"] = "12345"; yield b, "f", i
        if v.get("f_right") is not None:
            b = copy.deepcopy(a); b["vals"][i]["f_right"] = "12345"; yield b, "f_right", i


@pytest.mark.parametrize("e", REFS)
def test_each_wrong_cell_reported_once_at_right_place(e):
    s = build_variation_table(e, mode="practice")
    for ans, row, col in _mutations(s):
        r = check_table(s, ans)
        assert not r["correct"]
        assert [(m["row"], m["column"]) for m in r["mistakes"]] == [(row, col)]


def test_empty_answer_reports_everything():
    s = build_variation_table("x³ − 3x", mode="practice")
    assert len(check_table(s, {})["mistakes"]) == 4 + 5 + 3 + 4


def test_practice_from_text():
    assert build_from_text("exercice tableau de variations de x³ − 3x", COURSE)["payload"]["mode"] == "practice"
    assert "mode" not in build_from_text("tableau de variations de x³ − 3x", COURSE)["payload"]
