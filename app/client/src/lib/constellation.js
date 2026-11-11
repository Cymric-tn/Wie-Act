// The progress map as a constellation: each concept is a star, each line says "builds on". Concepts are laid out in
// learning order (step 1 = no prerequisite in the course), left to right (right to left in Arabic). No DOM here, so
// the geometry and the "what next" reading are tested in test/constellation.test.js.

/** levels as in demoData.masteryLevel; repeated here so this file has no import (tests load it alone). */
export function levelOf(value) {
  if (value >= 0.75) return "mastered";
  if (value >= 0.4) return "learning";
  if (value > 0) return "weak";
  return "new";
}

/** Prerequisites inside the course, dependents, and each concept's step (longest prerequisite chain). */
export function graphOf(units) {
  const ids = new Set(units.map((u) => u.id));
  const req = new Map(units.map((u) => [u.id, [...new Set((u.requires || []).filter((r) => ids.has(r) && r !== u.id))]]));
  const next = new Map(units.map((u) => [u.id, []]));
  req.forEach((rs, id) => rs.forEach((r) => next.get(r).push(id)));
  const layer = new Map();
  const visiting = new Set();
  const depth = (id) => {
    if (layer.has(id)) return layer.get(id);
    if (visiting.has(id)) return 0; // a cycle in the data: cut it here rather than loop
    visiting.add(id);
    const d = Math.max(-1, ...req.get(id).map(depth)) + 1;
    visiting.delete(id);
    layer.set(id, d);
    return d;
  };
  units.forEach((u) => depth(u.id));
  return { ids, req, next, layer };
}

function walk(start, edges) {
  const seen = new Set();
  const stack = [...(edges.get(start) || [])];
  while (stack.length) {
    const id = stack.pop();
    if (seen.has(id)) continue;
    seen.add(id);
    stack.push(...(edges.get(id) || []));
  }
  return seen;
}
export const ancestorsOf = (graph, id) => walk(id, graph.req);
export const descendantsOf = (graph, id) => walk(id, graph.next);

/** Concepts per step, ordered to keep lines short and uncrossed (barycentre sweeps; ties keep the course order). */
export function orderSteps(units, graph) {
  const count = units.length ? Math.max(...graph.layer.values()) + 1 : 0;
  const cols = Array.from({ length: count }, () => []);
  units.forEach((u) => cols[graph.layer.get(u.id)].push(u.id));
  const pos = new Map();
  const place = (col) => col.forEach((id, i) => pos.set(id, (i + 0.5) / col.length));
  cols.forEach(place);
  for (let sweep = 0; sweep < 4; sweep++) {
    const forward = sweep % 2 === 0;
    const order = cols.map((_, i) => i);
    const range = forward ? order.slice(1) : order.slice(0, -1).reverse();
    for (const l of range) {
      const near = (id) => (forward ? graph.req.get(id) : graph.next.get(id));
      const bary = (id) => {
        const n = near(id);
        return n.length ? n.reduce((s, x) => s + pos.get(x), 0) / n.length : pos.get(id);
      };
      cols[l] = [...cols[l]].sort((a, b) => bary(a) - bary(b));
      place(cols[l]);
    }
  }
  return cols;
}

/**
 * What the learner can do now. ready = not mastered and every prerequisite mastered. next = the one to suggest:
 * a ready concept already in progress, else one that needs review, else the earliest new one.
 */
export function progressOf(units, graph) {
  const value = new Map(units.map((u) => [u.id, u.mastery || 0]));
  const level = new Map(units.map((u) => [u.id, levelOf(u.mastery || 0)]));
  const ready = new Set(units.filter((u) => level.get(u.id) !== "mastered" && graph.req.get(u.id).every((r) => level.get(r) === "mastered")).map((u) => u.id));
  const rank = { learning: 0, weak: 1, new: 2 };
  const order = new Map(units.map((u, i) => [u.id, i]));
  const next = [...ready].sort((a, b) => rank[level.get(a)] - rank[level.get(b)] || graph.layer.get(a) - graph.layer.get(b) || order.get(a) - order.get(b))[0] || null;
  const counts = { mastered: 0, learning: 0, weak: 0, new: 0 };
  level.forEach((l) => counts[l]++);
  const overall = units.length ? units.reduce((s, u) => s + (u.mastery || 0), 0) / units.length : 0;
  return { value, level, ready, next, counts, overall, total: units.length };
}

export const SIZES = {
  full: { r: 15, line: 15, padX: 20, top: 40, bottom: 18, gap: 22, sub: true },
  compact: { r: 11, line: 13, padX: 10, top: 32, bottom: 10, gap: 12, sub: false }
};

/**
 * Positions in canvas pixels for a canvas `width` wide; the height follows from the busiest step.
 * wrap(text, maxWidth, maxLines) -> lines (the component measures with the real font). Returns nodes, edges, size.
 */
export function layoutConstellation({ units, width, compact = false, rtl = false, wrap }) {
  const s = compact ? SIZES.compact : SIZES.full;
  const graph = graphOf(units);
  const cols = orderSteps(units, graph);
  const steps = Math.max(1, cols.length);
  const colW = (width - 2 * s.padX) / steps;
  const labelWidth = Math.max(56, colW - (compact ? 8 : 16));
  const ring = s.r + 5; // the star with its progress ring
  const slot = 2 * ring + 6 + 2 * s.line + (s.sub ? s.line + 2 : 0) + s.gap;
  const most = Math.max(1, ...cols.map((c) => c.length));
  const height = Math.round(s.top + most * slot + s.bottom);
  const byId = new Map(units.map((u) => [u.id, u]));

  const nodes = [];
  cols.forEach((col, l) => {
    const offset = ((most - col.length) * slot) / 2;
    col.forEach((id, i) => {
      const left = s.padX + colW * (l + 0.5);
      nodes.push({
        id, unit: byId.get(id), step: l + 1,
        x: rtl ? width - left : left,
        y: s.top + offset + slot * i + ring,
        lines: wrap(byId.get(id).name, labelWidth, 2)
      });
    });
  });
  const at = new Map(nodes.map((n) => [n.id, n]));
  const dir = rtl ? -1 : 1;
  const edges = [];
  units.forEach((u) => graph.req.get(u.id).forEach((r) => {
    const a = at.get(r);
    const b = at.get(u.id);
    const x1 = a.x + dir * ring;
    const x2 = b.x - dir * ring;
    const mid = (x1 + x2) / 2;
    edges.push({ from: r, to: u.id, d: `M${x1} ${a.y} C${mid} ${a.y} ${mid} ${b.y} ${x2} ${b.y}` });
  }));
  return { nodes, edges, width, height, steps, cols, graph, ring, r: s.r, line: s.line, labelWidth, sub: s.sub };
}
