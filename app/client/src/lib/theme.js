// Palette and options shared by the board, panels and exporters.

export const PALETTE = [
  { name: "Starlight", value: "#f5f5ff" },
  { name: "Violet", value: "#a279ff" },
  { name: "Aqua", value: "#75e8ff" },
  { name: "Teal", value: "#40d9be" },
  { name: "Amber", value: "#ffc45c" },
  { name: "Rose", value: "#ff7fa8" },
  { name: "Ink", value: "#172554" }
];
export const STARLIGHT = PALETTE[0].value;
export const INK = PALETTE[PALETTE.length - 1].value;

export const NOTE_COLORS = [
  { name: "Amber", value: "#ffd98a" },
  { name: "Rose", value: "#ffb8cf" },
  { name: "Aqua", value: "#aeeeff" },
  { name: "Teal", value: "#a6f0dc" },
  { name: "Violet", value: "#d6c8ff" },
  { name: "Cloud", value: "#f1f3fb" }
];
export const NOTE_INK = "#1a1f3a";

export const REACTIONS = ["✦", "👍", "❤️", "💡", "❓", "🎉"];

export const BACKGROUNDS = [
  { id: "stars", label: "Starfield" },
  { id: "grid", label: "Dot grid" },
  { id: "graph", label: "Graph" },
  { id: "paper", label: "Paper" }
];

export const isLightBackground = (bg) => bg === "paper";
export const inkFor = (bg) => (isLightBackground(bg) ? INK : STARLIGHT);

/**
 * The two default inks follow the surface they are shown on, so work drawn on Paper stays readable on the dark board and
 * the other way round. Only the display changes: the stored item keeps its colour.
 */
export function inkOn(color, bg) {
  const c = String(color).toLowerCase();
  if (isLightBackground(bg)) return c === STARLIGHT ? INK : color;
  return c === INK ? STARLIGHT : color;
}

export const TIMER_PRESETS = [60, 180, 300, 600, 900];
