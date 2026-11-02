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
