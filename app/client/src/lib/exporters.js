// PNG, SVG and multi-page PDF export, plus copying to the clipboard.
import {
  bbox, unionBox, wrapLines, voteBadgeBox,
  NOTE_TEXT_SIZE, NOTE_PADDING, TEXT_LINE_HEIGHT
} from "./geometry.js";
import { drawItem, drawBackground, ensureAssets, penPathData, arrowHead, equationSrc, BACKGROUND_FILL, FONT_FAMILY } from "./render.js";
import { NOTE_INK, inkOn } from "./theme.js";

const PADDING = 60;

export function contentBox(items) {
  const box = unionBox(items.map(bbox));
  if (!box) return { x: 0, y: 0, w: 1600, h: 900 };
  return { x: box.x - PADDING, y: box.y - PADDING, w: box.w + PADDING * 2, h: box.h + PADDING * 2 };
}

/** Renders items onto a fresh canvas, fitted to their bounds. */
export async function renderToCanvas(items, background, { maxSide = 3200, scale = 2 } = {}) {
  await ensureAssets(items);
  const box = contentBox(items);
  const zoom = Math.min(scale, maxSide / Math.max(box.w, box.h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(box.w * zoom));
  canvas.height = Math.max(1, Math.round(box.h * zoom));
  const ctx = canvas.getContext("2d");
  const view = { x: box.x, y: box.y, zoom };
  drawBackground(ctx, background, view, canvas.width, canvas.height);
  ctx.setTransform(zoom, 0, 0, zoom, -box.x * zoom, -box.y * zoom);
  items.forEach((item) => drawItem(ctx, item));
  return canvas;
}

function download(href, filename) {
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

const safeName = (s) => String(s).replace(/[^\w-]+/g, "-").replace(/^-|-$/g, "") || "board";

export async function exportPng(items, background, name) {
  const canvas = await renderToCanvas(items, background);
  download(canvas.toDataURL("image/png"), `${safeName(name)}.png`);
}

export async function copyPng(items, background) {
  const canvas = await renderToCanvas(items, background);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
}

// ---------- SVG ----------
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function textLines(lines, x, y, size, color, weight = 600) {
  return `<text font-family="${esc(FONT_FAMILY)}" font-size="${size}" font-weight="${weight}" fill="${color}" dominant-baseline="hanging">${
    lines.map((line, i) => `<tspan x="${x}" y="${y + i * size * TEXT_LINE_HEIGHT}">${esc(line) || " "}</tspan>`).join("")
  }</text>`;
}

function itemToSvg(item) {
  switch (item.type) {
    case "path":
      if (item.tool === "highlighter") {
        const pts = item.points.map(([x, y]) => `${x},${y}`).join(" ");
        return `<polyline points="${pts}" fill="none" stroke="${item.color}" stroke-opacity="0.32" stroke-width="${item.width * 3}" stroke-linecap="square" stroke-linejoin="round"/>`;
      }
      return `<path d="${penPathData(item)}" fill="${item.color}"/>`;
    case "shape": {
      const { x0, y0, x1, y1, color, width, fill, shape } = item;
      const stroke = `stroke="${color}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round"`;
      const fillAttr = fill ? `fill="${color}" fill-opacity="0.18"` : `fill="none"`;
      if (shape === "rect") {
        return `<rect x="${Math.min(x0, x1)}" y="${Math.min(y0, y1)}" width="${Math.abs(x1 - x0)}" height="${Math.abs(y1 - y0)}" rx="6" ${fillAttr} ${stroke}/>`;
      }
      if (shape === "ellipse") {
        return `<ellipse cx="${(x0 + x1) / 2}" cy="${(y0 + y1) / 2}" rx="${Math.abs(x1 - x0) / 2}" ry="${Math.abs(y1 - y0) / 2}" ${fillAttr} ${stroke}/>`;
      }
      const line = `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" ${stroke}/>`;
      if (shape !== "arrow") return line;
      const head = arrowHead(x0, y0, x1, y1, width).map((p) => p.join(",")).join(" ");
      return `${line}<polygon points="${head}" fill="${color}"/>`;
    }
    case "text":
      return textLines(item.text.split("\n"), item.x, item.y, item.size, item.color);
    case "note": {
      const badge = voteBadgeBox(item);
      const votes = item.votes?.length || 0;
      const lines = wrapLines(item.text || "", NOTE_TEXT_SIZE, item.w - NOTE_PADDING * 2);
      return `<g><rect x="${item.x}" y="${item.y}" width="${item.w}" height="${item.h}" rx="14" fill="${item.color}"/>${
        textLines(lines, item.x + NOTE_PADDING, item.y + NOTE_PADDING, NOTE_TEXT_SIZE, NOTE_INK)
      }<rect x="${badge.x}" y="${badge.y}" width="${badge.w}" height="${badge.h}" rx="15" fill="${votes ? "#6c43d9" : "#1a1f3a1f"}"/><text x="${badge.x + badge.w / 2}" y="${badge.y + badge.h / 2 + 1}" font-family="${esc(FONT_FAMILY)}" font-size="15" font-weight="700" fill="${votes ? "#fff" : "#1a1f3a99"}" text-anchor="middle" dominant-baseline="central">▲ ${votes}</text></g>`;
    }
    case "image":
      return `<image href="${item.src}" x="${item.x}" y="${item.y}" width="${item.w}" height="${item.h}" preserveAspectRatio="none"/>`;
    case "equation": {
      const src = equationSrc(item);
      return src
        ? `<image href="${esc(src)}" x="${item.x}" y="${item.y}" width="${item.w}" height="${item.h}"/>`
        : textLines([item.latex], item.x, item.y, 18, item.color, 500);
    }
    default:
      return "";
  }
}

export async function exportSvg(items, background, name) {
  await ensureAssets(items);
  const box = contentBox(items);
  const body = items.map((item) => itemToSvg({ ...item, color: inkOn(item.color, background) })).join("\n  ");
  const svg = `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="${box.x} ${box.y} ${box.w} ${box.h}" width="${Math.round(box.w)}" height="${Math.round(box.h)}">
  <rect x="${box.x}" y="${box.y}" width="${box.w}" height="${box.h}" fill="${BACKGROUND_FILL[background] || BACKGROUND_FILL.stars}"/>
  ${body}
</svg>`;
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
  download(url, `${safeName(name)}.svg`);
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** One PDF page per board page, each fitted to its content. */
export async function exportPdf(pages, background, name) {
  const { jsPDF } = await import("jspdf");
  let doc = null;
  for (const page of pages) {
    const canvas = await renderToCanvas(page.items, background, { maxSide: 2400, scale: 1.5 });
    const w = canvas.width;
    const h = canvas.height;
    const orientation = w >= h ? "landscape" : "portrait";
    if (!doc) doc = new jsPDF({ orientation, unit: "px", format: [w, h], hotfixes: ["px_scaling"] });
    else doc.addPage([w, h], orientation);
    doc.addImage(canvas.toDataURL("image/jpeg", 0.9), "JPEG", 0, 0, w, h);
    doc.setFontSize(Math.max(12, w / 90));
    doc.setTextColor(background === "paper" ? 90 : 170);
    doc.text(page.name, 16, h - 16);
  }
  if (doc) doc.save(`${safeName(name)}.pdf`);
}
