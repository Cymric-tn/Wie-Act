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


def test_the_upload_answers_limit_without_a_stated_point_is_unchecked_not_flagged():
    """v1.4 first read this sentence as an identity (wrong_math, form x + 2). A limit needs its point: none is stated here, so
    the claim is listed in unchecked and never flagged."""
    c = check_formulas(UPLOAD_ANSWER, [UPLOAD_SOURCE])
    assert c["wrong"] == [] and c["verified"] == [] and c["ungrounded"] == []
    assert c["unchecked"] == ["(x^2 + 2x)/x = 2x + 2"] and "f(x) = x^2 + 2x" in c["new_examples"]


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


async def test_the_engine_lists_a_limit_without_a_point_as_unchecked_and_degrades_a_wrong_limit(vt, index):
    eng, fake, _ = engine(vt, index, lambda b: chat_ok(UPLOAD_ANSWER))
    r = await eng.run("explain", {"question": "C'est quoi la dérivée de 1/x et de racine de x ?"})
    assert not r.degraded and r.notes["unchecked_spans"] == ["(x^2 + 2x)/x = 2x + 2"]
    wrong = "Par exemple, la limite de (x²+2x)/x quand x tend vers l'infini est égale à 2x + 2."
    eng2, _, _ = engine(vt, index, lambda b: chat_ok(wrong))
    r2 = await eng2.run("explain", {"question": "C'est quoi la dérivée de 1/x et de racine de x ?"})
    assert r2.degraded and r2.degrade_reason == "wrong_math"
    assert r2.notes["wrong_math"] == ["lim(x→∞) (x²+2x)/x = 2x + 2"] and r2.notes["wrong_math_forms"]["lim(x→∞) (x²+2x)/x = 2x + 2"].endswith("= ∞")


def test_a_copula_inside_a_written_equation_is_not_a_spoken_equality():
    """Found by the recheck on the saved answers: "the derivative of f(x) = x² − 4x + 1 is f'(x) = 2x − 4" read as a chain."""
    text = "For example, the derivative of f(x) = x² − 4x + 1 is f'(x) = 2x − 4. It equals zero when x = 2."
    c = check_formulas(text, [])
    assert c["ungrounded"] == [] and c["wrong"] == []



# ---------------------------------------------------------------- spoken limits (v1.4)
@pytest.mark.parametrize("text", [
    "la limite de ((x+h)² − x²)/h quand h tend vers 0 est égale à 2x",
    "la limite de (x²−1)/(x−1) quand x tend vers 1 est égale à 2",
    "lorsque x tend vers l'infini, la limite de 1/x est égale à 0".replace("lorsque x tend vers l'infini, ", "") + " quand x tend vers l'infini",
    "the limit of ((x+h)² − x²)/h as h approaches 0 equals 2x",
    "the limit of (x²−1)/(x−1) as x → 1 is equal to 2",
    "نهاية ((x+h)² − x²)/h عندما h تؤول إلى 0 تساوي 2x",
    "lim(h→0) ((x+h)² − x²)/h est égal à 2x",
    "la limite de (x²+2x)/x quand x tend vers l'infini est égale à +∞"])
def test_a_spoken_limit_with_its_point_is_verified_by_sympy(text):
    c = check_formulas(text, [])
    assert len(c["verified"]) == 1 and c["wrong"] == [] and c["unchecked"] == [] and c["ungrounded"] == [], c


@pytest.mark.parametrize("text,span,form", [
    ("la limite de ((x+h)² − x²)/h quand h tend vers 0 est égale à x", "lim(h→0) ((x+h)² − x²)/h = x", "= 2x"),
    ("la limite de (x²+2x)/x quand x tend vers l'infini est égale à 2x + 2", "lim(x→∞) (x²+2x)/x = 2x + 2", "= ∞"),
    ("the limit of (x²−1)/(x−1) as x approaches 1 equals 3", "lim(x→1) (x²−1)/(x−1) = 3", "= 2"),
    ("la limite de (x²−1)/(x−1) quand x tend vers 1 est égale à ∞", "lim(x→1) (x²−1)/(x−1) = ∞", "= 2")])
def test_a_wrong_spoken_limit_is_wrong_math_with_the_correct_limit(text, span, form):
    c = check_formulas(text, [])
    assert c["wrong"] == [span] and c["forms"][span].endswith(form) and c["unchecked"] == [], c


@pytest.mark.parametrize("text", [
    "la limite de (x²−1)/(x−1) est égale à 2", "the limit of (x²−1)/(x−1) equals 2", "نهاية (x²−1)/(x−1) تساوي 2"])
def test_a_spoken_limit_without_a_point_is_unchecked_and_never_flagged(text):
    c = check_formulas(text, [])
    assert c["wrong"] == [] and c["verified"] == [] and c["ungrounded"] == [] and len(c["unchecked"]) == 1, c


def test_the_point_may_come_after_the_claim_in_the_same_sentence_and_other_sentences_do_not_count():
    assert check_formulas("la limite de (x²−1)/(x−1) est égale à 2 quand x → 1.", [])["verified"]
    c = check_formulas("On étudie x → 1. La limite de (x²−1)/(x−1) est égale à 2.", [])
    assert c["verified"] == [] and len(c["unchecked"]) == 1                 # the point belongs to another sentence


def test_a_clause_that_names_no_point_is_never_read_as_one():
    c = check_formulas("la limite de (x²−1)/(x−1) quand n tend vers la lune est égale à 2", [])
    assert c["wrong"] == [] and c["verified"] == [] and c["ungrounded"] == []


def test_worked_arithmetic_on_a_named_quantity_is_not_an_ungrounded_formula():
    """FelCloud scenarios 2026-11-11, finding 6: a correct 2×2 determinant was flagged `ungrounded_formula`."""
    from engine.grounding import check_formulas
    src = ["Le déterminant de la matrice (a b ; c d) est det A = ad − bc."]
    ok = check_formulas("detA = ad - bc\n\ndetA = (2)(5) - (3)(4) = 10 - 12 = -2", src)
    assert not ok["ungrounded"] and not ok["wrong"] and "10 - 12 = -2" in ok["verified"]
    bad = check_formulas("detA = (2)(5) - (3)(4) = 10 - 12 = 5", src)
    assert bad["wrong"] == ["10 - 12 = 5"]
    assert check_formulas("la pente = 7", src)["ungrounded"] == ["= 7"]          # a bare invented number is still reported


def test_an_assignment_said_with_is_and_the_limit_definition_are_not_flagged():
    """FelCloud replay 2026-11-11: two correct English answers came back `wrong_math` and `ungrounded_formula`."""
    from engine.grounding import check_formulas
    a = check_formulas("For f(x) = 1/(x + 2), the numerator u is 1 and the denominator v is x + 2.", [])
    assert not a["wrong"] and a["skipped"] == ["u = 1", "v = x + 2"]
    assert check_formulas("2x + 2 equals x", [])["wrong"] == ["2x + 2 = x"]                # a spoken identity still is
    for good in ("f'(a) = lim h→0 (f(a + h) − f(a)) / h", "f'(x) = lim(h → 0) (f(x + h) − f(x)) / h"):
        assert check_formulas(good, [])["verified"] == [good]
    for bad in ("f'(a) = lim h→0 (f(a + h) + f(a)) / h", "f'(a) = lim h→1 (f(a + h) − f(a)) / h"):
        assert check_formulas(bad, [])["ungrounded"] == [bad]
