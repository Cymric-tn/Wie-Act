"""
Deterministic answer checking with sympy: parse a learner's answer and a stored solution, decide
symbolic equivalence. No LLM.

Accepted: x^2, x², x**2, 2x and other implicit products, x(x + 3), √x / √(x+1) / sqrt(x), unicode minus,
×, ·, French decimal commas (2,5), and a left-hand side that is dropped ("f'(x) = …", "y = …", "f'(3) = …").
Anything else (a sentence, another variable) -> None, and the caller uses the LLM grader instead.

SECURITY: sympy's parser evaluates Python. Learner text is therefore reduced to a whitelist (digits, x,
+ - * / ( ) . and the word sqrt) BEFORE it reaches the parser; anything outside it is rejected, never parsed.
"""
from __future__ import annotations

import re
from functools import lru_cache

import sympy
from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication, parse_expr, split_symbols,
                                        standard_transformations)

X = sympy.Symbol("x")
_LOCALS = {"x": X, "sqrt": sympy.sqrt}
_TRANSFORMS = standard_transformations + (implicit_multiplication, convert_xor)
_SUP = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-",
        "ⁿ": "n"}
_SUP_RUN = re.compile("[" + "".join(_SUP) + "]+")
_ALLOWED = re.compile(r"[0-9x+\-*/(). ]*")
_LHS_OK = re.compile(r"(f\s*'?\s*\([^()]*\)|f\s*'|y)")      # f'(x), f(x), f'(3), f', y
MAX_LEN = 80


def _prepare(text: str) -> str | None:
    s = str(text).strip().rstrip(".").strip()
    if not s or len(s) > MAX_LEN:
        return None
    if "=" in s:                                   # keep the right-hand side: f'(x) = 4x³, y = 4x - 4
        lhs, s = (part.strip() for part in s.rsplit("=", 1))
        if not _LHS_OK.fullmatch(lhs):             # "maximum 5 atteint en x = 2" is a sentence, not "= 2"
            return None
    s = _SUP_RUN.sub(lambda m: "^(" + "".join(_SUP[c] for c in m.group()) + ")", s)
    for a, b in (("−", "-"), ("–", "-"), ("×", "*"), ("·", "*"), ("÷", "/"), ("X", "x")):
        s = s.replace(a, b)
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)          # French decimal comma
    s = re.sub(r"√\s*\(", "sqrt(", s)
    s = re.sub(r"√\s*([0-9]*x|[0-9.]+)", r"sqrt(\1)", s)
    s = s.replace("^", "**")
    if not _ALLOWED.fullmatch(s.replace("sqrt", "")):
        return None                                # words, other letters, symbols: not ours to parse
    return s or None


@lru_cache(maxsize=1024)
def parse_answer(text: str) -> sympy.Expr | None:
    s = _prepare(text)
    if s is None:
        return None
    try:
        expr = parse_expr(s, local_dict=dict(_LOCALS), transformations=_TRANSFORMS, evaluate=True)
    except Exception:                              # SyntaxError, TokenError, TypeError...: not parseable
        return None
    if not isinstance(expr, sympy.Expr) or not expr.free_symbols <= {X}:
        return None
    return expr


def equivalent(a: sympy.Expr, b: sympy.Expr) -> bool:
    try:
        return sympy.simplify(a - b) == 0
    except Exception:
        return False


# ------------------------------------------------------------------ general expressions (grounding check)
GROUNDING_VARS = "xahtnkuvy"
_GEN_SYMBOLS = {c: sympy.Symbol(c) for c in GROUNDING_VARS}
_GEN_FUNCS = {"sqrt": sympy.sqrt, "ln": sympy.log, "exp": sympy.exp, "sin": sympy.sin, "cos": sympy.cos,
              "tan": sympy.tan}
_GEN_TRANSFORMS = standard_transformations + (split_symbols, implicit_multiplication, convert_xor)   # 2ah = 2*a*h
_GEN_ALLOWED = re.compile(r"[0-9" + GROUNDING_VARS + r"+\-*/(). ]*")


@lru_cache(maxsize=4096)
def parse_expression(text: str) -> sympy.Expr | None:
    """A formula side from course text or an answer, variables x a h t n k u v y. Same whitelist rule as
    parse_answer (nothing else reaches sympy's parser); f(…), f'(…), lim, words -> None (exact match instead)."""
    s = str(text).strip().rstrip(".,;:").strip()
    if not s or len(s) > MAX_LEN:
        return None
    s = _SUP_RUN.sub(lambda m: "^(" + "".join(_SUP[c] for c in m.group()) + ")", s)
    for a, b in (("−", "-"), ("–", "-"), ("×", "*"), ("·", "*"), ("÷", "/")):
        s = s.replace(a, b)
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)
    s = re.sub(r"√\s*\(", "sqrt(", s)
    s = re.sub(r"√\s*([0-9]*[a-z]|[0-9.]+)", r"sqrt(\1)", s)
    s = s.replace("^", "**")
    # (lookarounds, not \b: "2sqrt(x)" has no word boundary between the digit and the name)
    if not _GEN_ALLOWED.fullmatch(re.sub(r"(?<![a-z])(sqrt|ln|exp|sin|cos|tan)(?![a-z])", "", s)):
        return None
    try:
        expr = parse_expr(s, local_dict={**_GEN_SYMBOLS, **_GEN_FUNCS}, transformations=_GEN_TRANSFORMS)
    except Exception:
        return None
    return expr if isinstance(expr, sympy.Expr) else None
