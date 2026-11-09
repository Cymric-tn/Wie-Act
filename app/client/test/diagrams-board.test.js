// What the generator sends to the board must pass the server's own item validation, or the whole operation is refused.
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { toBoardItems } from "../src/lib/diagrams/toBoard.js";
import { toBoardPrimitives } from "../src/lib/diagrams/primitives.js";
import { COURSE, py, skip } from "./diagrams-py.js";

const { cleanItem, LIMITS } = createRequire(import.meta.url)("../../server/src/validate.js");
const opts = { cx: 100, cy: -50, page: "page-1", z: 7, ink: "#101010" };
const build = (text, lang) => py("print(json.dumps(build_from_text(sys.argv[1],sys.argv[2],sys.argv[3]),ensure_ascii=False))", text, COURSE, lang);
const line = (extra) => ({ type: "line", color: "muted", x1: 0, y1: 0, x2: 240, y2: 0, dashed: false, width: 1.6, ...extra });

const CASES = [["courbe de x³ − 3x et sa tangente en 1 et sa dérivée", "fr"], ["tableau de variations de (x+1)/(x-2)", "fr"], ["exercice tableau de variations de x^3-3x", "fr"],
  ["courbe de 1/x", "en"], ["carte des notions autour de produit", "fr"], ["carte des notions", "ar"]];

for (const [text, lang] of CASES) test(`the server accepts every item: ${text}`, { skip }, () => {
  const items = toBoardItems(toBoardPrimitives(build(text, lang)), opts);
  assert.ok(items.length > 0 && items.length <= LIMITS.itemsPerOp);
  for (const item of items) assert.ok(cleanItem(item), JSON.stringify(item).slice(0, 200));
  assert.equal(new Set(items.map((i) => i.id)).size, items.length);
});

test("a diagram is centred on the view and its z order is preserved", () => {
  const items = toBoardItems([line({ x1: 0, x2: 100 }), line({ x1: 100, x2: 200, y1: 50, y2: 50 })], opts);
  const xs = items.flatMap((i) => [i.x0, i.x1]), ys = items.flatMap((i) => [i.y0, i.y1]);
  assert.equal((Math.min(...xs) + Math.max(...xs)) / 2, 100);
  assert.equal((Math.min(...ys) + Math.max(...ys)) / 2, -50);
  assert.deepEqual(items.map((i) => i.z), [7, 8]);
});

test("a curve keeps one thickness: pressure is constant", () => {
  const [path] = toBoardItems([{ type: "polyline", color: "ink", points: [[0, 0], [10, 10]], width: 2.6 }], opts);
  assert.equal(path.pressure, true);
  assert.ok(path.points.every((p) => p[2] === 0.5));
  assert.equal(path.color, "#101010");
});

test("text anchors become a left edge and a baseline becomes a top", () => {
  const prim = (anchor) => ({ type: "text", color: "ink", x: 50, y: 50, text: "abcd", size: 20, anchor, rtl: false });
  const at = (anchor) => toBoardItems([prim(anchor), line({ x1: 0, x2: 100, y1: 0, y2: 100 })], opts)[0];
  const start = at("start"), middle = at("middle"), end = at("end");
  assert.ok(end.x < middle.x && middle.x < start.x);
  assert.ok(Math.abs((start.x - middle.x) - (middle.x - end.x)) < 0.05);
  assert.ok(start.y < -50);
});

test("a dashed line becomes short lines; too many for one operation falls back to solid", () => {
  const dashed = toBoardItems([line({ dashed: true })], opts);
  assert.ok(dashed.length > 5 && dashed.every((i) => i.shape === "line"));
  const many = Array.from({ length: 100 }, () => line({ dashed: true }));
  const solid = toBoardItems(many, opts);
  assert.equal(solid.length, 100);
});

test("roles keep their colours apart and a dot is an ellipse", () => {
  const items = toBoardItems([line({ role: "d" }), line({ role: "t" }), { type: "rect", color: "accent", role: "ok", x: 0, y: 0, w: 10, h: 10, radius: 5, filled: true, width: 2 }], opts);
  assert.notEqual(items[0].color, items[1].color);
  assert.equal(items[2].shape, "ellipse");
});

test("nothing to draw is no items", () => assert.deepEqual(toBoardItems([], opts), []));
