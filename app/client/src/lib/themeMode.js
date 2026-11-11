// Light / dark / system appearance. The choice is per browser (localStorage "lumi-theme"); it is applied to <html> as
// data-theme="light" | "dark" before the first paint (main.jsx), and the tokens in styles/lumi-tokens.css follow it.
// Dark stays the default: it is the look the app was designed in. "system" follows the OS and changes with it.
import { useSyncExternalStore } from "react";
import { readStored, store } from "./identity.js";

const KEY = "lumi-theme";
export const THEMES = ["dark", "light", "system"];
const listeners = new Set();
const media = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null);

export function getThemePref() {
  const v = readStored(KEY, "dark");
  return THEMES.includes(v) ? v : "dark";
}

export const resolveTheme = (pref) => (pref === "system" ? (media()?.matches ? "light" : "dark") : pref);

export function applyTheme(pref = getThemePref()) {
  const theme = resolveTheme(pref);
  const html = document.documentElement;
  html.dataset.theme = theme;
  html.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#F3F4FC" : "#05061A");
  return theme;
}

let watching = false;
function watchSystem() {
  if (watching) return;
  watching = true;
  media()?.addEventListener?.("change", () => {
    if (getThemePref() !== "system") return;
    applyTheme("system");
    listeners.forEach((fn) => fn());
  });
}

export function setThemePref(pref) {
  if (!THEMES.includes(pref)) return;
  store(KEY, pref);
  applyTheme(pref);
  listeners.forEach((fn) => fn());
}

const subscribe = (fn) => {
  watchSystem();
  listeners.add(fn);
  return () => listeners.delete(fn);
};

/** { pref: "dark" | "light" | "system", theme: "dark" | "light", setPref } */
export function useTheme() {
  const pref = useSyncExternalStore(subscribe, getThemePref, () => "dark");
  const theme = useSyncExternalStore(subscribe, () => document.documentElement.dataset.theme || "dark", () => "dark");
  return { pref, theme, setPref: setThemePref };
}
