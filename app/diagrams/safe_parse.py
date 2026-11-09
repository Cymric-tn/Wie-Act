"""Safe parsing: learner text never reaches sympy's parser unless it passes a whitelist."""
import re
import sympy as sp
from sympy.parsing.sympy_parser import (
    parse_expr, standard_transformations, implicit_multiplication_application)
from .spec import DiagramError

x = sp.Symbol("x", real=True)
FUNCS = {"sqrt": sp.sqrt, "ln": sp.log, "exp": sp.exp, "sin": sp.sin, "cos": sp.cos, "tan": sp.tan}
NAMES = "sqrt|ln|exp|sin|cos|tan|pi"
SUP = {"²": "2", "³": "3", "⁻": "-", "ˣ": "x"}
TRANSFORMS = standard_transformations + (implicit_multiplication_application,)
MAX_LEN = 80


class ParseError(DiagramError):
    pass


def normalize(text: str) -> str:
    s = str(text).strip().lower()
    if not s:
        raise ParseError("empty expression")
    if len(s) > MAX_LEN:
        raise ParseError(f"expression too long ({MAX_LEN} characters max)")
    s = re.sub(r"^f\s*\(\s*x\s*\)\s*=\s*", "", s)
    for a, b in (("×", "*"), ("·", "*"), ("−", "-"), ("–", "-"), ("√", "sqrt"), ("π", "pi"), ("^", "**")):
        s = s.replace(a, b)
    s = re.sub(r"[²³⁻ˣ]+", lambda m: "**(" + "".join(SUP[c] for c in m.group()) + ")", s)
    s = re.sub(r"(\d),(\d)", r"\1.\2", s)                                   # French decimal comma
    s = re.sub(r"(?<![a-z])(sqrt|ln|exp|sin|cos|tan)\s*x(?![a-z0-9])", r"\1(x)", s)  # sin x -> sin(x)
    s = re.sub(r"(?<![a-z])(sqrt|ln|exp|sin|cos|tan)\s*(\d+(?:\.\d+)?)", r"\1(\2)", s)  # sqrt3 -> sqrt(3)
    s = re.sub(r"(?<=\d)\s*(?=(?:sqrt|ln|exp|sin|cos|tan|pi)(?![a-z]))", "*", s)  # 3sqrt(3) -> 3*sqrt(3)
    # whitelist: after removing allowed names only digits, x, e and operators may remain
    if not re.fullmatch(r"[0-9xe+\-*/().\s]*", re.sub(NAMES, "", s)):
        raise ParseError("forbidden characters in expression")
    if re.search(r"\d{7,}", s) or re.search(r"\*\*\s*\(?\s*-?\s*\d{3,}", s) \
            or re.search(r"\*\*\s*\(?\s*-?\s*\d+(\.\d+)?\s*\)?\s*\*\*", s):
        raise ParseError("number or exponent too large")
    return s


def parse(text) -> sp.Expr:
    if isinstance(text, sp.Basic):
        return text
    s = normalize(text)
    try:
        e = parse_expr(s, local_dict={**FUNCS, "x": x, "e": sp.E, "pi": sp.pi}, transformations=TRANSFORMS)
    except Exception as ex:  # noqa: BLE001
        raise ParseError(f"cannot read expression: {ex.__class__.__name__}") from None
    if not isinstance(e, sp.Expr) or (e.free_symbols - {x}):
        raise ParseError("not a function of x")
    return e


def pretty(e) -> str:
    """Exact, human-readable text for a sympy value (never rounded)."""
    if e == sp.oo:
        return "+∞"
    if e == -sp.oo:
        return "-∞"
    s = sp.sstr(e)
    s = re.sub(r"\bE\b", "e", s)
    s = re.sub(r"exp\(([^()]*)\)", r"e^(\1)", s)
    s = re.sub(r"sqrt\(([^()]*)\)",
               lambda m: "√" + (m.group(1) if re.fullmatch(r"\w+", m.group(1)) else f"({m.group(1)})"), s)
    s = s.replace("**2", "²").replace("**3", "³").replace("**", "^").replace("log", "ln").replace("pi", "π")
    s = re.sub(r"(?<=\d)\*(?=[xπ√(e])", "", s)
    return s.replace("*", "·")
