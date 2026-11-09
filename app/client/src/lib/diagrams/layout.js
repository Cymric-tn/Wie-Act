// Left-to-right layered layout (drop-in replacement point for elkjs/dagre).
export function layoutGraph(nodes, edges, { dx = 240, dy = 92 } = {}) {
  const layer = Object.fromEntries(nodes.map((n) => [n.id, 0]));
  const req = edges.filter((e) => e.kind === "requires" && e.from in layer && e.to in layer);
  for (let i = 0; i < nodes.length; i++) {
    let changed = false;
    for (const e of req) if (layer[e.to] < layer[e.from] + 1) { layer[e.to] = layer[e.from] + 1; changed = true; }
    if (!changed) break;
  }
  const cols = [];
  nodes.forEach((n) => (cols[layer[n.id]] ||= []).push(n.id));
  const row = {};
  const nb = (id) => edges.flatMap((e) => (e.from === id ? [e.to] : e.to === id ? [e.from] : []));
  cols.forEach((c, i) => {
    if (i > 0) {
      const bary = (id) => { const p = nb(id).filter((q) => layer[q] < i && row[q] != null); return p.length ? p.reduce((s, q) => s + row[q], 0) / p.length : 1e9; };
      c.sort((a, b) => bary(a) - bary(b));
    }
    c.forEach((id, k) => (row[id] = k));
  });
  const maxRows = Math.max(1, ...cols.map((c) => c.length));
  const pos = {};
  cols.forEach((c, i) => c.forEach((id, k) => (pos[id] = { x: i * dx, y: (k + (maxRows - c.length) / 2) * dy })));
  return { pos, w: Math.max(0, cols.length - 1) * dx, h: (maxRows - 1) * dy };
}
