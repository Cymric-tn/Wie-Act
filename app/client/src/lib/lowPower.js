// Low-power mode: no blur and a still aurora, for weak machines. The learner's choice in Settings wins;
// without one, it follows the number of CPU cores.
import { readStored, store } from "./identity";

const KEY = "lumi-low-power";

export function isLowPower() {
  const choice = readStored(KEY, "");
  if (choice) return choice === "on";
  return navigator.hardwareConcurrency <= 4;
}

export function applyLowPower() {
  document.documentElement.classList.toggle("low-power", isLowPower());
}

export function setLowPower(on) {
  store(KEY, on ? "on" : "off");
  applyLowPower();
}
