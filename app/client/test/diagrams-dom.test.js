// DOM behaviour of the interactive layer. Needs `npm install` (jsdom); skipped when it is not installed.
import test from "node:test";
import assert from "node:assert/strict";
import { py, skip as noPython } from "./diagrams-py.js";

const jsdom = await import("jsdom").catch(() => null);
const skip = noPython || (jsdom ? false : "jsdom not installed");

function setup() {
  const dom = new jsdom.JSDOM("<!doctype html><div id='el'></div>", { pretendToBeVisual: true });
  Object.assign(globalThis, { window: dom.window, document: dom.window.document });
  return dom.window.document.getElementById("el");
}
const checker = async (spec, ans) => py("print(json.dumps(check_table(json.loads(sys.argv[1]),json.loads(sys.argv[2]))))", JSON.stringify(spec), JSON.stringify(ans));

test("practice: blank table, reveal passes, a wrong click is highlighted, onCheck fires", { skip }, async () => {
  const { mountPractice } = await import("../src/lib/diagrams/interactive.js");
  const el = setup(), spec = py("print(json.dumps(build_variation_table('x^3-3x', mode='practice')))");
  const events = [];
  const ctl = mountPractice(el, spec, { checker, onCheck: (r) => events.push(r) });
  assert.equal(el.querySelectorAll("input.dg-in").length, 6); // 2 interior headers + 4 value cells
  assert.ok(!el.textContent.includes("−2"), "answer not shown");
  const empty = await ctl.check();
  assert.equal(empty.correct, false);
  ctl.reveal();
  assert.equal((await ctl.check()).correct, true);
  ctl.reset();
  const cell = el.querySelector('[data-act="sign:0"]');
  cell.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));   // "" -> "+"
  ctl.reveal();
  const first = el.querySelector('[data-act="sign:1"]');
  first.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));  // "0" -> "||" (wrong)
  const r = await ctl.check();
  assert.deepEqual(r.mistakes.map((m) => [m.row, m.column]), [["f_prime", 1]]);
  assert.ok(el.querySelector('[data-act="sign:1"] rect').getAttribute("stroke").includes("--dg-bad"));
  assert.equal(events.length, 3);
});

test("practice: typing a value is read back and judged exactly", { skip }, async () => {
  const { mountPractice } = await import("../src/lib/diagrams/interactive.js");
  const el = setup(), spec = py("print(json.dumps(build_variation_table('x^3-3x', mode='practice')))");
  const ctl = mountPractice(el, spec, { checker });
  ctl.reveal();
  const inp = el.querySelector('input[data-k="f:1"]');
  inp.value = "2.0"; inp.dispatchEvent(new window.Event("input", { bubbles: true }));
  assert.equal((await ctl.check()).correct, true);
  const inp2 = el.querySelector('input[data-k="f:1"]');
  inp2.value = "3"; inp2.dispatchEvent(new window.Event("input", { bubbles: true }));
  const r = await ctl.check();
  assert.deepEqual(r.mistakes.map((m) => [m.row, m.column]), [["f", 1]]);
});

test("draggable tangent: starts on the builder's point, arrow keys move it, panel updates", { skip }, async () => {
  const { mountDraggable } = await import("../src/lib/diagrams/interactive.js");
  const el = setup(), spec = py("print(json.dumps(build_function_graph('x^3-3x', tangent_at=1)))");
  const moves = [];
  const h = mountDraggable(el, spec, { onTangentMove: (v) => moves.push(v) });
  const texts = () => [...el.querySelectorAll("svg > text")].slice(-3).map((t) => t.textContent);
  assert.deepEqual(texts(), ["a = 1", "pente f′(a) = 0", "y = −2"]);
  const hit = el.querySelector("circle[role=slider]");
  hit.dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  assert.ok(h.get() > 1 && moves.length > 1);
  assert.notEqual(texts()[1], "pente f′(a) = 0");
});
