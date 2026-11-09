import { scene } from "./render.js";

const r2 = (v) => Math.round(v * 100) / 100;
const MAX = 500;

// Same drawing as renderDiagram, as simple editable pieces for the whiteboard.
export function toBoardPrimitives(spec) {
  let items = scene(spec).items.filter((q) => !q.halo);
  if (items.length > MAX) items = items.filter((q) => q.t === "text" || q.role !== "grid");
  return items.slice(0, MAX).map((q) => {
    const base = { type: q.t, color: q.color, ...(q.role ? { role: q.role } : {}) };
    switch (q.t) {
      case "polyline": return { ...base, points: q.pts.map(([x, y]) => [r2(x), r2(y)]), width: q.w || 2 };
      case "line": case "arrow": return { ...base, x1: r2(q.x1), y1: r2(q.y1), x2: r2(q.x2), y2: r2(q.y2), dashed: !!q.dash, width: q.w || 1.6 };
      case "rect": return { ...base, x: r2(q.x), y: r2(q.y), w: r2(q.w), h: r2(q.h), radius: q.r || 0, filled: !!q.fill, width: q.sw || 1.6 };
      default: return { ...base, x: r2(q.x), y: r2(q.y), text: q.s, size: q.size, anchor: q.anchor, rtl: /[\u0600-\u06FF]/.test(q.s) };
    }
  });
}
