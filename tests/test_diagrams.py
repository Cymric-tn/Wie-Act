import sys
from pathlib import Path
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "app"))
from diagrams import (DiagramError, ParseError, build_concept_map, build_function_graph, build_from_text,
                      build_variation_table, detect_request, parse)

COURSE = Path(__file__).resolve().parent / "fixtures" / "diagrams_course.yaml"
REFS = ["x²", "x³ − 3x", "1/x", "(x² − 1)/(x − 1)", "(x + 1)/(x − 2)", "√x", "ln x", "x·e⁻ˣ"]


@pytest.mark.parametrize("bad", ['__import__("os")', "lambda x: x", "os.system('ls')", "9**9**9", "x" * 81, ""])
def test_injection_refused(bad):
    with pytest.raises(ParseError):
        parse(bad)


@pytest.mark.parametrize("ok", ["x²", "2x", "x(x+1)", "√x", "2×x", "2·x", "2,5x", "f(x) = x^2", "sin x", "x·e⁻ˣ"])
def test_notations_accepted(ok):
    parse(ok)


@pytest.mark.parametrize("e", REFS)
def test_graphs_build(e):
    g = build_function_graph(e)
    assert g["type"] == "function_graph" and g["payload"]["curves"][0]["segments"]


def test_no_line_crosses_asymptote():
    for e, a in (("1/x", 0), ("(x+1)/(x-2)", 2)):
        for seg in build_function_graph(e)["payload"]["curves"][0]["segments"]:
            xs = [p[0] for p in seg]
            assert not (min(xs) < a < max(xs))


def test_hole_is_open_point():
    pts = build_function_graph("(x²−1)/(x−1)")["payload"]["points"]
    assert pts == [{"x": 1.0, "y": 2.0, "label": "(1, 2)", "open": True}]


def test_domains_respected():
    assert min(p[0] for s in build_function_graph("√x")["payload"]["curves"][0]["segments"] for p in s) >= 0
    assert min(p[0] for s in build_function_graph("ln x")["payload"]["curves"][0]["segments"] for p in s) > 0


def test_tangent_exact_and_refused():
    g = build_function_graph("x²", tangent_at=1)
    assert g["payload"]["curves"][-1]["label"] == "y = 2x - 1"
    with pytest.raises(DiagramError):
        build_function_graph("√x", tangent_at=0)


def _t(e, **k):
    p = build_variation_table(e, **k)["payload"]
    return p["columns"], p["derivative_signs"], [v["f"] for v in p["values"]], p["trends"]


def test_variation_fixtures():
    assert _t("x³ − 3x") == (["-∞", "-1", "1", "+∞"], ["+", "0", "-", "0", "+"], ["-∞", "2", "-2", "+∞"], ["up", "down", "up"])
    assert _t("1/x")[:2] == (["-∞", "0", "+∞"], ["-", "||", "-"])
    assert _t("(x + 1)/(x − 2)")[3] == ["down", "down"]
    assert _t("(x² − 1)/(x − 1)")[2] == ["-∞", "2", "+∞"]
    assert _t("ln x")[2] == ["-∞", "+∞"] and _t("√x")[2] == ["0", "+∞"]
    assert _t("x·e⁻ˣ")[2] == ["-∞", "e^(-1)", "0"]
    assert _t("sin x", domain=["0", "2pi"])[0] == ["0", "π/2", "3π/2", "2π"]


def test_periodic_refused():
    with pytest.raises(DiagramError):
        build_variation_table("sin x")


def test_concept_map_focus():
    ids = {n["id"] for n in build_concept_map(COURSE, focus="produit")["payload"]["nodes"]}
    assert {"produit", "puissance", "somme", "derivee", "quotient"} <= ids and "tangente" not in ids
    m = build_concept_map(COURSE, mastery={"produit": 0.4})["payload"]["nodes"]
    assert [n["mastery"] for n in m if n["id"] == "produit"] == [0.4]


SENTENCES = [
    ("courbe de x³ − 3x et sa tangente en 1", "function_graph"), ("trace la courbe de x^2", "function_graph"),
    ("graphe de 1/x", "function_graph"), ("plot x^2 and its derivative", "function_graph"),
    ("draw the graph of ln x", "function_graph"), ("tableau de variations de x^3-3x", "variation_table"),
    ("variations de (x+1)/(x-2)", "variation_table"), ("variation table of sqrt(x)", "variation_table"),
    ("carte des notions", "concept_map"), ("concept map around product rule", "concept_map"),
    ("ارسم منحنى x^2", "function_graph"), ("جدول التغيرات x^3-3x", "variation_table"), ("خريطة المفاهيم", "concept_map"),
    ("rsem x^2", "function_graph"), ("jadwal tagayyorat dial x^3-3x", "variation_table"), ("khartat dial lmafahim", "concept_map"),
    ("bonjour", None),
]


def test_detection_accuracy():
    ok = sum(detect_request(s, COURSE)["type"] == t for s, t in SENTENCES)
    assert ok / len(SENTENCES) >= 0.9


def test_build_from_text():
    assert build_from_text("courbe de 2,5x² et sa tangente en 1", COURSE)["payload"]["tangent_at"] == 1
    with pytest.raises(DiagramError):
        build_from_text("bonjour", COURSE)
