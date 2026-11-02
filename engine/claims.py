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


# ====================================================================== compound claims (engine v1.4)
# "P and Q" is two facts: each is checked on its own (sympy, the retrieval gate, the model) and the verdicts are
# combined in code, because a 3B model asked about both at once calls half-true claims SUPPORTED. The splitter is
# conservative on purpose: a claim it is not sure about stays ONE part, which is the old behaviour.
MAX_PARTS = 4                    # a claim with more facts keeps the rest in its last part (each part can cost a request)
MIN_PART_TOKENS = 3
_OPEN = "([{"
_CLOSE = ")]}"
_SUBJECT = frozenset("""
le la les l un une des ce cette ces celle celui ceux celles il elle ils elles son sa ses leur leurs
the a an its their this that these those it they each every any
""".split())
_AR_SUBJECT = re.compile(r"^(?:ال|مشتق|دالة|كل$|أي$|اي$|هذه$|هذا$)")
_SUBORDINATE = frozenset("si if quand lorsque lorsqu when whenever where wherever while once unless provided puisque since because comme as dès tant après avant après بما لو إذا اذا عندما حين حيث".split())
_COPULA = frozenset("""
est sont a ont était sera soit vaut valent donne donnent devient reste change changent tend tendent admet admettent existe
croît décroît passe peut peuvent doit doivent faut permet mesure représente indique définit équivaut égale égal
is are was were has have gives give becomes stays changes change tends tend exists increases decreases means measures
represents equals can must does do
هي هو هم تساوي يساوي تكون يكون تعني يعني تمثل يمثل تقيس ليست ليس توجد يوجد تتغير تزداد تنقص
""".split())
_FORMULA_DEF = re.compile(r"(?<!['′])\b[A-Za-z]\s*\(\s*[A-Za-z]\s*\)\s*=")   # "f(x) = ...": a definition, not a claim


def _plain(tok: str) -> str:
    return tok.strip(".,;:!?()[]{}«»\"“”'’").lower()


def _starts_subject(toks: list[str]) -> bool:
    if not toks:
        return False
    first = toks[0].lower()
    head = re.split(r"['’]", first)[0]
    if _plain(first) in _SUBJECT or head in _SUBJECT or _AR_SUBJECT.match(first):
        return True
    return G._is_math(toks[0]) and not first.isalpha()              # a formula opens the clause: "(x³)' = 3x²"


def _has_predicate(toks: list[str]) -> bool:
    return any("=" in t or _plain(t) in _COPULA or _plain(re.split(r"['’]", t)[-1]) in _COPULA for t in toks)


def _clause_ok(left: list[str], right: list[str], semicolon: bool = False) -> bool:
    if len(left) < MIN_PART_TOKENS or len(right) < MIN_PART_TOKENS:
        return False
    if not semicolon and _plain(left[0]) in _SUBORDINATE:       # "Si P, Q" / "Quand P et Q": one conditional claim
        return False
    return _has_predicate(left) and _has_predicate(right)


def split_claim(text: str) -> list[str]:
    """The atomic parts of a claim, in order. Boundaries: ";", "et" / "and" / "و" / "ainsi que" before a new clause, and
    a comma before a new subject or a new equation. Never inside parentheses or brackets, never between a function
    definition and what follows ("f(x) = x², f'(x) = 2x" is one claim), never when a side is too short or has no
    predicate. One part comes back as [text] unchanged."""
    toks = text.split()
    depth, cuts = 0, []               # cuts: (index of first token of the right part, tokens to drop before it)
    for i, tok in enumerate(toks):
        at_zero = depth == 0
        depth = max(0, depth + sum(tok.count(c) for c in _OPEN) - sum(tok.count(c) for c in _CLOSE))
        if not at_zero or depth != 0 or i + 1 >= len(toks):
            continue
        nxt = toks[i + 1:]
        if tok.endswith(";") or tok.endswith("؛"):
            cuts.append((i + 1, 0, i + 1, True))                # boundary after this token
        elif tok.endswith((",", "،")):
            left = toks[:i + 1]
            skip = 1 if _plain(nxt[0]) in ("et", "and", "ainsi", "و") else 0
            if _plain(nxt[0]) == "ainsi" and len(nxt) > 1 and _plain(nxt[1]) == "que":
                skip = 2
            right = nxt[skip:]
            if skip or _starts_subject(nxt):
                if skip or not _FORMULA_DEF.search(" ".join(left)) or not G._is_math(nxt[0]):
                    cuts.append((i + 1 + skip, 0, i + 1, False))
            elif "=" in " ".join(nxt[:6]) and "=" in " ".join(left) and not _FORMULA_DEF.search(" ".join(left)):
                cuts.append((i + 1, 0, i + 1, False))                  # a new equation after an equation
        elif _plain(tok) in ("et", "and", "و"):
            cuts.append((i + 1, 1, i, False))
        elif _plain(tok) == "ainsi" and _plain(nxt[0]) == "que":
            cuts.append((i + 2, 2, i, False))
        elif tok.startswith("و") and len(tok) > 2 and _AR_SUBJECT.match(tok[1:]):
            toks[i] = tok[1:]                                   # "ومشتقة": the conjunction is glued to the noun
            cuts.append((i, 0, i, False))
    parts, starts, start = [], [], 0
    for first_right, _drop, left_end, semi in cuts:
        left_toks = [t for t in toks[start:left_end] if t]
        right_toks = toks[first_right:]
        if not _clause_ok(left_toks, right_toks, semi):
            continue
        parts.append(" ".join(toks[start:left_end]).rstrip(",;؛ "))
        starts.append(start)
        start = first_right
    parts.append(" ".join(toks[start:]))
    starts.append(start)
    if len(parts) > MAX_PARTS:                                  # the rest stays in the last part, as written
        parts = parts[:MAX_PARTS - 1] + [" ".join(toks[starts[MAX_PARTS - 1]:])]
    return [p.strip() for p in parts if p.strip()] or [text]


def aggregate(parts: list[dict]) -> dict:
    """The verdict of a compound claim from its parts' verdicts, in code: all SUPPORTED -> SUPPORTED, all CONTRADICTED ->
    CONTRADICTED, all NOT_IN_COURSE -> NOT_IN_COURSE, anything else (a mix, a PARTLY, a NOT_IN_COURSE beside a judged
    part) -> PARTLY. data.parts keeps each part's own verdict, so the UI shows which part is right, wrong or outside the
    course. Corrections are joined per part, in order."""
    verdicts = {p["verdict"] for p in parts}
    verdict = next(iter(verdicts)) if len(verdicts) == 1 else "PARTLY"
    by = {p["checked_by"] for p in parts}
    return {"verdict": verdict, "correction": " ".join(p["correction"] for p in parts if p.get("correction")),
            "source_chunk_id": next((p["source_chunk_id"] for p in parts if p.get("source_chunk_id")), None),
            "checked_by": by.pop() if len(by) == 1 else "mixed", "parts": parts}
