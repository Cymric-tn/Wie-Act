// Interactive layer: draggable tangent and fill-the-table. No storage, no EdNova screens:
// it only computes, draws and emits events (onTangentMove, onCheck).
import { scene, toSVG, itemSVG, clip, LABELS, renderDiagram } from "./render.js";
import { makeTrack, valueAt, clampTo, startPoint, snap, describe } from "./tangent.js";

const NS = "http://www.w3.org/2000/svg";
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const v = (k, fb) => `var(--dg-${k}, ${fb})`;

export function mountDiagram(el, spec, opts = {}) {
  const p = spec && spec.payload;
  if (spec && spec.type === "variation_table" && p && p.mode === "practice") return mountPractice(el, spec, opts);
  if (spec && spec.type === "function_graph" && p && p.derivative_samples && (opts.draggable === true || (opts.draggable !== false && p.tangent_at != null)))
    return mountDraggable(el, spec, opts);
  el.innerHTML = renderDiagram(spec);
  return {};
}

export function mountDraggable(el, spec, opts = {}) {
  const p = spec.payload, lang = spec.lang, rtl = lang === "ar";
  const sc = scene(spec, { skipTangent: true }), m = sc.map;
  el.innerHTML = toSVG(sc.items, sc.w, sc.h, lang);
  const svg = el.querySelector("svg");
  const mk = (tag, at, txt) => { const e = document.createElementNS(NS, tag); for (const k in at) e.setAttribute(k, at[k]); if (txt != null) e.textContent = txt; svg.appendChild(e); return e; };
  const blocks = [...(p.asymptotes || []).filter((a) => a.kind === "vertical").map((a) => a.at), ...(p.points || []).filter((q) => q.open).map((q) => q.x)];
  const tr = makeTrack(p.derivative_samples, blocks);
  const snaps = (p.points || []).filter((q) => q.kind).map((q) => q.x);
  const tol = (8 / (m.W - m.L - m.R)) * (m.xb - m.xa);
  const tx = rtl ? m.W - m.R - 12 : m.L + 12, anchor = rtl ? "end" : "start";

  const panel = mk("rect", { x: rtl ? m.W - m.R - 226 : m.L + 4, y: m.T + 6, width: 222, height: 66, rx: 8, fill: v("panel", "#0f1230"), opacity: 0.82, "pointer-events": "none" });
  const line = mk("polyline", { fill: "none", stroke: v("t", "#ff7aa8"), "stroke-width": 2.2, "stroke-linecap": "round", "pointer-events": "none" });
  const dot = mk("circle", { r: 6.5, fill: v("t", "#ff7aa8"), stroke: v("ink", "#eceaff"), "stroke-width": 2, "pointer-events": "none" });
  const hit = mk("circle", { r: 16, fill: "transparent", tabindex: 0, role: "slider", "aria-label": "tangent point", style: "cursor:grab;touch-action:none;outline:none" });
  const T = [0, 1, 2].map((i) => mk("text", { x: tx, y: m.T + 26 + i * 18, "font-size": 13, "font-weight": i === 1 ? 700 : 500, "text-anchor": anchor, fill: v("ink", "#eceaff"), "pointer-events": "none" }, ""));
  const all = [panel, line, dot, hit, ...T];
  let cur = null;

  function set(a) {
    const val = valueAt(tr, a);
    if (!val) { all.forEach((e) => (e.style.display = "none")); return null; }
    all.forEach((e) => (e.style.display = ""));
    cur = a;
    const pts = [m.xa, m.xb].map((X) => [m.sx(X), m.sy(val.f + val.df * (X - a))]);
    const seg = clip(pts, { x0: m.L, x1: m.W - m.R, y0: m.T, y1: m.H - m.B })[0];
    line.setAttribute("points", seg ? seg.map((q) => q.map((n) => +n.toFixed(2)).join(",")).join(" ") : "");
    [dot, hit].forEach((e) => { e.setAttribute("cx", m.sx(a)); e.setAttribute("cy", m.sy(val.f)); });
    const d = describe(val, lang);
    T[0].textContent = d.a; T[1].textContent = d.slope; T[2].textContent = d.eq;
    hit.setAttribute("aria-valuenow", d.a);
    if (opts.onTangentMove) opts.onTangentMove({ a, f: val.f, df: val.df });
    return val;
  }
  const toX = (e) => { const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY; const q = pt.matrixTransform(svg.getScreenCTM().inverse()); return m.xa + ((q.x - m.L) / (m.W - m.L - m.R)) * (m.xb - m.xa); };
  const move = (to, doSnap) => { if (cur == null) return; let a = clampTo(tr, cur, to); if (a == null) return; if (doSnap) { const s = snap(a, snaps, tol); if (valueAt(tr, s)) a = s; } set(a); };
  let dragging = false;
  hit.addEventListener("pointerdown", (e) => { dragging = true; hit.setPointerCapture(e.pointerId); hit.style.cursor = "grabbing"; e.preventDefault(); });
  hit.addEventListener("pointermove", (e) => { if (dragging) move(toX(e), true); });
  const end = () => { dragging = false; hit.style.cursor = "grab"; };
  hit.addEventListener("pointerup", end); hit.addEventListener("pointercancel", end);
  hit.addEventListener("keydown", (e) => { const k = { ArrowLeft: -1, ArrowRight: 1 }[e.key]; if (k) { e.preventDefault(); move(cur + (k * (m.xb - m.xa)) / 120, false); } });

  const start = p.tangent_at != null && valueAt(tr, p.tangent_at) ? p.tangent_at : startPoint(tr, (m.xa + m.xb) / 2);
  if (start == null) all.forEach((e) => (e.style.display = "none")); else set(start);
  return { set, get: () => cur };
}

const CYCLE = { even: ["", "+", "-"], odd: ["", "0", "||"], arrow: ["", "up", "down"] };
const INSTYLE = "width:100%;height:100%;box-sizing:border-box;text-align:center;font:600 14px Inter,system-ui,sans-serif;color:var(--dg-ink,#eceaff);background:var(--dg-panel,#0f1230);border:1.5px solid var(--dg-muted,#6b6fa8);border-radius:6px;outline:none";
const CSS = ".dg-in:focus{border-color:var(--dg-accent,#a78bfa)!important}.dg-in.bad{border-color:var(--dg-bad,#fb7185)!important;box-shadow:0 0 0 2px rgba(251,113,133,.3)}";

export function mountPractice(el, spec, opts = {}) {
  const p = spec.payload, n = p.columns.length, lang = spec.lang, L = LABELS[lang] || LABELS.fr;
  const sc = scene(spec), g = sc.geo, X = g.cx;
  const blank = () => ({ columns: p.columns.map((c, i) => (i === 0 || i === n - 1 ? c : "")), signs: p.derivative_signs.map(() => ""),
    vals: p.columns.map(() => ({ f: "", f_right: "" })), trends: p.trends.map(() => "") });
  let st = blank(), wrong = new Set();
  el.innerHTML = `<div class="dg-wrap"><div class="dg-holder"></div><div class="dg-tools"><button type="button" data-t="check">${esc(L.check)}</button><button type="button" data-t="reveal">${esc(L.reveal)}</button><button type="button" data-t="reset">${esc(L.reset)}</button><span class="dg-status" role="status"></span></div></div>`;
  const holder = el.querySelector(".dg-holder"), status = el.querySelector(".dg-status");
  const forb = (i) => i > 0 && i < n - 1 && st.signs[2 * i - 1] === "||";

  function draw() {
    const bad = (k) => (wrong.has(k) ? " bad" : "");
    const inp = (k, cx, y, w, val, ro) => `<foreignObject x="${cx - w / 2}" y="${y - 14}" width="${w}" height="28"><input class="dg-in${bad(k)}" data-k="${k}" value="${esc(val)}"${ro ? " disabled" : ""} autocomplete="off" spellcheck="false" style="${INSTYLE}"/></foreignObject>`;
    const cell = (act, cx, cy, w, inner) => `<g data-act="${act}" style="cursor:pointer"><rect x="${cx - w / 2}" y="${cy - 16}" width="${w}" height="32" rx="6" fill="transparent" stroke="${wrong.has(act) ? v("bad", "#fb7185") : v("muted", "#6b6fa8")}" stroke-width="1.2" stroke-dasharray="3 3"/>${inner}</g>`;
    const label = (cx, cy, s) => itemSVG({ t: "text", x: cx, y: cy + 6, s: s === "-" ? "−" : s || "·", size: 17, anchor: "middle", color: "ink", weight: 700 });
    let o = `<style>${CSS}</style>`;
    for (let i = 1; i < n - 1; i++) o += inp("col:" + i, X(i), 75, 70, st.columns[i]);
    st.signs.forEach((s, k) => {
      const odd = k % 2, cx = odd ? X((k + 1) / 2) : (X(k / 2) + X(k / 2 + 1)) / 2;
      if (s === "||") o += `<line x1="${X((k + 1) / 2) - 2.5}" y1="${g.rows[1]}" x2="${X((k + 1) / 2) - 2.5}" y2="${g.rows[3]}" stroke="${v("ink", "#eceaff")}" stroke-width="1.4"/><line x1="${X((k + 1) / 2) + 2.5}" y1="${g.rows[1]}" x2="${X((k + 1) / 2) + 2.5}" y2="${g.rows[3]}" stroke="${v("ink", "#eceaff")}" stroke-width="1.4"/>`;
      o += cell("sign:" + k, cx, g.ySign, 44, label(cx, g.ySign, s === "||" ? "" : s));
    });
    for (let i = 0; i < n; i++) {
      if (forb(i)) o += inp("f:" + i, X(i) - 36, g.yM, 58, st.vals[i].f) + inp("fr:" + i, X(i) + 36, g.yM, 58, st.vals[i].f_right);
      else o += inp("f:" + i, X(i), g.yM, 70, st.vals[i].f);
    }
    for (let j = 0; j < n - 1; j++) {
      const a = X(j) + (forb(j) ? 70 : 44), b = X(j + 1) - (forb(j + 1) ? 70 : 44), mid = (a + b) / 2, tr = st.trends[j];
      const inner = tr ? itemSVG({ t: "arrow", x1: a, y1: tr === "up" ? g.yM + 14 : g.yM - 14, x2: b, y2: tr === "up" ? g.yM - 14 : g.yM + 14, color: "accent", role: tr === "up" ? "ok" : "t", w: 2 })
        : itemSVG({ t: "text", x: mid, y: g.yM + 6, s: "?", size: 16, anchor: "middle", color: "muted" });
      if (b - a > 20) o += cell("arrow:" + j, mid, g.yM, Math.min(b - a, 120), inner);
    }
    holder.innerHTML = toSVG(sc.items, sc.w, sc.h, lang, o);
  }

  holder.addEventListener("click", (e) => {
    const a = e.target.closest("[data-act]"); if (!a) return;
    const [kind, idx] = a.dataset.act.split(":"), i = +idx;
    const next = (list, cur) => list[(list.indexOf(cur) + 1) % list.length];
    if (kind === "sign") st.signs[i] = next(CYCLE[i % 2 ? "odd" : "even"], st.signs[i]);
    else st.trends[i] = next(CYCLE.arrow, st.trends[i]);
    wrong.delete(a.dataset.act); status.textContent = ""; draw();
  });
  holder.addEventListener("input", (e) => {
    const k = e.target.dataset && e.target.dataset.k; if (!k) return;
    const [kind, idx] = k.split(":"), i = +idx;
    if (kind === "col") st.columns[i] = e.target.value; else if (kind === "f") st.vals[i].f = e.target.value; else st.vals[i].f_right = e.target.value;
    wrong.delete(k); e.target.classList.remove("bad");
  });

  const answer = () => ({ columns: st.columns, signs: st.signs, vals: st.vals, trends: st.trends });
  const checker = opts.checker || (async (sp, ans) => {
    const r = await fetch(opts.checkUrl || "/api/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ spec: sp, answer: ans }) });
    const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j;
  });
  const ctl = {
    getAnswer: answer,
    async check() {
      let res;
      try { res = await checker(spec, answer()); }
      catch (e) { status.textContent = (opts.errorText && opts.errorText(e)) || e.message || L.err; return null; }
      const key = { x: "col", f_prime: "sign", arrows: "arrow", f: "f", f_right: "fr" };
      wrong = new Set(res.mistakes.map((q) => `${key[q.row]}:${q.column}`));
      draw(); status.textContent = res.correct ? L.ok : L.bad.replace("{n}", res.mistakes.length);
      if (opts.onCheck) opts.onCheck(res);
      return res;
    },
    reveal() { st = { columns: [...p.columns], signs: [...p.derivative_signs], vals: p.values.map((q) => ({ f: q.f, f_right: q.f_right || "" })), trends: [...p.trends] }; wrong.clear(); status.textContent = ""; draw(); },
    reset() { st = blank(); wrong.clear(); status.textContent = ""; draw(); },
  };
  el.querySelector(".dg-tools").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) ctl[b.dataset.t](); });
  draw();
  return ctl;
}
