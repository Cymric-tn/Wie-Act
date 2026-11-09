// Canvas rendering for items, backgrounds and laser trails.
import { getStroke } from "perfect-freehand";
import {
  setTextMeasurer, wrapLines, voteBadgeBox,
  NOTE_TEXT_SIZE, NOTE_PADDING, TEXT_LINE_HEIGHT
} from "./geometry.js";
import { NOTE_INK, inkOn } from "./theme.js";
import { texToSvg } from "./math.js";
import { lumiColors } from "./lumiColors.js";

export const FONT_FAMILY = "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif";
export const font = (size, weight = 600) => `${weight} ${size}px ${FONT_FAMILY}`;

const measureCtx = document.createElement("canvas").getContext("2d");
setTextMeasurer((text, size) => {
  measureCtx.font = font(size);
  return measureCtx.measureText(text).width;
});

// ---------- async assets (images, equations, fonts) ----------
let onAssetReady = () => {};
export function setAssetListener(fn) {
  onAssetReady = fn;
}
document.fonts?.ready.then(() => onAssetReady());

const assets = new Map(); // key -> { img, ready, error, promise }

function asset(key, getSrc) {
  let entry = assets.get(key);
  if (entry) return entry;
  entry = { img: new Image(), ready: false, error: null };
  entry.promise = Promise.resolve()
    .then(getSrc)
    .then((src) => new Promise((resolve) => {
      entry.img.onload = () => { entry.ready = true; resolve(); };
      entry.img.onerror = () => { entry.error = "Image failed to load"; resolve(); };
      entry.img.src = src;
    }))
    .catch((error) => { entry.error = error.message || String(error); })
    .finally(() => onAssetReady());
  assets.set(key, entry);
  if (assets.size > 400) assets.delete(assets.keys().next().value);
  return entry;
}

const equationKey = (item) => `eq|${item.color}|${item.latex}`;
const equationAsset = (item) => asset(equationKey(item), () => texToSvg(item.latex, item.color).then((r) => r.src));
const imageAsset = (item) => asset(item.src, () => item.src);

/** The equation's SVG data URL once rendered (used by SVG export). */
export function equationSrc(item) {
  const entry = equationAsset(item);
  return entry.ready ? entry.img.src : null;
}

/** Resolves when every image and equation among `items` has loaded. */
export function ensureAssets(items) {
  return Promise.all(items.map((item) => {
    if (item.type === "image") return imageAsset(item).promise;
    if (item.type === "equation") return equationAsset(item).promise;
    return null;
  }));
}

// ---------- freehand pen outline ----------
const outlineCache = new WeakMap();

function outlineToPathData(points) {
  if (points.length < 4) return "";
  let d = `M${points[0][0].toFixed(2)},${points[0][1].toFixed(2)} Q`;
  for (let i = 0; i < points.length; i++) {
    const [x0, y0] = points[i];
    const [x1, y1] = points[(i + 1) % points.length];
    d += `${x0.toFixed(2)},${y0.toFixed(2)} ${((x0 + x1) / 2).toFixed(2)},${((y0 + y1) / 2).toFixed(2)} `;
  }
  return `${d}Z`;
}

/** SVG path data for a pen stroke; widths follow pen pressure or speed. */
export function penPathData(item, complete = true) {
  if (complete && outlineCache.has(item)) return outlineCache.get(item);
  const points = item.points.map(([x, y, p]) => [x, y, p ?? 0.5]);
  const outline = getStroke(points, {
    size: item.width * 1.6,
    thinning: item.pressure ? 0.65 : 0.45,
    smoothing: 0.55,
    streamline: 0.4,
    simulatePressure: !item.pressure,
    last: complete
  });
  const d = outlineToPathData(outline);
  if (complete) outlineCache.set(item, d);
  return d;
}

function traceHighlighter(ctx, points) {
  ctx.beginPath();
  ctx.moveTo(points[0][0], points[0][1]);
  if (points.length === 1) ctx.lineTo(points[0][0] + 0.01, points[0][1]);
  for (let i = 1; i < points.length - 1; i++) {
    const [x, y] = points[i];
    const [nx, ny] = points[i + 1];
    ctx.quadraticCurveTo(x, y, (x + nx) / 2, (y + ny) / 2);
  }
  if (points.length > 1) ctx.lineTo(points[points.length - 1][0], points[points.length - 1][1]);
}

export function arrowHead(x0, y0, x1, y1, width) {
  const angle = Math.atan2(y1 - y0, x1 - x0);
  const size = Math.max(12, width * 3.2);
  return [
    [x1, y1],
    [x1 - size * Math.cos(angle - 0.45), y1 - size * Math.sin(angle - 0.45)],
    [x1 - size * Math.cos(angle + 0.45), y1 - size * Math.sin(angle + 0.45)]
  ];
}

function roundRectPath(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, Math.min(r, w / 2, h / 2));
}

// ---------- items ----------
// The surface the next items land on: drawBackground sets it, for the board and for exports alike.
let surface = "grid";
const shownByItem = new WeakMap(); // one display copy per recoloured item, so per-item caches keep working

function shownOnSurface(item) {
  const color = inkOn(item.color, surface);
  if (color === item.color) return item;
  let shown = shownByItem.get(item);
  if (!shown || shown.color !== color) shownByItem.set(item, (shown = { ...item, color }));
  return shown;
}

/**
 * Draws one item in world coordinates (the caller sets the view transform).
 * opts.live: stroke still being drawn (skip caching).
 */
export function drawItem(ctx, stored, opts = {}) {
  const item = shownOnSurface(stored);
  ctx.save();
  if (opts.alpha !== undefined) ctx.globalAlpha = opts.alpha;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = item.color;
  ctx.fillStyle = item.color;

  switch (item.type) {
    case "path":
      if (item.tool === "highlighter") {
        ctx.globalAlpha *= 0.32;
        ctx.lineCap = "square";
        ctx.lineWidth = item.width * 3;
        traceHighlighter(ctx, item.points);
        ctx.stroke();
      } else {
        const d = penPathData(item, !opts.live);
        if (d) ctx.fill(new Path2D(d));
        else {
          ctx.beginPath();
          ctx.arc(item.points[0][0], item.points[0][1], item.width * 0.6, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      break;

    case "shape": {
      const { shape, x0, y0, x1, y1, width, fill } = item;
      ctx.lineWidth = width;
      ctx.beginPath();
      if (shape === "rect") {
        ctx.roundRect(Math.min(x0, x1), Math.min(y0, y1), Math.abs(x1 - x0), Math.abs(y1 - y0), 6);
      } else if (shape === "ellipse") {
        ctx.ellipse((x0 + x1) / 2, (y0 + y1) / 2, Math.abs(x1 - x0) / 2, Math.abs(y1 - y0) / 2, 0, 0, Math.PI * 2);
      } else {
        ctx.moveTo(x0, y0);
        ctx.lineTo(x1, y1);
      }
      if (fill && (shape === "rect" || shape === "ellipse")) {
        const alpha = ctx.globalAlpha;
        ctx.globalAlpha = alpha * 0.18;
        ctx.fill();
        ctx.globalAlpha = alpha;
      }
      ctx.stroke();
      if (shape === "arrow") {
        const head = arrowHead(x0, y0, x1, y1, width);
        ctx.beginPath();
        ctx.moveTo(...head[0]);
        ctx.lineTo(...head[1]);
        ctx.lineTo(...head[2]);
        ctx.closePath();
        ctx.fill();
      }
      break;
    }

    case "text":
      ctx.font = font(item.size);
      ctx.textBaseline = "top";
      item.text.split("\n").forEach((line, i) => {
        ctx.fillText(line, item.x, item.y + i * item.size * TEXT_LINE_HEIGHT);
      });
      break;

    case "note": {
      const { x, y, w, h } = item;
      ctx.shadowColor = "#00000059";
      ctx.shadowBlur = 18;
      ctx.shadowOffsetY = 6;
      roundRectPath(ctx, x, y, w, h, 14);
      ctx.fill();
      ctx.shadowColor = "transparent";
      ctx.fillStyle = "#ffffff55";
      ctx.fillRect(x + 14, y, w - 28, 3);

      ctx.save();
      roundRectPath(ctx, x, y, w, h, 14);
      ctx.clip();
      ctx.fillStyle = NOTE_INK;
      ctx.font = font(NOTE_TEXT_SIZE);
      ctx.textBaseline = "top";
      wrapLines(item.text || "", NOTE_TEXT_SIZE, w - NOTE_PADDING * 2).forEach((line, i) => {
        ctx.fillText(line, x + NOTE_PADDING, y + NOTE_PADDING + i * NOTE_TEXT_SIZE * TEXT_LINE_HEIGHT);
      });
      ctx.restore();

      const votes = item.votes?.length || 0;
      const badge = voteBadgeBox(item);
      roundRectPath(ctx, badge.x, badge.y, badge.w, badge.h, 15);
      ctx.fillStyle = votes ? lumiColors().iris : "#1a1f3a1f";
      ctx.fill();
      ctx.fillStyle = votes ? "#ffffff" : "#1a1f3a99";
      ctx.font = font(15, 700);
      ctx.textBaseline = "middle";
      ctx.textAlign = "center";
      ctx.fillText(`▲ ${votes}`, badge.x + badge.w / 2, badge.y + badge.h / 2 + 1);
      break;
    }

    case "image": {
      const entry = imageAsset(item);
      if (entry.ready) {
        ctx.drawImage(entry.img, item.x, item.y, item.w, item.h);
      } else {
        ctx.fillStyle = "#ffffff12";
        ctx.fillRect(item.x, item.y, item.w, item.h);
      }
      break;
    }

    case "equation": {
      const entry = equationAsset(item);
      if (entry.ready) {
        ctx.drawImage(entry.img, item.x, item.y, item.w, item.h);
      } else {
        ctx.font = font(18, 500);
        ctx.textBaseline = "top";
        ctx.globalAlpha *= 0.6;
        ctx.fillText(entry.error ? `⚠ ${item.latex}` : item.latex, item.x, item.y);
      }
      break;
    }
    default:
      break;
  }
  ctx.restore();
}

// ---------- backgrounds (screen space) ----------
const STAR_TILES = [512, 733].map((size, layer) => {
  let seed = 37 + layer * 101;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const stars = Array.from({ length: layer ? 34 : 46 }, () => ({
    x: rand() * size,
    y: rand() * size,
    r: rand() * 1.3 + 0.3,
    a: 0.12 + rand() * 0.45,
    violet: rand() > 0.82
  }));
  return { size, stars, parallax: layer ? 0.18 : 0.3 };
});

export const BACKGROUND_FILL = {
  get stars() { return lumiColors().board; }, get grid() { return lumiColors().board; }, get graph() { return lumiColors().board; },
  get paper() { return lumiColors().room; }
};

export function drawBackground(ctx, mode, view, width, height) {
  surface = mode;
  ctx.save();
  if (mode === "paper") {
    ctx.fillStyle = BACKGROUND_FILL.paper;
    ctx.fillRect(0, 0, width, height);
    ctx.restore();
    return;
  }
  ctx.fillStyle = lumiColors().board;
  ctx.fillRect(0, 0, width, height);

  if (mode === "stars") {
    const glow = ctx.createRadialGradient(width * 0.58, height * 0.44, 0, width * 0.58, height * 0.44, width * 0.45);
    glow.addColorStop(0, `${lumiColors().iris}2a`);
    glow.addColorStop(1, `${lumiColors().iris}00`);
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, width, height);

    const scale = Math.min(1.5, Math.max(0.6, Math.sqrt(view.zoom)));
    STAR_TILES.forEach(({ size, stars, parallax }) => {
      const ox = -((view.x * view.zoom * parallax) % size + size) % size;
      const oy = -((view.y * view.zoom * parallax) % size + size) % size;
      for (let tx = ox; tx < width; tx += size) {
        for (let ty = oy; ty < height; ty += size) {
          stars.forEach((s) => {
            ctx.globalAlpha = s.a;
            ctx.fillStyle = "#ffffff";
            ctx.beginPath();
            ctx.arc(tx + s.x, ty + s.y, s.r * scale, 0, Math.PI * 2);
            ctx.fill();
          });
        }
      }
    });
  } else {
    // Grid spacing grows in steps so lines never get denser than ~14px on screen.
    let step = mode === "graph" ? 20 : 40;
    while (step * view.zoom < 14) step *= 5;
    const s = step * view.zoom;
    const startX = -((view.x * view.zoom) % s + s) % s;
    const startY = -((view.y * view.zoom) % s + s) % s;
    const firstX = Math.round((view.x + startX / view.zoom) / step);
    const firstY = Math.round((view.y + startY / view.zoom) / step);

    if (mode === "grid") {
      ctx.fillStyle = "#8ea2e03a";
      for (let x = startX; x < width; x += s) {
        for (let y = startY; y < height; y += s) ctx.fillRect(x - 1, y - 1, 2, 2);
      }
    } else {
      const minor = new Path2D();
      const major = new Path2D();
      let i = firstX;
      for (let x = startX; x < width; x += s, i++) {
        const path = i % 5 === 0 ? major : minor;
        path.moveTo(Math.round(x) + 0.5, 0);
        path.lineTo(Math.round(x) + 0.5, height);
      }
      i = firstY;
      for (let y = startY; y < height; y += s, i++) {
        const path = i % 5 === 0 ? major : minor;
        path.moveTo(0, Math.round(y) + 0.5);
        path.lineTo(width, Math.round(y) + 0.5);
      }
      ctx.lineWidth = 1;
      ctx.strokeStyle = "#7f95d81c";
      ctx.stroke(minor);
      ctx.strokeStyle = "#8ea2e04a";
      ctx.stroke(major);
    }
  }
  ctx.restore();
}

// ---------- laser pointer ----------
export const LASER_FADE_MS = 900;

/** Draws a fading trail; points are { x, y, t } in world space. */
export function drawLaser(ctx, points, now, color, zoom) {
  if (points.length < 1) return;
  ctx.save();
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.shadowColor = color;
  ctx.shadowBlur = 14;
  for (let i = 1; i < points.length; i++) {
    const age = now - points[i].t;
    if (age > LASER_FADE_MS) continue;
    const life = 1 - age / LASER_FADE_MS;
    ctx.globalAlpha = life;
    ctx.strokeStyle = color;
    ctx.lineWidth = (3 + 5 * life) / zoom;
    ctx.beginPath();
    ctx.moveTo(points[i - 1].x, points[i - 1].y);
    ctx.lineTo(points[i].x, points[i].y);
    ctx.stroke();
  }
  const head = points[points.length - 1];
  if (now - head.t < LASER_FADE_MS) {
    ctx.globalAlpha = 1 - (now - head.t) / LASER_FADE_MS;
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(head.x, head.y, 4 / zoom, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
}
