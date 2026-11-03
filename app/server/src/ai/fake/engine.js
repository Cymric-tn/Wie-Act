// A JavaScript stand-in for the EdNova LLM engine (v1.3 contract). It has the
// same run(task, inputs, context, priority, onEvent) signature, the same Result
// and Event shapes, the same degrade reasons and the same fairness rules (one
// waiting request per learner, one running request per room), so every screen
// can show every state before the real engine is installed. No LLM is called:
// answers come from the small course bank in bank.js.
//
// Modes (ENGINE_FAKE_MODE): normal, slow, degraded. context.fake_outcome forces
// one state for a single call: quota_low, service_busy, quota_exhausted,
// midsize_unavailable, deadline, not_found, wrong_math, room_busy.

const crypto = require("crypto");
const M = require("./mathexpr");
const { COURSE, CHUNKS, GLOSSARY, EXERCISES, TEMPLATES } = require("./bank");

const LANGS = ["FRENCH", "ENGLISH", "ARABIC", "DARIJA", "ARABIZI"];
const TASKS = ["classify", "hint", "explain", "grade", "quiz", "compress", "ocr_transcribe", "check_claim", "check_answer", "prep_plan", "generate_page"];
const REQUIRED = {
  classify: ["message"], hint: ["problem"], explain: ["question"], grade: ["problem", "solution", "attempt"],
  compress: ["question", "chunks"], ocr_transcribe: ["image"], check_claim: ["claim"], check_answer: ["problem", "lines"],
  prep_plan: [], generate_page: ["concept"], quiz: []
};
const SPEED = { normal: 1, slow: 3.5, degraded: 1 };
const MIN_COVERAGE = 0.34;
const PROFESSOR_BOOST = 1.3;

// ---------- language ----------
const DARIJA_AR = /(شنوة|شنية|نحب|برشا|باش|فهمتش|ياسر|توّا|توا|كيفاش|علاش|متاع|موش|نجم|عسلامة)/;
const ARABIZI = /\b(chnowa|chnoua|chnia|chniya|fhemt|fhemtech|kifech|kifeh|3lech|nheb|n7eb|barcha|ya3ni|mta3|aslema|3aslema|famma|fama|mouch|mech|9a3ed|nfhem|ma3neha|bech|w9tech|chkoun|ena|enti|enta)\b|\b[a-z]*[379][a-z]+\b/i;
const FRENCH = /[àâçéèêëîïôûùœ]|\b(le|la|les|est|une|un|des|comment|pourquoi|c'est|c’est|je|que|quoi|dérivée|derivee|explique|exercice|calcule|fonction|bonjour|salut|merci|cours)\b/i;
const CONFUSED = /(je comprends pas|je ne comprends pas|j'ai pas compris|pas compris|i don'?t (get|understand)|confused|lost|ma fhemt|ma fhemtech|mafhemtch|ما فهمتش|لم أفهم|ما فهمت|مش فاهم|perdu)/i;
const GREETINGS = new Set(["salut", "bonjour", "hello", "hi", "hey", "aslema", "3aslema", "merci", "thanks", "thank you", "شكرا", "مرحبا", "السلام عليكم", "عسلامة", "yo", "coucou", "salam"]);

function detectLanguage(text) {
  const s = String(text || "");
  if (/[؀-ۿ]/.test(s)) return DARIJA_AR.test(s) ? "DARIJA" : "ARABIC";
  if (ARABIZI.test(s)) return "ARABIZI";
  if (FRENCH.test(s)) return "FRENCH";
  return "ENGLISH";
}

const normLang = (v) => (LANGS.includes(String(v || "").toUpperCase()) ? String(v).toUpperCase() : null);
/** The language the model body is written in (one language only). */
const bodyLang = (lang) => (lang === "ARABIC" ? "ar" : lang === "ENGLISH" ? "en" : "fr");
const templateLang = (lang) => (lang === "DARIJA" || lang === "ARABIZI" ? lang : lang || "FRENCH");
const shortLang = (lang) => (lang === "ARABIC" || lang === "DARIJA" ? "ar" : lang === "ENGLISH" ? "en" : "fr");
const pick = (table, lang) => table[templateLang(lang)] ?? table.FRENCH;
const pick3 = (table, lang) => table[lang === "ARABIC" || lang === "DARIJA" ? "ARABIC" : lang === "ENGLISH" ? "ENGLISH" : "FRENCH"];

// ---------- retrieval (BM25-ish, glossary, coverage gate) ----------
const STOP = new Set(("a an the is are was what how why when which of to in on for and or it this that me my i you do does can wrong right true false mean means " +
  "le la les un une des de du et ou est que qui quoi comment pourquoi ce c ca ça je tu il on mon ma mes ton ta en au aux par pour avec dans sur pas plus " +
  "chnowa chnoua chnia ya3ni ma fhemt fhemtech kifech kifeh nheb n7eb barcha mta3 fama famma w ena enti " +
  "explain explique expliquer give donne moi please stp svp define definir définir " +
  "شنوه شنيه كيفاش علاش نحب فهمتش متاع هاذي هذا هذه ما هو هي في من على عن اشرح ماذا كيف لماذا").split(/\s+/).flatMap((w) => tokens(w)));

function fold(word) {
  return word.normalize("NFD").replace(/[̀-ͯ]/g, "");
}
function tokens(text) {
  const s = String(text || "").toLowerCase().normalize("NFKC")
    .replace(/[ً-ْـ]/g, "") // Arabic diacritics and tatweel
    .replace(/[أإآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي");
  return (s.match(/[\p{L}\p{N}']+/gu) || [])
    .map((w) => w.replace(/^'+|'+$/g, ""))
    .filter(Boolean)
    .map((w) => (/[a-z]/i.test(fold(w)) ? fold(w).replace(/(ees|ee|es|er|e|s)$/, "") : w.replace(/^(وال|بال|ال|و|ب)(?=.{3,})/, "")));
}

const GLOSSARY_MAP = new Map();
GLOSSARY.forEach((entry) => {
  const target = tokens(entry.fr);
  [entry.fr, ...entry.variants].forEach((v) => tokens(v).forEach((t) => GLOSSARY_MAP.set(t, target)));
});

function chunkTokens(chunk) {
  if (!chunk._tokens) {
    const concept = COURSE.concepts[chunk.concept];
    const titles = concept ? `${concept.title.fr} ${concept.title.en} ${concept.title.ar}` : "";
    chunk._tokens = new Set(tokens(`${chunk.title} ${titles} ${chunk.text}`).filter((t) => t.length > 1));
  }
  return chunk._tokens;
}

/** Language plan: which chunk languages a learner language may read. */
function languagePlan(lang) {
  if (lang === "ARABIC") return ["ARABIC", "FRENCH"];
  if (lang === "ENGLISH") return ["ENGLISH", "FRENCH"];
  if (lang === "DARIJA" || lang === "ARABIZI") return ["FRENCH"];
  return ["FRENCH", "ENGLISH", "ARABIC"];
}

function visible(chunk, ctx) {
  if (!chunk.course_id || chunk.course_id === (ctx.course_id || COURSE.id)) {
    if (!chunk.visibility || chunk.visibility === "public") return true;
    if (chunk.visibility === "class") return Boolean(ctx.class_id) && chunk.class_id === ctx.class_id;
    if (chunk.visibility === "private") return Boolean(ctx.learner_id) && chunk.learner_id === ctx.learner_id;
  }
  return false;
}

function retrieve(question, lang, ctx, materials, k = 2) {
  const q = [...new Set(tokens(question).filter((t) => t.length > 1 && !STOP.has(t)))];
  if (!q.length) return { hits: [], expansion: [] };
  const expansion = [...new Set(q.flatMap((t) => GLOSSARY_MAP.get(t) || []))].filter((t) => !q.includes(t));
  const plan = languagePlan(lang);
  const pool = [...CHUNKS, ...materials].filter((c) => visible(c, ctx) && (c.source === "professor" || c.source === "upload" || plan.includes(c.language)));
  // IDF over the visible pool: a word every chunk has ("dérivée") weighs little.
  const df = (t) => pool.reduce((n, c) => n + (chunkTokens(c).has(t) ? 1 : 0), 0);
  const idf = (t) => Math.log(1 + (pool.length + 1) / (1 + df(t)));
  const weight = new Map(q.map((t) => {
    const forms = [t, ...(GLOSSARY_MAP.get(t) || [])];
    return [t, Math.max(...forms.map(idf))];
  }));
  const total = [...weight.values()].reduce((a, b) => a + b, 0);
  const hits = pool.map((chunk) => {
    const set = chunkTokens(chunk);
    let matched = 0;
    let score = 0;
    q.forEach((t) => {
      const direct = set.has(t);
      const viaGlossary = (GLOSSARY_MAP.get(t) || []).some((g) => set.has(g));
      if (direct || viaGlossary) matched += weight.get(t);
      if (direct) score += weight.get(t);
      else if (viaGlossary) score += 0.5 * weight.get(t);
    });
    const coverage = matched / total;
    if (chunk.source === "professor") score *= PROFESSOR_BOOST;
    const languageBonus = chunk.language === plan[0] ? 0.05 : 0;
    return { chunk, coverage, score: score + languageBonus };
  }).filter((h) => h.coverage >= MIN_COVERAGE)
    .sort((a, b) => b.score - a.score || (b.chunk.source === "professor") - (a.chunk.source === "professor"));
  return { hits: hits.slice(0, k), expansion };
}

const sentences = (text) => String(text).match(/[^.!?؟]+[.!?؟]?/g)?.map((s) => s.trim()).filter(Boolean) || [];

// ---------- exercises ----------
function localizedExercise(ex, lang) {
  const l = shortLang(lang);
  return { id: ex.id, concept: ex.concept, level: ex.level, statement: ex.statement[l] || ex.statement.fr, expression: ex.expression };
}
const GENERIC = new Set(["derive", "deriv", "fonction", "function", "rul", "regl"]);
const contentTokens = (text) => tokens(text).filter((w) => w.length > 2 && !STOP.has(w));
function conceptsIn(text) {
  const t = new Set(contentTokens(text).flatMap((w) => [w, ...(GLOSSARY_MAP.get(w) || [])]));
  return Object.keys(COURSE.concepts).filter((id) => {
    const title = COURSE.concepts[id].title;
    return contentTokens(`${id} ${title.fr} ${title.en}`).some((w) => t.has(w) && !GENERIC.has(w));
  });
}
/** True when no word of the message belongs to the course or the glossary. */
function offTopic(text) {
  const vocab = new Set(CHUNKS.flatMap((c) => [...chunkTokens(c)]));
  return !contentTokens(text).some((w) => vocab.has(w) || GLOSSARY_MAP.has(w));
}
function chooseExercise({ concept, difficulty = 1, exclude = [] }) {
  const pool = EXERCISES.filter((e) => !exclude.includes(e.id) && (!concept || e.concept === concept));
  if (!pool.length) return null;
  return [...pool].sort((a, b) => Math.abs(a.level - difficulty) - Math.abs(b.level - difficulty))[0];
}

// ---------- engine ----------
function createFakeEngine(options = {}) {
  const mode = SPEED[options.mode] ? options.mode : "normal";
  const sleep = options.sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
  const scale = options.speed ?? SPEED[mode];
  const cache = new Map(); // key -> { body, request_ids:Set }
  const materials = []; // runtime chunks (uploads, professor material)
  const roomRuns = new Map(); // room_id -> { started, avg }
  const learnerWaiting = new Map(); // learner_id -> token
  let quotaCalls = [];

  const wait = (ms) => (ms > 0 ? sleep(Math.round(ms * scale)) : Promise.resolve());

  function quotaLow() {
    const now = Date.now();
    quotaCalls = quotaCalls.filter((t) => now - t < 60_000);
    return mode === "degraded" || quotaCalls.length >= 18;
  }

  async function run(task, inputs = {}, context = {}, priority = "INTERACTIVE", onEvent) {
    if (!TASKS.includes(task)) throw new RangeError(`unknown task: ${task}`);
    const missing = (REQUIRED[task] || []).filter((k) => inputs[k] === undefined || inputs[k] === null || inputs[k] === "");
    if (missing.length) throw new RangeError(`missing input: ${missing.join(", ")}`);

    const started = Date.now();
    const events = [];
    const notes = {};
    const request_id = crypto.randomUUID();
    const emit = (name, data = {}) => {
      const ev = { name, t_ms: Date.now() - started, data };
      events.push(ev);
      try { onEvent?.(ev); } catch { /* a UI callback never breaks the run */ }
    };
    const ctx = { ...context, language: normLang(context.language) };
    const forced = ctx.fake_outcome || null;
    const base = {
      request_id, task, text: null, data: null, model_class_used: null, model: null, from_cache: false,
      degraded: false, degrade_reason: null, eta_seconds: null, mode: null, followup_available: false,
      tokens_in: 0, tokens_out: 0, repaired: false, safe_default: false, route_reason: "", notes
    };
    const finish = (r) => {
      const result = { ...base, ...r, events, latency_ms: Date.now() - started };
      if (result.degraded) notes.degrade_reasons = [result.degrade_reason];
      return result;
    };
    const llm = (cls, tokensIn, tokensOut) => {
      quotaCalls.push(Date.now());
      return { model_class_used: cls, model: `fake-${cls}`, tokens_in: tokensIn, tokens_out: tokensOut };
    };

    // ----- fairness: one waiting request per learner, one running per room -----
    const room = priority === "INTERACTIVE" && ctx.room_id ? String(ctx.room_id) : null;
    if (room && (roomRuns.get(room)?.running || forced === "room_busy")) {
      const info = roomRuns.get(room) || { avg: 4000, started: Date.now() };
      const eta = Math.max(1, Math.round((info.avg - (Date.now() - info.started)) / 1000));
      return finish({ degraded: true, degrade_reason: "room_busy", eta_seconds: eta });
    }
    let token = null;
    if (priority === "INTERACTIVE" && ctx.learner_id) {
      token = Symbol(request_id);
      learnerWaiting.set(ctx.learner_id, token);
    }
    if (room) roomRuns.set(room, { ...(roomRuns.get(room) || { avg: 4000 }), running: true, started: Date.now() });

    try {
      if (forced === "service_busy" || forced === "quota_exhausted") {
        emit("queued", { position: 3, eta_s: 12 });
        await wait(500);
        emit("gave_up", { reason: forced });
        return finish({ degraded: true, degrade_reason: forced, eta_seconds: forced === "quota_exhausted" ? 42 : 12, text: task === "explain" || task === "hint" ? pick(TEMPLATES.framing, ctx.language) : null });
      }
      if (["classify", "hint", "grade", "check_claim", "check_answer", "explain", "ocr_transcribe", "generate_page"].includes(task) && token) {
        emit("queued", { position: 1, eta_s: Math.round(scale * 2) });
        await wait(250);
        if (learnerWaiting.get(ctx.learner_id) !== token) return finish({ degraded: true, degrade_reason: "superseded" });
      }
      if (token && learnerWaiting.get(ctx.learner_id) === token) learnerWaiting.delete(ctx.learner_id);

      const out = await TASK_IMPL[task]({ inputs, ctx, emit, wait, llm, notes, forced, priority, request_id });
      return finish(out);
    } finally {
      if (room) {
        const info = roomRuns.get(room);
        const spent = Date.now() - (info?.started || Date.now());
        roomRuns.set(room, { running: false, avg: Math.round(((info?.avg || spent) + spent) / 2), started: 0 });
      }
    }
  }

  const TASK_IMPL = {
    async classify({ inputs, ctx, emit, wait, llm, notes }) {
      const message = String(inputs.message);
      const lower = message.trim().toLowerCase().replace(/[!.?]+$/, "");
      const language = detectLanguage(message) || ctx.language || "FRENCH";
      if (GREETINGS.has(lower)) {
        emit("answered_by_rules", { rule: "greeting" });
        return { data: { intent: "CHITCHAT", language, confusion: "LOW", complexity: 1, confidence: 1.0 }, route_reason: "rules: greeting" };
      }
      emit("generating", { attempt: 1, model_class: "compact" });
      await wait(700);
      let intent = "EXPLAIN";
      if (/\b(hint|indice|aide[- ]?moi|help me|coup de pouce|clue)\b|مساعدة|تلميح/i.test(message)) intent = "HINT";
      else if (/\b(test de niveau|level test|diagnostic|mon niveau|my level)\b|مستواي|اختبار مستوى/i.test(message)) intent = "DIAGNOSTIC";
      else if (/\b(quiz|exercice|exercise|exo|entra[iî]ne|practi[cs]e|interroge|test me|problem)\b|تمرين|اختبرني/i.test(message)) intent = "QUIZ";
      else if (/^\s*(f\s*['′’]\s*\(x\)\s*=|y\s*['′’]\s*=|=)?\s*[-+0-9x^*/().,\s√²³]+$/i.test(message) && /\d|x/.test(message)) intent = "ANSWER_ATTEMPT";
      else if (offTopic(message) && !retrieve(message, language, ctx, materials).hits.length && message.split(/\s+/).length > 2) intent = "OFF_TOPIC";
      let confusion = message.includes("?") && message.length > 60 ? "MEDIUM" : "LOW";
      if (CONFUSED.test(message)) confusion = "HIGH";
      notes.raw = { intent, language, confusion };
      const complexity = Math.min(5, 1 + Math.floor(message.length / 60) + (confusion === "HIGH" ? 1 : 0));
      return { ...llm("compact", 820, 38), data: { intent, language, confusion, complexity, confidence: 0.8 }, route_reason: "classify → compact (routing table)" };
    },

    async hint({ inputs, ctx, emit, wait, llm }) {
      const lang = ctx.language || detectLanguage(inputs.problem);
      if (Array.isArray(inputs.stored_hints) && inputs.stored_hints.length) {
        const level = Math.min(inputs.stored_hints.length, Math.max(1, Number(inputs.hint_level) || 1));
        emit("answered_by_rules", { rule: "stored_hint", hint_level: level });
        return { text: String(inputs.stored_hints[level - 1]), data: { hint_level: level, hints_total: inputs.stored_hints.length, source: "bank" }, route_reason: "rules: stored hint" };
      }
      emit("generating", { attempt: 1, model_class: "compact" });
      await wait(900);
      const l = shortLang(lang);
      const text = { fr: "Quelle règle du cours s’applique à la forme de cette fonction ?", en: "Which rule from the course matches the shape of this function?", ar: "ما القاعدة من الدرس التي تناسب شكل هذه الدالة؟" }[l];
      return { ...llm("compact", 225, 22), text, data: { source: "model" }, route_reason: "hint → compact" };
    },

    async explain({ inputs, ctx, emit, wait, llm, notes, forced, request_id }) {
      const question = [inputs.passage, inputs.question].filter(Boolean).join(" — ");
      const lang = ctx.language || detectLanguage(inputs.question);
      emit("retrieving", {});
      await wait(150);
      // Like the engine: search on the selected passage and the question first, the board only if that finds nothing.
      let { hits, expansion } = retrieve(`${inputs.passage || ""} ${inputs.question}`, lang, ctx, materials);
      if (!hits.length && inputs.board_text) ({ hits, expansion } = retrieve(`${inputs.question} ${String(inputs.board_text).slice(0, 400)}`, lang, ctx, materials));
      if (expansion.length) emit("expanding_query", { terms: expansion });
      if (!hits.length || forced === "not_found") {
        emit("no_relevant_context", {});
        return { text: pick(TEMPLATES.notFound, lang), data: { not_found: true }, route_reason: "retrieval gate: nothing relevant" };
      }
      notes.retrieved = hits.map((h) => ({ chunk_id: h.chunk.id, title: h.chunk.title, coverage: Math.round(h.coverage * 100) / 100, source: h.chunk.source || "course" }));
      const top = hits[0].chunk;
      const opener = pick(TEMPLATES.opener, lang)[ctx.confusion || "MEDIUM"] || pick(TEMPLATES.opener, lang).MEDIUM;
      const exercise = chooseExercise({ concept: top.concept, difficulty: Number(ctx.difficulty) || 1, exclude: inputs.exclude_exercises || [] });
      const practice = exercise ? `${pick(TEMPLATES.yourTurn, lang)} ${localizedExercise(exercise, lang).statement}` : "";
      const partial = sentences(top.text).slice(0, 3).join(" ");
      emit("partial_result", { sentences: sentences(top.text).slice(0, 3), chunk_id: top.id });

      const key = crypto.createHash("sha256").update(JSON.stringify(["explain", question.normalize("NFC").replace(/\s+/g, " "), bodyLang(lang), ctx.level, ctx.style, hits.map((h) => h.chunk.id), inputs.board_text ? crypto.createHash("sha1").update(inputs.board_text).digest("hex") : null])).digest("hex");
      emit("checking_cache", {});
      const hit = cache.get(key);
      const decorate = (body) => [opener, body, practice].filter(Boolean).join("\n\n");
      const exerciseData = exercise ? localizedExercise(exercise, lang) : null;
      if (hit && !forced) {
        hit.request_ids.add(request_id);
        emit("cache_hit", {});
        return { text: decorate(hit.body), data: { body: hit.body, opener, exercise: exerciseData }, from_cache: true, model_class_used: "midsize", model: "fake-midsize", route_reason: "cache hit (preferred class: midsize)" };
      }

      const fast = (reason, eta = null) => {
        emit("fast_extractive", { reason });
        const excerpts = hits.flatMap((h) => sentences(h.chunk.text).slice(0, 3).map((text) => ({ chunk_id: h.chunk.id, text }))).slice(0, 5);
        const framing = pick(TEMPLATES.framing, lang);
        return {
          text: [opener, framing, excerpts.map((e) => `• ${e.text}`).join("\n"), practice].filter(Boolean).join("\n\n"),
          data: { framing, excerpts, exercise: exerciseData }, mode: "fast_extractive", followup_available: true,
          degraded: true, degrade_reason: reason, eta_seconds: eta, route_reason: `explain → fast mode (${reason})`
        };
      };
      const escalated = ctx.escalate === true || Number(ctx.complexity) >= 4;
      if (forced === "quota_low" || (quotaLow() && !escalated)) return fast("quota_low", 30);
      if (forced === "midsize_unavailable") { emit("falling_back_to_compact", {}); return fast("midsize_unavailable", 30); }

      emit("generating", { attempt: 1, model_class: "midsize" });
      await wait(1800);
      if (forced === "deadline") return fast("deadline");
      const l = bodyLang(lang);
      const sourceForBody = hits.find((h) => shortLang(h.chunk.language) === l)?.chunk || top;
      let body = sentences(sourceForBody.text).slice(0, 4).join(" ");
      if (l !== shortLang(sourceForBody.language)) notes.body_language_note = "fake engine: body taken from the closest course language";
      const out = { ...llm("midsize", 430, 140), text: decorate(body), data: { body, opener, exercise: exerciseData }, route_reason: "explain → midsize (default)" };
      if (forced === "wrong_math") {
        body += " Donc (x³)′ = x².";
        notes.formula_checks = [{ claim: "(x³)′ = x²", verdict: "false" }];
        return { ...out, text: decorate(body), data: { ...out.data, body }, degraded: true, degrade_reason: "wrong_math" };
      }
      cache.set(key, { body, request_ids: new Set([request_id]) });
      notes.partial = partial;
      return out;
    },

    async grade({ inputs, ctx, emit, wait, llm }) {
      const lang = ctx.language || "FRENCH";
      const attempt = M.parse(inputs.attempt);
      const solution = M.parse(inputs.solution);
      const fb = (verdict) => pick3(TEMPLATES.feedback[verdict], lang);
      if (attempt && solution) {
        emit("answered_by_rules", { rule: "sympy" });
        if (M.sameFunction(attempt, solution)) return { data: { verdict: "CORRECT", mistake: null, feedback: fb("CORRECT"), graded_by: "sympy" }, route_reason: "rules: equivalence" };
        const mistakes = (inputs.common_mistakes || []).map((m) => (Array.isArray(m) ? { expr: m[0], feedback: m[1] } : m));
        const known = mistakes.find((m) => { const f = M.parse(m.expr); return f && M.sameFunction(f, attempt); });
        return { data: { verdict: "INCORRECT", mistake: known ? known.expr : null, feedback: known ? known.feedback : fb("INCORRECT"), graded_by: "sympy" }, route_reason: "rules: equivalence" };
      }
      emit("generating", { attempt: 1, model_class: "compact" });
      await wait(900);
      const a = tokens(inputs.attempt);
      const s = new Set(tokens(inputs.solution));
      const overlap = a.filter((w) => s.has(w)).length / Math.max(1, s.size);
      const verdict = overlap > 0.8 ? "CORRECT" : overlap > 0.35 ? "PARTIAL" : "INCORRECT";
      return { ...llm("compact", 300, 60), data: { verdict, mistake: null, feedback: fb(verdict === "PARTIAL" ? "INCORRECT" : verdict), graded_by: "llm" }, route_reason: "grade → compact (text answer)" };
    },

    async quiz({ inputs, ctx, emit }) {
      const lang = ctx.language || detectLanguage(inputs.message || "") || "FRENCH";
      emit("picking_exercise", {});
      const requested = inputs.concept ? [inputs.concept] : conceptsIn(inputs.message || "");
      const exclude = inputs.exclude || [];
      let exercises;
      if (inputs.diagnostic) {
        const used = new Set();
        exercises = [1, 2, 3].map((level) => {
          const ex = EXERCISES.find((e) => e.level === level && !used.has(e.concept) && !exclude.includes(e.id)) || EXERCISES.find((e) => e.level === level);
          used.add(ex.concept);
          return ex;
        });
      } else {
        const ex = chooseExercise({ concept: requested[0], difficulty: Number(inputs.difficulty) || 1, exclude }) || chooseExercise({ difficulty: Number(inputs.difficulty) || 1, exclude });
        exercises = ex ? [ex] : [];
      }
      const list = exercises.map((e) => localizedExercise(e, lang));
      const intro = inputs.intro === false ? "" : pick(inputs.diagnostic ? TEMPLATES.diagnosticIntro : TEMPLATES.quizIntro, lang);
      return { text: [intro, ...list.map((e) => e.statement)].filter(Boolean).join("\n"), data: { exercises: list, requested_concepts: requested, diagnostic: Boolean(inputs.diagnostic), intro }, mode: "bank", route_reason: "quiz → exercise bank (0 requests)" };
    },

    async compress({ inputs, emit }) {
      emit("compressing", {});
      const chunks = Array.isArray(inputs.chunks) ? inputs.chunks : [];
      return { data: { sentences: chunks.flatMap((c) => sentences(c.text || c).slice(0, 2)) }, route_reason: "compress → lexical fallback" };
    },

    async ocr_transcribe({ inputs, emit, wait }) {
      const image = String(inputs.image || "");
      if (!/^data:image\/(png|jpe?g|webp|gif);base64,/.test(image) || image.length < 200) {
        return { data: { text: "", latex: "", confidence_note: "" }, degraded: true, degrade_reason: "invalid_image", route_reason: "vision: not a readable image" };
      }
      emit("generating", { attempt: 1, model_class: "vision" });
      await wait(1200);
      return {
        text: "", data: { text: "", latex: "", confidence_note: "The built-in fake engine cannot read images. Install the EdNova engine to transcribe handwriting." },
        degraded: true, degrade_reason: "low_confidence", model_class_used: "vision", model: "fake-vision", route_reason: "ocr_transcribe → vision"
      };
    },

    async check_claim({ inputs, ctx, emit, wait, llm }) {
      const claim = String(inputs.claim);
      const lang = ctx.language || detectLanguage(claim);
      const l = shortLang(lang);
      const say = (fr, en, ar) => ({ fr, en, ar })[l];
      // Known misconception: (uv)' = u'v'
      if (/\(\s*u\s*[·*.]?\s*v\s*\)\s*['′’]\s*=\s*u\s*['′’]\s*[·*.]?\s*v\s*['′’]\s*$/i.test(claim.replace(/\s+/g, " ").trim())) {
        emit("answered_by_rules", { rule: "formula" });
        return { data: { verdict: "CONTRADICTED", correct_form: "(u·v)′ = u′·v + u·v′", explanation: say("La dérivée d’un produit n’est pas le produit des dérivées.", "The derivative of a product is not the product of the derivatives.", "مشتقة الجداء ليست جداء المشتقتين."), evidence: [], checked_by: "rules" }, route_reason: "rules: formula" };
      }
      // Derivative claims: (E)' = D, "derivative of E is D", "dérivée de E est D"
      const m = claim.match(/^\s*\(?(.+?)\)?\s*['′’]\s*=\s*(.+)$/) ||
        claim.match(/(?:derivative of|dérivée de|derivee de|مشتقة)\s*(?:f\s*\(x\)\s*=\s*)?(.+?)\s*(?:is|est|=|هي)\s*(.+)$/i);
      if (m) {
        const verdict = M.isDerivative(m[1], m[2]);
        if (verdict !== null) {
          emit("answered_by_rules", { rule: "sympy" });
          const bankEx = EXERCISES.find((e) => { const f = M.parse(e.expression); const g = M.parse(m[1]); return f && g && M.sameFunction(f, g); });
          return {
            data: {
              verdict: verdict ? "SUPPORTED" : "CONTRADICTED",
              correct_form: verdict ? null : bankEx ? `(${bankEx.expression})′ = ${bankEx.solution}` : null,
              explanation: verdict ? say("La formule est vérifiée.", "The formula checks out.", "الصيغة صحيحة.") : say("Cette dérivée est fausse.", "This derivative is wrong.", "هذه المشتقة خاطئة."),
              evidence: [], checked_by: "sympy"
            },
            route_reason: "rules: formula check"
          };
        }
      }
      emit("retrieving", {});
      await wait(200);
      const { hits } = retrieve(claim, lang, ctx, materials);
      if (!hits.length) {
        emit("no_relevant_context", {});
        return { data: { verdict: "NOT_IN_COURSE", correct_form: null, explanation: say("Ce point n’apparaît pas dans les supports du cours.", "This isn’t covered in the course material.", "هذه النقطة غير موجودة في مراجع الدرس."), evidence: [], checked_by: "retrieval" }, route_reason: "retrieval gate" };
      }
      emit("generating", { attempt: 1, model_class: "compact" });
      await wait(1000);
      const evidence = hits.map((h) => ({ chunk_id: h.chunk.id, text: sentences(h.chunk.text)[0] }));
      return { ...llm("compact", 520, 70), data: { verdict: hits[0].coverage > 0.6 ? "SUPPORTED" : "PARTLY", correct_form: null, explanation: say("Le cours aborde ce point ; compare avec le passage ci-dessous.", "The course covers this; compare with the passage below.", "الدرس يتناول هذه النقطة؛ قارن مع المقطع أدناه."), evidence, checked_by: "llm" }, route_reason: "check_claim → compact" };
    },

    async check_answer({ inputs, ctx, emit, wait, llm }) {
      const lang = ctx.language || "FRENCH";
      const l = shortLang(lang);
      const lines = (Array.isArray(inputs.lines) ? inputs.lines : String(inputs.lines).split("\n")).map((s) => String(s).trim()).filter(Boolean).slice(0, 20);
      const fnText = String(inputs.problem).match(/f\s*\(\s*x\s*\)\s*=\s*([^.,;]+?)(?:\s*(?:pour|for|من|\.|$))/i)?.[1] || inputs.expression || "";
      const f = M.parse(fnText);
      const d = f && M.derivative(f);
      emit("answered_by_rules", { rule: "line_check" });
      await wait(300);
      let previous = null;
      const out = lines.map((text, index) => {
        const right = text.includes("=") ? text.split("=").pop() : text;
        const g = M.parse(right);
        if (!g || !d) return { index, text, status: "UNCHECKED" };
        const ok = M.sameLoose(g, d) || (previous && M.sameLoose(g, previous));
        if (ok) previous = g;
        return { index, text, status: ok ? "OK" : "WRONG" };
      });
      const firstWrong = out.find((x) => x.status === "WRONG");
      let question = null;
      if (firstWrong) {
        emit("generating", { attempt: 1, model_class: "compact" });
        await wait(800);
        question = { fr: `Ligne ${firstWrong.index + 1} : quelle règle du cours as-tu appliquée ici ? Vérifie chaque terme.`, en: `Line ${firstWrong.index + 1}: which rule did you apply here? Check each term.`, ar: `السطر ${firstWrong.index + 1}: ما القاعدة التي طبّقتها هنا؟ تحقّق من كل حدّ.` }[l];
      }
      const verdict = !out.length || out.every((x) => x.status === "UNCHECKED") ? "UNCHECKED" : firstWrong ? "WRONG" : "OK";
      return { ...(firstWrong ? llm("compact", 400, 40) : {}), data: { lines: out, verdict, question, function: fnText || null }, route_reason: firstWrong ? "check_answer → compact (guiding question)" : "rules: line check" };
    },

    async prep_plan({ inputs, ctx }) {
      const mastery = inputs.mastery || {};
      const days = Math.max(1, Math.min(30, Number(inputs.days_left) || 5));
      const perDay = Math.max(10, Math.min(240, Number(inputs.minutes_per_day) || 40));
      const ids = Object.keys(COURSE.concepts);
      const order = [];
      const done = new Set();
      while (order.length < ids.length) {
        const ready = ids.filter((id) => !done.has(id) && COURSE.concepts[id].requires.every((r) => done.has(r)));
        const next = (ready.length ? ready : ids.filter((id) => !done.has(id))).sort((a, b) => (mastery[a] ?? 0) - (mastery[b] ?? 0))[0];
        order.push(next);
        done.add(next);
      }
      const l = shortLang(ctx.language);
      const items = order.map((id) => {
        const m = mastery[id] ?? 0;
        const kind = m >= 0.8 ? "review" : "learn";
        return { concept: id, title: COURSE.concepts[id].title[l], kind, minutes: kind === "review" ? 8 : 20, mastery: m };
      });
      const sessions = Array.from({ length: days }, (_, i) => ({ day: i + 1, items: [], minutes: 0 }));
      let day = 0;
      items.forEach((item) => {
        if (sessions[day].minutes + item.minutes > perDay && day < days - 1) day++;
        sessions[day].items.push(item);
        sessions[day].minutes += item.minutes;
      });
      const weak = items.filter((i) => i.kind === "learn").map((i) => i.concept);
      const assessment = EXERCISES.filter((e) => weak.includes(e.concept)).slice(0, 10).map((e) => e.id);
      return { data: { course_id: COURSE.id, order, sessions: sessions.filter((s) => s.items.length), assessment, days, minutes_per_day: perDay }, route_reason: "prep_plan: rules (0 requests)" };
    },

    async generate_page({ inputs, ctx, emit, wait, llm, priority }) {
      const concept = COURSE.concepts[inputs.concept] ? inputs.concept : "produit";
      const lang = ctx.language || "FRENCH";
      const l = shortLang(lang);
      emit("retrieving", {});
      await wait(priority === "BACKGROUND" ? 600 : 300);
      const chunk = CHUNKS.find((c) => c.concept === concept && shortLang(c.language) === bodyLang(lang)) || CHUNKS.find((c) => c.concept === concept);
      emit("generating", { attempt: 1, model_class: "midsize" });
      await wait(1500);
      const worked = EXERCISES.find((e) => e.concept === concept);
      const quiz = EXERCISES.filter((e) => e.concept === concept && e.id !== worked?.id).concat(EXERCISES.filter((e) => e.concept !== concept && COURSE.concepts[concept].requires.includes(e.concept))).slice(0, 3);
      return {
        ...llm("midsize", 600, 180),
        data: {
          concept, title: COURSE.concepts[concept].title[l], explanation: chunk ? sentences(chunk.text).join(" ") : "",
          worked_example: worked ? { id: worked.id, statement: worked.statement[l] || worked.statement.fr, steps: worked.hints[l] || worked.hints.fr, answer: worked.solution } : null,
          quiz: quiz.map((e) => localizedExercise(e, lang))
        },
        route_reason: "generate_page → midsize (background)"
      };
    }
  };

  return {
    kind: "fake",
    mode,
    tasks: TASKS,
    run,
    followUp(classification = {}) {
      const intent = classification.intent;
      if (classification.confusion === "HIGH" && (intent === "QUIZ" || intent === "DIAGNOSTIC")) return { task: "explain", reason: "HIGH confusion → explain first", overridden: true };
      const map = { EXPLAIN: "explain", HINT: "hint", ANSWER_ATTEMPT: "hint", QUIZ: "quiz", DIAGNOSTIC: "quiz" };
      return map[intent] ? { task: map[intent], reason: `intent ${intent} → ${map[intent]}`, overridden: false } : { task: null, reason: `intent ${intent} → no follow-up`, overridden: false };
    },
    async getExercise(id) {
      const ex = EXERCISES.find((e) => e.id === id);
      if (!ex) return null;
      return { ...ex, hints: ex.hints, common_mistakes: ex.mistakes.map((m) => ({ expr: m.expr, feedback: m.feedback })) };
    },
    async course(id = COURSE.id) {
      return id === COURSE.id ? COURSE : null;
    },
    async addMaterial({ id, course_id, title, text, visibility, class_id, learner_id, source }) {
      if (!String(text || "").trim()) return { chunks: [], needs_ocr: true, detail: "The built-in engine only reads text. Install the EdNova engine to read Word files and scanned pages." };
      const parts = String(text || "").split(/\n(?=#{1,3}\s)/).map((p) => p.trim()).filter((p) => p.length > 20);
      const chunks = (parts.length ? parts : [String(text || "")]).filter(Boolean).slice(0, 200).map((part, i) => {
        const heading = part.match(/^#{1,3}\s+(.+)/)?.[1]?.trim();
        const body = part.replace(/^#{1,3}\s+.+\n?/, "").trim().slice(0, 1600);
        const concepts = conceptsIn(`${heading || ""} ${body}`);
        return {
          id: `${id}#${i + 1}`, material_id: id, course_id, title: heading || title, text: body, language: detectLanguage(body),
          concept: concepts[0] || null, concept_confidence: concepts.length ? (concepts.length === 1 ? 0.8 : 0.5) : 0,
          visibility, class_id, learner_id, source
        };
      });
      for (let i = materials.length - 1; i >= 0; i--) if (materials[i].material_id === id) materials.splice(i, 1);
      materials.push(...chunks);
      return {
        chunks: chunks.map(({ id: cid, title: t, concept, concept_confidence }) => ({ id: cid, title: t, concept, confidence: concept_confidence })),
        needs_ocr: !chunks.length,
        detail: chunks.length ? null : "The built-in engine only reads text. Install the EdNova engine to read Word files and scanned pages."
      };
    },
    async removeMaterial(id) {
      for (let i = materials.length - 1; i >= 0; i--) if (materials[i].material_id === id) materials.splice(i, 1);
      return true;
    },
    async invalidate(requestId) {
      let removed = 0;
      cache.forEach((entry, key) => { if (entry.request_ids.has(requestId)) { cache.delete(key); removed++; } });
      return { removed };
    },
    async health() {
      return { ok: true, engine: "fake", mode, version: "fake-1.3" };
    },
    async close() {}
  };
}

module.exports = { createFakeEngine, detectLanguage, TASKS, retrieve, tokens };
