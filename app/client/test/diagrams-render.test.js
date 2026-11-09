import test from "node:test";
import assert from "node:assert/strict";
import { renderDiagram } from "../src/lib/diagrams/render.js";
import { toBoardPrimitives } from "../src/lib/diagrams/primitives.js";
import { COURSE, py as run, skip } from "./diagrams-py.js";

const py = (text, lang, type) => run(`print(json.dumps(build_from_text(sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4] or None),ensure_ascii=False))`, text, COURSE, lang, type || "");

const CASES = ["courbe de x³ − 3x et sa tangente en 1 et sa dérivée", "tableau de variations de (x+1)/(x-2)", "tableau de variations de x³ − 3x",
  "courbe de (x²−1)/(x−1)", "carte des notions autour de produit", "carte des notions"];

for (const lang of ["fr", "en", "ar"]) for (const c of CASES) test(`${lang}: ${c}`, { skip }, () => {
  const spec = py(c, lang);
  const svg = renderDiagram(spec);
  assert.ok(svg.startsWith("<svg") && !svg.includes("dg-error"), svg.slice(0, 200));
  const prims = toBoardPrimitives(spec);
  assert.ok(prims.length > 0 && prims.length <= 500);
  assert.ok(prims.every((p) => ["polyline", "line", "arrow", "rect", "text"].includes(p.type)));
});

test("invalid spec shows an error box", () => {
  assert.ok(renderDiagram({ version: 1, type: "nope", payload: {} }).includes("dg-error"));
  assert.ok(renderDiagram(null).includes("dg-error"));
});
