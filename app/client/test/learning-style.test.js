import test from "node:test";
import assert from "node:assert/strict";
import { QUESTIONS, STYLES, STYLE_IDS, scoreStyle } from "../src/lib/learningStyle.js";
import * as AI from "../src/lib/i18n-ai.js";
const STRINGS = { en: AI.en, fr: AI.fr, ar: AI.ar };

test("the diagnostic: the most voted style wins, a tie goes to the first voted, no answer gives none", () => {
  assert.equal(scoreStyle({ q1: "a", q2: "a", q3: "c", q4: "a" }), "visual");
  assert.equal(scoreStyle({ q1: "c", q2: "b", q3: "b", q4: "c" }), "steps");
  assert.equal(scoreStyle({ q1: "b", q2: "c", q3: "a", q4: "c" }), "repetition");
  assert.equal(scoreStyle({ q1: "a", q2: "b" }), "visual"); // one vote each: the first
  assert.equal(scoreStyle({}), null);
  assert.equal(scoreStyle({ q1: "z" }), null);
});

test("every style can be reached, and its instruction fits the server's 40 characters", () => {
  const reachable = new Set(QUESTIONS.flatMap((q) => Object.values(q.options)));
  assert.deepEqual([...reachable].sort(), [...STYLE_IDS].sort());
  for (const text of Object.values(STYLES)) assert.ok(text.length <= 40, text);
});

test("the diagnostic is translated in the three languages", () => {
  for (const lang of ["en", "fr", "ar"]) {
    for (const q of QUESTIONS) {
      assert.ok(STRINGS[lang][`style.${q.id}`], `${lang} style.${q.id}`);
      for (const o of Object.keys(q.options)) assert.ok(STRINGS[lang][`style.${q.id}.${o}`], `${lang} style.${q.id}.${o}`);
    }
    for (const id of STYLE_IDS) assert.ok(STRINGS[lang][`style.${id}`], `${lang} style.${id}`);
  }
});
