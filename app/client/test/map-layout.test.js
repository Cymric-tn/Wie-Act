import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { fitOrbits, planetPositions, placeLabels, shortName, clearOfCircle, PAD, SUN_LABEL_H } from "../src/lib/mapLayout.js";

const measure = (text) => [...text].length * 6.6;
const NAMES = ["Nombre dérivé en un point", "Tangente", "Dérivée d’une constante", "Dérivée d’une puissance", "Dérivée d’un polynôme", "Dérivée d’un produit", "Dérivée d’un quotient", "Dérivée de l’inverse", "Dérivée de la racine", "Sens de variation", "Extremums"];
const SIZES = [[588, 400], [1050, 520], [1100, 560], [1590, 640], [800, 420], [588, 410], [330, 360]];

function frames(width, height, count) {
  const labels = NAMES.slice(0, count).map(shortName);
  const labelWidth = Math.max(...labels.map(measure));
  const extent = 14;
  const sunRadius = 20;
  const layout = fitOrbits({ width, height, count, sunRadius, sunLabelHeight: SUN_LABEL_H, sunLabelHalf: 75, extent, labelWidth, labelHeight: 16 });
  const sunBox = { x0: layout.cx - 75, x1: layout.cx + 75, y0: layout.cy + sunRadius + 4, y1: layout.cy + sunRadius + SUN_LABEL_H };
  let previous = [];
  const out = [];
  for (let t = 0; t < 400; t += 0.37) {
    const planets = planetPositions(layout, t).map((p, i) => ({ ...p, extent, labelWidth: measure(labels[i]) }));
    previous = placeLabels({ planets, width, height, circles: [{ x: layout.cx, y: layout.cy, r: sunRadius + 4 }], boxes: [sunBox], previous });
    out.push({ planets, placed: previous, layout, sunBox });
  }
  return out;
}

describe("progress map layout", () => {
  for (const [width, height] of SIZES) {
    const all = frames(width, height, 11);
    test(`${width}x${height}: every orbit and planet stays ${PAD}px inside the canvas`, () => {
      for (const { planets } of all) for (const p of planets) {
        assert.ok(p.x - p.extent >= PAD - 0.01 && p.x + p.extent <= width - PAD + 0.01, `x ${p.x}`);
        assert.ok(p.y - p.extent >= PAD - 0.01 && p.y + p.extent <= height - PAD + 0.01, `y ${p.y}`);
      }
    });
    test(`${width}x${height}: a shown label is inside, and overlaps no label, planet or sun`, () => {
      for (const { planets, placed, layout, sunBox } of all) {
        const shown = placed.filter((l) => l.visible);
        for (const l of shown) {
          assert.ok(l.box.x0 >= PAD && l.box.x1 <= width - PAD && l.box.y0 >= PAD && l.box.y1 <= height - PAD);
          for (const p of planets) assert.ok(clearOfCircle(l.box, { x: p.x, y: p.y, r: p.extent - 1 }));
          assert.ok(clearOfCircle(l.box, { x: layout.cx, y: layout.cy, r: 20 }));
          assert.ok(l.box.x1 <= sunBox.x0 || l.box.x0 >= sunBox.x1 || l.box.y1 <= sunBox.y0 || l.box.y0 >= sunBox.y1);
        }
        shown.forEach((a, i) => shown.slice(i + 1).forEach((b) => {
          assert.ok(a.box.x1 <= b.box.x0 || b.box.x1 <= a.box.x0 || a.box.y1 <= b.box.y0 || b.box.y1 <= a.box.y0);
        }));
      }
    });
    test(`${width}x${height}: planets never touch each other`, () => {
      for (const { planets } of all) planets.forEach((a, i) => planets.slice(i + 1).forEach((b) => {
        assert.ok(Math.hypot(a.x - b.x, a.y - b.y) >= 2 * (a.extent - 5), "planets overlap");
      }));
    });
    test(`${width}x${height}: planets never touch the sun or its label`, () => {
      for (const { planets, layout, sunBox } of all) for (const p of planets) {
        assert.ok(Math.hypot(p.x - layout.cx, p.y - layout.cy) >= 20 + p.extent);
        if (height >= 360 && width >= 500) assert.ok(clearOfCircle(sunBox, { x: p.x, y: p.y, r: p.extent }), "over the sun label");
      }
    });
  }
  test("a roomy canvas shows most labels; a dashboard-sized one still shows more than half", () => {
    for (const [width, height, min] of [[1100, 560, 0.75], [588, 410, 0.5]]) {
      const all = frames(width, height, 11);
      const share = all.reduce((n, f) => n + f.placed.filter((l) => l.visible).length, 0) / (all.length * 11);
      assert.ok(share >= min, `${width}x${height}: ${share.toFixed(2)}`);
    }
  });
  test("every orbit carries a planet, for any number of planets", () => {
    for (let count = 1; count <= 14; count++) {
      const layout = fitOrbits({ width: 900, height: 500, count, sunRadius: 20, sunLabelHeight: SUN_LABEL_H, extent: 14, labelWidth: 110, labelHeight: 16 });
      assert.equal(layout.slots.length, count);
      layout.orbits.forEach((_, o) => assert.ok(layout.slots.some((s) => s.orbit === o), `orbit ${o} of ${count}`));
    }
  });
  test("names are cut at 18 characters with an ellipsis", () => {
    assert.equal(shortName("Tangente"), "Tangente");
    assert.equal([...shortName("Dérivée d’une puissance entière")].length, 18);
    assert.ok(shortName("Dérivée d’une puissance entière").endsWith("…"));
  });
});
