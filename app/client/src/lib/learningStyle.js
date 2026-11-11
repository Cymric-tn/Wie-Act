// The diagnostic step of the challenge: four short questions find how this learner takes in a new idea, and every
// explanation is then asked for in that style (the engine's `style` context: one line in the explain prompt, and part
// of the cache key, so two learners with different styles never share an answer).
// The result lives in this browser only ("lumi-learning-style"); the learner can retake it or pick a style in Settings.
import { readStored, store } from "./identity.js";

/** style id -> the instruction the model reads (kept under the server's 40-character limit). */
export const STYLES = {
  visual: "describe the picture or the graph",
  analogy: "use an everyday-life analogy",
  steps: "numbered small steps, one idea each",
  examples: "lead with a worked example",
  repetition: "say the key idea twice, simply",
  practice: "short, then a question to try"
};
export const STYLE_IDS = Object.keys(STYLES);

/** Each option votes for one style. The i18n keys are style.q<n> and style.q<n>.<option>. */
export const QUESTIONS = [
  { id: "q1", options: { a: "visual", b: "analogy", c: "steps" } },
  { id: "q2", options: { a: "examples", b: "practice", c: "repetition" } },
  { id: "q3", options: { a: "practice", b: "steps", c: "examples" } },
  { id: "q4", options: { a: "visual", b: "analogy", c: "repetition" } }
];

/** The style with the most votes; a tie goes to the style voted for first. answers: { q1: "a", ... } */
export function scoreStyle(answers) {
  const votes = {};
  const order = [];
  for (const q of QUESTIONS) {
    const style = q.options[answers?.[q.id]];
    if (!style) continue;
    if (!(style in votes)) order.push(style);
    votes[style] = (votes[style] || 0) + 1;
  }
  return order.reduce((best, s) => (best === null || votes[s] > votes[best] ? s : best), null);
}

const KEY = "lumi-learning-style";
/** A style id, "skipped" (asked, declined) or null (never asked). */
export function getStyle() {
  const v = readStored(KEY, "");
  return STYLE_IDS.includes(v) || v === "skipped" ? v : null;
}
export const saveStyle = (id) => store(KEY, STYLE_IDS.includes(id) ? id : "skipped");
/** What goes to the engine as context.style, or undefined. */
export const styleHint = () => STYLES[getStyle()];
