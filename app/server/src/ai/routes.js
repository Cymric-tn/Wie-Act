// EdNova's feature layer over the engine: the HTTP API the web app calls.
//
// The engine knows nothing about learners, rooms or UI; this layer does. It
//  - whitelists and caps each task's inputs and builds the engine context
//    (language, confusion, learner_id / room_id / course_id / class_id),
//  - runs the tutor loop (classify → follow_up → explain / hint / quiz / grade),
//  - keeps exercise solutions, hints and mistakes on the server,
//  - streams engine Events to the browser as NDJSON for staged progress,
//  - logs every run and queues answers that failed the engine's checks for
//    professor review (rejecting one invalidates its cache entry),
//  - stores course materials and hands them to the engine's retriever.

const express = require("express");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const LANGUAGES = new Set(["FRENCH", "ENGLISH", "ARABIC", "DARIJA", "ARABIZI"]);
const CONFUSION = new Set(["LOW", "MEDIUM", "HIGH"]);
const PRIORITIES = new Set(["INTERACTIVE", "BACKGROUND"]);
// Reasons the engine raises when its own checks fail: a professor should look.
const AUTO_FLAG = new Set(["ungrounded_formula", "wrong_math", "explain_guard:language_drift", "explain_guard:contamination", "input_truncated"]);
// Outcomes the engine's FakeEngine can be told to produce (context.fake_outcome), for demos and tests; ignored by the real engine.
const FORCEABLE = new Set(["fast_extractive", "deadline", "quota_exhausted", "quota_wait", "not_in_course", "wrong_math", "room_busy", "superseded", "correct", "incorrect", "cache_hit", "SUPPORTED", "CONTRADICTED", "PARTLY", "COMPOUND", "NOT_IN_COURSE"]);
const MATERIAL_TYPES = {
  "application/pdf": ".pdf",
  "text/markdown": ".md",
  "text/plain": ".txt",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx"
};
const MAX_MATERIAL_BYTES = 15_000_000;
const SECRET_KEYS = ["solution", "hints", "common_mistakes", "mistakes"];
const DEFAULT_COURSE = process.env.AI_COURSE || "analyse"; // the one course the engine has content for

const isId = (v) => typeof v === "string" && /^[\w.:~-]{1,80}$/.test(v); // ~ : generated variants (d10~v1)
// Concept ids are the course graph's own: "nombre dérivé", "racine carrée", "polynôme" (letters, digits, spaces, accents).
const isConcept = (v) => typeof v === "string" && /^[\p{L}\p{N} _.:'’-]{1,80}$/u.test(v);
const str = (v, max) => (typeof v === "string" ? v.slice(0, max) : undefined);
const int = (v, lo, hi) => (Number.isFinite(Number(v)) ? Math.min(hi, Math.max(lo, Math.round(Number(v)))) : undefined);
const ids = (v, max = 50) => (Array.isArray(v) ? v.filter(isId).slice(0, max) : undefined);
const drop = (obj) => Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== ""));
const shortLang = (lang) => (lang === "ARABIC" || lang === "DARIJA" ? "ar" : lang === "ENGLISH" ? "en" : "fr");
const local = (value, lang) => (value && typeof value === "object" && !Array.isArray(value) ? value[shortLang(lang)] ?? value.fr ?? value.en ?? Object.values(value)[0] : value);

/** Per-task input whitelist. Anything not listed never reaches the engine. */
const INPUTS = {
  classify: (i) => drop({ message: str(i.message, 2000) }),
  explain: (i) => drop({
    question: str(i.question, 2000),
    passage: str(i.passage, 4000),
    board_text: str(i.board_text, 8000),
    exclude_exercises: ids(i.exclude_exercises),
    history: Array.isArray(i.history)
      ? i.history.slice(-8).filter((h) => h && (h.role === "user" || h.role === "assistant")).map((h) => ({ role: h.role, content: String(h.content || "").slice(0, 1000) }))
      : undefined
  }),
  hint: (i) => drop({ problem: str(i.problem, 2000), attempt: str(i.attempt, 1000) }),
  quiz: (i) => drop({
    message: str(i.message, 1000), concept: isConcept(i.concept) ? i.concept : undefined, difficulty: int(i.difficulty, 1, 3),
    exclude: ids(i.exclude), diagnostic: i.diagnostic === true ? true : undefined, intro: i.intro === false ? false : undefined
  }),
  check_claim: (i) => drop({ claim: str(i.claim, 2000) }),
  // The engine wants the problem and the learner's worked `steps`. The board sends `lines` (first line = the problem
  // when no problem is given); both spellings are accepted.
  check_answer: (i) => {
    const raw = Array.isArray(i.steps) ? i.steps : Array.isArray(i.lines) ? i.lines : typeof i.lines === "string" ? i.lines.split("\n") : [];
    const lines = raw.map((l) => String(l).trim().slice(0, 300)).filter(Boolean).slice(0, 30);
    const hasProblem = typeof i.problem === "string" && i.problem.trim();
    const problem = hasProblem ? i.problem : lines[0];
    const steps = hasProblem || lines.length < 2 ? lines : lines.slice(1);
    return drop({ problem: str(problem, 2000), steps: steps.length ? steps : undefined, exercise_id: isId(i.exercise_id) ? i.exercise_id : undefined });
  },
  ocr_transcribe: (i) => drop({ image: typeof i.image === "string" && /^data:image\/(png|jpe?g|webp|gif);base64,/.test(i.image) && i.image.length < 11_000_000 ? i.image : str(i.image, 100) }),
  // The engine plans from a scope, a deadline (a date) and a pace; the app speaks in days left and minutes per day.
  prep_plan: (i) => drop({
    course_id: isId(i.course_id) ? i.course_id : undefined, days_left: int(i.days_left, 1, 60), minutes_per_day: int(i.minutes_per_day, 10, 300),
    unit_ids: ids(i.unit_ids, 30),
    mastery: i.mastery && typeof i.mastery === "object"
      ? Object.fromEntries(Object.entries(i.mastery).filter(([k, v]) => isConcept(k) && Number.isFinite(Number(v))).slice(0, 200).map(([k, v]) => [k, Math.min(1, Math.max(0, Number(v)))]))
      : undefined
  }),
  generate_page: (i) => drop({ concept: isConcept(i.concept) ? i.concept : undefined, page_kind: i.page_kind === "review" ? "review" : undefined })
};

const isoDay = (d) => d.toISOString().slice(0, 10);

/** The engine's prep_plan input from the app's: scope = the given units, else every unit of the course. */
async function planInputs(engine, inputs, courseId) {
  const course = await engine.course(courseId).catch(() => null);
  const unitIds = inputs.unit_ids?.length ? inputs.unit_ids : (course?.units || []).map((u) => u.id);
  const days = inputs.days_left || 7;
  const deadline = new Date(Date.now() + days * 86_400_000);
  return drop({ scope: { unit_ids: unitIds }, deadline: isoDay(deadline), pace_min_per_day: inputs.minutes_per_day || 45, mastery: inputs.mastery });
}

/** The engine's focus_points grouped by day: the session list the planner screen renders (plus the raw plan). */
function shapePlan(result) {
  const plan = result?.data;
  if (!plan?.focus_points) return result;
  const days = new Map();
  for (const f of plan.focus_points) {
    const day = (f.day_index ?? 0) + 1;
    if (!days.has(day)) days.set(day, { day, minutes: 0, items: [] });
    const s = days.get(day);
    s.minutes += f.minutes || 0;
    s.items.push({ concept: f.concept_id, kind: f.page_kind === "review" ? "review" : "learn", minutes: f.minutes, reason: f.reason, reason_code: f.reason_code });
  }
  return { ...result, data: { ...plan, sessions: [...days.values()].sort((a, b) => a.day - b.day), days: result.notes?.days } };
}

/** The engine's course graph with the `title` objects the screens read (the engine says `titles`); concepts whose course
 * text is still a draft are left out of the units, as the planner leaves them out of plans. */
function shapeCourse(course) {
  const concepts = Object.fromEntries(Object.entries(course.concepts || {}).filter(([, c]) => c.status !== "draft").map(([id, c]) => [id, { ...c, title: c.titles }]));
  return {
    ...course, title: course.titles, concepts,
    units: (course.units || []).map((u) => ({ ...u, title: u.titles, concepts: (u.concepts || []).filter((id) => concepts[id]) }))
  };
}

const VARIANT_CAP = 500;

/** A prep page as the study screen reads it: the worked example's `solution` is shown as its answer. Quiz items the engine
 * generated (ids like `d10~v1`, with their solution) are kept on the server in `variants` so they can be graded; the
 * browser gets them without the solution. */
function shapePage(page, variants) {
  const w = page.worked_example;
  const quiz = (page.quiz || []).map((item) => {
    if (item.solution === undefined) return item;
    const { solution, ...rest } = item;
    if (variants) {
      if (variants.size >= VARIANT_CAP) variants.delete(variants.keys().next().value);
      variants.set(item.id, { id: item.id, concept: item.concept, level: item.level, solution, hints: [], common_mistakes: [],
        statement: { fr: item.statement, en: item.statement, ar: item.statement } });
    }
    return rest;
  });
  return {
    request_id: page.explain_request_id, task: "generate_page", text: page.explanation, degraded: page.degraded, degrade_reason: page.degrade_reason,
    eta_seconds: page.eta_seconds, from_cache: page.from_cache, events: [], notes: {},
    data: { concept_id: page.concept_id, page_kind: page.page_kind, title: page.title, language: page.language, explanation: page.explanation,
      explanation_sections: page.explanation_sections, worked_example: w ? { id: w.exercise_id, statement: w.statement, steps: w.steps || [], answer: w.solution } : null,
      quiz }
  };
}

/** Strips solutions/hints/mistakes from exercises before a result reaches the browser. */
function publicResult(result) {
  if (!result || !result.data) return result;
  const clean = (ex) => (ex && typeof ex === "object" ? Object.fromEntries(Object.entries(ex).filter(([k]) => !SECRET_KEYS.includes(k))) : ex);
  const data = { ...result.data };
  if (Array.isArray(data.exercises)) data.exercises = data.exercises.map(clean);
  if (data.exercise) data.exercise = clean(data.exercise);
  if (Array.isArray(data.quiz)) data.quiz = data.quiz.map(clean);
  return { ...result, data };
}

function rateLimiter({ perMinute }) {
  const hits = new Map();
  return (req, res, next) => {
    const key = req.ip || "?";
    const now = Date.now();
    const list = (hits.get(key) || []).filter((t) => now - t < 60_000);
    if (list.length >= perMinute) {
      res.set("retry-after", String(Math.ceil((60_000 - (now - list[0])) / 1000)));
      return res.status(429).json({ error: "Too many AI requests — wait a few seconds." });
    }
    list.push(now);
    hits.set(key, list);
    if (hits.size > 5000) hits.clear();
    next();
  };
}

function createAiRouter({ engine, store, uploadsDir, allowForce = false, log = console.log }) {
  const variants = new Map(); // generated practice items (id -> exercise with its solution), never sent to the browser
  const getExercise = async (id) => variants.get(id) || engine.getExercise(id);
  const forceAllowed = () => (typeof allowForce === "function" ? allowForce() : Boolean(allowForce));
  const router = express.Router();
  const json = express.json({ limit: "25mb" });
  const aiLimit = rateLimiter({ perMinute: 40 });
  const uploadLimit = rateLimiter({ perMinute: 10 });
  fs.mkdirSync(uploadsDir, { recursive: true });

  function contextFrom(body = {}) {
    const c = body.context || {};
    const lang = String(c.language || "").toUpperCase();
    return drop({
      language: LANGUAGES.has(lang) ? lang : undefined,
      level: str(c.level, 40),
      style: str(c.style, 40),
      confusion: CONFUSION.has(c.confusion) ? c.confusion : undefined,
      difficulty: int(c.difficulty, 1, 3),
      complexity: int(c.complexity, 1, 5),
      escalate: c.escalate === true ? true : undefined,
      topic: str(c.topic, 60),
      turn_id: isId(c.turn_id) ? c.turn_id : undefined,
      learner_id: isId(body.learner_id) ? body.learner_id : undefined,
      room_id: isId(c.room_id) ? c.room_id : undefined,
      course_id: isId(c.course_id) ? c.course_id : undefined,
      class_id: isId(c.class_id) ? c.class_id : undefined,
      fake_outcome: forceAllowed() && FORCEABLE.has(c.fake_outcome) ? c.fake_outcome : undefined
    });
  }

  /** engine.run plus the run log and automatic review flags. */
  async function run(task, inputs, ctx, priority, onEvent, summary = {}) {
    const result = await engine.run(task, inputs, ctx, priority, onEvent);
    const backend = engine.status().backend;
    try {
      const question = summary.question ?? inputs.question ?? inputs.message ?? inputs.claim ?? inputs.problem ?? null;
      const answer = typeof result.text === "string" ? result.text : result.data ? JSON.stringify(result.data) : null;
      store.logRun({
        request_id: result.request_id, task, backend, learner_id: ctx.learner_id, room_id: ctx.room_id, course_id: ctx.course_id,
        language: ctx.language, degraded: result.degraded, degrade_reason: result.degrade_reason, mode: result.mode,
        model_class: result.model_class_used, from_cache: result.from_cache, latency_ms: result.latency_ms,
        question: question && String(question).slice(0, 2000), answer: answer && answer.slice(0, 6000)
      });
      if (result.degraded && AUTO_FLAG.has(result.degrade_reason)) {
        store.flag({ request_id: result.request_id, task, reason: result.degrade_reason, flagged_by: "engine", course_id: ctx.course_id, question, answer: answer && answer.slice(0, 6000) });
      }
    } catch (error) {
      log("AI run log failed:", error.message);
    }
    return result;
  }

  /** Streams NDJSON once the run starts; a validation error before that is a 400. */
  function streamer(res) {
    let started = false;
    const start = () => {
      if (started) return;
      started = true;
      res.status(200).set({ "content-type": "application/x-ndjson; charset=utf-8", "cache-control": "no-store", "x-accel-buffering": "no" });
      res.flushHeaders?.();
    };
    return {
      send(obj) {
        start();
        res.write(JSON.stringify(obj) + "\n");
      },
      end(obj) {
        if (obj) this.send(obj);
        res.end();
      },
      fail(error) {
        if (!started) {
          const bad = error instanceof RangeError;
          if (!bad) log("AI request failed:", error);
          return res.status(bad ? 400 : 500).json({ error: bad ? error.message : "The AI service failed unexpectedly." });
        }
        res.write(JSON.stringify({ type: "error", error: error instanceof RangeError ? error.message : "The AI service failed unexpectedly." }) + "\n");
        res.end();
      }
    };
  }

  // ---------- status ----------
  router.get("/ai/status", (_req, res) => {
    res.json({ ...engine.status(), stats: store.stats(), open_flags: store.listFlags("open").length });
  });

  // ---------- one engine task ----------
  router.post("/ai/run", json, aiLimit, async (req, res) => {
    const out = streamer(res);
    try {
      const task = req.body?.task;
      if (!INPUTS[task]) throw new RangeError(`task not available from the web app: ${task}`);
      const inputs = INPUTS[task](req.body.inputs || {});
      const ctx = contextFrom(req.body);
      const priority = PRIORITIES.has(req.body.priority) ? req.body.priority : "INTERACTIVE";
      const onEvent = (event) => out.send({ type: "event", event });
      const courseId = inputs.course_id || ctx.course_id || DEFAULT_COURSE;
      if (task === "prep_plan") {
        // Real practice results override the demo mastery the browser sent.
        if (ctx.learner_id) {
          const measured = store.mastery(ctx.learner_id, courseId);
          inputs.mastery = { ...(inputs.mastery || {}), ...Object.fromEntries(Object.entries(measured).map(([k, v]) => [k, v.value])) };
        }
        const result = await run(task, await planInputs(engine, inputs, courseId), { ...ctx, course_id: ctx.course_id || courseId }, "BACKGROUND", onEvent, { question: `prep plan: ${inputs.days_left || 7} days` });
        return out.end({ type: "result", result: shapePlan(result) });
      }
      if (task === "generate_page") {
        if (!inputs.concept) throw new RangeError("missing input: concept");
        const m = ctx.learner_id ? store.mastery(ctx.learner_id, courseId)[inputs.concept] : null;
        const focus = drop({ concept_id: inputs.concept, page_kind: inputs.page_kind || "learn", mastery: m ? m.value : undefined });
        const page = await engine.generatePage(focus, { ...ctx, course_id: ctx.course_id || courseId }, onEvent);
        if (page.concept_id === undefined) return out.end({ type: "result", result: page }); // the bridge was unreachable: already a degraded Result
        const result = shapePage(page, variants);
        try {
          store.logRun({ request_id: result.request_id, task: "generate_page", backend: engine.status().backend, learner_id: ctx.learner_id, room_id: ctx.room_id, course_id: ctx.course_id, language: ctx.language, degraded: result.degraded, degrade_reason: result.degrade_reason, mode: null, model_class: null, from_cache: result.from_cache, latency_ms: null, question: inputs.concept, answer: (result.text || "").slice(0, 6000) });
        } catch (error) {
          log("AI run log failed:", error.message);
        }
        return out.end({ type: "result", result });
      }
      const result = await run(task, inputs, ctx, priority, onEvent);
      out.end({ type: "result", result: publicResult(result) });
    } catch (error) {
      out.fail(error);
    }
  });

  // ---------- one tutor turn: classify → follow-up task ----------
  router.post("/ai/turn", json, aiLimit, async (req, res) => {
    const out = streamer(res);
    try {
      const message = str(req.body?.message, 2000);
      if (!message || !message.trim()) throw new RangeError("missing input: message");
      const ctx = contextFrom(req.body);
      const turn_id = ctx.turn_id || crypto.randomUUID().slice(0, 12);
      const base = { ...ctx, turn_id };
      const stage = (name) => (event) => out.send({ type: "event", stage: name, event });

      const classification = await run("classify", { message }, base, "INTERACTIVE", stage("classify"));
      out.send({ type: "stage", stage: "classify", result: publicResult(classification) });
      if (classification.degraded && classification.degrade_reason === "superseded") return out.end({ type: "result", superseded: true });
      const c = classification.data || {};
      const follow = await engine.followUp(c);
      const language = ctx.language || c.language;
      const turnCtx = drop({ ...base, language, confusion: c.confusion, complexity: c.complexity });
      const openExercise = isId(req.body.exercise_id) ? await getExercise(req.body.exercise_id) : null;

      let task = follow?.task || null;
      let inputs = null;
      if (c.intent === "ANSWER_ATTEMPT" && openExercise) {
        const graded = await gradeExercise(openExercise, message, turnCtx, int(req.body.hints_used, 0, 10) || 0, stage("grade"));
        return out.end({ type: "result", classification: publicResult(classification), follow: { task: "grade", reason: "answer to the open exercise" }, result: graded });
      }
      if (task === "explain") inputs = INPUTS.explain({ question: message, history: req.body.history, exclude_exercises: req.body.exclude });
      else if (task === "quiz") inputs = INPUTS.quiz({ message, exclude: req.body.exclude, diagnostic: c.intent === "DIAGNOSTIC" });
      else if (task === "hint") {
        if (openExercise) {
          const level = Math.min(3, (int(req.body.hints_used, 0, 10) || 0) + 1);
          const result = await exerciseHint(openExercise, level, turnCtx, stage("hint"));
          return out.end({ type: "result", classification: publicResult(classification), follow, result });
        }
        inputs = INPUTS.hint({ problem: message, attempt: message });
      }
      const result = task ? await run(task, inputs, turnCtx, "INTERACTIVE", stage(task), { question: message }) : null;
      out.end({ type: "result", classification: publicResult(classification), follow, result: publicResult(result) });
    } catch (error) {
      out.fail(error);
    }
  });

  // ---------- exercises (solutions stay here) ----------
  async function gradeExercise(ex, attempt, ctx, hintsUsed, onEvent) {
    const result = await run("grade", {
      problem: local(ex.statement, ctx.language),
      solution: ex.solution,
      attempt,
      common_mistakes: (ex.common_mistakes || ex.mistakes || []).map((m) => (Array.isArray(m) ? { expr: m[0], feedback: local(m[1], ctx.language) } : { expr: m.expr, feedback: local(m.feedback, ctx.language) }))
    }, ctx, "INTERACTIVE", onEvent, { question: `${ex.id}: ${attempt}` });
    if (ctx.learner_id && result.data?.verdict) {
      store.addAttempt({ learner_id: ctx.learner_id, course_id: ctx.course_id || DEFAULT_COURSE, exercise_id: ex.id, concept: ex.concept, verdict: result.data.verdict, hints_used: hintsUsed });
    }
    return { ...result, data: { ...result.data, exercise_id: ex.id, concept: ex.concept } };
  }

  async function exerciseHint(ex, level, ctx, onEvent) {
    const hints = local(ex.hints, ctx.language) || [];
    return run("hint", { problem: local(ex.statement, ctx.language), stored_hints: Array.isArray(hints) ? hints : [], hint_level: level }, ctx, "INTERACTIVE", onEvent);
  }

  router.post("/ai/exercises/:id/:action", json, aiLimit, async (req, res) => {
    const out = streamer(res);
    try {
      const { id, action } = req.params;
      if (!isId(id) || !["answer", "hint", "reveal"].includes(action)) throw new RangeError("unknown exercise action");
      const ex = await getExercise(id);
      if (!ex) throw new RangeError("unknown exercise");
      const ctx = contextFrom(req.body);
      const onEvent = (event) => out.send({ type: "event", event });
      if (action === "answer") {
        const attempt = str(req.body.attempt, 1000);
        if (!attempt || !attempt.trim()) throw new RangeError("missing input: attempt");
        const result = await gradeExercise(ex, attempt, ctx, int(req.body.hints_used, 0, 10) || 0, onEvent);
        const mastery = ctx.learner_id ? store.mastery(ctx.learner_id, ctx.course_id || DEFAULT_COURSE)[ex.concept] || null : null;
        return out.end({ type: "result", result, mastery });
      }
      if (action === "hint") {
        const result = await exerciseHint(ex, int(req.body.level, 1, 10) || 1, ctx, onEvent);
        return out.end({ type: "result", result });
      }
      // reveal: the learner gives up — counts as a missed attempt.
      if (ctx.learner_id) store.addAttempt({ learner_id: ctx.learner_id, course_id: ctx.course_id || DEFAULT_COURSE, exercise_id: ex.id, concept: ex.concept, verdict: "INCORRECT", hints_used: 3 });
      out.end({ type: "result", result: { data: { exercise_id: ex.id, solution: ex.solution } } });
    } catch (error) {
      out.fail(error);
    }
  });

  router.get("/ai/mastery", (req, res) => {
    const learner = isId(req.query.learner_id) ? req.query.learner_id : null;
    const course = isId(req.query.course_id) ? req.query.course_id : DEFAULT_COURSE;
    res.json({ course_id: course, mastery: learner ? store.mastery(learner, course) : {} });
  });

  router.get("/ai/courses/:id", async (req, res) => {
    try {
      const course = isId(req.params.id) ? await engine.course(req.params.id) : null;
      if (!course) return res.status(404).json({ error: "This course has no graph in the engine yet." });
      res.json({ course: shapeCourse(course) });
    } catch (error) {
      res.status(502).json({ error: error.message });
    }
  });

  // ---------- review queue ----------
  router.post("/ai/flags", json, aiLimit, (req, res) => {
    const requestId = req.body?.request_id;
    const run = isId(requestId) && store.getRun(requestId);
    if (!run) return res.status(404).json({ error: "unknown answer" });
    store.flag({
      request_id: run.request_id, task: run.task, reason: "learner_report", note: str(req.body.note, 500),
      flagged_by: isId(req.body.learner_id) ? req.body.learner_id : "anonymous", course_id: run.course_id, question: run.question, answer: run.answer
    });
    res.json({ ok: true });
  });

  router.get("/ai/flags", (req, res) => {
    const status = ["open", "rejected", "approved"].includes(req.query.status) ? req.query.status : null;
    // Several reports of one answer show as one item.
    const grouped = new Map();
    store.listFlags(status).forEach((f) => {
      const g = grouped.get(f.request_id);
      if (g) g.reports.push({ reason: f.reason, note: f.note, at: f.created_at });
      else grouped.set(f.request_id, { ...f, reports: [{ reason: f.reason, note: f.note, at: f.created_at }] });
    });
    res.json({ flags: [...grouped.values()] });
  });

  router.post("/ai/flags/:requestId/resolve", json, async (req, res) => {
    const { requestId } = req.params;
    const action = req.body?.action;
    if (!isId(requestId) || !["reject", "approve"].includes(action)) return res.status(400).json({ error: "bad request" });
    if (req.body.role !== "professor") return res.status(403).json({ error: "Only professors can review answers." });
    let removed = 0;
    if (action === "reject") {
      try {
        removed = (await engine.invalidate(requestId))?.removed ?? 0;
      } catch (error) {
        return res.status(502).json({ error: `The engine could not drop the cached answer: ${error.message}` });
      }
    }
    store.resolveFlag(requestId, action === "reject" ? "rejected" : "approved");
    res.json({ ok: true, cache_entries_removed: removed });
  });

  // ---------- course materials ----------
  const canSee = (m, q) => {
    if (m.visibility === "public") return true;
    if (q.role === "professor") return m.visibility === "class" || m.owner_id === q.learner_id;
    if (m.visibility === "class") return Boolean(q.class_id) && m.class_id === q.class_id;
    return m.owner_id === q.learner_id;
  };
  // Owner ids stay on the server; the browser only learns whether a file is its own.
  const publicMaterial = (m, viewer) => {
    const { owner_id, ...rest } = m;
    return { ...rest, mine: Boolean(viewer) && owner_id === viewer, url: `/api/materials/${m.id}/file` };
  };

  router.get("/materials", (req, res) => {
    const q = { learner_id: req.query.learner_id, class_id: req.query.class_id, role: req.query.role };
    const course = isId(req.query.course_id) ? req.query.course_id : null;
    res.json({ materials: store.listMaterials(course).filter((m) => canSee(m, q)).map((m) => publicMaterial(m, q.learner_id)) });
  });

  router.post("/materials", json, uploadLimit, async (req, res) => {
    const b = req.body || {};
    const ext = MATERIAL_TYPES[b.mime];
    if (!ext) return res.status(400).json({ error: "Upload a PDF, Word (.docx), Markdown or text file." });
    if (!isId(b.owner_id) || !isId(b.course_id)) return res.status(400).json({ error: "missing owner or course" });
    const bytes = Buffer.from(String(b.data || ""), "base64");
    if (!bytes.length || bytes.length > MAX_MATERIAL_BYTES) return res.status(400).json({ error: "Files must be between 1 byte and 15 MB." });
    const professor = b.role === "professor";
    const visibility = professor ? (b.visibility === "class" ? "class" : "public") : (b.visibility === "class" ? "class" : "private");
    const id = `mat-${crypto.randomUUID().slice(0, 12)}`;
    const file = path.join(uploadsDir, id + ext);
    fs.writeFileSync(file, bytes);
    const material = {
      id, course_id: b.course_id, title: str(b.title, 160)?.trim() || str(b.filename, 160) || "Untitled", filename: str(b.filename, 160) || id + ext,
      mime: b.mime, size: bytes.length, visibility, class_id: isId(b.class_id) ? b.class_id : null, owner_id: b.owner_id,
      owner_name: str(b.owner_name, 80) || "Someone", source: professor ? "professor" : "upload", status: "processing", chunks: []
    };
    store.addMaterial(material);
    res.status(201).json({ material: publicMaterial({ ...material, created_at: Date.now() }, material.owner_id) });

    // Ingest after answering: PDFs with scanned pages can take a while (OCR runs at BACKGROUND).
    const text = b.mime === "text/markdown" || b.mime === "text/plain" ? bytes.toString("utf8") : str(b.text, 2_000_000) || "";
    try {
      const ingest = await engine.addMaterial({
        id, course_id: material.course_id, title: material.title, filename: material.filename, mime: material.mime, path: file,
        text, visibility, class_id: material.class_id, learner_id: material.owner_id, source: material.source
      });
      const chunks = ingest?.chunks || [];
      const status = chunks.length ? "indexed" : ingest?.needs_ocr ? "needs_ocr" : "empty";
      const notes = [ingest?.needs_ocr_pages?.length ? `${ingest.needs_ocr_pages.length} page(s) had no text` : null, ...(ingest?.warnings || [])].filter(Boolean);
      store.updateMaterial(id, { status, chunks, detail: notes.join(" · ") || ingest?.detail || null });
    } catch (error) {
      store.updateMaterial(id, { status: "failed", chunks: [], detail: String(error.message).slice(0, 300) });
    }
  });

  router.get("/materials/:id/file", (req, res) => {
    const m = isId(req.params.id) && store.getMaterial(req.params.id);
    if (!m || !canSee(m, req.query)) return res.status(404).end();
    res.type(m.mime).set("content-disposition", `inline; filename="${m.filename.replace(/["\\\r\n]/g, "_")}"`)
      .sendFile(path.join(uploadsDir, m.id + MATERIAL_TYPES[m.mime]));
  });

  router.patch("/materials/:id/chunks/:chunkId", json, async (req, res) => {
    const m = isId(req.params.id) && store.getMaterial(req.params.id);
    if (!m) return res.status(404).json({ error: "unknown material" });
    if (req.body?.role !== "professor") return res.status(403).json({ error: "Only professors confirm concept tags." });
    const concept = req.body.concept === null ? null : isId(req.body.concept) ? req.body.concept : undefined;
    if (concept === undefined) return res.status(400).json({ error: "bad concept" });
    if (!m.chunks.some((c) => c.id === req.params.chunkId)) return res.status(404).json({ error: "unknown section" });
    try {
      await engine.confirmChunk(req.params.chunkId, concept); // the retriever uses the confirmed tag from now on
    } catch (error) {
      return res.status(error.status === 404 ? 404 : 502).json({ error: error.status === 404 ? "The engine no longer has this section (restarted?). Upload the file again." : `The engine could not save the tag: ${error.message}` });
    }
    const chunks = m.chunks.map((c) => (c.id === req.params.chunkId ? { ...c, concept, confidence: 1, low_confidence: false, confirmed: true } : c));
    store.updateMaterial(m.id, { status: m.status, chunks, detail: m.detail });
    res.json({ material: publicMaterial({ ...m, chunks }) });
  });

  router.delete("/materials/:id", json, async (req, res) => {
    const m = isId(req.params.id) && store.getMaterial(req.params.id);
    if (!m) return res.status(404).json({ error: "unknown material" });
    const q = { ...req.query, ...(req.body || {}) };
    if (m.owner_id !== q.learner_id && q.role !== "professor") return res.status(403).json({ error: "Only the uploader or a professor can remove this file." });
    try {
      await engine.removeMaterial(m.id, m.course_id, m.owner_id);
    } catch (error) {
      log("Engine could not remove material:", error.message);
    }
    store.deleteMaterial(m.id);
    fs.rm(path.join(uploadsDir, m.id + MATERIAL_TYPES[m.mime]), { force: true }, () => {});
    res.json({ ok: true });
  });

  return router;
}

module.exports = { createAiRouter, publicResult, INPUTS, planInputs, shapePlan, shapePage, shapeCourse };
