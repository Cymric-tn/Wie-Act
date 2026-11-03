// Ready-made layouts inserted as ordinary items, centred on (cx, cy).
import { makeId, textWidth } from "./geometry.js";
import { NOTE_COLORS } from "./theme.js";

export const TEMPLATES = [
  { id: "axes", label: "Graph axes", hint: "x/y axes with ticks" },
  { id: "venn", label: "Venn diagram", hint: "Compare two ideas" },
  { id: "mindmap", label: "Mind map", hint: "Central idea, six branches" },
  { id: "kwl", label: "KWL chart", hint: "Know · Want · Learned" },
  { id: "cornell", label: "Cornell notes", hint: "Cues, notes, summary" },
  { id: "exit", label: "Exit ticket", hint: "Three reflection notes" }
];

const ACCENT = { violet: "#a279ff", aqua: "#75e8ff", teal: "#40d9be", amber: "#ffc45c", rose: "#ff7fa8" };

/** Builds the items for a template. `ink` is the text/line colour for the background. */
export function buildTemplate(id, { cx, cy, page, z = 1, ink }) {
  let zi = z;
  const base = () => ({ id: makeId("t"), page, z: zi++ });
  const line = (x0, y0, x1, y1, color = ink, width = 3, shape = "line") =>
    ({ ...base(), type: "shape", shape, x0, y0, x1, y1, color, width, fill: false });
  const box = (x0, y0, x1, y1, color = ink, fill = false, width = 3, shape = "rect") =>
    ({ ...base(), type: "shape", shape, x0, y0, x1, y1, color, width, fill });
  const text = (x, y, value, size = 28, color = ink) => ({ ...base(), type: "text", x, y, text: value, size, color });
  const centered = (x, y, value, size = 28, color = ink) => text(x - textWidth(value, size) / 2, y - size * 0.6, value, size, color);
  const note = (x, y, value, color, w = 200, h = 130) => ({ ...base(), type: "note", x: x - w / 2, y: y - h / 2, w, h, color, text: value });

  switch (id) {
    case "axes": {
      const items = [
        line(cx - 320, cy, cx + 320, cy, ink, 3, "arrow"),
        line(cx, cy + 230, cx, cy - 230, ink, 3, "arrow")
      ];
      for (let k = -7; k <= 7; k++) if (k) items.push(line(cx + k * 40, cy - 7, cx + k * 40, cy + 7, ink, 2));
      for (let k = -5; k <= 5; k++) if (k) items.push(line(cx - 7, cy + k * 40, cx + 7, cy + k * 40, ink, 2));
      items.push(text(cx + 300, cy + 14, "x", 30), text(cx + 16, cy - 250, "y", 30), text(cx - 30, cy + 10, "O", 24));
      return items;
    }
    case "venn":
      return [
        box(cx - 300, cy - 180, cx + 60, cy + 180, ACCENT.violet, true, 4, "ellipse"),
        box(cx - 60, cy - 180, cx + 300, cy + 180, ACCENT.aqua, true, 4, "ellipse"),
        centered(cx - 160, cy - 215, "A", 34, ACCENT.violet),
        centered(cx + 160, cy - 215, "B", 34, ACCENT.aqua),
        centered(cx, cy, "Both", 26),
        centered(cx, cy + 240, "Compare & contrast", 26)
      ];
    case "mindmap": {
      const items = [box(cx - 140, cy - 70, cx + 140, cy + 70, ACCENT.violet, true, 4, "ellipse"), centered(cx, cy, "Main idea", 32)];
      for (let k = 0; k < 6; k++) {
        const angle = (Math.PI * 2 * k) / 6 - Math.PI / 2;
        const nx = cx + Math.cos(angle) * 360;
        const ny = cy + Math.sin(angle) * 250;
        items.unshift(line(cx + Math.cos(angle) * 140, cy + Math.sin(angle) * 70, nx - Math.cos(angle) * 100, ny - Math.sin(angle) * 65, ink, 3));
        items.push(note(nx, ny, `Branch ${k + 1}`, NOTE_COLORS[k % NOTE_COLORS.length].value, 190, 120));
      }
      return items;
    }
    case "kwl": {
      const heads = [["K", "What I know", ACCENT.teal], ["W", "Want to know", ACCENT.amber], ["L", "What I learned", ACCENT.violet]];
      const items = [];
      heads.forEach(([letter, label, color], k) => {
        const x0 = cx - 420 + k * 285;
        items.push(box(x0, cy - 230, x0 + 265, cy + 250, color, false, 3));
        items.push(text(x0 + 20, cy - 210, letter, 44, color), text(x0 + 70, cy - 196, label, 24));
        items.push(line(x0, cy - 150, x0 + 265, cy - 150, color, 2));
      });
      return items;
    }
    case "cornell":
      return [
        box(cx - 400, cy - 280, cx + 400, cy + 280, ink, false, 3),
        line(cx - 170, cy - 220, cx - 170, cy + 150, ink, 2),
        line(cx - 400, cy - 220, cx + 400, cy - 220, ink, 2),
        line(cx - 400, cy + 150, cx + 400, cy + 150, ink, 2),
        text(cx - 380, cy - 262, "Topic:", 26, ACCENT.violet),
        text(cx - 380, cy - 200, "Cues", 24, ACCENT.aqua),
        text(cx - 150, cy - 200, "Notes", 24, ACCENT.aqua),
        text(cx - 380, cy + 168, "Summary", 24, ACCENT.aqua)
      ];
    case "exit":
      return [
        centered(cx, cy - 170, "Exit ticket", 34, ACCENT.violet),
        note(cx - 240, cy, "One thing I learned today…", NOTE_COLORS[2].value, 220, 160),
        note(cx, cy, "One question I still have…", NOTE_COLORS[0].value, 220, 160),
        note(cx + 240, cy, "How confident am I? (1–5)", NOTE_COLORS[4].value, 220, 160)
      ];
    default:
      return [];
  }
}
