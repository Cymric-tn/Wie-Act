// Pure geometry for board items: bounds, hit testing, transforms and shape
// recognition. No DOM access, so it runs under Node's test runner too.

export const NOTE_TEXT_SIZE = 20;
export const NOTE_PADDING = 16;
export const TEXT_LINE_HEIGHT = 1.25;

// Text width depends on fonts; the renderer swaps in a canvas-based measurer.
let measureText = (text, size) => text.length * size * 0.56;
export function setTextMeasurer(fn) {
  measureText = fn;
}
export const textWidth = (text, size) => measureText(text, size);

export function makeId(prefix = "i") {
  const rand = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now().toString(36)}${rand}`;
}

export const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
export const dist = (ax, ay, bx, by) => Math.hypot(ax - bx, ay - by);

export function distToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) return dist(px, py, ax, ay);
  const t = clamp(((px - ax) * dx + (py - ay) * dy) / len2, 0, 1);
  return dist(px, py, ax + t * dx, ay + t * dy);
}

/** Visual stroke thickness in world units. */
export function strokeThickness(item) {
  if (item.type === "path" && item.tool === "highlighter") return item.width * 3;
  return item.width || 0;
}

export function wrapLines(text, size, maxWidth) {
  const lines = [];
  text.split("\n").forEach((paragraph) => {
    const words = paragraph.split(/(\s+)/);
    let line = "";
    words.forEach((word) => {
      const candidate = line + word;
      if (line && textWidth(candidate.trimEnd(), size) > maxWidth) {
        lines.push(line.trimEnd());
        line = word.trimStart();
      } else {
        line = candidate;
      }
    });
    lines.push(line.trimEnd());
  });
  return lines;
}

export function textBox(item) {
  const lines = item.text.split("\n");
  const w = Math.max(...lines.map((l) => textWidth(l, item.size)), item.size * 0.5);
  return { x: item.x, y: item.y, w, h: lines.length * item.size * TEXT_LINE_HEIGHT };
}

/** Axis-aligned bounds {x, y, w, h} including stroke thickness. */
export function bbox(item) {
  switch (item.type) {
    case "path": {
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      item.points.forEach(([x, y]) => {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      });
      const pad = strokeThickness(item) / 2;
      return { x: minX - pad, y: minY - pad, w: maxX - minX + pad * 2, h: maxY - minY + pad * 2 };
    }
    case "shape": {
      const pad = item.width / 2 + (item.shape === "arrow" ? Math.max(12, item.width * 3.2) : 0);
      const x = Math.min(item.x0, item.x1) - pad;
      const y = Math.min(item.y0, item.y1) - pad;
      return { x, y, w: Math.abs(item.x1 - item.x0) + pad * 2, h: Math.abs(item.y1 - item.y0) + pad * 2 };
    }
    case "text":
      return textBox(item);
    default:
      return { x: item.x, y: item.y, w: item.w, h: item.h };
  }
}

export function unionBox(boxes) {
  if (!boxes.length) return null;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  boxes.forEach((b) => {
    minX = Math.min(minX, b.x);
    minY = Math.min(minY, b.y);
    maxX = Math.max(maxX, b.x + b.w);
    maxY = Math.max(maxY, b.y + b.h);
  });
  return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
}

export const boxesIntersect = (a, b) =>
  a.x <= b.x + b.w && b.x <= a.x + a.w && a.y <= b.y + b.h && b.y <= a.y + a.h;

export const boxContains = (outer, inner) =>
  inner.x >= outer.x && inner.y >= outer.y &&
  inner.x + inner.w <= outer.x + outer.w && inner.y + inner.h <= outer.y + outer.h;

export const pointInBox = (x, y, b, pad = 0) =>
  x >= b.x - pad && x <= b.x + b.w + pad && y >= b.y - pad && y <= b.y + b.h + pad;

/** Does point (x, y) touch the item, within `tol` world units? */
export function hitTest(item, x, y, tol = 4) {
  if (!pointInBox(x, y, bbox(item), tol)) return false;
  if (item.type === "path") {
    const reach = strokeThickness(item) / 2 + tol;
    const pts = item.points;
    if (pts.length === 1) return dist(x, y, pts[0][0], pts[0][1]) <= reach;
    for (let i = 1; i < pts.length; i++) {
      if (distToSegment(x, y, pts[i - 1][0], pts[i - 1][1], pts[i][0], pts[i][1]) <= reach) return true;
    }
    return false;
  }
  if (item.type === "shape") {
    const reach = item.width / 2 + tol;
    const { x0, y0, x1, y1 } = item;
    if (item.shape === "line" || item.shape === "arrow") return distToSegment(x, y, x0, y0, x1, y1) <= reach;
    if (item.shape === "rect") {
      const l = Math.min(x0, x1), r = Math.max(x0, x1), t = Math.min(y0, y1), b = Math.max(y0, y1);
      const inside = x >= l && x <= r && y >= t && y <= b;
      if (item.fill && inside) return true;
      return Math.min(
        distToSegment(x, y, l, t, r, t), distToSegment(x, y, r, t, r, b),
        distToSegment(x, y, r, b, l, b), distToSegment(x, y, l, b, l, t)
      ) <= reach;
    }
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const rx = Math.max(Math.abs(x1 - x0) / 2, 1), ry = Math.max(Math.abs(y1 - y0) / 2, 1);
    const r = Math.hypot((x - cx) / rx, (y - cy) / ry);
    if (item.fill && r <= 1) return true;
    return Math.abs(r - 1) * Math.min(rx, ry) <= reach;
  }
  return true; // text, note, image, equation: the box is the hit area
}

/** Topmost item under a point. `items` must be in paint order. */
export function topItemAt(items, x, y, tol) {
  for (let i = items.length - 1; i >= 0; i--) {
    if (hitTest(items[i], x, y, tol)) return items[i];
  }
  return null;
}

/**
 * Applies translate + scale about an origin.
 * t = { dx, dy, sx = 1, sy = 1, ox = 0, oy = 0 }
 */
export function transformItem(item, t) {
  const sx = t.sx ?? 1;
  const sy = t.sy ?? 1;
  const ox = t.ox ?? 0;
  const oy = t.oy ?? 0;
  const map = (x, y) => [ox + (x - ox) * sx + (t.dx || 0), oy + (y - oy) * sy + (t.dy || 0)];
  const strokeScale = clamp((Math.abs(sx) + Math.abs(sy)) / 2, 0.05, 50);
  const round = (n) => Math.round(n * 100) / 100;

  switch (item.type) {
    case "path":
      return {
        ...item,
        width: clamp(round(item.width * strokeScale), 1, 400),
        points: item.points.map(([x, y, p]) => {
          const [nx, ny] = map(x, y);
          return p === undefined ? [round(nx), round(ny)] : [round(nx), round(ny), p];
        })
      };
    case "shape": {
      const [x0, y0] = map(item.x0, item.y0);
      const [x1, y1] = map(item.x1, item.y1);
      return { ...item, x0: round(x0), y0: round(y0), x1: round(x1), y1: round(y1), width: clamp(round(item.width * strokeScale), 1, 400) };
    }
    case "text": {
      const [x, y] = map(item.x, item.y);
      return { ...item, x: round(x), y: round(y), size: clamp(round(item.size * Math.abs(sy)), 6, 600) };
    }
    default: {
      const [ax, ay] = map(item.x, item.y);
      const [bx, by] = map(item.x + item.w, item.y + item.h);
      const min = item.type === "note" ? 40 : 1;
      return {
        ...item,
        x: round(Math.min(ax, bx)),
        y: round(Math.min(ay, by)),
        w: round(Math.max(min, Math.abs(bx - ax))),
        h: round(Math.max(min, Math.abs(by - ay)))
      };
    }
  }
}

/** Does the item have a colour the selection dock can change? */
export const isRecolorable = (item) => item.type !== "image";

// ---------- shape assist ----------

/**
 * Recognises a freehand stroke as a straight line, rectangle or ellipse.
 * Returns { shape, x0, y0, x1, y1 } or null when nothing fits well.
 */
export function recognizeShape(points) {
  if (points.length < 6) return null;
  let length = 0;
  for (let i = 1; i < points.length; i++) {
    length += dist(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]);
  }
  const [sx, sy] = points[0];
  const [ex, ey] = points[points.length - 1];
  const chord = dist(sx, sy, ex, ey);

  // Straight line: every point hugs the chord.
  if (chord > 30 && length < chord * 1.15) {
    const maxDev = Math.max(...points.map(([x, y]) => distToSegment(x, y, sx, sy, ex, ey)));
    if (maxDev <= Math.max(6, chord * 0.06)) return { shape: "line", x0: sx, y0: sy, x1: ex, y1: ey };
  }

  // Closed shapes: the stroke ends near where it started.
  if (length < 80 || chord > length * 0.2) return null;
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const w = maxX - minX, h = maxY - minY;
  if (w < 20 || h < 20) return null;
  const diag = Math.hypot(w, h);

  const rectErr = points.reduce((sum, [x, y]) =>
    sum + Math.min(Math.abs(x - minX), Math.abs(x - maxX), Math.abs(y - minY), Math.abs(y - maxY)), 0) / points.length / diag;

  const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2, rx = w / 2, ry = h / 2;
  const ellipseErr = points.reduce((sum, [x, y]) =>
    sum + Math.abs(Math.hypot((x - cx) / rx, (y - cy) / ry) - 1) * Math.min(rx, ry), 0) / points.length / diag;

  const best = rectErr < ellipseErr ? { shape: "rect", err: rectErr } : { shape: "ellipse", err: ellipseErr };
  if (best.err > 0.045) return null;
  return { shape: best.shape, x0: minX, y0: minY, x1: maxX, y1: maxY };
}

// ---------- viewport ----------

export const screenToWorld = (view, sx, sy) => ({ x: view.x + sx / view.zoom, y: view.y + sy / view.zoom });
export const worldToScreen = (view, wx, wy) => ({ x: (wx - view.x) * view.zoom, y: (wy - view.y) * view.zoom });

export const MIN_ZOOM = 0.1;
export const MAX_ZOOM = 8;

/** Zoom by `factor` keeping screen point (sx, sy) fixed. */
export function zoomAt(view, factor, sx, sy) {
  const zoom = clamp(view.zoom * factor, MIN_ZOOM, MAX_ZOOM);
  const world = screenToWorld(view, sx, sy);
  return { x: world.x - sx / zoom, y: world.y - sy / zoom, zoom };
}

/** A view that shows `box` inside a screen of width × height. */
export function fitView(box, width, height, padding = 60, maxZoom = 1.5) {
  if (!box || box.w <= 0 || box.h <= 0) return { x: -width / 2, y: -height / 2, zoom: 1 };
  const zoom = clamp(Math.min((width - padding * 2) / box.w, (height - padding * 2) / box.h), MIN_ZOOM, maxZoom);
  return {
    x: box.x + box.w / 2 - width / 2 / zoom,
    y: box.y + box.h / 2 - height / 2 / zoom,
    zoom
  };
}

/** World rectangle visible on screen. */
export const visibleBox = (view, width, height) => ({ x: view.x, y: view.y, w: width / view.zoom, h: height / view.zoom });

// ---------- sticky-note vote badge ----------

export const VOTE_BADGE = { w: 58, h: 30, margin: 10 };

export function voteBadgeBox(note) {
  return {
    x: note.x + note.w - VOTE_BADGE.w - VOTE_BADGE.margin,
    y: note.y + note.h - VOTE_BADGE.h - VOTE_BADGE.margin,
    w: VOTE_BADGE.w,
    h: VOTE_BADGE.h
  };
}
