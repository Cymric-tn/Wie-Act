"""
check_answer, the part that needs no model: a learner's worked solution, line by line.

  target_for(problem, exercise)   what the problem asks: derive f, f'(a), or the tangent at a. None when the problem
                                  is not a derivative problem we can read (then no line is ever called WRONG).
  check_steps(lines, target)      one StepResult per line: OK | WRONG | UNCHECKED
  diagnose(value, target, ...)    why a wrong line is wrong: the exercise's stored common mistakes first, then the
                                  buggy rules below; None when neither explains it (then one compact call writes a
                                  guiding question)

Verdict rules (derivative problems). A line's label says what it claims to be: "f'(x) = ..." must be the derivative, "f(3) = ..." the value of f at 3,
"f'(3) = ..." the derivative at 3, "y = ..." the tangent; a value that is right for something else ("y = 2x" for a
tangent problem: that is f') is WRONG under that label. A line with no label may be any of the problem's values.
A line is parsed with the safe whitelist parser (engine.mathcheck: digits, x,
+ - * / ( ) . and sqrt; nothing else ever reaches sympy). A line it cannot parse is UNCHECKED, never WRONG: a
sentence, "u = x, v = x + 3", another variable. A parsed line is OK when it equals the true derivative, or the
function itself, or the value the problem asks for (f'(a), f(a), the tangent), or an algebraic rewrite of the line
before it, which must itself be OK. Otherwise WRONG. A line that chains equalities ("= 3×2x + 5 = 6x + 5") must be
consistent with itself. The first WRONG line is `first_wrong`.

Buggy rules, precomputed per function: the derivative a learner gets when they apply ONE rule wrongly.
  PRODUCT_AS_PRODUCT  (u·v)' taken as u'·v'
  POW_NO_DECREMENT    (xⁿ)' taken as n·xⁿ (the exponent is not lowered)
  CHAIN_NO_INNER      (g(x)ⁿ)' taken as n·g(x)ⁿ⁻¹ (no factor g'(x))
  CONST_KEPT          the derivative of a constant taken as the constant, not 0
A rule is listed for a function only if it gives a result different from the true derivative.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

import sympy

from engine.claims import pretty
from engine.exercises import normalize_answer, phrase_language
from engine.mathcheck import X, equivalent, parse_answer

RULES = ("PRODUCT_AS_PRODUCT", "POW_NO_DECREMENT", "CHAIN_NO_INNER", "CONST_KEPT")
MAX_LINES = 30

RULE_FEEDBACK = {
    "PRODUCT_AS_PRODUCT": {
        "fr": "Tu as dérivé chaque facteur puis multiplié : la dérivée d'un produit n'est pas le produit des dérivées, "
              "utilise (u·v)' = u'·v + u·v'.",
        "en": "You differentiated each factor and multiplied: the derivative of a product is not the product of the "
              "derivatives, use (u·v)' = u'·v + u·v'.",
        "ar": "اشتققت كل عامل ثم ضربت: مشتقة الجداء ليست جداء المشتقات، استعمل (u·v)' = u'·v + u·v'."},
    "POW_NO_DECREMENT": {
        "fr": "Tu as multiplié par l'exposant mais tu n'as pas baissé l'exposant de 1.",
        "en": "You multiplied by the exponent but did not lower the exponent by 1.",
        "ar": "ضربت في الأس لكنك لم تُنقص الأس بواحد."},
    "CHAIN_NO_INNER": {
        "fr": "Il manque le facteur « dérivée de l'intérieur » : dérive aussi ce qui est entre parenthèses.",
        "en": "The factor “derivative of the inside” is missing: also differentiate what is in the brackets.",
        "ar": "ينقص العامل «مشتقة ما بداخل القوسين»: اشتق أيضاً ما بين القوسين."},
    "CONST_KEPT": {
        "fr": "La dérivée d'une constante est 0 : elle disparaît, elle ne reste pas.",
        "en": "The derivative of a constant is 0: it disappears, it does not stay.",
        "ar": "مشتقة الثابت تساوي 0: يختفي ولا يبقى."},
}
NOTES = {
    "wrong": {"fr": "Cette ligne ne correspond pas à la précédente ni à la dérivée.",
              "en": "This line matches neither the previous line nor the derivative.",
              "ar": "هذا السطر لا يطابق السطر السابق ولا المشتقة."},
    "inconsistent": {"fr": "Les deux côtés de cette égalité ne sont pas égaux.",
                     "en": "The two sides of this equality are not equal.",
                     "ar": "طرفا هذه المساواة غير متساويين."},
    "unchecked": {"fr": "Ligne non reconnue : je ne peux pas la vérifier.",
                  "en": "Line not recognised: I cannot check it.",
                  "ar": "سطر غير مفهوم: لا أستطيع التحقق منه."},
}


# ------------------------------------------------------------------ buggy derivatives
def bdiff(e: sympy.Expr, rule: str) -> sympy.Expr:
    """d/dx of e, differentiating structurally with exactly one rule applied wrongly."""
    if not e.has(X):
        return e if rule == "CONST_KEPT" else sympy.Integer(0)
    if e == X:
        return sympy.Integer(1)
    if e.is_Add:
        return sympy.Add(*[bdiff(t, rule) for t in e.args])
    if e.is_Mul:
        consts = sympy.Mul(*[a for a in e.args if not a.has(X)])
        parts = [a for a in e.args if a.has(X)]
        if len(parts) == 1:
            return consts * bdiff(parts[0], rule)
        if rule == "PRODUCT_AS_PRODUCT":
            return consts * sympy.Mul(*[bdiff(p, rule) for p in parts])
        u, v = parts[0], sympy.Mul(*parts[1:])
        return consts * (bdiff(u, rule) * v + u * bdiff(v, rule))
    if e.is_Pow and not e.exp.has(X):
        base, n = e.base, e.exp
        if rule == "POW_NO_DECREMENT":
            return n * base ** n * bdiff(base, rule)
        if rule == "CHAIN_NO_INNER":
            return n * base ** (n - 1)
        return n * base ** (n - 1) * bdiff(base, rule)
    return sympy.diff(e, X)


@lru_cache(maxsize=256)
def buggy_derivatives(f: sympy.Expr) -> tuple[tuple[str, sympy.Expr], ...]:
    """(rule, buggy derivative) for the rules that go wrong on f, without duplicates, in RULES order."""
    true = sympy.diff(f, X)
    out, seen = [], [true]
    for rule in RULES:
        b = bdiff(f, rule)
        if not any(equivalent(b, s) for s in seen):
            out.append((rule, b))
            seen.append(b)
    return tuple(out)


# ------------------------------------------------------------------ what the problem asks
@dataclass(frozen=True)
class Target:
    f: sympy.Expr
    kind: str = "derive"                      # derive | value (f'(a)) | tangent
    a: sympy.Expr | None = None

    @property
    def derivative(self) -> sympy.Expr:
        return sympy.diff(self.f, X)

    def allowed(self) -> list[sympy.Expr]:
        out = [self.f, self.derivative]
        if self.a is not None and self.kind in ("value", "tangent"):
            fa, da = self.f.subs(X, self.a), self.derivative.subs(X, self.a)
            out += [fa, da]
            if self.kind == "tangent":
                out.append(da * (X - self.a) + fa)
        return out

    def solution_text(self) -> str:
        if self.kind == "derive":
            return f"f'(x) = {pretty(self.derivative)}"
        da = self.derivative.subs(X, self.a)
        if self.kind == "value":
            return f"f'({pretty(self.a)}) = {pretty(da)}"
        return "y = " + pretty(sympy.expand(da * (X - self.a) + self.f.subs(X, self.a)))


_CUT = re.compile(r"\s+(?:au point|en x\b|à l'abscisse|at\b|pour\b|for\b|où\b|where\b|avec\b|with\b)|[;?\n]|\.\s|\.$")
_VERBS = re.compile(r"(?:d[ée]rive[rz]?|derive|differentiate|derivative of|la d[ée]riv[ée]e de|مشتقة|اشتق)\s+(?:la fonction\s+)?(.+)",
                    re.I)
_POINT = re.compile(r"(?:abscisse|\bx\s*=|\ba\s*=|\bat x\s*=)\s*(-?\d+(?:[.,]\d+)?)|f\s*'\s*\(\s*(-?\d+(?:[.,]\d+)?)\s*\)")


def _parse_function(text: str) -> sympy.Expr | None:
    t = _CUT.split(text.strip(), maxsplit=1)[0].strip().rstrip(".,")
    t = re.sub(r"^f\s*\(\s*x\s*\)\s*=\s*", "", t)
    while t:
        e = parse_answer(t)
        if e is not None and e.has(X):
            return e
        t = t.rsplit(" ", 1)[0] if " " in t else ""
    return None


def target_for(problem: str, exercise=None) -> Target | None:
    """The function and what is asked of it. From the bank exercise when given, else read from the problem text."""
    a = None
    if exercise is not None:
        f = parse_answer(exercise.expression) if exercise.expression else None
        if f is not None and f.has(X):
            a_txt = dict(exercise.params).get("a")
            a = parse_answer(a_txt) if a_txt else None
            sol = exercise.solution.strip()
            kind = "tangent" if sol.startswith("y") else "value" if re.match(r"f\s*'\s*\((?!x\))", sol) else "derive"
            return Target(f, kind, a) if kind == "derive" or a is not None else Target(f, "derive")
    text = str(problem)
    m = re.search(r"f\s*\(\s*x\s*\)\s*=\s*(.+)", text)
    f = _parse_function(m.group(1)) if m else None
    if f is None and (v := _VERBS.search(text)):
        f = _parse_function(v.group(1))
    if f is None:
        return None
    pm = _POINT.search(text)
    if pm:
        a = parse_answer((pm.group(1) or pm.group(2)).replace(",", "."))
    kind = "tangent" if re.search(r"tangent|مماس", text, re.I) and a is not None else \
        "value" if a is not None and re.search(r"f\s*'\s*\(", text) else "derive"
    return Target(f, kind, a)


# ------------------------------------------------------------------ lines
_LABEL = re.compile(r"^(?:f\s*'?\s*\([^()]*\)|f\s*'|y)$")
_LABEL_PARTS = re.compile(r"^(f'?|y)\s*(?:\(\s*([^()]*?)\s*\))?$")
_REL = re.compile(r"[<>≤≥≠]|\bet\b|\band\b")


@dataclass
class StepResult:
    index: int
    verdict: str                              # OK | WRONG | UNCHECKED
    note: str = ""                            # key into NOTES ("" for OK)
    value: sympy.Expr | None = None
    line: str = ""


Label = tuple[str, sympy.Expr | None] | None      # ("f'" | "f" | "y", the point or None for x)


def parse_line(line: str) -> tuple[sympy.Expr | None, bool, Label]:
    """(value, consistent, label). value = the last expression of the line, None = not parseable (UNCHECKED).
    label = what the line says it is, from its left-hand side: ("f'", None) for f'(x), ("f", 3) for f(3), ("y", None)."""
    s = line.strip().lstrip("⇒→").strip()
    if not s or _REL.search(s):
        return None, True, None
    parts = [p.strip() for p in s.split("=")]
    label: Label = None
    while parts and (parts[0] == "" or _LABEL.fullmatch(parts[0].replace(" ", ""))):
        head = parts.pop(0)
        if label is None and (m := _LABEL_PARTS.match(head.replace(" ", ""))):
            point = m.group(2)
            pt = None if point in (None, "", "x") else parse_answer(point)
            label = (m.group(1), pt) if point in (None, "", "x") or pt is not None else None
    if not parts:
        return None, True, None
    vals = [parse_answer(p) for p in parts]
    if any(v is None for v in vals):
        return None, True, None
    return vals[-1], all(equivalent(vals[0], v) for v in vals[1:]), label


def _label_targets(label: Label, target: Target) -> list[sympy.Expr] | None:
    """The one value a labelled line must have, or None when the label gives no constraint here."""
    if label is None:
        return None
    kind, pt = label
    if kind == "y":
        return [target.derivative.subs(X, target.a) * (X - target.a) + target.f.subs(X, target.a)] \
            if target.kind == "tangent" and target.a is not None else None
    base = target.derivative if kind == "f'" else target.f
    return [base if pt is None else base.subs(X, pt)]


def check_steps(lines: list[str], target: Target | None) -> list[StepResult]:
    out: list[StepResult] = []
    prev_ok, prev_val = False, None
    allowed = target.allowed() if target is not None else []
    for i, line in enumerate(lines):
        value, consistent, label = parse_line(line)
        if target is None or value is None:
            out.append(StepResult(i, "UNCHECKED", "unchecked", None, line))
            continue
        if not consistent:
            out.append(StepResult(i, "WRONG", "inconsistent", value, line))
            prev_ok, prev_val = False, value
            continue
        required = _label_targets(label, target)          # a labelled line must be what its label says
        ok = any(equivalent(value, t) for t in (required if required is not None else allowed)) or \
            (prev_ok and prev_val is not None and equivalent(value, prev_val) and
             (required is None or any(equivalent(value, t) for t in required)))
        out.append(StepResult(i, "OK" if ok else "WRONG", "" if ok else "wrong", value, line))
        prev_ok, prev_val = ok, value
    return out


def note(key: str, language: str | None) -> str:
    return NOTES[key][phrase_language(language)] if key else ""


# ------------------------------------------------------------------ why a line is wrong
def diagnose(value: sympy.Expr, target: Target, exercise, language: str | None) -> tuple[str, str] | None:
    """(feedback, source) with source "bank" or "rule:NAME". The exercise's own stored common mistakes win: they
    carry the feedback its author wrote for exactly that wrong answer."""
    lang = phrase_language(language)
    if exercise is not None:
        for expr, feedback in exercise.common_mistakes:
            m = parse_answer(expr)
            if m is not None and equivalent(value, m):
                return feedback, "bank"
    forms = [lambda b: b]
    if target.a is not None and target.kind in ("value", "tangent"):
        forms.append(lambda b: b.subs(X, target.a))                   # the same mistake, then evaluated at a
    for rule, bug in buggy_derivatives(target.f):
        if any(equivalent(value, fn(bug)) for fn in forms):
            return RULE_FEEDBACK[rule][lang], f"rule:{rule}"
    return None


def leaks(text: str, target: Target | None, solution: str | None) -> bool:
    """Would this guiding question hand over the answer? The hint guard (stored solution, verbatim or as "= value")
    plus the true derivative written out, in any of the usual notations."""
    from engine.tasks import reveals_answer
    if reveals_answer(text, solution):
        return True
    if target is None:
        return False
    flat = normalize_answer(text)
    for e in {target.derivative, *( [target.derivative.subs(X, target.a)] if target.a is not None else [])}:
        for form in {pretty(e), str(e).replace("**", "^").replace("*", "")}:
            n = normalize_answer(form)
            if len(n) >= 2 and n in flat:
                return True
    return False
