// Pure helpers for the draggable tangent: no DOM, no server call while dragging.
export function makeTrack(samples, blocks = []) {
  const { x, f, df } = samples, n = x.length;
  const ok = (i) => i >= 0 && i < n && Number.isFinite(f[i]) && Number.isFinite(df[i]);
  const link = (i) => ok(i) && ok(i + 1) && !blocks.some((b) => b > x[i] && b < x[i + 1]);
  const indexAt = (a) => { let lo = 0, hi = n - 1; while (hi - lo > 1) { const m = (lo + hi) >> 1; if (x[m] <= a) lo = m; else hi = m; } return a >= x[n - 1] ? n - 1 : lo; };
  return { x, f, df, n, ok, link, indexAt };
}

// f(a) and f′(a) interpolated from the samples; null where undefined or non-differentiable.
export function valueAt(tr, a) {
  const i = tr.indexAt(a);
  if (a === tr.x[i]) return tr.ok(i) ? { a, f: tr.f[i], df: tr.df[i] } : null;
  if (i >= tr.n - 1 || a < tr.x[0] || !tr.link(i)) return null;
  const t = (a - tr.x[i]) / (tr.x[i + 1] - tr.x[i]);
  return { a, f: tr.f[i] + t * (tr.f[i + 1] - tr.f[i]), df: tr.df[i] + t * (tr.df[i + 1] - tr.df[i]) };
}

// Move from `from` toward `to` but never across an undefined, blocked or non-differentiable sample.
export function clampTo(tr, from, to) {
  let i = tr.indexAt(from);
  if (!tr.ok(i)) { if (!tr.ok(i + 1)) return null; i += 1; }
  let lo = i, hi = i;
  while (tr.link(lo - 1)) lo--;
  while (tr.link(hi)) hi++;
  return Math.min(Math.max(to, tr.x[lo]), tr.x[hi]);
}

export function startPoint(tr, target) {
  let best = null;
  for (let i = 0; i < tr.n; i++)
    if (tr.ok(i) && (tr.link(i) || tr.link(i - 1)) && (best === null || Math.abs(tr.x[i] - target) < Math.abs(tr.x[best] - target))) best = i;
  return best === null ? null : tr.x[best];
}

export function snap(a, snaps, tol) {
  let best = a, d = tol;
  for (const c of snaps) if (Math.abs(c - a) <= d) { best = c; d = Math.abs(c - a); }
  return best;
}

const r2 = (v) => { const t = String(+v.toFixed(2)); return t === "-0" ? "0" : t; };
const mn = (t) => t.replace("-", "−");
const WORD = { fr: "pente", en: "slope", ar: "الميل" };

// Display only (2 decimals); exact values stay in the spec.
export function describe(v, lang = "fr") {
  const m = v.df, b = v.f - m * v.a;
  let eq;
  if (Math.abs(m) < 0.005) eq = `y = ${mn(r2(v.f))}`;
  else {
    const ms = Math.abs(m - 1) < 0.005 ? "" : Math.abs(m + 1) < 0.005 ? "−" : mn(r2(m));
    eq = `y = ${ms}x${Math.abs(b) < 0.005 ? "" : ` ${b < 0 ? "−" : "+"} ${r2(Math.abs(b))}`}`;
  }
  return { slope: `${WORD[lang] || WORD.fr} f′(a) = ${mn(r2(m))}`, eq, a: `a = ${mn(r2(v.a))}` };
}
