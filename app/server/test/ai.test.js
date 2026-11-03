const { test, describe, before, after } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const { createServer } = require("../src/app");
const { createEngine } = require("../src/ai");
const { INPUTS, planInputs, shapePlan, shapePage, shapeCourse } = require("../src/ai/routes");

const quiet = () => {};
const TOKEN = "app-test-token";
const ROOT = path.join(__dirname, "../../.."); // the repo root: engine/, data/, prompts/

async function ndjson(res) {
  const text = await res.text();
  return text.split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

// The python that can import the engine (ENGINE_PYTHON, else python3 / python). The AI tests run the web app against
// the ENGINE'S OWN FakeEngine through bridge.py --fake: the same Result / Event shapes and degrade reasons as the real one.
const python = [process.env.ENGINE_PYTHON, "python3", "python"].filter(Boolean).find((cmd) =>
  spawnSync(cmd, ["-c", `import sys; sys.path.insert(0, ${JSON.stringify(ROOT)}); import engine.fake`], { cwd: ROOT }).status === 0);
const skip = python ? false : "no python with the engine's requirements (set ENGINE_PYTHON)";

function startBridge(port, token = TOKEN) {
  return spawn(python, [path.join(__dirname, "../../bridge/bridge.py"), "--fake", "--fake-speed", "0", "--engine-dir", ROOT, "--port", String(port)], {
    cwd: ROOT, stdio: "ignore", env: { ...process.env, ENGINE_SERVICE_TOKEN: token }
  });
}

async function waitUp(engine) {
  for (let i = 0; i < 100; i++) {
    if (await engine.probe()) return;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("bridge did not start");
}

function harness(ai) {
  const ctx = {};
  before(async () => {
    ctx.server = createServer({ log: quiet, ai: { ...ai } });
    const port = await ctx.server.listen(0, "127.0.0.1");
    ctx.base = `http://127.0.0.1:${port}`;
    ctx.post = (url, body) => fetch(ctx.base + url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    ctx.stream = async (url, body) => {
      const res = await ctx.post(url, body);
      assert.equal(res.status, 200, await res.clone().text());
      const lines = await ndjson(res);
      return { lines, events: lines.filter((l) => l.type === "event"), final: lines.find((l) => l.type === "result") };
    };
  });
  after(() => ctx.server.close());
  return ctx;
}

describe("the engine's contract, as the feature layer adapts it", () => {
  test("check_answer: the board's lines become the engine's problem + steps", () => {
    assert.deepEqual(INPUTS.check_answer({ lines: ["f(x) = x⁴", "f'(x) = x³", "f'(x) = 4x³"] }), { problem: "f(x) = x⁴", steps: ["f'(x) = x³", "f'(x) = 4x³"] });
    assert.deepEqual(INPUTS.check_answer({ problem: "Dérive x⁴", lines: ["f'(x) = 4x³"] }), { problem: "Dérive x⁴", steps: ["f'(x) = 4x³"] });
    assert.deepEqual(INPUTS.check_answer({ problem: "p", steps: ["a", "b"], exercise_id: "d01" }), { problem: "p", steps: ["a", "b"], exercise_id: "d01" });
    assert.equal(INPUTS.check_answer({ lines: ["only one line"] }).steps[0], "only one line");
  });

  test("the course graph arrives with the title objects the screens read, without draft concepts", () => {
    const c = shapeCourse({ id: "analyse", titles: { fr: "Analyse" }, units: [{ id: "u", titles: { fr: "U" }, concepts: ["limite", "tangente"] }],
      concepts: { limite: { id: "limite", titles: { fr: "Limite" }, status: "draft" }, tangente: { id: "tangente", titles: { fr: "Tangente" }, status: "published" } } });
    assert.equal(c.title.fr, "Analyse");
    assert.deepEqual(c.units[0].concepts, ["tangente"]);
    assert.equal(c.units[0].title.fr, "U");
    assert.equal(c.concepts.tangente.title.fr, "Tangente");
    assert.equal(c.concepts.limite, undefined);
  });

  test("concept ids keep their spaces and accents (nombre dérivé, racine carrée, polynôme)", () => {
    assert.equal(INPUTS.generate_page({ concept: "nombre dérivé" }).concept, "nombre dérivé");
    assert.equal(INPUTS.quiz({ concept: "racine carrée" }).concept, "racine carrée");
    assert.deepEqual(INPUTS.prep_plan({ mastery: { "polynôme": 0.4, "x; drop table": 1 } }).mastery, { "polynôme": 0.4 });
    assert.equal(INPUTS.generate_page({ concept: "<script>" }).concept, undefined);
  });

  test("prep_plan: days left and minutes per day become scope, a deadline date and a pace", async () => {
    const engine = { course: async () => ({ units: [{ id: "derivees" }, { id: "variations" }] }) };
    const out = await planInputs(engine, INPUTS.prep_plan({ days_left: 3, minutes_per_day: 40, mastery: { puissance: 0.2 } }), "analyse");
    assert.deepEqual(out.scope, { unit_ids: ["derivees", "variations"] });
    assert.equal(out.pace_min_per_day, 40);
    assert.match(out.deadline, /^\d{4}-\d\d-\d\d$/);
    assert.ok(Math.abs(new Date(out.deadline) - Date.now() - 3 * 86_400_000) < 2 * 86_400_000);
    assert.deepEqual(out.mastery, { puissance: 0.2 });
    assert.deepEqual((await planInputs(engine, INPUTS.prep_plan({ unit_ids: ["variations"] }), "analyse")).scope, { unit_ids: ["variations"] });
  });

  test("a plan's focus points are grouped into days; a page's worked solution becomes its answer", () => {
    const plan = shapePlan({ data: { focus_points: [
      { concept_id: "puissance", day_index: 0, minutes: 20, page_kind: "learn", reason: "r", reason_code: "low_mastery" },
      { concept_id: "produit", day_index: 0, minutes: 20, page_kind: "learn" },
      { concept_id: "nombre dérivé", day_index: 1, minutes: 8, page_kind: "review" }], assessment_exercise_ids: ["d01"] }, notes: { days: 2 } });
    assert.deepEqual(plan.data.sessions.map((s) => [s.day, s.minutes, s.items.length]), [[1, 40, 2], [2, 8, 1]]);
    assert.equal(plan.data.sessions[1].items[0].kind, "review");
    assert.deepEqual(plan.data.assessment_exercise_ids, ["d01"]);
    const page = shapePage({ concept_id: "puissance", title: "Puissance", explanation: "texte", degraded: false, explain_request_id: "r1",
      worked_example: { exercise_id: "d01", statement: "s", steps: ["a"], solution: "f'(x) = 4x³" }, quiz: [{ id: "d02" }] });
    assert.equal(page.data.worked_example.answer, "f'(x) = 4x³");
    assert.equal(page.request_id, "r1");
    assert.equal(page.data.quiz.length, 1);
  });
});

describe("generated practice items", () => {
  test("a generated quiz item reaches the browser without its solution and can still be graded", async () => {
    const calls = [];
    const stub = {
      kind: "engine", status: () => ({ backend: "engine", connected: true }), probe: async () => true, close() {},
      course: async () => ({ id: "analyse", units: [{ id: "derivees" }], concepts: {} }),
      followUp: async () => ({ task: null }), getExercise: async () => null,
      generatePage: async () => ({ concept_id: "nombre dérivé", page_kind: "learn", title: "Nombre dérivé", language: "FRENCH", explanation: "x",
        explain_request_id: "r1", degraded: false, worked_example: { exercise_id: "d10", statement: "s", steps: [], solution: "f'(3) = 6" },
        quiz: [{ id: "d11", concept: "nombre dérivé", level: 2, statement: "bank", source: "bank" },
               { id: "d10~v1", concept: "nombre dérivé", level: 1, statement: "Soit f(x) = 2x². Calculer f'(3).", source: "variant", solution: "f'(3) = 12" }] }),
      run: async (task, inputs) => { calls.push([task, inputs]); return { request_id: "g1", task, data: { verdict: inputs.attempt === inputs.solution ? "CORRECT" : "INCORRECT", feedback: "" }, events: [], degraded: false }; }
    };
    const server = createServer({ log: quiet, ai: { engine: stub } });
    const port = await server.listen(0, "127.0.0.1");
    const base = `http://127.0.0.1:${port}`;
    const post = (url, body) => fetch(base + url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    try {
      const page = (await ndjson(await post("/api/ai/run", { task: "generate_page", inputs: { concept: "nombre dérivé" }, learner_id: "l1" }))).find((l) => l.type === "result").result;
      assert.equal(page.data.quiz.length, 2);
      assert.ok(!JSON.stringify(page.data.quiz).includes("f'(3) = 12"), "the solution reached the browser");
      assert.equal(page.data.worked_example.answer, "f'(3) = 6");                      // the worked example's answer is shown on purpose
      const graded = (await ndjson(await post("/api/ai/exercises/d10~v1/answer", { attempt: "f'(3) = 12", learner_id: "l1", context: { course_id: "analyse" } }))).find((l) => l.type === "result");
      assert.equal(graded.result.data.verdict, "CORRECT");
      assert.deepEqual(calls.at(-1)[1].solution, "f'(3) = 12");
      assert.equal((await post("/api/ai/exercises/d99~v9/answer", { attempt: "1", learner_id: "l1" })).status, 400);   // unknown id: refused
    } finally {
      await server.close();
    }
  });
});

describe("the tutor loop passes its exercise state to the follow-up rule", () => {
  test("a HINT with no open exercise is routed by the engine to explain; with one, to the stored hint", async () => {
    const seen = [];
    const stub = {
      kind: "engine", status: () => ({ backend: "engine", connected: true }), probe: async () => true, close() {},
      course: async () => null,
      getExercise: async (id) => (id === "d01" ? { id, concept: "puissance", level: 1, statement: { fr: "Dérive x⁴" }, solution: "4x³", hints: ["h1", "h2", "h3"], common_mistakes: [] } : null),
      followUp: async (c, hasOpen) => { seen.push(hasOpen); return hasOpen ? { task: "hint", reason: "r", overridden: false } : { task: "explain", reason: "hint without an open exercise", overridden: true }; },
      run: async (task) => ({ request_id: `r-${task}`, task, text: task === "classify" ? null : "x", data: task === "classify" ? { intent: "HINT", language: "ARABIZI", confusion: "MEDIUM", complexity: 2 } : { body: "b" }, events: [], degraded: false })
    };
    const server = createServer({ log: quiet, ai: { engine: stub } });
    const port = await server.listen(0, "127.0.0.1");
    const post = (body) => fetch(`http://127.0.0.1:${port}/api/ai/turn`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }).then(ndjson);
    try {
      const none = (await post({ message: "chnowa el derivee mta3 x² w kifech nal9aha ?", learner_id: "l1" })).find((l) => l.type === "result");
      assert.equal(none.follow.task, "explain");
      assert.equal(none.follow.reason, "hint without an open exercise");
      assert.equal(none.result.task, "explain");
      const open = (await post({ message: "un indice ?", exercise_id: "d01", hints_used: 0, learner_id: "l1" })).find((l) => l.type === "result");
      assert.equal(open.follow.task, "hint");
      assert.deepEqual(seen, [false, true]);
    } finally {
      await server.close();
    }
  });
});

describe("AI API through the bridge (the engine's FakeEngine)", { skip }, () => {
  const port = 8790 + Math.floor(Math.random() * 100);
  let bridge;
  const ctx = harness({ url: `http://127.0.0.1:${port}`, token: TOKEN });
  const learner = "learner-1";

  before(async () => {
    bridge = startBridge(port);
    await waitUp(ctx.server.engine);
  });
  after(() => bridge?.kill());

  test("status reports the backend and the engine's version", async () => {
    const s = await (await fetch(ctx.base + "/api/ai/status")).json();
    assert.equal(s.backend, "fake");
    assert.equal(s.connected, true);
    assert.match(s.version, /^\d+\.\d+/);
    assert.ok(s.tasks.includes("check_answer"));
  });

  test("the course graph is served with titles for the screens", async () => {
    const { course } = await (await fetch(ctx.base + "/api/ai/courses/analyse")).json();
    assert.ok(course.title.fr && course.units[0].title.fr && course.concepts["nombre dérivé"].title.en);
    assert.equal((await fetch(ctx.base + "/api/ai/courses/nope")).status, 404);
  });

  test("/api/ai/run streams the engine's events then the result; solutions never reach the browser", async () => {
    const { events, final } = await ctx.stream("/api/ai/run", { task: "explain", inputs: { question: "dérivée d’un produit" }, context: { language: "FRENCH", course_id: "analyse" }, learner_id: learner });
    assert.ok(events.some((e) => e.event.name === "retrieving"));
    assert.ok(final.result.data.body);
    assert.equal(final.result.data.exercise.solution, undefined);
  });

  test("bad tasks, internal tasks and missing inputs are refused with 400", async () => {
    assert.equal((await ctx.post("/api/ai/run", { task: "grade", inputs: {} })).status, 400);
    assert.equal((await ctx.post("/api/ai/run", { task: "explain", inputs: {} })).status, 400);
    assert.equal((await ctx.post("/api/ai/run", { task: "check_answer", inputs: {} })).status, 400);
  });

  test("the board's check-my-work reaches the engine as problem + steps and comes back as steps", async () => {
    const { final } = await ctx.stream("/api/ai/run", { task: "check_answer", inputs: { lines: ["f(x) = x⁴", "f'(x) = x³"] }, context: { language: "FRENCH" }, learner_id: learner });
    assert.ok(Array.isArray(final.result.data.steps) && final.result.data.steps.length);
    assert.ok(["CORRECT", "INCORRECT", "UNKNOWN"].includes(final.result.data.final_verdict));
  });

  test("a compound fact-check comes back with its parts", async () => {
    const { final } = await ctx.stream("/api/ai/run", { task: "check_claim", inputs: { claim: "(x²)' = 2x et (x³)' = 2x²" }, context: { fake_outcome: "PARTLY" }, learner_id: learner });
    assert.equal(final.result.data.verdict, "PARTLY");
    assert.equal(final.result.data.parts.length, 2);
  });

  test("a prep plan is built from days left and a pace, and arrives as days of sessions", async () => {
    const { final } = await ctx.stream("/api/ai/run", { task: "prep_plan", inputs: { course_id: "analyse", days_left: 5, minutes_per_day: 30 }, learner_id: learner, context: { course_id: "analyse" } });
    assert.ok(final.result.data.sessions.length >= 1);
    assert.ok(final.result.data.sessions[0].items[0].concept);
    assert.ok(final.result.data.focus_points.length >= 1);
  });

  test("a study page is generated through the page route", async () => {
    const { events, final } = await ctx.stream("/api/ai/run", { task: "generate_page", inputs: { concept: "puissance" }, priority: "BACKGROUND", learner_id: learner, context: { language: "FRENCH", course_id: "analyse" } });
    assert.equal(final.result.data.concept_id, "puissance");
    assert.ok(final.result.data.worked_example.answer);
    assert.ok(final.result.data.quiz.length >= 1);
    assert.ok(events.length >= 0);
  });

  test("an exercise answered wrong, then right, changes mastery", async () => {
    const wrong = await ctx.stream("/api/ai/exercises/d01/answer", { attempt: "x^3", learner_id: learner, context: { course_id: "analyse", fake_outcome: "incorrect" } });
    assert.equal(wrong.final.result.data.verdict, "INCORRECT");
    const before = wrong.final.mastery?.value ?? 0;
    const right = await ctx.stream("/api/ai/exercises/d01/answer", { attempt: "4x^3", learner_id: learner, context: { course_id: "analyse", fake_outcome: "correct" } });
    assert.equal(right.final.result.data.verdict, "CORRECT");
    assert.ok(right.final.mastery.attempts >= 2);
    assert.ok(right.final.mastery.value > before);
    const mastery = await (await fetch(`${ctx.base}/api/ai/mastery?learner_id=${learner}&course_id=analyse`)).json();
    assert.ok(mastery.mastery.puissance.value > 0);
    const reveal = await ctx.stream("/api/ai/exercises/d01/reveal", { learner_id: learner, context: { course_id: "analyse" } });
    assert.ok(reveal.final.result.data.solution);
  });

  test("answers that fail a check are queued for review; rejecting drops the cache entry", async () => {
    const { final } = await ctx.stream("/api/ai/run", { task: "explain", inputs: { question: "dérivée d’une puissance" }, context: { fake_outcome: "wrong_math", course_id: "analyse" }, learner_id: learner });
    assert.equal(final.result.degrade_reason, "wrong_math");
    let flags = (await (await fetch(ctx.base + "/api/ai/flags?status=open")).json()).flags;
    const flag = flags.find((f) => f.request_id === final.result.request_id);
    assert.equal(flag.reason, "wrong_math");
    assert.equal((await ctx.post(`/api/ai/flags/${flag.request_id}/resolve`, { action: "reject", role: "student" })).status, 403);
    const ok = await (await ctx.post(`/api/ai/flags/${flag.request_id}/resolve`, { action: "reject", role: "professor" })).json();
    assert.equal(ok.ok, true);
    flags = (await (await fetch(ctx.base + "/api/ai/flags?status=open")).json()).flags;
    assert.ok(!flags.some((f) => f.request_id === flag.request_id));
  });

  test("learners can report an answer", async () => {
    const { final } = await ctx.stream("/api/ai/run", { task: "explain", inputs: { question: "la tangente" }, learner_id: learner });
    assert.equal((await ctx.post("/api/ai/flags", { request_id: final.result.request_id, learner_id: learner, note: "unclear" })).status, 200);
    const flags = (await (await fetch(ctx.base + "/api/ai/flags?status=open")).json()).flags;
    assert.equal(flags.find((f) => f.request_id === final.result.request_id).reason, "learner_report");
  });

  async function upload(body) {
    const up = await ctx.post("/api/materials", body);
    assert.equal(up.status, 201, await up.clone().text());
    const { material } = await up.json();
    let stored;
    for (let i = 0; i < 50; i++) {
      const list = (await (await fetch(`${ctx.base}/api/materials?course_id=analyse&learner_id=${body.owner_id}&role=${body.role || "student"}`)).json()).materials;
      stored = list.find((m) => m.id === material.id);
      if (stored?.status !== "processing") break;
      await new Promise((r) => setTimeout(r, 40));
    }
    return stored;
  }

  test("professor material is indexed with its sections, and a professor can confirm a section's concept", async () => {
    const md = "# Asymptotes obliques\n\nUne asymptote oblique est une droite y = ax + b dont la courbe se rapproche quand x tend vers l'infini.";
    const stored = await upload({ title: "Asymptotes", filename: "asymptotes.md", mime: "text/markdown", data: Buffer.from(md).toString("base64"), course_id: "analyse", owner_id: "prof-1", owner_name: "Dr Ben Ali", role: "professor" });
    assert.equal(stored.status, "indexed", stored.detail);
    assert.equal(stored.source, "professor");
    assert.ok(stored.chunks.length >= 1 && stored.chunks[0].id.startsWith("professor/prof-1/"));
    assert.equal((await fetch(ctx.base + stored.url)).status, 200);
    const chunk = stored.chunks[0];
    const asStudent = await fetch(`${ctx.base}/api/materials/${stored.id}/chunks/${encodeURIComponent(chunk.id)}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ concept: "tangente", role: "student" }) });
    assert.equal(asStudent.status, 403);
    const patch = await fetch(`${ctx.base}/api/materials/${stored.id}/chunks/${encodeURIComponent(chunk.id)}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ concept: "tangente", role: "professor" }) });
    assert.equal(patch.status, 200, await patch.clone().text());
    const after = (await patch.json()).material.chunks.find((c) => c.id === chunk.id);
    assert.equal(after.concept, "tangente");
    assert.equal(after.confirmed, true);
    assert.equal((await fetch(`${ctx.base}/api/materials/${stored.id}/chunks/nope`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ concept: "tangente", role: "professor" }) })).status, 404);
    const gone = await fetch(`${ctx.base}/api/materials/${stored.id}`, { method: "DELETE", headers: { "content-type": "application/json" }, body: JSON.stringify({ learner_id: "prof-1", role: "professor" }) });
    assert.equal(gone.status, 200);
  });

  test("private uploads are only visible to their owner; other types are refused", async () => {
    const stored = await upload({ title: "Mes notes", filename: "notes.txt", mime: "text/plain", data: Buffer.from("Mes notes sur les dérivées, la règle du produit et ses pièges.").toString("base64"), course_id: "analyse", owner_id: "stu-a", owner_name: "Amira" });
    assert.equal(stored.visibility, "private");
    assert.equal(stored.status, "indexed", stored.detail);
    assert.ok(stored.chunks[0].id.startsWith("learner/stu-a/"));
    const theirs = (await (await fetch(`${ctx.base}/api/materials?learner_id=stu-b`)).json()).materials;
    assert.ok(!theirs.some((m) => m.id === stored.id));
    assert.equal((await ctx.post("/api/materials", { mime: "application/x-sh", data: "eA==", owner_id: "a", course_id: "analyse" })).status, 400);
  });

  test("a bridge that does not know our token is unreachable, not trusted", async () => {
    const wrong = createEngine({ url: `http://127.0.0.1:${port}`, token: "not-the-token", log: quiet });
    assert.equal(await wrong.probe(), false);
    const r = await wrong.run("hint", { problem: "x" });
    assert.equal(r.degrade_reason, "service_unavailable");
    assert.match(r.notes.bridge_error, /token/);
    wrong.close();
  });

  test("a stopped bridge gives a degraded result, not an error", async () => {
    bridge.kill();
    await new Promise((r) => setTimeout(r, 300));
    const { final } = await ctx.stream("/api/ai/run", { task: "explain", inputs: { question: "q" } });
    assert.equal(final.result.degrade_reason, "service_unavailable");
  });
});
