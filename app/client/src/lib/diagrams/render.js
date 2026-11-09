import { layoutGraph } from "./layout.js";

const FALLBACK = { ink: "#e9e7ff", accent: "#a78bfa", muted: "#6b6fa8", f: "#8fb3ff", d: "#ffc15e", t: "#ff7aa8", asy: "#8a90d6",
  grid: "#2b2e63", ok: "#2dd4bf", warn: "#fbbf24", bad: "#fb7185", new: "#a78bfa", req: "#9db4ff", rel: "#6b6fa8", panel: "#0f1230", neutral: "#8f98ff" };
export const LABELS = {
  fr: { x: "x", d: "f′(x)", f: "f(x)", err: "Diagramme invalide", slope: "pente", check: "Vérifier", reveal: "Voir la réponse", reset: "Effacer", ok: "Correct !", bad: "{n} erreur(s)" },
  en: { x: "x", d: "f′(x)", f: "f(x)", err: "Invalid diagram", slope: "slope", check: "Check", reveal: "Reveal answer", reset: "Clear", ok: "Correct!", bad: "{n} mistake(s)" },
  ar: { x: "x", d: "f′(x)", f: "f(x)", err: "مخطط غير صالح", slope: "الميل", check: "تحقق", reveal: "أظهر الحل", reset: "مسح", ok: "صحيح!", bad: "{n} أخطاء" } };

const line = (x1, y1, x2, y2, color, o = {}) => ({ t: "line", x1, y1, x2, y2, color, ...o });
const arrow = (x1, y1, x2, y2, color, o = {}) => ({ t: "arrow", x1, y1, x2, y2, color, ...o });
const poly = (pts, color, o = {}) => ({ t: "polyline", pts, color, ...o });
const rect = (x, y, w, h, color, o = {}) => ({ t: "rect", x, y, w, h, color, fill: false, r: 0, ...o });
const dot = (cx, cy, r, color, o = {}) => rect(cx - r, cy - r, 2 * r, 2 * r, color, { r, ...o });
const text = (x, y, s, o = {}) => ({ t: "text", x, y, s: String(s), size: 12, anchor: "start", color: "ink", ...o });
export const fmt = (v) => String(+v.toPrecision(10)).replace("-", "−");
const nice = (r) => { const e = 10 ** Math.floor(Math.log10(r)), m = r / e; return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * e; };
const ticks = (a, b) => { const s = nice((b - a) / 8), o = []; for (let i = Math.ceil(a / s); i * s <= b + 1e-9; i++) o.push(+(i * s).toPrecision(10)); return o; };
const wrap = (s, n = 16) => { const out = []; let cur = ""; for (const w of String(s).split(" ")) { if (cur && (cur + " " + w).length > n) { out.push(cur); cur = w; } else cur = cur ? cur + " " + w : w; } return out.concat(cur ? [cur] : []); };
const stateOf = (m) => (m == null || m <= 0 ? "new" : m < 0.4 ? "bad" : m < 0.8 ? "warn" : "ok");
export const masteryState = stateOf;

export function validate(spec) {
  if (!spec || typeof spec !== "object") return "spec is not an object";
  if (spec.version !== 1 && spec.version !== 2) return "unsupported version";
  const p = spec.payload;
  if (!p || typeof p !== "object") return "missing payload";
  const arr = (...k) => k.every((n) => Array.isArray(p[n]));
  if (spec.type === "function_graph") return arr("x_range", "y_range", "curves") ? null : "bad function_graph payload";
  if (spec.type === "variation_table") return arr("columns", "derivative_signs", "values", "trends") ? null : "bad variation_table payload";
  if (spec.type === "concept_map") return arr("nodes", "edges") ? null : "bad concept_map payload";
  return "unknown type: " + spec.type;
}

export function clip(pts, r) { // Liang–Barsky, returns polylines inside rect r
  const out = []; let cur = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[i + 1], dx = x2 - x1, dy = y2 - y1;
    let t0 = 0, t1 = 1, ok = true;
    for (const [p, q] of [[-dx, x1 - r.x0], [dx, r.x1 - x1], [-dy, y1 - r.y0], [dy, r.y1 - y1]]) {
      if (p === 0) { if (q < 0) { ok = false; break; } } else {
        const u = q / p;
        if (p < 0) { if (u > t1) { ok = false; break; } if (u > t0) t0 = u; } else { if (u < t0) { ok = false; break; } if (u < t1) t1 = u; }
      }
    }
    if (!ok) { if (cur.length > 1) out.push(cur); cur = []; continue; }
    const a = [x1 + t0 * dx, y1 + t0 * dy], b = [x1 + t1 * dx, y1 + t1 * dy];
    if (!cur.length) cur.push(a); else if (t0 > 0) { out.push(cur); cur = [a]; }
    cur.push(b);
    if (t1 < 1) { out.push(cur); cur = []; }
  }
  if (cur.length > 1) out.push(cur);
  return out;
}

function graphScene(spec, opt = {}) {
  const p = spec.payload, rtl = spec.lang === "ar";
  const W = 780, H = 480, L = 60, R = 28, T = 80, B = 44;
  const [xa, xb] = p.x_range, [ya, yb] = p.y_range;
  const sx = (v) => L + ((v - xa) / (xb - xa)) * (W - L - R), sy = (v) => H - B - ((v - ya) / (yb - ya)) * (H - T - B);
  const it = [];
  for (const v of ticks(xa, xb)) { it.push(line(sx(v), T, sx(v), H - B, "muted", { role: "grid" })); it.push(text(sx(v), H - B + 17, fmt(v), { anchor: "middle", size: 11, color: "muted" })); }
  for (const v of ticks(ya, yb)) { it.push(line(L, sy(v), W - R, sy(v), "muted", { role: "grid" })); it.push(text(L - 8, sy(v) + 4, fmt(v), { anchor: "end", size: 11, color: "muted" })); }
  const ax = xa <= 0 && 0 <= xb ? sx(0) : L, ay = ya <= 0 && 0 <= yb ? sy(0) : H - B;
  it.push(arrow(L, ay, W - R + 6, ay, "ink", { w: 1.6 }), arrow(ax, H - B, ax, T - 6, "ink", { w: 1.6 }));
  it.push(text(W - R + 8, ay + 16, "x", { size: 12, anchor: "end" }), text(ax + 8, T - 8, "y", { size: 12 }));
  const box = { x0: L, x1: W - R, y0: T, y1: H - B };
  for (const a of p.asymptotes || []) {
    if (a.kind === "vertical") { if (a.at >= xa && a.at <= xb) it.push(line(sx(a.at), T, sx(a.at), H - B, "muted", { role: "asy", dash: true })); }
    else if (a.at >= ya && a.at <= yb) it.push(line(L, sy(a.at), W - R, sy(a.at), "muted", { role: "asy", dash: true }));
  }
  const roleCls = { f: "f", derivative: "d", tangent: "t" };
  for (const c of p.curves) if (!(opt.skipTangent && c.role === "tangent")) for (const seg of c.segments)
    for (const s of clip(seg.map(([a, b]) => [sx(a), sy(b)]), box)) it.push(poly(s, c.role === "f" ? "ink" : "accent", { role: roleCls[c.role], w: c.role === "tangent" ? 2 : 2.6 }));
  for (const pt of p.points || []) {
    if (pt.x < xa || pt.x > xb || pt.y < ya || pt.y > yb) continue;
    if (opt.skipTangent && pt.kind === "tangent") continue;
    const role = pt.kind === "max" ? "ok" : pt.kind === "min" ? "warn" : "t";
    it.push(dot(sx(pt.x), sy(pt.y), 5, "accent", { role, fill: !pt.open, sw: 2.2 }));
    if (pt.label) it.push(text(sx(pt.x) + 9, sy(pt.y) + (pt.kind === "min" ? 20 : -9), pt.label, { size: 12, color: "ink" }));
  }
  it.push(rect(L, T, W - L - R, H - T - B, "muted", { role: "grid", sw: 1 }));
  it.unshift(text(rtl ? W - R : L, 28, spec.title, { size: 16, weight: 700, anchor: rtl ? "end" : "start" }));
  let lx = rtl ? W - R : L;
  for (const c of p.curves.filter((c) => !(opt.skipTangent && c.role === "tangent"))) {
    const wd = c.label.length * 7.4 + 30, s = rtl ? -1 : 1;
    it.push(line(lx, 56, lx + s * 22, 56, c.role === "f" ? "ink" : "accent", { role: roleCls[c.role], w: 3 }));
    it.push(text(lx + s * 30, 60, c.label, { size: 12, anchor: rtl ? "end" : "start" }));
    lx += s * (wd + 36);
  }
  return { w: W, h: H, items: it, map: { sx, sy, L, R, T, B, W, H, xa, xb, ya, yb } };
}

function tableScene(spec) {
  const p = spec.payload, lab = LABELS[spec.lang] || LABELS.fr, n = p.columns.length, rtl = spec.lang === "ar";
  const LW = 110, W = Math.max(640, 180 + n * 150), H = 290, x0 = LW + 90, x1 = W - 60, top = 52;
  const rows = [top, top + 44, top + 88, H - 14], yH = 162, yL = 236, yM = 199;
  const cx = (i) => (n === 1 ? (x0 + x1) / 2 : x0 + (i * (x1 - x0)) / (n - 1));
  const blank = p.mode === "practice", geo = { W, H, LW, top, rows, cx, yM, ySign: 124 };
  const it = [text(rtl ? W - 12 : 12, 28, spec.title, { size: 16, weight: 700, anchor: rtl ? "end" : "start" })];
  it.push(rect(10, top, W - 20, rows[3] - top, "muted", { role: "grid", sw: 1.4, r: 6 }));
  it.push(line(LW + 10, top, LW + 10, rows[3], "muted", { role: "grid" }), line(10, rows[1], W - 10, rows[1], "muted", { role: "grid" }), line(10, rows[2], W - 10, rows[2], "muted", { role: "grid" }));
  [[lab.x, 75], [lab.d, 119], [lab.f, 175]].forEach(([s, y]) => it.push(text(LW / 2 + 10, y, s, { anchor: "middle", size: 14, weight: 600 })));
  p.columns.forEach((c, i) => { if (!blank || i === 0 || i === n - 1) it.push(text(cx(i), 75, c, { anchor: "middle", size: 14, weight: 600 })); });
  if (blank) return { w: W, h: H, items: it, geo };
  const sg = (s) => (s === "-" ? "−" : s);
  p.derivative_signs.forEach((s, k) => {
    if (k % 2 === 0) { const j = k / 2; it.push(text((cx(j) + cx(j + 1)) / 2, 124, sg(s), { anchor: "middle", size: 17, weight: 700, color: "accent", role: s === "+" ? "ok" : "t" })); }
    else { const c = cx((k + 1) / 2); if (s === "||") it.push(line(c - 2.5, rows[1], c - 2.5, rows[3], "ink", { w: 1.4 }), line(c + 2.5, rows[1], c + 2.5, rows[3], "ink", { w: 1.4 })); else it.push(text(c, 124, s, { anchor: "middle", size: 14 })); }
  });
  const up = (j) => p.trends[j] === "up", side = [];
  p.values.forEach((v, i) => {
    const forb = v.f_right != null;
    let l = i > 0 ? (up(i - 1) ? yH : yL) : null, r = i < n - 1 ? (up(i) ? yL : yH) : null;
    if (l == null) l = r; if (r == null) r = l;
    if (!forb && l !== r) l = r = yM;
    side.push({ l, r, forb });
    const hot = v.kind ? { weight: 700, color: "accent", role: v.kind === "max" ? "ok" : "t" } : {};
    if (forb) { it.push(text(cx(i) - 12, l + 5, v.f, { anchor: "end", size: 14 }), text(cx(i) + 12, r + 5, v.f_right, { anchor: "start", size: 14 })); }
    else it.push(text(cx(i), l + 5, v.f, { anchor: "middle", size: 14, ...hot }));
  });
  for (let j = 0; j < n - 1; j++) {
    const a = p.values[j], b = p.values[j + 1], A = side[j], Bs = side[j + 1];
    const sx = cx(j) + (A.forb ? 18 + a.f_right.length * 8 : a.f.length * 4.6 + 12);
    const ex = cx(j + 1) - (Bs.forb ? 18 + b.f.length * 8 : b.f.length * 4.6 + 12);
    if (ex > sx + 12) it.push(arrow(sx, A.r, ex, Bs.l, "accent", { role: up(j) ? "ok" : "t", w: 2 }));
  }
  return { w: W, h: H, items: it, geo };
}

function mapScene(spec) {
  const { nodes, edges } = spec.payload, rtl = spec.lang === "ar", hasM = nodes.some((n) => n.mastery != null);
  const { pos, w, h } = layoutGraph(nodes, edges), PAD = 90, W = w + 2 * PAD, H = h + 2 * PAD + 30;
  const P = (id) => ({ x: pos[id].x + PAD, y: pos[id].y + PAD + 24 });
  const it = [text(rtl ? W - 16 : 16, 28, spec.title, { size: 16, weight: 700, anchor: rtl ? "end" : "start" })];
  for (const e of edges) {
    if (!pos[e.from] || !pos[e.to]) continue;
    const a = P(e.from), b = P(e.to), d = Math.hypot(b.x - a.x, b.y - a.y) || 1, ux = (b.x - a.x) / d, uy = (b.y - a.y) / d;
    if (e.kind === "requires") it.push(arrow(a.x + ux * 24, a.y + uy * 24, b.x - ux * 28, b.y - uy * 28, "accent", { role: "req", w: 1.8 }));
    else it.push(line(a.x + ux * 24, a.y + uy * 24, b.x - ux * 24, b.y - uy * 24, "muted", { role: "rel", dash: true, w: 1.2 }));
  }
  for (const n of nodes) {
    const c = P(n.id), st = hasM ? stateOf(n.mastery) : "neutral";
    it.push(dot(c.x, c.y, 24, "accent", { role: st, fill: true, opacity: 0.2, halo: true }));
    it.push(dot(c.x, c.y, 13, "accent", { role: st, fill: true }), dot(c.x, c.y, 4.5, "ink", { fill: true }));
    if (n.id === spec.payload.focus) it.push(dot(c.x, c.y, 32, "ink", { dash: true, sw: 1.6 }));
    wrap(n.label).forEach((s, k) => it.push(text(c.x, c.y + 42 + k * 14, s, { anchor: "middle", size: 12, weight: 600 })));
  }
  return { w: W, h: H, items: it };
}

export function scene(spec, opt = {}) {
  const err = validate(spec);
  if (err) throw new Error(err);
  return { function_graph: graphScene, variation_table: tableScene, concept_map: mapScene }[spec.type](spec, opt);
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const n2 = (v) => +v.toFixed(2);
const col = (q) => { const k = q.role || q.color; return `var(--dg-${k}, ${FALLBACK[k] || FALLBACK.ink})`; };
const AR = /[\u0600-\u06FF]/;

export function itemSVG(q) {
  const c = col(q), da = q.dash ? ' stroke-dasharray="6 5"' : "", op = q.opacity != null ? ` opacity="${q.opacity}"` : "", w = q.w || 1.6;
  if (q.t === "polyline") return `<polyline points="${q.pts.map((p) => n2(p[0]) + "," + n2(p[1])).join(" ")}" fill="none" stroke="${c}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"${da}/>`;
  if (q.t === "line") return `<line x1="${n2(q.x1)}" y1="${n2(q.y1)}" x2="${n2(q.x2)}" y2="${n2(q.y2)}" stroke="${c}" stroke-width="${w}"${da}/>`;
  if (q.t === "arrow") {
    const a = Math.atan2(q.y2 - q.y1, q.x2 - q.x1), hx = (d) => n2(q.x2 - 9 * Math.cos(a + d)), hy = (d) => n2(q.y2 - 9 * Math.sin(a + d));
    return `<g stroke="${c}" stroke-width="${w}" stroke-linecap="round" fill="none"><line x1="${n2(q.x1)}" y1="${n2(q.y1)}" x2="${n2(q.x2)}" y2="${n2(q.y2)}"/><polyline points="${hx(0.4)},${hy(0.4)} ${n2(q.x2)},${n2(q.y2)} ${hx(-0.4)},${hy(-0.4)}"/></g>`;
  }
  if (q.t === "rect") return `<rect x="${n2(q.x)}" y="${n2(q.y)}" width="${n2(q.w)}" height="${n2(q.h)}" rx="${q.r || 0}" fill="${q.fill ? c : "none"}" stroke="${c}" stroke-width="${q.sw || (q.fill ? 0 : 1.6)}"${da}${op}/>`;
  const rtl = AR.test(q.s), an = rtl ? { start: "end", end: "start" }[q.anchor] || q.anchor : q.anchor;
  return `<text x="${n2(q.x)}" y="${n2(q.y)}" font-size="${q.size}" font-weight="${q.weight || 400}" text-anchor="${an}"${rtl ? ' direction="rtl"' : ""} fill="${c}">${esc(q.s)}</text>`;
}

export function toSVG(items, W, H, lang, extra = "") {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n2(W)} ${n2(H)}" width="${n2(W)}" height="${n2(H)}" class="dg dg-${lang}" role="img" font-family="Inter, 'Noto Sans Arabic', system-ui, sans-serif">${items.map(itemSVG).join("")}${extra}</svg>`;
}

function errorBox(msg, lang = "fr") {
  const l = (LABELS[lang] || LABELS.fr).err;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 70" width="420" height="70" class="dg dg-error" role="alert"><rect x="1" y="1" width="418" height="68" rx="10" fill="none" stroke="var(--dg-bad, ${FALLBACK.bad})" stroke-width="1.5"/><text x="16" y="28" font-size="14" font-weight="700" fill="var(--dg-bad, ${FALLBACK.bad})">${esc(l)}</text><text x="16" y="50" font-size="12" fill="var(--dg-ink, ${FALLBACK.ink})">${esc(String(msg).slice(0, 60))}</text></svg>`;
}

export function renderDiagram(spec) {
  try { const s = scene(spec); return toSVG(s.items, s.w, s.h, spec.lang); }
  catch (e) { return errorBox(e.message || e, spec && spec.lang); }
}
