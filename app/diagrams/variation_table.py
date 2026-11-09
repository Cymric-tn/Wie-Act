import sympy as sp
from sympy.calculus.util import continuous_domain
from .safe_parse import parse, x, pretty
from .spec import DiagramError, VariationTablePayload, make
from .i18n import t


def _crit(fp, iv, lang):
    s = sp.solveset(fp, x, iv)
    if s == sp.S.EmptySet:
        return []
    if not isinstance(s, sp.FiniteSet):
        raise DiagramError(t(lang, "err_solve"))
    return sorted(s, key=float)


def _test_point(a, b):
    if a == -sp.oo and b == sp.oo:
        return sp.Integer(0)
    if a == -sp.oo:
        return b - 1
    if b == sp.oo:
        return a + 1
    return (a + b) / 2


def _lim(f, c, side=None):
    v = sp.limit(f, x, c, side) if side and c.is_finite else sp.limit(f, x, c)
    if v.is_real is not True and not v.is_infinite:
        raise DiagramError("limit not real")
    return v


def _periodic(f):
    try:
        return sp.periodicity(f, x) is not None
    except Exception:  # noqa: BLE001
        return False


def build_variation_table(expr, domain=None, lang="fr", mode=None):
    f = parse(expr)
    if domain is None and _periodic(f):
        raise DiagramError(t(lang, "err_periodic"))
    fp = sp.simplify(sp.diff(f, x))
    lo, hi = (-sp.oo, sp.oo) if domain is None else (parse(str(domain[0])), parse(str(domain[1])))
    try:
        dom = continuous_domain(f, x, sp.S.Reals).intersect(sp.Interval(lo, hi))
    except Exception:  # noqa: BLE001
        raise DiagramError(t(lang, "err_domain")) from None
    ivs = list(dom.args) if isinstance(dom, sp.Union) else [dom]
    if not ivs or not all(isinstance(i, sp.Interval) for i in ivs):
        raise DiagramError(t(lang, "err_domain"))
    ivs.sort(key=lambda i: float(i.start))

    cols = []  # (kind, value)
    for i, iv in enumerate(ivs):
        if i == 0:
            cols.append(("edge", iv.start))
        elif ivs[i - 1].end != iv.start:
            raise DiagramError(t(lang, "err_domain"))
        cols += [("crit", c) for c in _crit(fp, iv, lang)]
        cols.append(("forbid", iv.end) if i < len(ivs) - 1 else ("edge", iv.end))

    signs = []
    for (_, a), (_, b) in zip(cols, cols[1:]):
        v = sp.N(fp.subs(x, _test_point(a, b)))
        if not v.is_real or v == 0:
            raise DiagramError(t(lang, "err_verify"))
        signs.append("+" if v > 0 else "-")
    trends = ["up" if s == "+" else "down" for s in signs]

    left, right, n = [], [], len(cols)  # value on each side of each column
    for i, (kind, c) in enumerate(cols):
        if kind == "crit":
            v = sp.simplify(f.subs(x, c))
            left.append(v); right.append(v)
        elif kind == "forbid":
            left.append(_lim(f, c, "-")); right.append(_lim(f, c, "+"))
        elif i == 0:
            v = _lim(f, c, "+"); left.append(v); right.append(v)
        else:
            v = _lim(f, c, "-"); left.append(v); right.append(v)

    for j, tr in enumerate(trends):  # never draw an inconsistent table
        a, b = float(right[j]), float(left[j + 1])
        if (tr == "up" and not a < b) or (tr == "down" and not a > b):
            raise DiagramError(t(lang, "err_verify"))

    out_signs = []
    for j, s in enumerate(signs):
        out_signs.append(s)
        if j + 1 < len(signs):
            out_signs.append("||" if cols[j + 1][0] == "forbid" else "0")
    names = [pretty(c) for _, c in cols]
    values = []
    for i, (kind, _) in enumerate(cols):
        v = {"at": names[i], "f": pretty(left[i] if i else right[i])}
        if kind == "forbid":
            v["f_right"] = pretty(right[i])
        elif kind == "crit" and 0 < i < n - 1:
            if signs[i - 1] == "+" and signs[i] == "-":
                v["kind"] = "max"
            elif signs[i - 1] == "-" and signs[i] == "+":
                v["kind"] = "min"
        values.append(v)
    payload = VariationTablePayload(columns=names, derivative_signs=out_signs, values=values, trends=trends, mode="practice" if mode == "practice" else None)
    return make("variation_table", lang, t(lang, "v_title", f=pretty(f)), payload)
