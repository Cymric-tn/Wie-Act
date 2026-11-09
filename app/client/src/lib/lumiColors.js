// The Lumi colours for code that cannot use CSS variables (the canvas). Read once from the design tokens; the fallbacks
// are the same values, for tests and exports outside a browser.
const FALLBACK = { iris: "#5B5BD6", glow: "#22C3E6", spark: "#FFCC55", room: "#ECEBFF", void: "#05061A", board: "#0E1030" };
let cached = null;

export function lumiColors() {
  if (cached) return cached;
  if (typeof document === "undefined") return FALLBACK;
  const style = getComputedStyle(document.documentElement);
  const read = (name) => style.getPropertyValue(`--lumi-${name}`).trim() || FALLBACK[name];
  cached = Object.fromEntries(Object.keys(FALLBACK).map((name) => [name, read(name)]));
  return cached;
}
