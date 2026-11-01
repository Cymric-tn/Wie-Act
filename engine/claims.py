"""
check_claim, the part that needs no model: formulas in a claim are checked with sympy (engine.grounding).

  analyse(claim)  -> Formula(verified=[...], wrong=[...], prose=n, correction=str|None)
      verified   formula spans sympy proves ("(x²)' = 2x", "f(x) = x², f'(x) = 2x")
      wrong      formula spans sympy refutes ("(x²)' = x", "f(x) = x², f'(x) = 3x")
      prose      content words outside the formulas (connectives such as "donc" / "so" do not count)
      correction the correct form of the first refuted span, when sympy can state it

  decide(f, language) -> data | None
      a refuted span, nothing verified     -> CONTRADICTED with the correct form (whatever else the claim says)
      a refuted span AND a verified one    -> PARTLY with the correct form of the refuted one (half right is not "wrong")
      verified, nothing wrong, no content word around the formulas -> SUPPORTED
      anything else (no formula, a formula plus more claims, nothing verifiable) -> None: go on to retrieval

A claim like "(x²)' = 2x, and the derivative is always positive" is NOT decided here: the formula is right, the rest
is a claim sympy cannot see, so it goes to the course and the model, with `verified` as a note.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sympy

from engine import grounding as G
from engine.mathcheck import equivalent, parse_expression

MAX_PROSE = 0                  # content words allowed around a formula: none (connectives like "donc", "so" are free)
# Words that join formulas without claiming anything. Everything else ("la dérivée d'une constante est 1") is a claim
# sympy cannot see, so the claim goes to the course and the model instead of being declared SUPPORTED.
_CONNECTIVES = frozenset("""
donc alors ainsi car puisque pour avec où que qui est sont soit sachant exemple comme et ou on nous a ont
so then thus hence therefore since because for with where that which is are we have has know and or let
نعلم أن ان إذن اذن لذلك لأن حيث و مع لدينا
""".split())
MAX_CORRECTION_WORDS = 25
_PROSE_WORD = re.compile(r"[A-Za-zÀ-ÿ؀-ۿ]{3,}")
_LEAD = {"FRENCH": "Plutôt : ", "ENGLISH": "Rather: ", "ARABIC": "الصحيح: ", "DARIJA": "الصحيح: ", "ARABIZI": "Sa7i7: "}
_NO_FORM = {"FRENCH": "Cette égalité est fausse : vérifie le calcul.", "ENGLISH": "This equality is wrong: check the calculation.",
            "ARABIC": "هذه المساواة خاطئة: راجع الحساب.", "DARIJA": "المساواة هذي غالطة: عاود الحساب.",
            "ARABIZI": "El mosawet hedhi ghalta: 3awed el 7sab."}


@dataclass
class Formula:
    verified: list[str] = field(default_factory=list)
    wrong: list[str] = field(default_factory=list)
    prose: int = 0
    correction: str | None = None

    @property
    def has_formula(self) -> bool:
        return bool(self.verified or self.wrong)


def pretty(e: sympy.Expr) -> str:
    """sympy -> the plain notation of the course: 2*x**2 -> 2x²."""
    s = str(sympy.simplify(e)).replace("**", "^").replace("*", "")
    for i, sup in zip("23456789", "²³⁴⁵⁶⁷⁸⁹"):
        s = re.sub(rf"\^{i}(?!\d)", sup, s)
    return s.replace("sqrt(", "√(")


def _correct_form(claim: str, span: str) -> str | None:
    """The right-hand side sympy computes for a refuted span: (E)' = r -> diff(E); f'(x) = r with f(x) = E in the
    claim -> diff(E); numbers only -> the value. None when it cannot be stated."""
    lhs, _, _ = span.partition("=")
    lhs = lhs.strip()
    le = G._expr(lhs)
    if le is not None:                                               # (x²)'  /  3 × 2
        return f"{lhs} = {pretty(le)}"
    m = G._DERIV_AT.match(G._norm(lhs).split(",")[-1])
    if m:
        for l2, r2, _neg in G.equations(claim, with_negation=True):
            d = G._FUNC_DEF.match(G._norm(l2).split(",")[-1])
            if d and d.group(1) == m.group(1) and (e := G._expr(r2)) is not None:
                var = sympy.Symbol(d.group(2))
                at = m.group(2)
                val = sympy.diff(e, var)
                if at != str(var):
                    pt = parse_expression(at)
                    if pt is None or pt.free_symbols:
                        return None
                    val = val.subs(var, pt)
                return f"{lhs} = {pretty(val)}"
    return None


_DERIV_LHS = re.compile(r"'|d\s*/\s*dx", re.I)


def analyse(claim: str) -> Formula:
    c = G.check_formulas(claim, [])
    f = Formula(verified=list(c["verified"]), wrong=list(c["wrong"]))
    # In a claim, "(x²)' = x" is an assertion to verify. check_formulas (written for explanations) files an equation
    # whose both sides parse under "equations to solve", so the derivative-on-the-left case is decided here.
    forms: dict[str, str] = {}
    for lhs, rhs, negated in G.equations(claim, with_negation=True):
        if negated or not _DERIV_LHS.search(lhs) or G._DERIV_AT.match(G._norm(lhs).split(",")[-1]):
            continue                                              # f'(x) = ... is check_formulas' job
        le, r = G._expr(lhs), G._expr(rhs)
        span = f"{lhs} = {rhs}".strip()
        if le is None or r is None or span in f.verified or span in f.wrong:
            continue
        if equivalent(le, r):
            f.verified.append(span)
        else:
            f.wrong.append(span)
            forms[span] = f"{lhs} = {pretty(le)}"
    if f.wrong:
        f.correction = next((forms[s] for s in f.wrong if s in forms), None)
        for span in f.wrong:
            if f.correction is None and (form := _correct_form(claim, span)) is not None:
                f.correction = form
    f.prose = sum(1 for tok in claim.split() if not G._is_math(tok) and _PROSE_WORD.search(tok)
                  and tok.strip(".,;:!?()«»\"“”").lower() not in _CONNECTIVES)
    return f


def _words(text: str, n: int = MAX_CORRECTION_WORDS) -> str:
    w = text.split()
    return " ".join(w[:n])


def decide(f: Formula, language: str | None) -> dict | None:
    lang = language or "FRENCH"
    if f.wrong:
        text = (_LEAD.get(lang, _LEAD["FRENCH"]) + f.correction) if f.correction \
            else _NO_FORM.get(lang, _NO_FORM["FRENCH"])
        return {"verdict": "PARTLY" if f.verified else "CONTRADICTED", "correction": _words(text),
                "source_chunk_id": None, "checked_by": "sympy"}
    if f.verified and f.prose <= MAX_PROSE:
        return {"verdict": "SUPPORTED", "correction": "", "source_chunk_id": None, "checked_by": "sympy"}
    return None
