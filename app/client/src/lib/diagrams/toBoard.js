// A diagram as ordinary whiteboard items: pen paths, shapes and text the group can move, erase and annotate.
//
// The board differs from SVG on three points handled here: its text is left-aligned and positioned by its top, it has no
// dashed line (a dash is a row of short lines), and a pen path is fed a constant pressure so a curve keeps one thickness.
import { makeId, textWidth } from "../geometry.js";

const SCALE = 1.3;
const MAX_ITEMS = 500; // the server's limit for one operation
const DASH = 14, GAP = 10;
const BASELINE = 0.8; // from a text's baseline up to its top, as a share of its size
const MUTED = "#8a90b8";
const ROLE = { d: "#ffc45c", t: "#ff7fa8", asy: MUTED, grid: "#8a90b855", ok: "#40d9be", mastered: "#FFCC55", warn: "#ffc45c", bad: "#ff7fa8", new: "#a279ff", neutral: "#75e8ff", req: "#75e8ff", rel: MUTED };
const round = (v) => Math.round(v * 100) / 100;

function extent(p) {
  if (p.type === "polyline") return p.points;
  if (p.type === "rect") return [[p.x, p.y], [p.x + p.w, p.y + p.h]];
  if (p.type === "text") return [[p.x, p.y]];
  return [[p.x1, p.y1], [p.x2, p.y2]];
}

function dashes(p) {
  const length = Math.hypot(p.x2 - p.x1, p.y2 - p.y1);
  if (!length) return [];
  const ux = (p.x2 - p.x1) / length, uy = (p.y2 - p.y1) / length, out = [];
  for (let d = 0; d < length; d += DASH + GAP) {
    const end = Math.min(length, d + DASH);
    out.push({ ...p, dashed: false, x1: p.x1 + ux * d, y1: p.y1 + uy * d, x2: p.x1 + ux * end, y2: p.y1 + uy * end });
  }
  return out;
}

/** toBoardPrimitives(spec) -> items centred on (cx, cy), for an op { kind: "add", items }. */
export function toBoardItems(primitives, { cx, cy, page, z = 1, ink }) {
  const pts = primitives.flatMap(extent);
  if (!pts.length) return [];
  const x0 = Math.min(...pts.map((p) => p[0])), x1 = Math.max(...pts.map((p) => p[0]));
  const y0 = Math.min(...pts.map((p) => p[1])), y1 = Math.max(...pts.map((p) => p[1]));
  const X = (v) => round(cx + (v - (x0 + x1) / 2) * SCALE), Y = (v) => round(cy + (v - (y0 + y1) / 2) * SCALE);
  const stroke = (w) => Math.max(1, round((w || 1.6) * SCALE));
  const color = (p) => ROLE[p.role] || (p.color === "ink" ? ink : p.color === "muted" ? MUTED : "#a279ff");

  const expanded = primitives.flatMap((p) => (p.dashed && p.type === "line" ? dashes(p) : [p]));
  const parts = expanded.length <= MAX_ITEMS ? expanded : primitives; // too many dashes for one operation: solid lines instead
  let zi = z;
  const base = (prefix) => ({ id: makeId(prefix), page, z: zi++ });
  return parts.slice(0, MAX_ITEMS).map((p) => {
    if (p.type === "polyline")
      return { ...base("dg"), type: "path", tool: "pen", color: color(p), width: stroke(p.width), pressure: true, points: p.points.map(([x, y]) => [X(x), Y(y), 0.5]) };
    if (p.type === "text") {
      const size = round(p.size * SCALE);
      const shift = p.anchor === "middle" ? textWidth(p.text, size) / 2 : p.anchor === "end" ? textWidth(p.text, size) : 0;
      return { ...base("dg"), type: "text", x: round(X(p.x) - shift), y: round(Y(p.y) - size * BASELINE), size, color: color(p), text: p.text };
    }
    if (p.type === "rect") {
      const shape = p.radius > 0 && p.radius * 2 >= Math.min(p.w, p.h) ? "ellipse" : "rect";
      return { ...base("dg"), type: "shape", shape, x0: X(p.x), y0: Y(p.y), x1: X(p.x + p.w), y1: Y(p.y + p.h), color: color(p), width: stroke(p.width), fill: p.filled };
    }
    return { ...base("dg"), type: "shape", shape: p.type, x0: X(p.x1), y0: Y(p.y1), x1: X(p.x2), y1: Y(p.y2), color: color(p), width: stroke(p.width), fill: false };
  });
}
