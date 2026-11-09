import { test, describe } from "node:test";
import assert from "node:assert/strict";
import {
  bbox, hitTest, topItemAt, transformItem, recognizeShape,
  zoomAt, screenToWorld, fitView, wrapLines
} from "../src/lib/geometry.js";
import { BoardStore, UndoHistory } from "../src/lib/store.js";
import { buildTimeline, stateAt } from "../src/lib/history.js";
import { buildTemplate, TEMPLATES } from "../src/lib/templates.js";
import { courseForRoom } from "../src/lib/courses.js";
import { daysLeft, inDays } from "../src/lib/exams.js";

const rect = (extra = {}) => ({
  id: "r1", type: "shape", shape: "rect", page: "p1", z: 1,
  x0: 0, y0: 0, x1: 100, y1: 50, color: "#fff", width: 4, fill: false, ...extra
});
const path = (points, extra = {}) => ({ id: "s1", type: "path", tool: "pen", page: "p1", z: 1, color: "#fff", width: 4, points, ...extra });

describe("geometry", () => {
  test("bbox includes stroke thickness", () => {
    assert.deepEqual(bbox(rect()), { x: -2, y: -2, w: 104, h: 54 });
    const b = bbox(path([[10, 10], [30, 40]]));
    assert.deepEqual(b, { x: 8, y: 8, w: 24, h: 34 });
  });

  test("hit testing follows outlines unless filled", () => {
    assert.equal(hitTest(rect(), 50, 1), true, "on the top edge");
    assert.equal(hitTest(rect(), 50, 25), false, "empty interior");
    assert.equal(hitTest(rect({ fill: true }), 50, 25), true, "filled interior");
    const ellipse = rect({ shape: "ellipse" });
    assert.equal(hitTest(ellipse, 50, 0), true);
    assert.equal(hitTest(ellipse, 1, 1), false, "outside the curve, inside the box");
    assert.equal(hitTest(path([[0, 0], [100, 0]]), 50, 3), true);
    assert.equal(hitTest(path([[0, 0], [100, 0]]), 50, 20), false);
  });

  test("topItemAt returns the last painted item", () => {
    const a = rect({ id: "a", fill: true });
    const b = rect({ id: "b", fill: true, z: 2 });
    assert.equal(topItemAt([a, b], 50, 25).id, "b");
  });

  test("transforms move and scale every item type", () => {
    assert.deepEqual(
      [transformItem(rect(), { dx: 10, dy: 5 })].map(({ x0, y0, x1, y1 }) => [x0, y0, x1, y1])[0],
      [10, 5, 110, 55]
    );
    const scaled = transformItem(rect(), { sx: 2, sy: 2, ox: 0, oy: 0 });
    assert.equal(scaled.x1, 200);
    assert.equal(scaled.width, 8);
    const text = transformItem({ id: "t", type: "text", x: 10, y: 10, size: 20, text: "hi", color: "#fff" }, { sx: 1.5, sy: 1.5 });
    assert.equal(text.size, 30);
    const flipped = transformItem({ id: "n", type: "note", x: 0, y: 0, w: 100, h: 100 }, { sx: -1, sy: 1, ox: 0 });
    assert.equal(flipped.x, -100);
    assert.equal(flipped.w, 100);
    const pressure = transformItem(path([[0, 0, 0.3]]), { dx: 1 });
    assert.deepEqual(pressure.points[0], [1, 0, 0.3]);
  });

  test("shape assist recognises lines, rectangles and ellipses", () => {
    const linePts = Array.from({ length: 20 }, (_, i) => [i * 10, i * 5 + (i % 2)]);
    assert.equal(recognizeShape(linePts).shape, "line");

    const rectPts = [];
    for (let i = 0; i <= 20; i++) rectPts.push([i * 10, 0]);
    for (let i = 0; i <= 10; i++) rectPts.push([200, i * 10]);
    for (let i = 20; i >= 0; i--) rectPts.push([i * 10, 100]);
    for (let i = 10; i >= 0; i--) rectPts.push([0, i * 10]);
    assert.equal(recognizeShape(rectPts).shape, "rect");

    const circle = Array.from({ length: 40 }, (_, i) => {
      const a = (i / 39) * Math.PI * 2;
      return [100 + Math.cos(a) * 80, 100 + Math.sin(a) * 50];
    });
    const r = recognizeShape(circle);
    assert.equal(r.shape, "ellipse");
    assert.ok(Math.abs(r.x1 - r.x0 - 160) < 2);

    const scribble = Array.from({ length: 30 }, (_, i) => [Math.sin(i) * 50 + i * 3, Math.cos(i * 1.7) * 40]);
    assert.equal(recognizeShape(scribble), null);
  });

  test("zooming keeps the point under the cursor fixed", () => {
    const view = { x: 100, y: 50, zoom: 1 };
    const before = screenToWorld(view, 400, 300);
    const after = screenToWorld(zoomAt(view, 2, 400, 300), 400, 300);
    assert.ok(Math.abs(before.x - after.x) < 1e-9 && Math.abs(before.y - after.y) < 1e-9);
  });

  test("fitView centres the content", () => {
    const view = fitView({ x: 0, y: 0, w: 1000, h: 500 }, 800, 600, 0, 10);
    assert.equal(view.zoom, 0.8);
    assert.equal(view.x + 400 / view.zoom, 500);
  });

  test("wrapLines breaks long text at word boundaries", () => {
    const lines = wrapLines("one two three four five six", 10, 60);
    assert.ok(lines.length > 1);
    assert.equal(lines.join(" "), "one two three four five six");
  });
});

describe("store and undo", () => {
  test("every operation returns a working inverse", () => {
    const store = new BoardStore();
    const undo = new UndoHistory();
    const forward = { kind: "add", items: [rect()] };
    undo.push(forward, store.apply(forward));
    const moved = transformItem(rect(), { dx: 50 });
    const update = { kind: "update", items: [moved] };
    undo.push(update, store.apply(update));
    assert.equal(store.get("r1").x0, 50);

    store.apply(undo.takeUndo());
    assert.equal(store.get("r1").x0, 0);
    store.apply(undo.takeUndo());
    assert.equal(store.get("r1"), undefined);
    store.apply(undo.takeRedo());
    assert.equal(store.get("r1").x0, 0);
    assert.equal(undo.canRedo, true);
  });

  test("clear only affects one page and can be undone", () => {
    const store = new BoardStore([rect(), rect({ id: "r2", page: "p2" })]);
    const inverse = store.apply({ kind: "clear", page: "p1" });
    assert.deepEqual([...store.items.keys()], ["r2"]);
    store.apply(inverse);
    assert.equal(store.items.size, 2);
  });

  test("pageItems is ordered by z", () => {
    const store = new BoardStore([rect({ id: "top", z: 9 }), rect({ id: "bottom", z: 1 })]);
    assert.deepEqual(store.pageItems("p1").map((i) => i.id), ["bottom", "top"]);
  });
});

describe("history timeline", () => {
  test("reconstructs every earlier state from the current one", () => {
    const a = rect({ id: "a" });
    const a2 = { ...a, x1: 300 };
    const b = rect({ id: "b" });
    const entries = [
      { op: { t: "add", items: [a] } },
      { op: { t: "add", items: [b] } },
      { op: { t: "update", before: [a], after: [a2] } },
      { op: { t: "remove", items: [b] } }
    ];
    const timeline = buildTimeline([a2], entries, 2);
    assert.deepEqual(stateAt(timeline, 0), []);
    assert.deepEqual(stateAt(timeline, 2).map((i) => i.id).sort(), ["a", "b"]);
    assert.equal(stateAt(timeline, 2).find((i) => i.id === "a").x1, 100);
    assert.equal(stateAt(timeline, 3).find((i) => i.id === "a").x1, 300);
    assert.deepEqual(stateAt(timeline, 4), [a2]);
  });
});

describe("templates and courses", () => {
  test("every template builds valid, uniquely-identified items", () => {
    TEMPLATES.forEach(({ id }) => {
      const items = buildTemplate(id, { cx: 0, cy: 0, page: "p1", z: 5, ink: "#ffffff" });
      assert.ok(items.length > 0, id);
      assert.equal(new Set(items.map((i) => i.id)).size, items.length, id);
      assert.ok(items.every((i) => i.page === "p1" && i.z >= 5), id);
    });
  });

  test("course rooms resolve case-insensitively", () => {
    assert.equal(courseForRoom("ANALYSE").name, "Analyse : dérivation");
    assert.equal(courseForRoom("math102"), null);                       // Phase 1 has one course
    assert.equal(courseForRoom("brainstorm"), null);
  });
});

describe("engine-backed UI strings", () => {
  test("every AI string exists in French, English and Arabic", async () => {
    const ai = await import("../src/lib/i18n-ai.js");
    const keys = (d) => Object.keys(d).sort();
    assert.deepEqual(keys(ai.fr), keys(ai.en));
    assert.deepEqual(keys(ai.ar), keys(ai.en));
    for (const lang of ["en", "fr", "ar"]) {
      for (const [key, value] of Object.entries(ai[lang])) assert.ok(value.trim(), `${lang} ${key} is empty`);
    }
  });

  test("placeholders match across languages", async () => {
    const ai = await import("../src/lib/i18n-ai.js");
    const vars = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
    for (const key of Object.keys(ai.en)) {
      assert.equal(vars(ai.fr[key]), vars(ai.en[key]), `fr ${key}`);
      assert.equal(vars(ai.ar[key]), vars(ai.en[key]), `ar ${key}`);
    }
  });
});

describe("exam dates", () => {
  const noon = new Date(2026, 10, 9, 12, 30);
  test("whole days from today, whatever the hour", () => {
    assert.equal(daysLeft("2026-11-09", noon), 0);
    assert.equal(daysLeft("2026-11-10", noon), 1);
    assert.equal(daysLeft("2026-12-09", noon), 30);
    assert.equal(daysLeft("2026-11-08", noon), -1);
  });
  test("inDays is the inverse of daysLeft, across a month end", () => {
    assert.equal(inDays(22, noon), "2026-12-01");
    assert.equal(daysLeft(inDays(22, noon), noon), 22);
  });
});
