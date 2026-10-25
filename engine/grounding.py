"""
Formula grounding check for explain: every equation the model writes must come from the sources
(retrieved chunks, the appended bank exercise, the learner's own question).

  equations(text)                -> [(lhs, rhs)]: the math run on each side of every "=" (works inside
                                    French, English or Arabic sentences; a chain A = B = C gives (A, B), (B, C))
  ungrounded(answer, sources)    -> the answer's equations with no source equation such that
                                      * the left sides match (normalized; one may end with the other:
                                        "on a f(1)" vs "f(1)"), and
                                      * the right sides are equivalent: sympy when both parse
                                        (variables x a h t n k u v y), exact normalized text otherwise.

Comparing the left side too is what makes "f'(x) = 1/x" ungrounded when the course only says "f(x) = 1/x".
Formulas written without "=" ("the limit of [f(x + h) - f(x)]/h") are checked too (`formulas`): a math run
that applies a function or a bracket and uses an operator must appear in the sources (text or sympy).
Known limits: a correct NEW computation (e.g. f'(3) = 6 for a function the course does not use) is flagged
too, by design; and two different expressions that both contain f(…) can only match as exact text.
"""
from __future__ import annotations

import re
import unicodedata

from engine.mathcheck import equivalent, parse_expression

_MATH_CHARS = re.compile(r"[\d()+\-−*/^²³⁴⁵⁶⁷⁸⁹⁰¹ⁿ⁻√·×'≠≤≥<>\[\]→]")
_LETTERS_OK = {"x", "y", "f", "a", "h", "n", "k", "u", "v", "t", "g"}
_FUNCS = {"ln", "lim", "sin", "cos", "tan", "exp", "sqrt", "log"}
_EDGE_PUNCT = ".,;:!?…»«\"“”،؛؟)"
_OPS = set("+-−*/×·^(")


_WORD = re.compile(r"[A-Za-zÀ-ÿ\u0600-\u06FF]{3,}")
_COMPARE = re.compile(r"[<>≤≥≠]")


def _is_math(tok: str) -> bool:
    t = tok.strip(_EDGE_PUNCT + "(")
    if not t or _COMPARE.search(t):
        return False                                   # an inequality ends the formula side
    words = [w for w in _WORD.findall(t) if w.lower() not in _FUNCS]
    if words:                                          # "(puisque", "C'est", "(racine": prose, not math
        return False
    return bool(_MATH_CHARS.search(tok)) or t.lower() in _FUNCS or t in _LETTERS_OK


def _single(tok: str) -> bool:
    t = tok.strip(_EDGE_PUNCT + "(")
    return t in _LETTERS_OK and not _MATH_CHARS.search(tok)


def _tail(s: str) -> str:
    """The math run that ends at the "=" (left side)."""
    toks = s.split()
    out: list[str] = []
    for i in range(len(toks) - 1, -1, -1):
        tok = toks[i]
        if not _is_math(tok):
            break
        # a lone letter ("a" in "on a f(1)") belongs to the formula only right next to "=", next to an
        # operator ("x + h"), or between math tokens
        if out and _single(tok) and not (out[0][:1] in _OPS or
                                         (i > 0 and _is_math(toks[i - 1]) and not _single(toks[i - 1]))):
            break
        if tok.rstrip().endswith((",", ";", ":", ".", "،", "؛")) and out:
            break
        out.insert(0, tok)
    return " ".join(out).strip(_EDGE_PUNCT.replace(")", "") + " ")


def _head(s: str) -> str:
    """The math run that starts after the "=" (right side)."""
    toks = s.split()
    out: list[str] = []
    for i, tok in enumerate(toks):
        if not _is_math(tok):
            break
        if out and _single(tok) and not (out[-1][-1:] in _OPS or (i + 1 < len(toks) and _is_math(toks[i + 1]))):
            break
        out.append(tok)
        if tok.endswith((",", ";", ":", "،", "؛", ".")):        # a period ends it ("²".isdigit() is True!)
            break
    return " ".join(out).rstrip(_EDGE_PUNCT.replace(")", "") + " ")


def _norm(s: str) -> str:
    t = unicodedata.normalize("NFC", s).lower()
    for a, b in (("−", "-"), ("–", "-"), ("×", "*"), ("·", "*"), ("’", "'")):
        t = t.replace(a, b)
    return re.sub(r"\s+", "", t).strip(".,;:")


_NEGATION = re.compile(r"(?:\b(?:non|pas|not|never|jamais|isn't|moch|mouch|mech)|ليس|لا|موش|ماهوش)\W*$", re.I)


def _split_steps(line: str) -> list[str]:
    """Split at arrows outside brackets: "2x − 4 = 0 → 2x = 4" -> 2 steps; "lim(h → 0) …" stays whole."""
    out, depth, last = [], 0, 0
    for m in re.finditer(r"[(\[]|[)\]]|\s(?:→|⇒|=>|⟹)\s", line):
        tok = m.group()
        if tok in "([":
            depth += 1
        elif tok in ")]":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(line[last:m.start()])
            last = m.end()
    return out + [line[last:]]


def equations(text: str, with_negation: bool = False) -> list:
    """(lhs, rhs) per "=". with_negation=True adds a third item: the equation follows a negation
    ("f'(x) = 2x, et non f'(x) = 2x + 7") and is a refuted claim, not an assertion."""
    out = []
    for line in text.splitlines():
        for piece in _split_steps(line):
            parts = piece.split("=")
            for i in range(len(parts) - 1):
                lhs, rhs = _tail(parts[i]), _head(parts[i + 1])
                if rhs and _MATH_CHARS.search(rhs + lhs):
                    before = parts[i][: len(parts[i].rstrip()) - len(lhs)] if lhs else parts[i]
                    neg = bool(_NEGATION.search(" ".join(before.split()[-3:])))
                    out.append((lhs, rhs, neg) if with_negation else (lhs, rhs))
    return out


def _lhs_match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return True                                   # an equation written as "= 2a" names no left side
    return a == b or a.endswith(b) or b.endswith(a)


def _rhs_match(a: str, b: str) -> bool:
    if _norm(a) == _norm(b):
        return True
    ea, eb = parse_expression(a), parse_expression(b)
    return ea is not None and eb is not None and equivalent(ea, eb)


_APPLIED = re.compile(r"[a-z]'?\(|\[")
_OPERATOR = re.compile(r"[+\-−*/×·]")


def formulas(text: str) -> list[str]:
    """Standalone formulas: math runs with no "=" that apply a function or a bracket AND use an operator,
    e.g. "[f(x + h) - f(x)]/h" in "the limit as h approaches zero of [f(x + h) - f(x)]/h"."""
    out = []
    for line in text.splitlines():
        for chunk in line.split("="):
            run: list[str] = []
            for tok in chunk.split() + [""]:
                if tok and _is_math(tok) and not (run and run[-1].endswith((",", ";", ":", "،", "؛"))):
                    run.append(tok)                                   # a comma ends the formula
                    continue
                span = " ".join(run).strip(_EDGE_PUNCT.replace(")", "").replace("]", "") + " ")
                if span and _APPLIED.search(span) and _OPERATOR.search(span):
                    out.append(span)                          # equation sides are re-checked harmlessly
                run = []
    return out


def ungrounded(answer: str, sources: list[str]) -> list[str]:
    pool = [eq for s in sources for eq in equations(s)]
    bad = []
    for lhs, rhs in equations(answer):
        if not any(_lhs_match(lhs, l2) and _rhs_match(rhs, r2) for l2, r2 in pool):
            span = f"{lhs} = {rhs}".strip()
            if span not in bad:
                bad.append(span)
    flat = _norm(" ".join(sources))
    sides = [side for eq in pool for side in eq]
    for f in formulas(answer):
        if any(_norm(f) in _norm(span) or _norm(span).endswith(_norm(f)) for span in bad):
            continue                                          # already reported as part of an equation
        if _norm(f) in flat or any(_rhs_match(f, side) for side in sides):
            continue
        if f not in bad:
            bad.append(f)
    return bad


# ------------------------------------------------------------------ correctness first (engine-v1)
import sympy  # noqa: E402

from engine.mathcheck import parse_expression  # noqa: E402,F811

_SINGLE_VAR = re.compile(r"[a-z]")
_FUNC_DEF = re.compile(r"^([a-z])\s*\(\s*([a-z])\s*\)$")              # f(x), f(t)
_DERIV_AT = re.compile(r"^([a-z])\s*'\s*\(\s*([^()]+?)\s*\)$")         # f'(x), f'(3), f'(a)
_PRIME_GROUP = re.compile(r"\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*'")    # (x²)'  (3x² + 5x)'
_DDX_GROUP = re.compile(r"d\s*/\s*dx\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)")   # d/dx (x² + x)
_BARE_PRIME = re.compile(r"(?<![\w)\]'])(x|\d+(?:\.\d+)?)\s*'(?!\s*\()")      # x' -> 1, 7' -> 0 (not f'(…))


def _eval_primes(text: str) -> str | None:
    """'(x²)' + (x)'', 'd/dx (x² + x)', "x'·(x + 1)" -> each derivative replaced by diff(E, x);
    None if any E does not parse."""
    x = sympy.Symbol("x")
    for pattern in (_DDX_GROUP, _PRIME_GROUP):
        out, pos = [], 0
        for m in pattern.finditer(text):
            e = parse_expression(m.group(1))
            if e is None:
                return None
            out.append(text[pos:m.start()] + f"({str(sympy.diff(e, x)).replace('**', '^')})")
            pos = m.end()
        text = "".join(out) + text[pos:] if out else text
    return _BARE_PRIME.sub(lambda m: "(1)" if m.group(1) == "x" else "(0)", text)


def _expr(text: str) -> sympy.Expr | None:
    t = _eval_primes(_balance(text))
    if t is None or "'" in t:
        return None
    return parse_expression(t)


def _balance(text: str) -> str:
    """Drop a stray closing bracket picked up from the sentence: "x⁻¹)" -> "x⁻¹"."""
    t = text.strip()
    while t.endswith(")") and t.count(")") > t.count("("):
        t = t[:-1].rstrip()
    return t


def check_formulas(answer: str, sources: list[str]) -> dict:
    """Classify every formula of an explain answer:
      verified      derivative claims checked with sympy against the answer's own f(x) = E, (E)' notation,
                    and identities whose two sides parse (3 × 2x = 6x)
      wrong         a claim sympy can check that is FALSE (f'(x) = 3x for f(x) = x², 3 × 2 = 7)
      ungrounded    neither verifiable nor in the sources (the limit definition when the course lacks it)
      skipped       single-variable assignments (u = 1, n = 2) and equations to solve (2x − 4 = 0)
      new_examples  functions the answer defines that the sources do not (correct ones pass)"""
    out = {"verified": [], "wrong": [], "ungrounded": [], "skipped": [], "new_examples": []}
    pool = [eq for s in sources for eq in equations(s)]
    source_defs = [_expr(r) for l, r in pool if _FUNC_DEF.match(_norm(l))]
    defs: dict[str, tuple[sympy.Symbol, sympy.Expr]] = {}
    assigned: dict[sympy.Symbol, sympy.Expr] = {}                    # n = 2 -> used by later claims
    eqs = equations(answer, with_negation=True)
    later: dict[str, tuple[sympy.Symbol, sympy.Expr]] = {}           # a claim may precede "where f(x) = E"
    for lhs, rhs, _neg in reversed(eqs):
        m = _FUNC_DEF.match(_norm(lhs).split(",")[-1])
        if m and (e := _expr(rhs)) is not None:
            later[m.group(1)] = (sympy.Symbol(m.group(2)), e)
    for lhs, rhs, negated in eqs:
        span, l = f"{lhs} = {rhs}".strip(), _norm(lhs)
        if negated:                                                   # "et non f'(x) = 2x + 7": refuted
            out["skipped"].append(span)
            continue
        l = l.split(",")[-1]
        m_def, m_der = _FUNC_DEF.match(l), _DERIV_AT.match(l)
        r = _expr(rhs)
        if m_def and r is not None:                                   # a definition: f(x) = E
            var = sympy.Symbol(m_def.group(2))
            defs[m_def.group(1)] = (var, r)
            if not any(d is not None and equivalent(d.subs(var, sympy.Symbol("x")), r.subs(var, sympy.Symbol("x")))
                       for d in source_defs):
                out["new_examples"].append(span)
            continue
        if m_der and (m_der.group(1) in defs or m_der.group(1) in later) and r is not None:   # a claim
            var, e = defs.get(m_der.group(1)) or later[m_der.group(1)]
            subs = {k: v for k, v in assigned.items() if k != var}
            d = sympy.diff(e, var).subs(subs)
            r = r.subs(subs)
            at = m_der.group(2)
            if at != str(var):
                point = parse_expression(at)
                if point is None or point.free_symbols - {sympy.Symbol("a")}:
                    out["ungrounded"].append(span)
                    continue
                d = d.subs(var, point)
            elif not r.free_symbols and d.free_symbols:               # "setting f'(x) = 0": a condition
                out["skipped"].append(span)
                continue
            (out["verified"] if equivalent(d, r) else out["wrong"]).append(span)
            continue
        le = _expr(lhs) if l else None
        if le is not None and r is not None:                          # both sides math: a step
            if equivalent(le, r):
                out["verified"].append(span)
            elif not (le.free_symbols | r.free_symbols):              # numbers only and not equal: wrong
                out["wrong"].append(span)
            elif _SINGLE_VAR.fullmatch(l):
                out["skipped"].append(span)                           # u = 1, v = x + 2, n = 2
                assigned[sympy.Symbol(l)] = r
            else:
                out["skipped"].append(span)                           # an equation to solve: 2x − 4 = 0
            continue
        if _SINGLE_VAR.fullmatch(l) and r is not None:
            out["skipped"].append(span)
            assigned[sympy.Symbol(l)] = r
            continue
        if any(_lhs_match(lhs, l2) and _rhs_match(rhs, r2) for l2, r2 in pool):
            continue                                                  # in the sources: fine
        out["ungrounded"].append(span)
    flat = _norm(" ".join(sources))
    sides = [side for eq in pool for side in eq]
    reported = out["ungrounded"] + out["wrong"] + out["verified"] + out["skipped"] + out["new_examples"]
    for f in formulas(answer):
        if any(_norm(f) in _norm(s) or _norm(s).endswith(_norm(f)) for s in reported):
            continue
        if _norm(f) in flat or any(_rhs_match(f, side) for side in sides) or _expr(f) is not None:
            continue
        if f not in out["ungrounded"]:
            out["ungrounded"].append(f)
    return out
