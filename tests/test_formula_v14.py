"""v1.4 step 3: the formula check reads the parameters a, b, c, k, m; a span it could not read to the end is
unchecked (listed in notes.unchecked_spans), not ungrounded."""
import pytest

from engine.grounding import check_formulas, incomplete
from engine.mathcheck import GROUNDING_VARS, parse_expression
from tests.conftest import chat_ok
from tests.test_tutoring import engine, index  # noqa: F401  (index: a course index in tmp)

SOURCE = ["f(x) = x² + 1"]


@pytest.mark.parametrize("text", ["f(x) = x² + c", "f(x) = x² + bx + c", "Soit f(x) = ax² + bx + c avec a non nul.",
                                  "Prenons f(x) = mx + k.", "f(x) = x² + c où c est une constante."])
def test_the_new_parameters_are_read_as_symbols(text):
    c = check_formulas(text, SOURCE)
    assert c["ungrounded"] == [] and c["unchecked"] == [] and c["wrong"] == []
    assert len(c["new_examples"]) == 1                      # a function the course does not use: a new example


def test_the_character_whitelist_is_still_closed():
    assert set(GROUNDING_VARS) == set("xabchkmntuvy")
    for hostile in ("__import__('os').system('x')", "x; y", "a.b", "open(1)", "lambda: 1", "x**9999999999", "e", "i", "z"):
        assert parse_expression(hostile) is None or hostile in ("x**9999999999",), hostile


@pytest.mark.parametrize("text,span", [
    ("Soit f(x) = x² + p.", "f(x) = x² +"),                 # p is no symbol of the parser
    ("On prend f(x) = x² +", "f(x) = x² +"),                # cut by the end of the answer
    ("Alors f(x) = x² + q·x + 1.", "f(x) = x² + q·x + 1"),   # the unknown letter inside the span
])
def test_a_span_cut_at_an_unknown_letter_is_unchecked_not_ungrounded(text, span):
    c = check_formulas(text, SOURCE)
    assert c["ungrounded"] == [] and c["wrong"] == [] and span in c["unchecked"]


def test_a_complete_unsupported_formula_is_still_ungrounded():
    c = check_formulas("La dérivée se calcule comme f'(x) = lim(h → 0) [f(x + h) - f(x)]/h.", SOURCE)
    assert c["ungrounded"] and c["unchecked"] == []


def test_wrong_math_is_still_wrong_with_the_new_symbols():
    c = check_formulas("Soit f(x) = x² + c. Alors f'(x) = 3x.", SOURCE)
    assert c["wrong"] == ["f'(x) = 3x"]


def test_incomplete():
    assert incomplete("f(x) = x² +", "f(x) = x² +", "x² +")
    assert incomplete("f(x) = x² + p", "f(x) = x² +", "x² +")
    assert not incomplete("f(x) = x² + 1.", "f(x) = x² + 1", "x² + 1")


async def test_the_engine_lists_unchecked_spans_and_does_not_degrade(vt, index):
    reply = "Prenons par exemple f(x) = x² + p, une parabole décalée."
    eng, fake, _ = engine(vt, index, lambda b: chat_ok(reply))
    r = await eng.run("explain", {"question": "C'est quoi la dérivée de 1/x et de racine de x ?"})
    assert not r.degraded and r.notes["unchecked_spans"] == ["f(x) = x² +"] and "ungrounded" not in r.notes


async def test_the_old_flag_for_a_parameter_c_is_gone(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok("Par exemple f(x) = x² + c, où c est un réel."))
    r = await eng.run("explain", {"question": "C'est quoi la dérivée de 1/x et de racine de x ?"})
    assert not r.degraded and "unchecked_spans" not in r.notes


# ---------------------------------------------------------------- verbal equality (v1.4)
UPLOAD_SOURCE = ("Une asymptote oblique est une droite d'équation y = ax + b dont la courbe se rapproche quand x tend vers "
                 "l'infini. On la cherche quand f(x)/x tend vers un nombre a non nul.")
UPLOAD_ANSWER = ("Par exemple, si f(x) = x^2 + 2x, alors la limite de (x^2 + 2x)/x est égale à 2x + 2, ce qui implique "
                 "qu'une asymptote oblique de la courbe est la droite y = 2x + 2.")


def test_the_upload_answers_spoken_equality_is_wrong_math_with_its_correct_form():
    c = check_formulas(UPLOAD_ANSWER, [UPLOAD_SOURCE])
    assert c["wrong"] == ["(x^2 + 2x)/x = 2x + 2"] and c["forms"] == {"(x^2 + 2x)/x = 2x + 2": "(x^2 + 2x)/x = x + 2"}
    assert c["ungrounded"] == [] and "f(x) = x^2 + 2x" in c["new_examples"]


@pytest.mark.parametrize("text", [
    "(x^2 + 2x)/x vaut x + 2", "On a (x+1)^2 est égal à x^2 + 2x + 1, donc", "2x + 2 equals 2(x + 1) here", "(x+1)^2 is x^2 + 2x + 1",
    "(x²)' يساوي 2x", "(x² + 2x)/x يساوي x + 2", "(x+1)^2 égale x^2 + 2x + 1"])
def test_correct_verbal_equalities_are_verified(text):
    c = check_formulas(text, [])
    assert c["wrong"] == [] and c["ungrounded"] == [] and c["verified"], c


@pytest.mark.parametrize("text,span", [
    ("(x+1)^2 is x^2 + 1", "(x+1)^2 = x^2 + 1"), ("2x + 2 equals x", "2x + 2 = x"), ("3 vaut 4", "3 = 4"),
    ("(x+1)^2 تساوي x^2 + 1", "(x+1)^2 = x^2 + 1")])
def test_wrong_verbal_equalities_are_wrong_math(text, span):
    assert check_formulas(text, [])["wrong"] == [span]


@pytest.mark.parametrize("text", [
    "la dérivée de x² est égale à 2x", "the derivative of x^2 is 2x", "مشتقة x² تساوي 2x", "la valeur de f(2) est égale à 4",
    "la limite est égale à 2x",                       # a word on the left, not a math run
    "which is 2x + 3", "this is 2x + 3", "x is the variable",         # "is" only directly between two math runs
    "Voici : x vaut", "il vaut mieux 2x"])
def test_spoken_equalities_that_are_not_between_two_math_runs_or_are_about_an_argument_are_not_read(text):
    c = check_formulas(text, [])
    assert c["wrong"] == [] and c["verified"] == [] and c["ungrounded"] == [], c


def test_the_verbal_equality_still_needs_both_neighbours_to_be_math():
    from engine.grounding import verbalize
    assert verbalize("x est égal à y")[0].count("=") == 1
    assert verbalize("le résultat est égal à 2x")[0].count("=") == 0
    assert verbalize("2x est égal au double")[0].count("=") == 0


async def test_the_engine_degrades_the_upload_answer_as_wrong_math_and_lists_the_correct_form(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok(UPLOAD_ANSWER))
    r = await eng.run("explain", {"question": "C'est quoi la dérivée de 1/x et de racine de x ?"})
    assert r.degraded and r.degrade_reason == "wrong_math"
    assert r.notes["wrong_math"] == ["(x^2 + 2x)/x = 2x + 2"] and r.notes["wrong_math_forms"]["(x^2 + 2x)/x = 2x + 2"].endswith("= x + 2")


def test_a_copula_inside_a_written_equation_is_not_a_spoken_equality():
    """Found by the recheck on the saved answers: "the derivative of f(x) = x² − 4x + 1 is f'(x) = 2x − 4" read as a chain."""
    text = "For example, the derivative of f(x) = x² − 4x + 1 is f'(x) = 2x − 4. It equals zero when x = 2."
    c = check_formulas(text, [])
    assert c["ungrounded"] == [] and c["wrong"] == []
