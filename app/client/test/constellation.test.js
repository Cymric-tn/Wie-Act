import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { graphOf, orderSteps, progressOf, layoutConstellation, ancestorsOf, descendantsOf, levelOf } from "../src/lib/constellation.js";
import { buildCourse } from "../src/lib/demoData.js";

// Rough text measure for tests: 7px a character, wrapped greedily like the component does.
const wrap = (text, max, lines) => {
  const out = [];
  let line = "";
  for (const w of text.split(" ")) {
    const next = line ? `${line} ${w}` : w;
    if (line && next.length * 7 > max) { out.push(line); line = w; } else line = next;
  }
  out.push(line);
  return out.slice(0, lines);
};

const units = (mastery = {}) => buildCourse(mastery, "en").units;

describe("constellation map", () => {
  test("steps follow the longest prerequisite chain of the real course", () => {
    const g = graphOf(units());
    const step = (id) => g.layer.get(id) + 1;
    assert.equal(step("nombre dérivé"), 1); // its only prerequisite (limite) is a draft, so it starts the course
    assert.deepEqual(["tangente", "constante", "puissance"].map(step), [2, 2, 2]);
    assert.equal(step("polynôme"), 3);
    assert.equal(step("variations"), 4); // after polynôme, not just after nombre dérivé
    assert.deepEqual(["quotient", "extremum"].map(step), [5, 5]);
  });

  test("every concept appears once and a cycle in the data does not hang", () => {
    const cols = orderSteps(units(), graphOf(units()));
    const all = cols.flat();
    assert.equal(all.length, units().length);
    assert.equal(new Set(all).size, all.length);
    const cyclic = [{ id: "a", requires: ["b"], name: "A" }, { id: "b", requires: ["a"], name: "B" }];
    assert.equal(orderSteps(cyclic, graphOf(cyclic)).flat().length, 2);
  });

  test("ancestors and descendants", () => {
    const g = graphOf(units());
    assert.deepEqual([...ancestorsOf(g, "polynôme")].sort(), ["constante", "nombre dérivé", "puissance"]);
    assert.ok(descendantsOf(g, "polynôme").has("extremum") && descendantsOf(g, "polynôme").has("quotient"));
    assert.ok(!descendantsOf(g, "polynôme").has("inverse"));
  });

  test("next up: a new learner starts at step 1; in-progress beats review beats new", () => {
    let u = units();
    let p = progressOf(u, graphOf(u));
    assert.equal(p.next, "nombre dérivé");
    assert.deepEqual([...p.ready], ["nombre dérivé"]);
    assert.equal(p.counts.new, u.length);

    u = units({ "nombre dérivé": 0.9, puissance: 0.2, tangente: 0.5 });
    p = progressOf(u, graphOf(u));
    assert.equal(p.next, "tangente"); // in progress
    assert.ok(p.ready.has("puissance") && p.ready.has("constante") && !p.ready.has("polynôme"));

    u = units({ "nombre dérivé": 0.9, puissance: 0.2 });
    assert.equal(progressOf(u, graphOf(u)).next, "puissance"); // needs review, before a new one

    const all = Object.fromEntries(units().map((x) => [x.id, 1]));
    u = units(all);
    p = progressOf(u, graphOf(u));
    assert.equal(p.next, null);
    assert.equal(p.counts.mastered, u.length);
    assert.equal(levelOf(0.75), "mastered");
  });

  for (const width of [600, 700, 1100, 1500]) {
    for (const compact of [false, true]) {
      test(`layout at ${width}px${compact ? " (compact)" : ""}: inside the canvas, in step order, no two labels overlap`, () => {
        const u = units();
        const L = layoutConstellation({ units: u, width, compact, wrap });
        assert.equal(L.nodes.length, u.length);
        const step = new Map(L.nodes.map((n) => [n.id, n.step]));
        for (const n of L.nodes) {
          assert.ok(n.x - L.ring >= 0 && n.x + L.ring <= width && n.y - L.ring >= 0 && n.y + L.ring <= L.height, `${n.id} inside`);
          assert.ok(n.lines.length >= 1 && n.lines.length <= 2);
        }
        // left to right in step order
        for (const n of L.nodes) for (const m of L.nodes) if (step.get(n.id) < step.get(m.id)) assert.ok(n.x < m.x);
        // label boxes (under each star) never overlap
        const boxes = L.nodes.map((n) => ({ id: n.id, x0: n.x - L.labelWidth / 2, x1: n.x + L.labelWidth / 2, y0: n.y - L.ring, y1: n.y + L.ring + 6 + L.line * (n.lines.length + (L.sub ? 1 : 0)) }));
        for (const a of boxes) for (const b of boxes) {
          if (a.id >= b.id) continue;
          const apart = a.x1 <= b.x0 || b.x1 <= a.x0 || a.y1 <= b.y0 || b.y1 <= a.y0;
          assert.ok(apart, `${a.id} / ${b.id}`);
        }
        // one edge per in-course prerequisite
        assert.equal(L.edges.length, u.reduce((s, x) => s + x.requires.length, 0));
      });
    }
  }

  test("Arabic mirrors the map: step 1 on the right", () => {
    const u = units();
    const ltr = layoutConstellation({ units: u, width: 1000, wrap });
    const rtl = layoutConstellation({ units: u, width: 1000, rtl: true, wrap });
    const first = (L) => L.nodes.find((n) => n.id === "nombre dérivé").x;
    assert.ok(first(ltr) < 500 && first(rtl) > 500);
    assert.equal(first(ltr) + first(rtl), 1000);
  });
});
