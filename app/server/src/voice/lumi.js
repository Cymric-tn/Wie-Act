// "Ask Lumi" in a group voice call: a member holds the button, speaks, releases; everyone in the voice channel gets the
// question and Lumi's answer as text, and each browser reads the answer aloud.
//
//   audio --(bridge /voice/transcribe, faster-whisper, no LLM)--> transcript + language
//         --(engine.run classify -> follow_up -> explain | hint | quiz, the tutor's own path)--> answer
//         --(speech.js)--> display text + spoken text --> lumi:reply to the room
//
// The answer goes through engine.run like every other AI request of the app (gatekeeper, cache, quota pacing, metrics,
// review flags), with the room's whiteboard text as explain's board_text and the last voice turns as its history.
// Chit-chat and off-topic questions get a fixed line and cost no model call beyond classify.
//
// Guards: member of the voice channel, tier (canUseLumiVoice), audio type and size, one question in flight per room,
// one question per user every 5 s, an overall deadline. The audio is never stored.

const crypto = require("crypto");
const { forSpeech, line, guessLang } = require("./speech");
const { INPUTS } = require("../ai/routes");

const AUDIO_MAX = Number(process.env.LUMI_AUDIO_MAX_BYTES) || 2_097_152;
const TIMEOUT_MS = Number(process.env.LUMI_VOICE_TIMEOUT_MS) || 60_000; // STT (≤ 30 s) + classify + explain (bounded at 25 s)
const MIN_GAP_MS = 5_000;
const HISTORY_KEEP = 20;
const HISTORY_SENT = 4; // turns passed to explain (8 messages: INPUTS.explain keeps the last 8)
const BOARD_MAX = 2_000;
const COURSE = process.env.AI_COURSE || "analyse";

const isId = (v) => typeof v === "string" && /^[\w.:~-]{1,80}$/.test(v);
const drop = (obj) => Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined && v !== null && v !== ""));
const SHORT = { FRENCH: "fr", ENGLISH: "en", ARABIC: "ar", DARIJA: "ar", ARABIZI: "ar" };
const ENGINE_LANG = { fr: "FRENCH", en: "ENGLISH", ar: "ARABIC" };
const BUSY = new Set(["room_busy", "service_busy", "superseded", "midsize_saturated", "breaker_open"]);
const QUOTA = new Set(["quota_exhausted", "quota_low", "quota_wait"]);

/**
 * Freemium rule: humans talk for free, Lumi in voice is premium. The app has no tiers yet (accounts live in the
 * browser), so every member may ask. TODO(tiers): set user.tier on the server at sign-in and this check enforces it.
 */
function canUseLumiVoice(user) {
  return Boolean(user) && user.tier !== "free";
}

/** The room's whiteboard as plain text for the model: text items, sticky notes and equations, in reading order. */
function getWhiteboardSummary(room, max = BOARD_MAX) {
  if (!room?.items) return "";
  const order = new Map((room.meta?.pages || []).map((p, i) => [p.id, i]));
  const text = [...room.items.values()]
    .filter((i) => ((i.type === "text" || i.type === "note") && i.text?.trim()) || (i.type === "equation" && i.latex?.trim()))
    .sort((a, b) => (order.get(a.page) ?? 0) - (order.get(b.page) ?? 0) || (a.y ?? 0) - (b.y ?? 0) || (a.x ?? 0) - (b.x ?? 0))
    .map((i) => (i.type === "equation" ? `$${i.latex.trim()}$` : i.type === "note" ? `Note: ${i.text.trim()}` : i.text.trim()))
    .join("\n");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function toBuffer(audio) {
  if (Buffer.isBuffer(audio)) return audio;
  if (audio instanceof ArrayBuffer) return Buffer.from(audio);
  if (ArrayBuffer.isView(audio)) return Buffer.from(audio.buffer, audio.byteOffset, audio.byteLength);
  return null;
}

/** An error whose message is meant for the learner. */
function said(message, cause) {
  const error = new Error(message);
  error.userMessage = message;
  if (cause) error.cause = cause;
  return error;
}

function withTimeout(promise, ms) {
  let timer;
  const deadline = new Promise((_, reject) => {
    timer = setTimeout(() => reject(said("Lumi took too long to answer. Try again in a moment.")), ms);
  });
  return Promise.race([promise, deadline]).finally(() => clearTimeout(timer));
}

function createLumiVoice({
  io, voice, rooms, engine, run, log = () => {},
  canUse = canUseLumiVoice, timeoutMs = TIMEOUT_MS, audioMax = AUDIO_MAX, minGapMs = MIN_GAP_MS
}) {
  const busy = new Set(); // roomIds with a question in flight
  const lastAsk = new Map(); // userId -> ts
  const histories = new Map(); // roomId -> last HISTORY_KEEP replies (in memory; the run log keeps the engine side)
  const runTask = run || ((task, inputs, ctx, priority, onEvent) => engine.run(task, inputs, ctx, priority, onEvent));

  const history = (roomId) => histories.get(roomId) || [];

  /** A fixed line instead of a model answer (off-topic, busy, quota...). */
  const fixed = (name, lang, vars) => ({ text: line(name, lang, vars), task: null, degraded: name !== "offTopic", reason: name });

  function degradedLine(result, lang) {
    const reason = result?.degrade_reason || "";
    const s = Math.max(5, Math.round(result?.eta_seconds || 15));
    if (QUOTA.has(reason)) return fixed("quota", lang, { s });
    if (BUSY.has(reason)) return fixed("busy", lang, { s });
    return fixed("unavailable", lang);
  }

  async function answer(roomId, user, audio, mime, askedBy) {
    const channel = voice.channel(roomId);
    let heard;
    try {
      heard = await engine.transcribe(audio.toString("base64"), mime);
    } catch (error) {
      if (error instanceof RangeError) throw said("Didn't catch that, try again.");
      if (error.status === 503) throw said("Speech recognition isn't available on the server right now.", error);
      throw said("Lumi can't hear right now: the speech service is not answering.", error);
    }
    const transcript = String(heard?.text || "").trim().slice(0, 1000);
    if (transcript.length < 2) throw said("Didn't catch that, try again.");
    const heardLang = ["fr", "en", "ar"].includes(heard.lang) ? heard.lang : "en";
    io.to(channel).emit("lumi:thinking", { askedBy, stage: "answering", transcript });

    const ctx = drop({
      learner_id: isId(user.clientId) ? user.clientId : undefined,
      room_id: isId(roomId) ? roomId : undefined,
      course_id: COURSE,
      turn_id: crypto.randomUUID().slice(0, 12)
    });

    const out = await (async () => {
      const classification = await runTask("classify", { message: transcript }, ctx, "INTERACTIVE", null, { question: `voice: ${transcript}` });
      const c = classification?.data || {};
      const trusted = c.language && !classification.safe_default;
      const lang = (trusted && SHORT[c.language]) || heardLang;
      if (!classification?.data) return { ...degradedLine(classification, lang), lang };

      const follow = await engine.followUp(c, false, transcript).catch(() => ({ task: "explain" }));
      if (!follow?.task) return { ...fixed("offTopic", lang), lang };

      const turnCtx = drop({ ...ctx, language: (trusted && c.language) || ENGINE_LANG[heardLang], confusion: c.confusion, complexity: c.complexity });
      let task = follow.task;
      let inputs;
      if (task === "quiz" || task === "diagnostic") {
        inputs = INPUTS.quiz({ message: transcript, diagnostic: task === "diagnostic" || c.intent === "DIAGNOSTIC" });
        task = "quiz";
      } else if (task === "hint") {
        inputs = INPUTS.hint({ problem: transcript, attempt: transcript });
      } else {
        task = "explain";
        // No name leaves the server: the model only needs to tell the speakers apart, so each one is "Student n"
        // in the order they first spoke in the turns sent.
        const speakers = new Map();
        const label = (id) => speakers.get(id) || speakers.set(id, `Student ${speakers.size + 1}`).get(id);
        const prior = history(roomId).slice(-HISTORY_SENT).flatMap((h) => [
          { role: "user", content: `${label(h.askedBy.userId)}: ${h.transcript}` },
          { role: "assistant", content: h.reply }
        ]);
        const board = getWhiteboardSummary(rooms.get(roomId));
        inputs = INPUTS.explain({ question: transcript, board_text: board || undefined, history: prior });
      }
      const result = await runTask(task, inputs, turnCtx, "INTERACTIVE", null, { question: `voice: ${transcript}` });
      if (typeof result?.text !== "string" || !result.text.trim()) return { ...degradedLine(result, lang), lang };
      return { text: result.text, task, degraded: Boolean(result.degraded), reason: result.degrade_reason || null, request_id: result.request_id, lang };
    })();

    const { display, spoken } = forSpeech(out.text);
    return {
      id: crypto.randomUUID(),
      askedBy,
      transcript,
      reply: display,
      spoken,
      // The voice to read it with: the answer's own script decides (a Darija question can get a French answer body).
      lang: guessLang(display, out.lang),
      ts: Date.now(),
      task: out.task,
      degraded: out.degraded,
      degrade_reason: out.reason,
      request_id: out.request_id || null
    };
  }

  async function ask(socket, payload, ack) {
    const reply = typeof ack === "function" ? ack : () => {};
    const roomId = socket.data.voiceRoom;
    const user = socket.data.user;
    if (!roomId || !user || payload?.roomId !== roomId || !voice.voiceRooms.get(roomId)?.has(socket.id)) {
      return reply({ ok: false, error: "Join the voice channel first." });
    }
    if (!canUse(user)) return reply({ ok: false, error: "Lumi voice is a premium feature" });
    const audio = toBuffer(payload.audio);
    if (!audio || !audio.length) return reply({ ok: false, error: "No audio received." });
    if (audio.length > audioMax) return reply({ ok: false, error: "That recording is too long." });
    const mime = typeof payload.mimeType === "string" ? payload.mimeType.slice(0, 100) : "";
    if (!/^audio\/[\w.+-]+/.test(mime)) return reply({ ok: false, error: "Unsupported audio format." });
    if (busy.has(roomId)) return reply({ ok: false, error: "Lumi is answering, wait a moment" });
    const now = Date.now();
    const wait = minGapMs - (now - (lastAsk.get(user.clientId) || 0));
    if (wait > 0) return reply({ ok: false, error: `Wait ${Math.ceil(wait / 1000)} s before asking again.` });
    if (lastAsk.size > 5000) lastAsk.clear();
    lastAsk.set(user.clientId, now);

    busy.add(roomId);
    const askedBy = { userId: user.clientId, name: user.name, color: user.color };
    const channel = voice.channel(roomId);
    io.to(channel).emit("lumi:thinking", { askedBy, stage: "listening" });
    try {
      const entry = await withTimeout(answer(roomId, user, audio, mime, askedBy), timeoutMs);
      const list = [...history(roomId), entry].slice(-HISTORY_KEEP);
      histories.set(roomId, list);
      io.to(channel).emit("lumi:reply", entry);
      reply({ ok: true, id: entry.id });
    } catch (error) {
      const message = error.userMessage || "Lumi could not answer. Try again.";
      if (!error.userMessage || error.cause) log("Lumi voice:", error.cause?.message || error.message);
      // To the whole channel: everyone clears "Lumi is thinking"; the asker also shows the message.
      io.to(channel).emit("lumi:error", { message, askedBy });
      reply({ ok: false, error: message });
    } finally {
      busy.delete(roomId);
    }
  }

  function register(socket, on) {
    on("lumi:ask", (payload, ack) => {
      ask(socket, payload, ack).catch((error) => log("lumi:ask failed:", error));
    }, 20);
  }

  return { register, ask, history, canUse, busy };
}

module.exports = { createLumiVoice, canUseLumiVoice, getWhiteboardSummary };
