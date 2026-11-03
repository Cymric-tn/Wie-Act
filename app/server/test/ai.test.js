const { test, describe, before, after } = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const { spawn, spawnSync } = require("child_process");
const { createServer } = require("../src/app");
const { createFakeEngine } = require("../src/ai/fake/engine");
const M = require("../src/ai/fake/mathexpr");

const quiet = () => {};

async function ndjson(res) {
  const text = await res.text();
  return text.split("\n").filter(Boolean).map((l) => JSON.parse(l));
}

function harness(ai) {
  const ctx = {};
  before(async () => {
    ctx.server = createServer({ log: quiet, ai: { mode: "fake", fakeSpeed: 0, ...ai } });
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

describe("safe math parser", () => {
  test("accepts learner notation and rejects anything else", () => {
    assert.ok(M.sameFunction(M.parse("f′(x) = 6x+5"), M.parse("5 + 6*x")));
    assert.ok(M.sameFunction(M.parse("x(x+3)"), M.parse("x²+3x")));
    assert.equal(M.parse("2,5x")(2), 5);
    assert.equal(M.parse("process.exit()"), null);
    assert.equal(M.parse("x".repeat(81)), null);
    assert.equal(M.isDerivative("3x^2+5x", "6x+5"), true);
    assert.equal(M.isDerivative("x^4", "x^3"), false);
  });
});

describe("fake engine contract", () => {
  const engine = createFakeEngine({ speed: 0 });

  test("classify spots Arabizi confusion and follow_up routes to explain", async () => {
    const r = await engine.run("classify", { message: "chnowa ya3ni derivee? ma fhemt chay" });
    assert.equal(r.data.language, "ARABIZI");
    assert.equal(r.data.confusion, "HIGH");
    assert.equal(engine.followUp(r.data).task, "explain");
    assert.equal(engine.followUp({ intent: "QUIZ", confusion: "HIGH" }).overridden, true);
  });

  test("explain is grounded, cached on repeat, and says when it is not in the course", async () => {
    const first = await engine.run("explain", { question: "C’est quoi la dérivée d’un produit ?" }, { language: "FRENCH" });
    assert.equal(first.notes.retrieved[0].chunk_id, "derivees.fr#produit");
    assert.equal(first.model_class_used, "midsize");
    assert.ok(first.events.some((e) => e.name === "partial_result"));
    const again = await engine.run("explain", { question: "C’est quoi la dérivée d’un produit ?" }, { language: "FRENCH", confusion: "HIGH" });
    assert.equal(again.from_cache, true);
    assert.notEqual(again.data.opener, first.data.opener);
    const off = await engine.run("explain", { question: "the function in my code is slow" }, { language: "ENGLISH" });
    assert.equal(off.data.not_found, true);
    assert.equal(off.degraded, false);
  });

  test("Darija in Arabic script finds the French chunk through the glossary", async () => {
    const r = await engine.run("explain", { question: "شنوة مشتقة الجداء" });
    assert.equal(r.notes.retrieved[0].chunk_id, "derivees.fr#produit");
  });

  test("forced outcomes degrade with the documented reasons", async () => {
    const fast = await engine.run("explain", { question: "dérivée d’un produit" }, { fake_outcome: "quota_low" });
    assert.equal(fast.mode, "fast_extractive");
    assert.equal(fast.degrade_reason, "quota_low");
    assert.ok(fast.data.excerpts.length > 0);
    const busy = await engine.run("hint", { problem: "x" }, { fake_outcome: "service_busy" });
    assert.equal(busy.degrade_reason, "service_busy");
    assert.ok(busy.eta_seconds > 0);
  });

  test("one running request per room and one waiting request per learner", async () => {
    const [a, b] = await Promise.all([
      engine.run("explain", { question: "produit" }, { room_id: "r1", learner_id: "a" }),
      engine.run("explain", { question: "produit" }, { room_id: "r1", learner_id: "b" })
    ]);
    assert.equal(a.degraded, false);
    assert.equal(b.degrade_reason, "room_busy");
    const [old, fresh] = await Promise.all([engine.run("hint", { problem: "x" }, { learner_id: "z" }), engine.run("hint", { problem: "x" }, { learner_id: "z" })]);
    assert.equal(old.degrade_reason, "superseded");
    assert.equal(fresh.degraded, false);
  });

  test("check_claim and check_answer decide formulas without a model", async () => {
    assert.equal((await engine.run("check_claim", { claim: "(x^3)′ = x^2" })).data.verdict, "CONTRADICTED");
    assert.equal((await engine.run("check_claim", { claim: "(uv)′ = u′v′" })).data.correct_form, "(u·v)′ = u′·v + u·v′");
    assert.equal((await engine.run("check_claim", { claim: "Paris is the capital of France" }, { language: "ENGLISH" })).data.verdict, "NOT_IN_COURSE");
    const work = await engine.run("check_answer", { problem: "f(x) = 3x^2+5x", lines: ["f′(x) = 6x + 5", "something unclear"] });
    assert.deepEqual(work.data.lines.map((l) => l.status), ["OK", "UNCHECKED"]);
  });

  test("unknown task and missing inputs throw before any work", async () => {
    await assert.rejects(engine.run("nope", {}), RangeError);
    await assert.rejects(engine.run("explain", {}), /missing input: question/);
  });
});

describe("AI API (fake engine)", () => {
  const ctx = harness();
  const learner = "learner-1";

  test("status reports the backend", async () => {
    const s = await (await fetch(ctx.base + "/api/ai/status")).json();
    assert.equal(s.backend, "fake");
    assert.equal(s.connected, true);
  });

  test("/api/ai/run streams events then the result", async () => {
    const { events, final } = await ctx.stream("/api/ai/run", { task: "explain", inputs: { question: "dérivée d’un produit" }, context: { language: "FRENCH", course_id: "MATH101" }, learner_id: learner });
    assert.ok(events.some((e) => e.event.name === "retrieving"));
    assert.match(final.result.text, /u′·v \+ u·v′/);
    assert.equal(final.result.data.exercise.solution, undefined, "solutions never reach the browser");
  });

  test("bad tasks and internal tasks are refused with 400", async () => {
    assert.equal((await ctx.post("/api/ai/run", { task: "grade", inputs: {} })).status, 400);
    assert.equal((await ctx.post("/api/ai/run", { task: "explain", inputs: {} })).status, 400);
  });

  test("a tutor turn quizzes, grades the open exercise and records mastery", async () => {
    const quiz = await ctx.stream("/api/ai/turn", { message: "donne-moi un exercice sur le produit", learner_id: learner, context: { course_id: "MATH101" } });
    assert.equal(quiz.final.follow.task, "quiz");
    const ex = quiz.final.result.data.exercises[0];
    assert.equal(ex.concept, "produit");
    assert.equal(ex.solution, undefined);
    assert.equal(ex.hints, undefined);

    const hint = await ctx.stream(`/api/ai/exercises/${ex.id}/hint`, { level: 2, learner_id: learner, context: { language: "ENGLISH" } });
    assert.equal(hint.final.result.data.source, "bank");

    const wrong = await ctx.stream("/api/ai/turn", { message: "1", exercise_id: ex.id, learner_id: learner, context: { course_id: "MATH101", language: "FRENCH" } });
    assert.equal(wrong.final.follow.task, "grade");
    assert.equal(wrong.final.result.data.verdict, "INCORRECT");
    assert.match(wrong.final.result.data.feedback, /u′·v′/);

    const right = await ctx.stream(`/api/ai/exercises/${ex.id}/answer`, { attempt: "2x + 1", learner_id: learner, context: { course_id: "MATH101" } });
    assert.equal(right.final.result.data.verdict, "CORRECT");
    assert.ok(right.final.mastery.attempts >= 2);

    const mastery = await (await fetch(`${ctx.base}/api/ai/mastery?learner_id=${learner}&course_id=MATH101`)).json();
    assert.ok(mastery.mastery.produit.value > 0);
  });

  test("answers that fail a check are queued for review; rejecting drops the cache entry", async () => {
    const { final } = await ctx.stream("/api/ai/run", { task: "explain", inputs: { question: "dérivée d’une puissance" }, context: { fake_outcome: "wrong_math", course_id: "MATH101" }, learner_id: learner });
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

  test("professor material is indexed, visible to everyone, and used in answers", async () => {
    const md = "# Règle de l’Hôpital\nPour une forme indéterminée 0/0, la règle de l’Hôpital dit que la limite de f/g est celle de f′/g′ quand elle existe.";
    const up = await ctx.post("/api/materials", { title: "L’Hôpital", filename: "hopital.md", mime: "text/markdown", data: Buffer.from(md).toString("base64"), course_id: "MATH101", owner_id: "prof-1", owner_name: "Dr Ben Ali", role: "professor" });
    assert.equal(up.status, 201);
    const { material } = await up.json();
    let list;
    for (let i = 0; i < 20; i++) {
      list = (await (await fetch(`${ctx.base}/api/materials?course_id=MATH101&learner_id=${learner}`)).json()).materials;
      if (list.find((m) => m.id === material.id)?.status !== "processing") break;
      await new Promise((r) => setTimeout(r, 20));
    }
    const stored = list.find((m) => m.id === material.id);
    assert.equal(stored.status, "indexed");
    assert.equal(stored.source, "professor");
    const { final } = await ctx.stream("/api/ai/run", { task: "explain", inputs: { question: "la règle de l’Hôpital" }, learner_id: learner, context: { course_id: "MATH101" } });
    assert.equal(final.result.notes.retrieved[0].source, "professor");
    assert.equal((await fetch(ctx.base + stored.url)).status, 200);
    assert.equal((await fetch(`${ctx.base}/api/materials/${material.id}`, { method: "DELETE", headers: { "content-type": "application/json" }, body: JSON.stringify({ learner_id: learner }) })).status, 403);
  });

  test("private uploads are only visible to their owner", async () => {
    const up = await ctx.post("/api/materials", { title: "Mes notes", filename: "notes.txt", mime: "text/plain", data: Buffer.from("Mes notes sur les dérivées, la règle du produit et ses pièges.").toString("base64"), course_id: "MATH101", owner_id: "stu-a", owner_name: "Amira" });
    const { material } = await up.json();
    assert.equal(material.visibility, "private");
    const mine = (await (await fetch(`${ctx.base}/api/materials?learner_id=stu-a`)).json()).materials;
    const theirs = (await (await fetch(`${ctx.base}/api/materials?learner_id=stu-b`)).json()).materials;
    assert.ok(mine.some((m) => m.id === material.id));
    assert.ok(!theirs.some((m) => m.id === material.id));
    assert.equal((await ctx.post("/api/materials", { mime: "application/x-sh", data: "eA==", owner_id: "a", course_id: "MATH101" })).status, 400);
  });
});

const python = ["python3", "python"].find((cmd) => spawnSync(cmd, ["--version"]).status === 0);

describe("AI API through the Python bridge", { skip: python ? false : "python3 not installed" }, () => {
  const port = 8790 + Math.floor(Math.random() * 100);
  let bridge;
  const ctx = harness({ mode: "engine", url: `http://127.0.0.1:${port}` });

  before(async () => {
    bridge = spawn(python, [path.join(__dirname, "../../bridge/bridge.py"), "--engine-dir", path.join(__dirname, "../../bridge/test/stand_in"), "--port", String(port)], { stdio: "ignore" });
    for (let i = 0; i < 50; i++) {
      if (await ctx.server.engine.probe()) return;
      await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error("bridge did not start");
  });
  after(() => bridge?.kill());

  test("runs a task on the real engine interface with events and priority", async () => {
    const { events, final } = await ctx.stream("/api/ai/run", { task: "explain", inputs: { question: "q" }, context: { language: "FRENCH", course_id: "MATH101" }, priority: "BACKGROUND", learner_id: "l-1" });
    assert.deepEqual(events.map((e) => e.event.name), ["retrieving", "partial_result", "generating"]);
    assert.equal(final.result.text, "stand-in explanation");
    assert.equal(final.result.notes.priority, "BACKGROUND");
    assert.equal(final.result.notes.ctx.learner_id, "l-1");
    assert.equal(final.result.notes.ctx.course_id, "MATH101");
  });

  test("grades an exercise with the solution kept on the server", async () => {
    const { final } = await ctx.stream("/api/ai/exercises/d01/answer", { attempt: "4x^3", learner_id: "l-1" });
    assert.equal(final.result.data.verdict, "CORRECT");
  });

  test("a stopped bridge gives a degraded result, not an error", async () => {
    bridge.kill();
    await new Promise((r) => setTimeout(r, 200));
    const { final } = await ctx.stream("/api/ai/run", { task: "explain", inputs: { question: "q" } });
    assert.equal(final.result.degrade_reason, "service_unavailable");
  });
});
