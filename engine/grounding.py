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

from engine.mathcheck import GROUNDING_VARS, equivalent, parse_expression

_MATH_CHARS = re.compile(r"[\d()+\-−*/^²³⁴⁵⁶⁷⁸⁹⁰¹ⁿ⁻√·×'≠≤≥<>\[\]→]")
_LETTERS_OK = {"x", "y", "f", "a", "b", "c", "h", "k", "m", "n", "u", "v", "t", "g"}
_FUNCS = {"ln", "lim", "sin", "cos", "tan", "exp", "sqrt", "log"}
_EDGE_PUNCT = ".,;:!?…»«\"“”،؛؟)"
_OPS = set("+-−*/×·^(")


_WORD = re.compile(r"[A-Za-zÀ-ÿ\u0600-\u06FF]{3,}")
_COMPARE = re.compile(r"[<>≤≥≠]")
_COEFF_X = re.compile(r"[abckm]x")                  # a coefficient times x: "bx" in x² + bx + c (no other 2-letter token: "by", "at", "un")


def _is_math(tok: str) -> bool:
    t = tok.strip(_EDGE_PUNCT + "(")
    if not t or _COMPARE.search(t):
        return False                                   # an inequality ends the formula side
    words = [w for w in _WORD.findall(t) if w.lower() not in _FUNCS]
    if words:                                          # "(puisque", "C'est", "(racine": prose, not math
        return False
    return bool(_MATH_CHARS.search(tok)) or t.lower() in _FUNCS or t in _LETTERS_OK or bool(_COEFF_X.fullmatch(t))


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
        if out and _single(tok) and not ((out[0][:1] in _OPS and not (tok == "a" and out[0][:1] == "(")) or
                                         (i > 0 and _is_math(toks[i - 1]) and not _single(toks[i - 1]))):
            break                                      # ("on a (x+1)² = ..." : the French verb "a" is not a factor)
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


_LONE_LETTER = re.compile(r"(?<![A-Za-zÀ-ÿ])([A-Za-z])(?![A-Za-zÀ-ÿ(])")
_DANGLING_END = re.compile(r"[+\-−*/×·^(=,]$")


def _unknown_letter(text: str) -> bool:
    """A lone latin letter outside the parser's symbols (x a b c h k m n t u v y) and the function letters f g."""
    return any(c.lower() not in GROUNDING_VARS and c.lower() not in "fg" for c in _LONE_LETTER.findall(text))


def incomplete(answer: str, span: str, rhs: str | None = None) -> bool:
    """True when a span the extractor cut cannot be judged: it ends on an operator or an open bracket (the next token was
    not math: a symbol the parser does not know, a word), it holds a lone letter the parser does not know ("x² + p"), or
    the answer continues with such a letter right after it. Such a span is reported as unchecked, not as ungrounded: the
    check could not read it, which is no evidence against it."""
    tail = (rhs if rhs is not None else span).rstrip()
    if _DANGLING_END.search(tail) or tail.count("(") > tail.count(")"):
        return True
    if _unknown_letter(tail):
        return True
    at = answer.find(tail) if tail else -1
    if at >= 0:
        m = re.match(r"\s*([A-Za-z])(?![A-Za-zÀ-ÿ])", answer[at + len(tail):])
        if m and m.group(1).lower() not in GROUNDING_VARS and m.group(1).lower() not in "fg":
            return True
    return False


# ------------------------------------------------------------------ verbal equality (v1.4)
# "(x² + 2x)/x est égale à 2x + 2", "f(2) vaut 4", "2x + 2 equals x", "يساوي": between two math runs these are "=".
# Conservative on purpose: BOTH neighbouring tokens must be math (so "la limite est égale à 2x" is not read: "limite" is a word),
# and the short "is" counts only directly between two math tokens.
_VERBAL = re.compile(
    r"(?<![\w])(?:est\s+[ée]gale?\s+[àa]|[ée]gale?\s+[àa]|[ée]gale?|vaut|is\s+equal\s+to|equals?|is|يساوي|تساوي)(?![\w])",
    re.I)


# A math run that is the ARGUMENT of a derivative / an integral / a value ("la dérivée de x² est égale à 2x", "the derivative of x²
# is 2x", "مشتقة x² تساوي 2x") is not what the spoken equality is about, so it is not read. A limit is NOT in the list: "la limite de
# (x² + 2x)/x est égale à 2x + 2" is judged as the equality it states (and a true limit written this way, e.g. the limit of
# (x² − 1)/(x − 1) at 1 equal to 2, is a known false-positive risk of reading it).
_ARGUMENT_NOUNS = frozenset("dérivée derivee dérivé derive derivative derivatives intégrale integrale integral valeur value مشتقة مشتقه مشتق تكامل قيمة دالة".split())
_PREPOSITIONS = frozenset("de du des d of for pour".split())


def _bare(word: str) -> str:
    return re.sub(r"^[^\w]+|[^\w]+$", "", word.lower().split("'")[-1].split("’")[-1])


def _chained(before: list[str], after: list[str]) -> bool:
    """The spoken equality sits inside a written one ("of f(x) = x² − 4x + 1 is f'(x) = 2x − 4"): the run on its left already is
    the right side of an "=", or the run on its right is the left side of one. It is a copula there, not an equality."""
    k = len(before) - 1
    while k >= 0 and _is_math(before[k]):
        k -= 1
    if k >= 0 and before[k].endswith("="):
        return True
    j = 0
    while j < len(after) and _is_math(after[j]):
        j += 1
    return j < len(after) and after[j].startswith("=")


def _argument_of_a_word(before: list[str]) -> bool:
    k = len(before) - 1
    while k >= 0 and _is_math(before[k]):
        k -= 1
    if k < 0 or k == len(before) - 1:
        return False
    word = _bare(before[k])
    if word in _ARGUMENT_NOUNS or before[k].lower() in _ARGUMENT_NOUNS:
        return True
    return word in _PREPOSITIONS and k > 0 and _bare(before[k - 1]) in _ARGUMENT_NOUNS


# ------------------------------------------------------------------ spoken limits (v1.4)
# "la limite de ((x+h)² − x²)/h quand h tend vers 0 est égale à 2x": judged with sympy.limit at the stated point, never as the
# identity it would be without one. No stated point -> the claim is listed as unchecked, never flagged.
_LIMIT_NOUNS = frozenset("limite limit نهاية نهايه".split())
_INF_TOKENS = frozenset(["∞", "+∞", "-∞", "−∞", "infini", "l'infini", "l’infini", "+infini", "infinity", "+infinity", "-infinity", "−infinity"])
_ARROW = r"(?:tend(?:ent)?\s+vers|tends?\s+to|approach(?:es)?|→|->|⟶|تؤول\s+إلى|يؤول\s+إلى|تقترب\s+من|يقترب\s+من|تسعى\s+إلى)"
_POINT = r"([+\-−]?\s*(?:∞|l['’]infini|infini|infinity|\d+(?:[.,]\d+)?|[a-z]\b))"      # infinity first: "l'infini" is not the letter l
_CLAUSE_END = re.compile(r"(?:quand|lorsque|lorsqu['’]|when|as|عندما|حين)\s+([a-z])\s*" + _ARROW + r"\s*" + _POINT + r"[\s,]*$", re.I)
_CLAUSE_ANY = re.compile(r"(?:quand|lorsque|lorsqu['’]|when|as|عندما|حين)\s+([a-z])\s*" + _ARROW + r"\s*" + _POINT, re.I)
_ARROW_ANY = re.compile(r"\b([a-z])\s*(?:→|->|⟶)\s*" + _POINT, re.I)
_LIM_TOKEN = re.compile(r"^lim[_({\s]*([a-z])\s*(?:→|->|⟶|\\to)\s*([^)}\s]+)[)}\s]*$", re.I)


def _limit_point(var_and_point: tuple[str, str]) -> tuple[str, str]:
    var, pt = var_and_point
    pt = re.sub(r"\s+", "", pt.replace("’", "'").replace("−", "-"))
    if pt.lstrip("+-").lower() in {x.lstrip("+-−").lower() for x in _INF_TOKENS}:
        return var, "-∞" if pt.startswith("-") else "∞"                # one spelling of infinity in the reports
    return var, pt


def _find_point(text: str, start: int, end: int) -> tuple[str, str] | None:
    """The stated (variable, point) in the sentence that holds text[start:end]."""
    lo = max(text.rfind(". ", 0, start), text.rfind("\n", 0, start)) + 1
    hi_candidates = [i for i in (text.find(". ", end), text.find("\n", end)) if i >= 0]
    hi = min(hi_candidates) if hi_candidates else len(text)
    sentence = text[lo:hi]
    for rx in (_CLAUSE_ANY, _ARROW_ANY):
        m = rx.search(sentence)
        if m:
            return _limit_point((m.group(1).lower(), m.group(2)))
    return None


def _limit_claim(text: str, m: re.Match, before: list[str], after: list[str]) -> dict | None:
    """If the spoken equality at `m` states a limit, the claim {expr, var, point, rhs, span_start}; else None.
    The expression sits right before the point clause, after "limite de" / "limit of" / "lim(h→0)" / "نهاية"."""
    head = text[:m.start()]
    clause = _CLAUSE_END.search(head)
    var = pt = None
    if clause:
        var, pt = _limit_point((clause.group(1).lower(), clause.group(2)))
        head = head[:clause.start()].rstrip(" ,")
    toks = head.split()
    k = len(toks)
    while k > 0 and _is_math(toks[k - 1]):
        k -= 1
    run = toks[k:]
    if not run:
        return None
    word_before = _bare(toks[k - 1]) if k > 0 else ""
    first = run[0]
    expr_tokens = run
    lim_tok = _LIM_TOKEN.match(first)
    if lim_tok:                                                 # lim(h→0) (...) : the point is in the first token
        var, pt = _limit_point((lim_tok.group(1).lower(), lim_tok.group(2)))
        expr_tokens = run[1:]
    elif first.lower() == "lim" and len(run) > 1 and _LIM_TOKEN.match("lim" + run[1]):
        lm = _LIM_TOKEN.match("lim" + run[1])
        var, pt = _limit_point((lm.group(1).lower(), lm.group(2)))
        expr_tokens = run[2:]
    elif word_before in _LIMIT_NOUNS or (word_before in _PREPOSITIONS and k > 1 and _bare(toks[k - 2]) in _LIMIT_NOUNS):
        pass
    else:
        return None
    if not expr_tokens:
        return None
    # the right-hand side: the math tokens (or ∞) that follow, up to a comma / full stop
    rhs_tokens = []
    for tok in after:
        bare = tok.rstrip(".,;:؛،")
        if not (_is_math(tok) or bare in _INF_TOKENS):
            break
        rhs_tokens.append(bare)
        if tok != bare:
            break
    if not rhs_tokens:
        return None
    if pt is None:
        found = _find_point(text, m.start() - len(" ".join(toks[k:])) - 2, m.end() + len(" ".join(after[:len(rhs_tokens)])))
        if found:
            var, pt = found
    return {"expr": " ".join(expr_tokens), "rhs": " ".join(rhs_tokens), "var": var, "point": pt}


def _parse_point(pt: str):
    t = pt.replace("−", "-").replace("’", "'").strip()
    if t.lower().lstrip("+-") in {x.lstrip("+-−").lower() for x in _INF_TOKENS}:
        return -sympy.oo if t.startswith("-") else sympy.oo
    return parse_expression(t)


def _show(value) -> str:
    if value == sympy.oo:
        return "∞"
    if value == -sympy.oo:
        return "-∞"
    return _simplified(value)


def judge_limit(claim: dict, out: dict) -> None:
    """Verified / wrong (with the correct limit in out["forms"]) / unchecked, into `out`."""
    spoken = f"{claim['expr']} = {claim['rhs']}"
    if not claim.get("var") or claim.get("point") is None:
        out["unchecked"].append(spoken)                           # no stated point: never flagged
        return
    var = sympy.Symbol(claim["var"])
    expr, point, rhs = parse_expression(claim["expr"]), _parse_point(claim["point"]), None
    rhs_text = claim["rhs"].strip()
    rhs = _parse_point(rhs_text) if rhs_text.lstrip("+-−").lower() in {x.lstrip("+-−").lower() for x in _INF_TOKENS} else parse_expression(rhs_text)
    if expr is None or point is None or rhs is None or var not in expr.free_symbols:
        out["unchecked"].append(spoken)
        return
    try:
        res = sympy.limit(expr, var, point)
    except Exception:
        out["unchecked"].append(spoken)
        return
    if isinstance(res, sympy.Limit) or res.has(sympy.nan, sympy.zoo):
        out["unchecked"].append(spoken)
        return
    span = f"lim({claim['var']}→{claim['point']}) {claim['expr']} = {claim['rhs']}"
    same = (res == rhs) if (res.is_infinite or rhs.is_infinite) else equivalent(res, rhs)
    if same:
        out["verified"].append(span)
    else:
        out["wrong"].append(span)
        out["forms"][span] = f"lim({claim['var']}→{claim['point']}) {claim['expr']} = {_show(res)}"


def verbalize(text: str) -> tuple[str, set[tuple[str, str]], list[dict]]:
    """The text with verbal equalities between two math tokens replaced by "=", the (previous token, next token) pairs
    (normalized) that mark which "=" were spoken, and the spoken LIMIT claims, which are not turned into "=" (the
    checker judges them with sympy.limit at the stated point)."""
    out, pairs, limits, pos = [], set(), [], 0
    for m in _VERBAL.finditer(text):
        if m.start() < pos:
            continue
        before, after = text[:m.start()].split(), text[m.end():].split()
        claim = _limit_claim(text, m, before, after)
        if claim is not None:
            limits.append(claim)
            out.append(text[pos:m.start()])
            out.append(" ⊢ ")                                       # not math: no written equation is built from it
            pos = m.end()
            continue
        if not before or not after or not _is_math(before[-1]) or not _is_math(after[0]):
            continue
        if before[-1].rstrip().endswith((",", ";", ":", ".")):
            continue                                                  # a sentence boundary, not an equation
        if _argument_of_a_word(before) or _chained(before, after):
            continue
        out.append(text[pos:m.start()])
        out.append("=")
        pos = m.end()
        pairs.add((_norm(before[-1]), _norm(after[0])))
    out.append(text[pos:])
    return "".join(out), pairs, limits


def _simplified(e: sympy.Expr) -> str:
    return str(sympy.simplify(e)).replace("**", "^").replace("*", "")


# "f'(a) = lim h→0 (f(a + h) − f(a)) / h": the definition of the derivative, however the limit is written. The French
# course states it in words ("la limite du taux d'accroissement quand h tend vers 0"), so the written form matched no
# source and a correct English definition was flagged `ungrounded_formula` (FelCloud replay 2026-11-11, UNI-01).
_DIFF_QUOTIENT = re.compile(r"\(?f\(([a-z0-9]+)\+([a-z])\)[-−]f\(\1\)\)?/\(?\2\)?")


def _is_limit_definition(m_der: re.Match, rhs: str) -> bool:
    flat = re.sub(r"\s+", "", rhs.lower())
    q = _DIFF_QUOTIENT.search(flat)
    return bool(q) and flat.startswith("lim") and q.group(1) == m_der.group(2).replace(" ", "").lower() \
        and re.search(q.group(2) + r"(?:→|->|⟶|tendvers|tendsto|\\to)\(?0", flat) is not None


def check_formulas(answer: str, sources: list[str]) -> dict:
    """Classify every formula of an explain answer:
      verified      derivative claims checked with sympy against the answer's own f(x) = E, (E)' notation,
                    and identities whose two sides parse (3 × 2x = 6x)
      wrong         a claim sympy can check that is FALSE (f'(x) = 3x for f(x) = x², 3 × 2 = 7)
      ungrounded    neither verifiable nor in the sources (the limit definition when the course lacks it)
      skipped       single-variable assignments (u = 1, n = 2) and equations to solve (2x − 4 = 0)
      new_examples  functions the answer defines that the sources do not (correct ones pass)"""
    out = {"verified": [], "wrong": [], "ungrounded": [], "skipped": [], "new_examples": [], "unchecked": [], "forms": {}}
    answer, verbal, limits = verbalize(answer)
    for claim in limits:
        judge_limit(claim, out)
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
            elif not _SINGLE_VAR.fullmatch(l) and any(l.endswith(p) and _norm(rhs).startswith(n) for p, n in verbal):
                out["wrong"].append(span)                             # "A est égale à B", both sides math, not equal: a false claim
                out["forms"][span] = f"{lhs} = {_simplified(le)}"
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
        if m_der and _is_limit_definition(m_der, rhs):
            out["verified"].append(span)                              # f'(a) = lim (f(a + h) − f(a)) / h: the definition
            continue
        if not l and r is not None and not r.free_symbols and (_OPERATOR.search(_norm(rhs)) or re.search(r"\)\s*\(", rhs)):
            # "detA = (2)(5) - (3)(4)": a named quantity worked out on numbers. It is arithmetic on an example, not a
            # formula the course should contain; flagged, it filled the professor's review queue with correct answers
            # (FelCloud scenarios 2026-11-11, finding 6). A bare number ("la pente = 7") is still reported.
            out["skipped"].append(span)
            continue
        if incomplete(answer, span, rhs):
            out["unchecked"].append(span)                             # cut at a symbol the parser does not know
            continue
        out["ungrounded"].append(span)
    flat = _norm(" ".join(sources))
    sides = [side for eq in pool for side in eq]
    reported = out["ungrounded"] + out["wrong"] + out["verified"] + out["skipped"] + out["new_examples"] + out["unchecked"]
    for f in formulas(answer):
        if any(_norm(f) in _norm(s) or _norm(s).endswith(_norm(f)) for s in reported):
            continue
        if _norm(f) in flat or any(_rhs_match(f, side) for side in sides) or _expr(f) is not None:
            continue
        if incomplete(answer, f):
            out["unchecked"].append(f)
        elif f not in out["ungrounded"]:
            out["ungrounded"].append(f)
    return out
