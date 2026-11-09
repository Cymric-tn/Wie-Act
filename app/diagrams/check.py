"""Fill-the-table checker. Exact (sympy), stateless: the module stores nothing, it only returns a result."""
import re
import sympy as sp
from .safe_parse import parse
from .spec import DiagramError

INF = re.compile(r"^([+-]?)(∞|oo|inf|infinity|infini)$", re.I)


def value_of(s):
    """'-2.0', '−2', '√3', 'π/2', '-∞' -> sympy value, or None when unreadable."""
    s = str(s or "").strip().replace("−", "-").replace("–", "-").replace(" ", "")
    if not s:
        return None
    m = INF.match(s)
    if m:
        return -sp.oo if m.group(1) == "-" else sp.oo
    try:
        v = parse(s)
    except DiagramError:
        return None
    return None if v.free_symbols else v


def same(expected, given):
    ve, vg = value_of(expected), value_of(given)
    if vg is None:
        return False
    if ve is None:
        return str(expected).strip() == str(given).strip()
    if ve.is_infinite or vg.is_infinite:
        return ve == vg
    try:
        return abs(complex(sp.N(ve - vg))) < 1e-9
    except Exception:  # noqa: BLE001
        return False


def _sign(s):
    return str(s or "").strip().replace("−", "-").replace("–", "-").replace("‖", "||")


def check_table(spec, answer):
    """answer = {columns:[str], signs:[str], vals:[{f, f_right}], trends:["up"|"down"|""]}
    -> {correct, mistakes:[{row, column, expected, given}]}
    rows: x (column index) · f_prime (index in derivative_signs) · arrows (interval index) · f / f_right (column index)"""
    p = spec["payload"]
    mistakes = []

    def bad(row, col, exp, given):
        mistakes.append({"row": row, "column": col, "expected": exp, "given": given})

    def get(lst, i, default=""):
        return lst[i] if i < len(lst) else default

    for i, e in enumerate(p["columns"]):
        g = get(answer.get("columns", []), i)
        if not same(e, g):
            bad("x", i, e, g)
    for k, e in enumerate(p["derivative_signs"]):
        g = _sign(get(answer.get("signs", []), k))
        if g != e:
            bad("f_prime", k, e, g)
    for j, e in enumerate(p["trends"]):
        g = get(answer.get("trends", []), j)
        if g != e:
            bad("arrows", j, e, g)
    for i, v in enumerate(p["values"]):
        cell = get(answer.get("vals", []), i, {}) or {}
        if not same(v["f"], cell.get("f", "")):
            bad("f", i, v["f"], cell.get("f", ""))
        if v.get("f_right") is not None and not same(v["f_right"], cell.get("f_right", "")):
            bad("f_right", i, v["f_right"], cell.get("f_right", ""))
    return {"correct": not mistakes, "mistakes": mistakes}


def solution_of(spec):
    """The answer key as an answer object (used by reveal() and tests)."""
    p = spec["payload"]
    return {"columns": list(p["columns"]), "signs": list(p["derivative_signs"]),
            "vals": [{"f": v["f"], "f_right": v.get("f_right", "")} for v in p["values"]], "trends": list(p["trends"])}
