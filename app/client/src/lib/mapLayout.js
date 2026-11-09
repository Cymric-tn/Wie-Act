// Geometry of the progress map, in canvas pixels. No DOM here, so "nothing clipped, nothing overlapping" can be tested.
export const PAD = 32;
const GAP = 4;            // between a planet and its label
const RING_GAP = 30;      // the closest two orbits may be
const MAX_TILT = 0.6;     // orbit height / width, where there is room to choose
export const LABEL_H = 16;
export const SUN_LABEL_H = 44;

export function shortName(name, max = 18) {
  const chars = [...String(name)];
  return chars.length <= max ? String(name) : `${chars.slice(0, max - 1).join("").trimEnd()}…`;
}

/** How many planets each orbit carries, inner first: roughly in proportion to its length. */
function share(count, weights) {
  const total = weights.reduce((a, b) => a + b, 0);
  const exact = weights.map((w) => (w / total) * count);
  const sizes = exact.map((v) => Math.max(1, Math.floor(v)));
  let left = count - sizes.reduce((a, b) => a + b, 0);
  const byRemainder = exact.map((v, i) => [v - Math.floor(v), i]).sort((a, b) => b[0] - a[0]);
  for (let k = 0; left > 0; k++, left--) sizes[byRemainder[k % sizes.length][1]]++;
  for (let k = sizes.length - 1; left < 0; k = (k + sizes.length - 1) % sizes.length) if (sizes[k] > 1) { sizes[k]--; left++; }
  return sizes;
}

/**
 * Orbits that fit the canvas: outermost orbit + planet + label stay `pad` away from every edge. `extent` is a planet's
 * radius including its progress arc. Only as many orbits as there is room for, and each one carries at least one planet.
 */
export function fitOrbits({ width, height, count, sunRadius, sunLabelHeight = 0, sunLabelHalf = 0, extent, labelWidth = 0, labelHeight = 0, cx = width / 2, pad = PAD }) {
  const roomX = Math.max(extent, Math.min(cx, width - cx) - pad - extent);
  const roomY = Math.max(extent, height / 2 - pad - extent);
  // A label at the far left or right normally needs its whole width outside the orbit. On a narrow canvas the orbit keeps
  // half of that, and placeLabels moves such a label above or below its planet instead.
  const full = roomX - (labelWidth ? GAP + labelWidth : 0);
  const squeezed = roomX - labelWidth / 2;
  const rx = Math.max(full, Math.min(squeezed, sunLabelHalf * 2 + 60), roomX * 0.55);
  const maxY = Math.max(roomY - (labelHeight ? GAP + labelHeight : 0), roomY * 0.6);

  // The height an orbit of this width needs so a planet passes clear of the sun and of the corners of its label.
  const clearance = (orbitRx) => {
    const round = sunRadius + extent + 8;
    if (!sunLabelHeight) return round;
    const half = sunLabelHalf + extent;
    const room = half < orbitRx - 1 ? Math.sqrt(1 - (half / orbitRx) ** 2) : 0.15;
    return Math.max(round, (sunRadius + sunLabelHeight + extent) / room + 2);
  };

  let rings = Math.max(1, Math.min(3, Math.ceil(count / 4), count));
  let stepX = 0;
  let innerY = 0;
  for (; rings >= 1; rings--) {
    stepX = rings > 1 ? Math.max(RING_GAP, Math.min((rx * 0.45) / (rings - 1), (rx - sunLabelHalf - extent - 50) / (rings - 1))) : 0;
    innerY = Math.min(maxY, clearance(rx - stepX * (rings - 1)));
    if (rings === 1 || (maxY - innerY) / (rings - 1) >= RING_GAP) break;
  }
  const outerY = Math.min(maxY, Math.max(innerY + (rings - 1) * RING_GAP, rx * MAX_TILT));

  const orbits = Array.from({ length: rings }, (_, i) => ({
    rx: rx - stepX * (rings - 1 - i),
    ry: rings === 1 ? outerY : innerY + ((outerY - innerY) * i) / (rings - 1),
    speed: 0.3 - i * 0.07,
    phase: i * 1.1
  }));
  const sizes = count ? share(count, orbits.map((o) => o.rx + o.ry)) : orbits.map(() => 0);
  const slots = [];
  sizes.forEach((size, o) => { for (let j = 0; j < size; j++) slots.push({ orbit: o, turn: (j / size) * Math.PI * 2 }); });
  return { cx, cy: height / 2, orbits: orbits.filter((_, o) => sizes[o] > 0), slots };
}

/** Where each planet is at time t, and the unit vector pointing away from the sun. */
export function planetPositions(layout, t) {
  return layout.slots.map(({ orbit, turn }) => {
    const o = layout.orbits[orbit];
    const angle = t * o.speed + o.phase + turn;
    const dx = Math.cos(angle) * o.rx;
    const dy = Math.sin(angle) * o.ry;
    const length = Math.hypot(dx, dy) || 1;
    return { x: layout.cx + dx, y: layout.cy + dy, ux: dx / length, uy: dy / length };
  });
}

const box = (cx, cy, w, h) => ({ x0: cx - w / 2, x1: cx + w / 2, y0: cy - h / 2, y1: cy + h / 2 });
const boxesApart = (a, b, margin) => a.x1 + margin <= b.x0 || b.x1 + margin <= a.x0 || a.y1 + margin <= b.y0 || b.y1 + margin <= a.y0;
export function clearOfCircle(b, c, margin = 0) {
  const dx = Math.max(b.x0 - c.x, 0, c.x - b.x1);
  const dy = Math.max(b.y0 - c.y, 0, c.y - b.y1);
  return dx * dx + dy * dy >= (c.r + margin) ** 2;
}

/** The label's box on the side away from the sun: it slides round its planet smoothly, with no flip from side to side. */
function outwardBox(p, w, h) {
  const circle = { x: p.x, y: p.y, r: p.extent };
  let b;
  for (let d = p.extent + GAP, i = 0; i < 48; i++, d++) {
    b = box(p.x + p.ux * (d + w / 2), p.y + p.uy * (d + h / 2), w, h);
    if (clearOfCircle(b, circle, 1)) break;
  }
  return b;
}

/** Slides a box sideways until it is inside the canvas; the vertical nudges then take it off its own planet. */
function keepInside(b, width, pad) {
  const shift = b.x0 < pad ? pad - b.x0 : b.x1 > width - pad ? width - pad - b.x1 : 0;
  return { ...b, x0: b.x0 + shift, x1: b.x1 + shift };
}

const SLOTS = [0, 1, -1, 2, -2];

/**
 * One label per planet: away from the sun, nudged up or down if something is in the way, hidden if it still does not fit.
 * `previous` is the last frame's result: a label keeps its nudge while that stays free, and a hidden one needs a little
 * extra room to come back, so labels on moving planets do not flicker.
 */
export function placeLabels({ planets, width, height, circles = [], boxes = [], previous = [], pad = PAD }) {
  const allCircles = [...circles, ...planets.map((p) => ({ x: p.x, y: p.y, r: p.extent }))];
  const taken = [...boxes];
  return planets.map((p, i) => {
    const before = previous[i];
    const margin = before && !before.visible ? 3 : 0;
    const order = before?.visible ? [before.slot, ...SLOTS.filter((s) => s !== before.slot)] : SLOTS;
    const base = keepInside(outwardBox(p, p.labelWidth, LABEL_H), width, pad);
    for (const slot of order) {
      const shift = slot * (LABEL_H + 2);
      const b = { ...base, y0: base.y0 + shift, y1: base.y1 + shift };
      const inside = b.x0 >= pad && b.x1 <= width - pad && b.y0 >= pad && b.y1 <= height - pad;
      if (inside && allCircles.every((c) => clearOfCircle(b, c, margin)) && taken.every((t) => boxesApart(b, t, 2 + margin))) {
        taken.push(b);
        return { x: (b.x0 + b.x1) / 2, y: (b.y0 + b.y1) / 2, visible: true, slot, box: b };
      }
    }
    const b = base;
    return { x: (b.x0 + b.x1) / 2, y: (b.y0 + b.y1) / 2, visible: false, slot: 0, box: b };
  });
}
