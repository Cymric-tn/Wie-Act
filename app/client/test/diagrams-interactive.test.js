import test from "node:test";
import assert from "node:assert/strict";
import { makeTrack, valueAt, clampTo, snap, describe, startPoint } from "../src/lib/diagrams/tangent.js";
import { renderDiagram } from "../src/lib/diagrams/render.js";
import { py, skip } from "./diagrams-py.js";

const cubic = skip ? null : py("print(json.dumps(build_function_graph('x^3-3x')))");
const S = cubic?.payload.derivative_samples, snaps = cubic?.payload.points.filter((q) => q.kind).map((q) => q.x);

test("dragging to x = ±1 snaps to a horizontal tangent: slope 0, equation matches the builder", { skip }, () => {
  const tr = makeTrack(S);
  for (const c of [-1, 1]) {
    const a = snap(c + 0.05, snaps, 0.1), v = valueAt(tr, a), d = describe(v, "fr");
    assert.equal(a, c);
    assert.match(d.slope, /f′\(a\) = 0$/);
    const exact = py(`print(json.dumps(build_function_graph('x^3-3x', tangent_at=${c})))`).payload.curves.at(-1).label;
    assert.equal(d.eq.replace("−", "-"), exact);
  }
});

test("slope text is localised and rounded to 2 decimals for display", () => {
  assert.equal(describe({ a: 1, f: 1, df: 0.75 }, "fr").slope, "pente f′(a) = 0.75");
  assert.equal(describe({ a: 1, f: 1, df: 0.75 }, "en").eq, "y = 0.75x + 0.25");
  assert.match(describe({ a: 1, f: 1, df: 0.3333333 }, "ar").slope, /^الميل/);
});

test("the point cannot cross an asymptote or land on a non-differentiable point", { skip }, () => {
  const inv = py("print(json.dumps(build_function_graph('1/x')))").payload;
  const tr = makeTrack(inv.derivative_samples, inv.asymptotes.filter((a) => a.kind === "vertical").map((a) => a.at));
  const a0 = startPoint(tr, 1);
  assert.ok(clampTo(tr, a0, -2) > 0, "stopped before crossing x = 0");
  const sq = py("print(json.dumps(build_function_graph('sqrt(x)')))").payload;
  const t2 = makeTrack(sq.derivative_samples);
  assert.equal(valueAt(t2, 0), null);
  assert.ok(clampTo(t2, startPoint(t2, 1), -5) > 0);
});

test("hole is a blocked point too", { skip }, () => {
  const h = py("print(json.dumps(build_function_graph('(x^2-1)/(x-1)')))").payload;
  const tr = makeTrack(h.derivative_samples, h.points.filter((q) => q.open).map((q) => q.x));
  assert.ok(clampTo(tr, startPoint(tr, 0), 1.9) < 1);
});

test("practice spec renders blank and legend-free concept map uses the neutral colour", { skip }, () => {
  const pr = py("print(json.dumps(build_variation_table('x^3-3x', mode='practice')))");
  const svg = renderDiagram(pr);
  assert.ok(!svg.includes("dg-error") && !svg.includes("−2") && svg.includes("-∞"));
  const m = py("print(json.dumps(build_concept_map('tests/fixtures/diagrams_course.yaml')))");
  assert.ok(renderDiagram(m).includes("--dg-neutral") && !renderDiagram(m).includes("--dg-new"));
  const m2 = py("print(json.dumps(build_concept_map('tests/fixtures/diagrams_course.yaml', mastery={'produit': 0.4})))");
  assert.ok(renderDiagram(m2).includes("--dg-warn"));
});
