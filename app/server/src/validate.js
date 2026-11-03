// Input validation. Everything that arrives over the socket passes through here
// before it can touch room state or the database.

const LIMITS = {
  coord: 1e6,
  pointsPerStroke: 5000,
  itemsPerOp: 500,
  idsPerOp: 2000,
  textLength: 500,
  latexLength: 500,
  imageBytes: 2_500_000,
  nameLength: 24,
  roomLength: 80,
  pageNameLength: 40
};

const TOOLS = new Set(["pen", "highlighter"]);
const SHAPES = new Set(["line", "arrow", "rect", "ellipse"]);
const REACTIONS = new Set(["✦", "👍", "❤️", "💡", "❓", "🎉"]);
const IMAGE_PREFIX = /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$/;

const isNum = (v, min = -LIMITS.coord, max = LIMITS.coord) =>
  typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;
const isColor = (c) => typeof c === "string" && /^#[0-9a-fA-F]{3,8}$/.test(c);
const isId = (id) => typeof id === "string" && /^[\w-]{4,64}$/.test(id);
const isPoint = (p) =>
  Array.isArray(p) && (p.length === 2 || p.length === 3) &&
  isNum(p[0]) && isNum(p[1]) && (p.length === 2 || isNum(p[2], 0, 1));

function cleanString(value, max) {
  if (typeof value !== "string") return "";
  return value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "").slice(0, max);
}

function cleanName(name) {
  return cleanString(name, 60).replace(/\s+/g, " ").trim().slice(0, LIMITS.nameLength);
}

function cleanRoom(room) {
  return cleanString(room, 200).trim().slice(0, LIMITS.roomLength);
}

const round = (n) => Math.round(n * 100) / 100;

/**
 * Returns a sanitized copy of an item, or null if it is malformed.
 * Server-owned fields (owner, votes) are not taken from the client.
 */
function cleanItem(raw) {
  if (!raw || typeof raw !== "object") return null;
  if (!isId(raw.id) || !isId(raw.page) || !isNum(raw.z)) return null;
  const base = { id: raw.id, type: raw.type, page: raw.page, z: raw.z };

  switch (raw.type) {
    case "path": {
      if (!TOOLS.has(raw.tool) || !isColor(raw.color) || !isNum(raw.width, 1, 400)) return null;
      if (!Array.isArray(raw.points) || raw.points.length === 0) return null;
      if (raw.points.length > LIMITS.pointsPerStroke || !raw.points.every(isPoint)) return null;
      return {
        ...base, tool: raw.tool, color: raw.color, width: raw.width,
        pressure: Boolean(raw.pressure),
        points: raw.points.map((p) => p.map(round))
      };
    }
    case "shape": {
      const { shape, x0, y0, x1, y1, width, fill } = raw;
      if (!SHAPES.has(shape) || !isColor(raw.color) || !isNum(width, 1, 400)) return null;
      if (![x0, y0, x1, y1].every((v) => isNum(v))) return null;
      return { ...base, shape, x0, y0, x1, y1, width, color: raw.color, fill: Boolean(fill) };
    }
    case "text": {
      const text = cleanString(raw.text, LIMITS.textLength);
      if (!text.trim() || !isColor(raw.color) || !isNum(raw.x) || !isNum(raw.y) || !isNum(raw.size, 6, 600)) return null;
      return { ...base, x: raw.x, y: raw.y, size: raw.size, color: raw.color, text };
    }
    case "note": {
      if (!isColor(raw.color) || !isNum(raw.x) || !isNum(raw.y) || !isNum(raw.w, 40, 4000) || !isNum(raw.h, 40, 4000)) return null;
      return { ...base, x: raw.x, y: raw.y, w: raw.w, h: raw.h, color: raw.color, text: cleanString(raw.text, LIMITS.textLength) };
    }
    case "image": {
      if (typeof raw.src !== "string" || raw.src.length > LIMITS.imageBytes || !IMAGE_PREFIX.test(raw.src)) return null;
      if (!isNum(raw.x) || !isNum(raw.y) || !isNum(raw.w, 1, 50000) || !isNum(raw.h, 1, 50000)) return null;
      return { ...base, x: raw.x, y: raw.y, w: raw.w, h: raw.h, src: raw.src, name: cleanString(raw.name, 80) };
    }
    case "equation": {
      const latex = cleanString(raw.latex, LIMITS.latexLength);
      if (!latex.trim() || !isColor(raw.color)) return null;
      if (!isNum(raw.x) || !isNum(raw.y) || !isNum(raw.w, 1, 50000) || !isNum(raw.h, 1, 50000)) return null;
      return { ...base, x: raw.x, y: raw.y, w: raw.w, h: raw.h, color: raw.color, latex };
    }
    default:
      return null;
  }
}

module.exports = {
  LIMITS, TOOLS, REACTIONS,
  isNum, isColor, isId, isPoint,
  cleanItem, cleanName, cleanRoom, cleanString
};
