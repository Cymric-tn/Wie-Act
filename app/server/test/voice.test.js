// Group voice (signaling) and "Ask Lumi" in voice, against a stub engine: no bridge, no model, no microphone.
const { test, describe, before, after } = require("node:test");
const assert = require("node:assert/strict");
const { io: connectClient } = require("socket.io-client");
const { createServer } = require("../src/app");
const { forSpeech, cutAtSentence, guessLang, line } = require("../src/voice/speech");
const { cleanSignal } = require("../src/voice/signaling");
const { getWhiteboardSummary } = require("../src/voice/lumi");

const quiet = () => {};
const sockets = [];
let seq = 0;
const uid = (p = "id") => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`;

function once(socket, event, timeout = 2000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timed out waiting for ${event}`)), timeout);
    socket.once(event, (data) => {
      clearTimeout(timer);
      resolve(data);
    });
  });
}

function never(socket, event, window = 250) {
  return new Promise((resolve, reject) => {
    const handler = (data) => reject(new Error(`unexpected ${event}: ${JSON.stringify(data)}`));
    socket.once(event, handler);
    setTimeout(() => {
      socket.off(event, handler);
      resolve();
    }, window);
  });
}

const emit = (socket, event, payload) => new Promise((resolve) => socket.emit(event, payload, resolve));

/** A stub of the engine surface Lumi voice uses; `calls` records every run. */
function stubEngine() {
  const engine = {
    calls: [],
    transcribeImpl: async () => ({ text: "c'est quoi le nombre dérivé ?", lang: "fr" }),
    intent: "EXPLAIN",
    delayMs: 0,
    status: () => ({ backend: "fake", connected: true }),
    close() {},
    async transcribe(audio, mime) {
      engine.calls.push({ task: "transcribe", mime, bytes: Buffer.from(audio, "base64").length });
      return engine.transcribeImpl();
    },
    async followUp(c) {
      const tasks = { EXPLAIN: "explain", QUIZ: "quiz", CHITCHAT: null, OFF_TOPIC: null };
      return { task: c.intent in tasks ? tasks[c.intent] : "explain" };
    },
    async run(task, inputs, ctx) {
      engine.calls.push({ task, inputs, ctx });
      if (engine.delayMs) await new Promise((r) => setTimeout(r, engine.delayMs));
      const base = { request_id: uid("req"), task, degraded: false, from_cache: false, events: [] };
      if (task === "classify") return { ...base, data: { intent: engine.intent, language: "FRENCH", confusion: "LOW", complexity: 2 } };
      if (engine.degrade) return { ...base, text: null, data: null, degraded: true, degrade_reason: engine.degrade, eta_seconds: 42 };
      return { ...base, text: "## Le nombre dérivé\n\n**Le nombre dérivé** de $f$ en $a$ est la limite du taux d'accroissement. 🙂\n- C'est la pente de la tangente." };
    }
  };
  return engine;
}

function harness(voice = {}) {
  const ctx = { engine: stubEngine() };
  before(async () => {
    ctx.server = createServer({ log: quiet, ai: { engine: ctx.engine }, voice: { minGapMs: 0, ...voice } });
    ctx.port = await ctx.server.listen(0, "127.0.0.1");
  });
  after(async () => {
    sockets.splice(0).forEach((s) => s.disconnect());
    await ctx.server.close();
  });
  ctx.connect = () => {
    const s = connectClient(`http://localhost:${ctx.port}`, { transports: ["websocket"], forceNew: true });
    sockets.push(s);
    return s;
  };
  /** A socket that has joined the board room (and, with voice: true, its voice channel). */
  ctx.member = async (room, name = "Ana", { voice: inVoice = true } = {}) => {
    const s = ctx.connect();
    const joined = once(s, "board:joined");
    s.emit("board:join", { room, name, clientId: uid("c") });
    s.session = await joined;
    if (inVoice) s.voice = await emit(s, "voice:join", { roomId: room });
    return s;
  };
  return ctx;
}

const AUDIO = Buffer.alloc(4000, 7);

describe("voice signaling", () => {
  const h = harness({ maxPeers: 3 });

  test("joining needs the board room; the ack lists the others and the newcomer is announced", async () => {
    const room = uid("room");
    const outsider = h.connect();
    await once(outsider, "connect");
    const refused = await emit(outsider, "voice:join", { roomId: room });
    assert.equal(refused.ok, false);

    const a = await h.member(room, "Ana");
    assert.equal(a.voice.ok, true);
    assert.deepEqual(a.voice.peers, []);
    assert.ok(Array.isArray(a.voice.iceServers) && a.voice.iceServers.length);
    assert.equal(a.voice.lumi, true);

    const announced = once(a, "voice:peer-joined");
    const b = await h.member(room, "Ben");
    assert.equal(b.voice.peers.length, 1);
    assert.equal(b.voice.peers[0].name, "Ana");
    assert.equal((await announced).name, "Ben");
  });

  test("a member of another board room cannot join this voice channel", async () => {
    const room = uid("room");
    await h.member(room, "Ana");
    const other = await h.member(uid("room"), "Eve", { voice: false });
    const res = await emit(other, "voice:join", { roomId: room });
    assert.equal(res.ok, false);
  });

  test("the channel is capped", async () => {
    const room = uid("room");
    await h.member(room, "A");
    await h.member(room, "B");
    await h.member(room, "C");
    const d = await h.member(room, "D");
    assert.deepEqual(d.voice, { ok: false, error: "Room full" });
  });

  test("offers, answers and ICE are relayed only inside one voice channel, with clean payloads", async () => {
    const room = uid("room");
    const a = await h.member(room, "Ana");
    const b = await h.member(room, "Ben");
    const eve = await h.member(uid("room"), "Eve");

    const got = once(b, "voice:offer");
    a.emit("voice:offer", { to: b.id, data: { type: "offer", sdp: "v=0 test", extra: "x" } });
    assert.deepEqual(await got, { from: a.id, data: { type: "offer", sdp: "v=0 test" } });

    const ice = once(a, "voice:ice");
    b.emit("voice:ice", { to: a.id, data: { candidate: "candidate:1 1 udp 1 1.2.3.4 5 typ host", sdpMid: "0", sdpMLineIndex: 0 } });
    assert.equal((await ice).from, b.id);

    // Eve is in another room: neither can reach the other.
    const quiet1 = never(b, "voice:offer");
    eve.emit("voice:offer", { to: b.id, data: { type: "offer", sdp: "v=0" } });
    await quiet1;
    const quiet2 = never(eve, "voice:answer");
    a.emit("voice:answer", { to: eve.id, data: { type: "answer", sdp: "v=0" } });
    await quiet2;
    // Wrong type for the event is dropped.
    const quiet3 = never(b, "voice:answer");
    a.emit("voice:answer", { to: b.id, data: { type: "offer", sdp: "v=0" } });
    await quiet3;
  });

  test("mute state is broadcast; leaving the board or disconnecting leaves the voice channel", async () => {
    const room = uid("room");
    const a = await h.member(room, "Ana");
    const b = await h.member(room, "Ben");
    const muted = once(a, "voice:peer-muted");
    b.emit("voice:mute", { roomId: room, muted: true });
    assert.deepEqual(await muted, { socketId: b.id, muted: true });

    const left = once(a, "voice:peer-left");
    b.emit("board:leave");
    assert.equal((await left).socketId, b.id);

    const c = await h.member(room, "Cy");
    assert.equal(c.voice.peers.length, 1);
    const gone = once(a, "voice:peer-left");
    const cid = c.id;
    c.disconnect();
    assert.equal((await gone).socketId, cid);
    assert.equal(h.server.voice.voiceRooms.get(room).size, 1);
  });
});

describe("ask Lumi in voice", () => {
  const h = harness();

  test("the question and the answer reach everyone in the channel; the board text and history go to explain", async () => {
    const room = uid("room");
    const a = await h.member(room, "Ana");
    const b = await h.member(room, "Ben");
    a.emit("op", { opId: "o1", op: { kind: "add", items: [{ id: uid("n"), type: "note", page: "page-1", z: 1, x: 0, y: 0, w: 200, h: 100, color: "#ffd98a", text: "f(x) = x²" }] } });
    await new Promise((r) => setTimeout(r, 50));

    const thinking = once(b, "lumi:thinking");
    const replyB = once(b, "lumi:reply");
    const ack = await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm;codecs=opus" });
    assert.equal(ack.ok, true, ack.error);
    assert.equal((await thinking).askedBy.name, "Ana");
    const reply = await replyB;
    assert.equal(reply.transcript, "c'est quoi le nombre dérivé ?");
    assert.equal(reply.lang, "fr");
    assert.equal(reply.task, "explain");
    assert.ok(!/[#*$🙂]/u.test(reply.reply) && !/[#*$🙂]/u.test(reply.spoken), reply.spoken);
    assert.match(reply.spoken, /Le nombre dérivé\. Le nombre dérivé de f en a/);

    const explain = h.engine.calls.find((c) => c.task === "explain");
    assert.match(explain.inputs.board_text, /Note: f\(x\) = x²/);
    assert.equal(explain.ctx.room_id, room);
    assert.equal(h.engine.calls.find((c) => c.task === "transcribe").bytes, AUDIO.length);

    // The second question carries the first turn as history; a latecomer gets the strip in its join ack.
    await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" });
    const second = h.engine.calls.filter((c) => c.task === "explain").at(-1);
    assert.equal(second.inputs.history[0].content, "Ana: c'est quoi le nombre dérivé ?");
    const late = await h.member(room, "Cy");
    assert.equal(late.voice.history.length, 2);
  });

  test("guards: membership, audio type and size, one question per room at a time", async () => {
    const room = uid("room");
    const outside = await h.member(room, "Out", { voice: false });
    assert.equal((await emit(outside, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" })).ok, false);

    const a = await h.member(room, "Ana");
    const b = await h.member(room, "Ben");
    assert.match((await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "video/webm" })).error, /format/);
    assert.match((await emit(a, "lumi:ask", { roomId: room, audio: Buffer.alloc(2_100_000), mimeType: "audio/webm" })).error, /too long/);
    assert.match((await emit(a, "lumi:ask", { roomId: room, audio: "not audio", mimeType: "audio/webm" })).error, /No audio/);

    h.engine.delayMs = 300;
    const first = emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" });
    await new Promise((r) => setTimeout(r, 30));
    const busy = await emit(b, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" });
    assert.equal(busy.error, "Lumi is answering, wait a moment");
    assert.equal((await first).ok, true);
    h.engine.delayMs = 0;
    assert.equal((await emit(b, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" })).ok, true);
  });

  test("a failed transcription is reported to the room and releases the lock", async () => {
    const room = uid("room");
    const a = await h.member(room, "Ana");
    const b = await h.member(room, "Ben");
    h.engine.transcribeImpl = async () => { throw new Error("connect ECONNREFUSED"); };
    const seen = once(b, "lumi:error");
    const ack = await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" });
    assert.equal(ack.ok, false);
    assert.match(ack.error, /speech service/);
    assert.equal((await seen).askedBy.name, "Ana");
    h.engine.transcribeImpl = async () => { const e = new RangeError("nothing heard"); throw e; };
    assert.match((await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" })).error, /catch that/);
    h.engine.transcribeImpl = async () => ({ text: "what is a derivative?", lang: "en" });
    assert.equal((await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" })).ok, true);
    assert.equal(h.server.lumiVoice.busy.size, 0);
  });

  test("off-topic gets a fixed line without a model answer; a degraded run becomes a spoken wait", async () => {
    const room = uid("room");
    const a = await h.member(room, "Ana");
    h.engine.transcribeImpl = async () => ({ text: "tu as vu le match hier ?", lang: "fr" });
    h.engine.intent = "CHITCHAT";
    const before = h.engine.calls.length;
    const r1 = once(a, "lumi:reply");
    await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" });
    assert.equal((await r1).reply, line("offTopic", "fr"));
    assert.deepEqual(h.engine.calls.slice(before).map((c) => c.task), ["transcribe", "classify"]);

    h.engine.intent = "EXPLAIN";
    h.engine.degrade = "quota_exhausted";
    const r2 = once(a, "lumi:reply");
    await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" });
    const quota = await r2;
    assert.equal(quota.degraded, true);
    assert.match(quota.spoken, /42 secondes/);
    h.engine.degrade = null;
  });
});

describe("ask Lumi: tiers and rate", () => {
  const h = harness({ canUseLumi: (user) => user.name !== "Free", minGapMs: 5000 });

  test("a free-tier member sees no Lumi and a forged ask is refused on the server", async () => {
    const room = uid("room");
    const free = await h.member(room, "Free");
    assert.equal(free.voice.lumi, false);
    const res = await emit(free, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" });
    assert.equal(res.error, "Lumi voice is a premium feature");
  });

  test("one question per user every 5 s", async () => {
    const room = uid("room");
    const a = await h.member(room, "Ana");
    assert.equal((await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" })).ok, true);
    assert.match((await emit(a, "lumi:ask", { roomId: room, audio: AUDIO, mimeType: "audio/webm" })).error, /^Wait \d s/);
  });
});

describe("speech text", () => {
  test("markdown, LaTeX, links and emoji are stripped; lines get pauses", () => {
    const { display, spoken } = forSpeech("# Titre\n\n**Gras** et `code` [lien](http://x.y) 🎉\n- $\\frac{1}{x}$ et $\\sqrt{x}$\n1) Dérive f");
    assert.equal(display, "Titre\nGras et code lien\n(1)/(x) et √x\n1) Dérive f"); // numbered exercises keep their number
    assert.equal(spoken, "Titre. Gras et code lien. (1)/(x) et √x. 1) Dérive f.");
  });

  test("long answers are cut at a sentence end, including the Arabic question mark", () => {
    const text = "Première phrase assez longue pour compter. Deuxième phrase qui dépasse la limite fixée ici.";
    assert.equal(cutAtSentence(text, 60), "Première phrase assez longue pour compter.");
    assert.equal(cutAtSentence("ما هو العدد المشتق؟ هو نهاية نسبة التغير عندما يقترب h من الصفر", 40), "ما هو العدد المشتق؟");
    assert.ok(cutAtSentence("a".repeat(30) + " " + "b".repeat(40), 50).endsWith("…"));
    assert.ok(forSpeech("Phrase. ".repeat(200)).spoken.length <= 600);
  });

  test("the reader language follows the answer's script", () => {
    assert.equal(guessLang("العدد المشتق هو", "fr"), "ar");
    assert.equal(guessLang("Le nombre dérivé est", "ar"), "fr");
    assert.equal(guessLang("The derivative", "en"), "en");
  });

  test("signals are reduced to their WebRTC fields", () => {
    assert.equal(cleanSignal("voice:offer", { type: "answer", sdp: "x" }), null);
    assert.equal(cleanSignal("voice:ice", { candidate: 5 }), null);
    assert.equal(cleanSignal("voice:offer", { type: "offer", sdp: "x".repeat(30_000) }), null);
  });

  test("the whiteboard summary keeps text, notes and equations in reading order", () => {
    const room = {
      meta: { pages: [{ id: "p1" }, { id: "p2" }] },
      items: new Map([
        ["a", { type: "text", page: "p2", x: 0, y: 0, text: "second page" }],
        ["b", { type: "note", page: "p1", x: 0, y: 50, text: "below" }],
        ["c", { type: "equation", page: "p1", x: 0, y: 10, latex: "x^2" }],
        ["d", { type: "shape", page: "p1", x0: 0, y0: 0 }]
      ])
    };
    assert.equal(getWhiteboardSummary(room), "$x^2$\nNote: below\nsecond page");
    assert.equal(getWhiteboardSummary(room, 10).length, 10);
  });
});
