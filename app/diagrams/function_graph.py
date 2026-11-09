import math
import sympy as sp
from .safe_parse import parse, x, pretty
from .spec import DiagramError, FunctionGraphPayload, make
from .i18n import t

N = 400
PERIODIC = (sp.sin, sp.cos, sp.tan)


def real_roots(expr, dom=sp.S.Reals):
    try:
        s = sp.solveset(expr, x, dom)
    except Exception:  # noqa: BLE001
        return []
    return sorted((v for v in s if v.is_real), key=float) if isinstance(s, sp.FiniteSet) else []


def singular_candidates(f):
    cand = set()
    for p in f.atoms(sp.Pow):
        b, ex = p.as_base_exp()
        if ex.is_negative or (ex.is_Rational and not ex.is_Integer):
            cand |= set(real_roots(b))
    for l in f.atoms(sp.log):
        cand |= set(real_roots(l.args[0]))
    return sorted(cand, key=float)


def classify(f, a):
    """-> ('asymptote'|'hole'|None, limit)"""
    lims = []
    for side in ("-", "+"):
        try:
            lims.append(sp.limit(f, x, a, side))
        except Exception:  # noqa: BLE001
            lims.append(None)
    if any(v is not None and v.is_infinite for v in lims):
        return "asymptote", None
    l, r = lims
    if l is not None and r is not None and l == r and l.is_real is True:
        fa = f.subs(x, a)
        if fa.is_real is not True:
            return "hole", l
    return None, None


def _ev(fn, v):
    try:
        y = fn(v)
        if isinstance(y, complex):
            if abs(y.imag) > 1e-12:
                return None
            y = y.real
        y = float(y)
        return None if y != y or abs(y) == math.inf else y
    except (ValueError, ZeroDivisionError, OverflowError, TypeError):
        return None


def _segments(xs, ys, cuts, band, jump):
    segs, cur = [], []
    for xv, yv in zip(xs, ys):
        if yv is None or not (band[0] <= yv <= band[1]):
            if len(cur) > 1:
                segs.append(cur)
            cur = []
            continue
        if cur and (any(cur[-1][0] < c < xv for c in cuts) or abs(yv - cur[-1][1]) > jump):
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        cur.append([round(xv, 4), round(yv, 4)])
    if len(cur) > 1:
        segs.append(cur)
    return segs


def _nice(r):
    e = 10 ** math.floor(math.log10(r))
    m = r / e
    return (1 if m < 1.5 else 2 if m < 3.5 else 5 if m < 7.5 else 10) * e


def _default_x_range(feats):
    """Window around the features (critical points, zeros, inflections, asymptotes, tangent point) + margin 1."""
    pts = [float(v) for v in feats if abs(float(v)) < 1e6]
    if not pts:
        return [-5.0, 5.0]
    lo, hi = min(pts) - 1, max(pts) + 1
    if hi - lo < 4:
        c = (lo + hi) / 2
        lo, hi = c - 2, c + 2
    return [round(lo * 2) / 2, round(hi * 2) / 2]


def _extrema(f, fp, fpp, crit, lang):
    out = []
    for c in crit[:8]:
        try:
            s2, val = sp.N(fpp.subs(x, c)), sp.simplify(f.subs(x, c))
            if val.is_real is not True or s2.is_real is not True:
                continue
            k = "max" if s2 < -1e-12 else "min" if s2 > 1e-12 else None
            if k is None:
                l, r = sp.N(fp.subs(x, c - sp.Rational(1, 100))), sp.N(fp.subs(x, c + sp.Rational(1, 100)))
                k = "min" if l < 0 < r else "max" if l > 0 > r else None
        except Exception:  # noqa: BLE001
            continue
        if k:
            out.append({"x": float(c), "y": float(val), "kind": k, "open": False,
                        "label": f"{t(lang, 'pt_' + k)} ({pretty(c)}, {pretty(val)})"})
    return out


def build_function_graph(expr, tangent_at=None, with_derivative=False, x_range=None, lang="fr"):
    f = parse(expr)
    fp = sp.simplify(sp.diff(f, x))
    fpp = sp.simplify(sp.diff(fp, x))
    a = parse(tangent_at) if tangent_at is not None else None
    warnings = []

    asym, holes, special = [], [], []
    for c in singular_candidates(f):
        special.append(c)
        kind, lim = classify(f, c)
        if kind == "asymptote":
            asym.append({"kind": "vertical", "at": float(c)})
        elif kind == "hole":
            holes.append({"x": float(c), "y": float(lim), "label": f"({pretty(c)}, {pretty(lim)})", "open": True})
    if not f.has(*PERIODIC):
        try:
            h = sp.limit(f, x, sp.oo)
            if h.is_finite is True and h.is_real is True:
                asym.append({"kind": "horizontal", "at": float(h)})
        except Exception:  # noqa: BLE001
            pass

    feats = real_roots(fp) + real_roots(f) + real_roots(fpp) + special + ([a] if a is not None else [])
    xr = [float(v) for v in x_range] if x_range else _default_x_range(feats)
    crit = real_roots(fp, sp.Interval(xr[0], xr[1]))
    extra = [float(c) for c in crit] + ([float(a)] if a is not None else [])
    xs = sorted(set([xr[0] + (xr[1] - xr[0]) * i / (N - 1) for i in range(N)] + [v for v in extra if xr[0] <= v <= xr[1]]))
    ff, fd = sp.lambdify(x, f, "math"), sp.lambdify(x, fp, "math")
    ys = [_ev(ff, v) for v in xs]
    yd = [_ev(fd, v) for v in xs]
    extrema = _extrema(f, fp, fpp, crit, lang)

    tan_curve, tan_point, fa = None, None, None
    if a is not None:
        try:
            fa, da = sp.simplify(f.subs(x, a)), sp.simplify(fp.subs(x, a))
        except Exception:  # noqa: BLE001
            fa = da = sp.nan
        if not (fa.is_real is True and da.is_real is True and fa.is_finite and da.is_finite):
            raise DiagramError(t(lang, "err_diff", a=pretty(a)))
        line = sp.expand(da * (x - a) + fa)
        tan_curve = {"role": "tangent", "label": "y = " + pretty(line),
                     "segments": [[[xr[0], float(line.subs(x, xr[0]))], [xr[1], float(line.subs(x, xr[1]))]]]}
        tan_point = {"x": float(a), "y": float(fa), "label": f"A({pretty(a)}, {pretty(fa)})", "open": False, "kind": "tangent"}

    pool = sorted(v for v in ys + (yd if with_derivative else []) if v is not None)
    if not pool:
        raise DiagramError(t(lang, "err_domain"))
    lo, hi = pool[int(0.02 * (len(pool) - 1))], pool[int(0.98 * (len(pool) - 1))]
    feat_y = [e["y"] for e in extrema] + [q["y"] for q in holes] + ([tan_point["y"]] if tan_point else [])
    for v in feat_y:
        lo, hi = min(lo, v), max(hi, v)
    h = (hi - lo) or 1.0
    step = _nice(h / 6)
    yr = [round(math.floor((lo - 0.1 * h) / step) * step, 6), round(math.ceil((hi + 0.1 * h) / step) * step, 6)]
    if sum(1 for v in pool if v < yr[0] or v > yr[1]) > 0.02 * len(pool):
        warnings.append(t(lang, "w_clip"))
    H = yr[1] - yr[0]
    band, jump = (yr[0] - 2 * H, yr[1] + 2 * H), H
    cuts = [q["at"] for q in asym if q["kind"] == "vertical"]

    curves = [{"role": "f", "label": "f(x) = " + pretty(f), "segments": _segments(xs, ys, cuts, band, jump)}]
    if with_derivative:
        curves.append({"role": "derivative", "label": "f′(x) = " + pretty(fp), "segments": _segments(xs, yd, cuts, band, jump)})
    title = t(lang, "g_title", f=pretty(f)) + (t(lang, "g_der") if with_derivative else "")
    points = holes + extrema
    if tan_curve:
        curves.append(tan_curve)
        points.append(tan_point)
        title += t(lang, "g_tan", a=pretty(a))

    samples = {"x": xs,
               "f": [round(v, 9) if v is not None and band[0] <= v <= band[1] else None for v in ys],
               "df": [None if v is None else round(v, 9) for v in yd]}
    payload = FunctionGraphPayload(x_range=xr, y_range=yr, curves=curves, points=points, asymptotes=asym,
                                   derivative_samples=samples, tangent_at=float(a) if a is not None else None)
    return make("function_graph", lang, title, payload, warnings)
