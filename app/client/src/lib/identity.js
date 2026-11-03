// A stable per-browser identity, so host status, votes and undo survive reconnects.
import { makeId } from "./geometry.js";

export function readStored(key, fallback = "") {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function store(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable (private mode); the value just isn't remembered.
  }
}

let cached = null;
export function clientId() {
  if (cached) return cached;
  cached = readStored("ednova-board-client", "");
  if (!/^[\w-]{4,64}$/.test(cached)) {
    cached = makeId("c");
    store("ednova-board-client", cached);
  }
  return cached;
}
