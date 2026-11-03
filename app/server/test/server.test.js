const { test, describe, before, after } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { io: connectClient } = require("socket.io-client");
const { createServer } = require("../src/app");

const quiet = () => {};
const sockets = [];

function connect(port) {
  const socket = connectClient(`http://localhost:${port}`, { transports: ["websocket"], forceNew: true });
  sockets.push(socket);
  return socket;
}

function once(socket, event, timeout = 1500) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`timed out waiting for ${event}`)), timeout);
    socket.once(event, (data) => {
      clearTimeout(timer);
      resolve(data);
    });
  });
}

/** Resolves when nothing arrives for `event` within the window. */
function never(socket, event, window = 300) {
  return new Promise((resolve, reject) => {
    const handler = (data) => reject(new Error(`unexpected ${event}: ${JSON.stringify(data)}`));
    socket.once(event, handler);
    setTimeout(() => {
      socket.off(event, handler);
      resolve();
    }, window);
  });
}

function join(socket, payload) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("join timed out")), 1500);
    socket.once("board:joined", (data) => { clearTimeout(timer); resolve(data); });
    socket.once("board:error", (err) => { clearTimeout(timer); resolve({ error: err }); });
    socket.emit("board:join", payload);
  });
}

let seq = 0;
const uid = (p = "id") => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`;
const rect = (page = "page-1", extra = {}) => ({
  id: uid("rect"), type: "shape", shape: "rect", page, z: 1,
  x0: 0, y0: 0, x1: 100, y1: 50, color: "#a279ff", width: 3, ...extra
});
const note = (page = "page-1") => ({
  id: uid("note"), type: "note", page, z: 2, x: 10, y: 10, w: 200, h: 150, color: "#ffd98a", text: "Idea"
});

function harness(options = {}) {
  const ctx = {};
  before(async () => {
    ctx.server = createServer({ log: quiet, ...options });
    ctx.port = await ctx.server.listen(0, "127.0.0.1");
  });
  after(async () => {
    sockets.splice(0).forEach((s) => s.disconnect());
    await ctx.server.close();
  });
  return ctx;
}

describe("rooms and live sync", () => {
  const ctx = harness();

  test("joining returns identity, users and the default page", async () => {
    const a = connect(ctx.port);
    const joined = await join(a, { room: "alpha", name: "Amira", clientId: "client-a" });
    assert.equal(joined.room, "alpha");
    assert.equal(joined.me.name, "Amira");
    assert.deepEqual(joined.meta.pages.map((p) => p.id), ["page-1"]);
    assert.equal(joined.meta.host, "client-a");
  });

  test("ops reach others in the room but not other rooms", async () => {
    const a = connect(ctx.port);
    const b = connect(ctx.port);
    const outsider = connect(ctx.port);
    await join(a, { room: "beta", name: "A", clientId: "beta-a" });
    await join(b, { room: "beta", name: "B", clientId: "beta-b" });
    await join(outsider, { room: "gamma", name: "C", clientId: "gamma-c" });

    const item = rect();
    const received = once(b, "op");
    const silent = never(outsider, "op");
    a.emit("op", { op: { kind: "add", items: [item] } });
    const { op, by } = await received;
    await silent;
    assert.equal(op.kind, "add");
    assert.equal(op.items[0].id, item.id);
    assert.equal(op.items[0].owner, "beta-a");
    assert.equal(by, "beta-a");
  });

  test("late joiners receive everything drawn so far", async () => {
    const a = connect(ctx.port);
    await join(a, { room: "delta", name: "A", clientId: "delta-a" });
    const items = [rect(), rect()];
    a.emit("op", { op: { kind: "add", items } });
    await new Promise((r) => setTimeout(r, 80));
    const late = connect(ctx.port);
    const joined = await join(late, { room: "delta", name: "Late", clientId: "delta-late" });
    assert.deepEqual(joined.items.map((i) => i.id).sort(), items.map((i) => i.id).sort());
  });

  test("streamed strokes are relayed and committed", async () => {
    const a = connect(ctx.port);
    const b = connect(ctx.port);
    await join(a, { room: "eps", name: "A", clientId: "eps-a" });
    await join(b, { room: "eps", name: "B", clientId: "eps-b" });
    const id = uid("stroke");
    const begin = once(b, "stroke:begin");
    a.emit("stroke:begin", { id, tool: "pen", color: "#f5f5ff", width: 4, pressure: true, page: "page-1", z: 3, point: [0, 0, 0.5] });
    assert.equal((await begin).id, id);
    const points = once(b, "stroke:points");
    a.emit("stroke:points", { id, points: [[5, 5, 0.6], [10, 12, 0.7]] });
    assert.equal((await points).points.length, 2);
    const end = once(b, "stroke:end");
    a.emit("stroke:end", { id });
    await end;
    const c = connect(ctx.port);
    const joined = await join(c, { room: "eps", name: "C", clientId: "eps-c" });
    const stroke = joined.items.find((i) => i.id === id);
    assert.equal(stroke.points.length, 3);
    assert.equal(stroke.pressure, true);
  });

  test("updates keep ownership and removes are relayed", async () => {
    const a = connect(ctx.port);
    const b = connect(ctx.port);
    await join(a, { room: "zeta", name: "A", clientId: "zeta-a" });
    await join(b, { room: "zeta", name: "B", clientId: "zeta-b" });
    const item = rect();
    const added = once(b, "op");
    a.emit("op", { op: { kind: "add", items: [item] } });
    await added;

    const updated = once(a, "op");
    b.emit("op", { op: { kind: "update", items: [{ ...item, x1: 300, owner: "zeta-b" }] } });
    const { op } = await updated;
    assert.equal(op.items[0].x1, 300);
    assert.equal(op.items[0].owner, "zeta-a", "owner cannot be reassigned by an update");

    const removed = once(b, "op");
    a.emit("op", { op: { kind: "remove", ids: [item.id] } });
    assert.deepEqual((await removed).op.ids, [item.id]);
  });
});

describe("validation", () => {
  const ctx = harness();

  test("malformed items are rejected and never broadcast", async () => {
    const a = connect(ctx.port);
    const b = connect(ctx.port);
    await join(a, { room: "v", name: "A", clientId: "v-a" });
    await join(b, { room: "v", name: "B", clientId: "v-b" });
    const bad = [
      { ...rect(), color: "red; drop table" },
      { ...rect(), x0: Infinity },
      { ...rect(), page: "no-such-page" },
      { ...rect(), type: "script" },
      { id: uid("img"), type: "image", page: "page-1", z: 1, x: 0, y: 0, w: 10, h: 10, src: "javascript:alert(1)" },
      { id: uid("path"), type: "path", tool: "pen", page: "page-1", z: 1, color: "#fff", width: 3, points: [] }
    ];
    const silent = never(b, "op", 400);
    for (const item of bad) {
      const rejected = once(a, "op:rejected");
      a.emit("op", { op: { kind: "add", items: [item] }, opId: item.id });
      assert.equal((await rejected).opId, item.id);
    }
    await silent;
  });

  test("garbage payloads do not crash the server", async () => {
    const a = connect(ctx.port);
    await join(a, { room: "v2", name: "A", clientId: "v2-a" });
    for (const payload of [null, 42, "x", [], { op: null }, { op: { kind: "add", items: "nope" } }]) {
      a.emit("op", payload);
      a.emit("stroke:points", payload);
      a.emit("cursor:move", payload);
      a.emit("page:add", payload);
    }
    const res = await fetch(`http://127.0.0.1:${ctx.port}/health`).then((r) => r.json());
    assert.equal(res.status, "ok");
  });

  test("names and room names are trimmed and bounded", async () => {
    const a = connect(ctx.port);
    const joined = await join(a, { room: "  spaced  ", name: "x".repeat(200), clientId: "v3-a" });
    assert.equal(joined.room, "spaced");
    assert.equal(joined.me.name.length, 24);
  });
});

describe("teacher controls", () => {
  const ctx = harness();

  test("the creator is host; a locked board refuses student edits", async () => {
    const teacher = connect(ctx.port);
    const student = connect(ctx.port);
    await join(teacher, { room: "class", name: "Teacher", clientId: "teacher-1" });
    const joined = await join(student, { room: "class", name: "Student", clientId: "student-1" });
    assert.equal(joined.meta.host, "teacher-1");

    const meta = once(student, "room:meta");
    teacher.emit("host:lock", true);
    assert.equal((await meta).locked, true);

    const rejected = once(student, "op:rejected");
    const silent = never(teacher, "op");
    student.emit("op", { op: { kind: "add", items: [rect()] } });
    assert.equal((await rejected).reason, "locked");
    await silent;

    // Votes still work on a locked board.
    teacher.emit("op", { op: { kind: "add", items: [note()] } });
    const noteAdded = await once(student, "op");
    const voted = once(teacher, "op");
    student.emit("note:vote", { id: noteAdded.op.items[0].id });
    assert.deepEqual((await voted).op.items[0].votes, ["student-1"]);
  });

  test("students cannot use host actions or clear the board", async () => {
    const teacher = connect(ctx.port);
    const student = connect(ctx.port);
    await join(teacher, { room: "class2", name: "Teacher", clientId: "teacher-2" });
    const joinAnnounced = once(teacher, "room:meta");
    await join(student, { room: "class2", name: "Student", clientId: "student-2" });
    await joinAnnounced;
    const silentMeta = never(teacher, "room:meta");
    student.emit("host:lock", true);
    student.emit("host:present", true);
    student.emit("timer:control", { action: "start" });
    await silentMeta;
    const rejected = once(student, "op:rejected");
    student.emit("op", { op: { kind: "clear", page: "page-1" } });
    assert.equal((await rejected).reason, "host-only");
  });

  test("host moves to the longest-present user and back to the owner", async () => {
    const owner = connect(ctx.port);
    const s1 = connect(ctx.port);
    const s2 = connect(ctx.port);
    await join(owner, { room: "class3", name: "Owner", clientId: "owner-3" });
    await join(s1, { room: "class3", name: "S1", clientId: "s1-3" });
    await join(s2, { room: "class3", name: "S2", clientId: "s2-3" });
    const meta = once(s2, "room:meta");
    owner.disconnect();
    assert.equal((await meta).host, "s1-3");

    const back = connect(ctx.port);
    const joined = await join(back, { room: "class3", name: "Owner", clientId: "owner-3" });
    assert.equal(joined.meta.host, "owner-3");
  });

  test("presenting relays the host view to followers only from the host", async () => {
    const teacher = connect(ctx.port);
    const student = connect(ctx.port);
    await join(teacher, { room: "class4", name: "T", clientId: "teacher-4" });
    await join(student, { room: "class4", name: "S", clientId: "student-4" });
    teacher.emit("host:present", true);
    await once(student, "room:meta");
    const follow = once(student, "view:follow");
    teacher.emit("view:update", { page: "page-1", x: 100, y: 50, zoom: 1.5, width: 1200, height: 800 });
    assert.equal((await follow).zoom, 1.5);
    const silent = never(teacher, "view:follow");
    student.emit("view:update", { page: "page-1", x: 0, y: 0, zoom: 1, width: 10, height: 10 });
    await silent;
  });

  test("timer start, pause and reset are host-controlled", async () => {
    const teacher = connect(ctx.port);
    await join(teacher, { room: "class5", name: "T", clientId: "teacher-5" });
    let meta = once(teacher, "room:meta");
    teacher.emit("timer:control", { action: "set", seconds: 60 });
    assert.equal((await meta).timer.remaining, 60);
    meta = once(teacher, "room:meta");
    teacher.emit("timer:control", { action: "start" });
    assert.ok((await meta).timer.endsAt > Date.now());
    meta = once(teacher, "room:meta");
    teacher.emit("timer:control", { action: "pause" });
    const paused = await meta;
    assert.equal(paused.timer.endsAt, null);
    assert.ok(paused.timer.remaining <= 60 && paused.timer.remaining > 58);
  });
});

describe("private rooms", () => {
  const ctx = harness();

  test("a room created with a code requires it", async () => {
    const owner = connect(ctx.port);
    const created = await join(owner, { room: "secret", name: "O", clientId: "secret-o", code: "orbit-42" });
    assert.equal(created.meta.hasCode, true);

    const noCode = await join(connect(ctx.port), { room: "secret", name: "X", clientId: "secret-x" });
    assert.equal(noCode.error.code, "code-required");
    const wrong = await join(connect(ctx.port), { room: "secret", name: "X", clientId: "secret-x", code: "nope" });
    assert.equal(wrong.error.code, "code-wrong");
    const right = await join(connect(ctx.port), { room: "secret", name: "X", clientId: "secret-x", code: "orbit-42" });
    assert.equal(right.room, "secret");
  });
});

describe("pages and history", () => {
  const ctx = harness();

  test("pages can be added and renamed; items are scoped to pages", async () => {
    const a = connect(ctx.port);
    await join(a, { room: "pages", name: "A", clientId: "pages-a" });
    let meta = once(a, "room:meta");
    a.emit("page:add", { id: "page-two", name: "Practice" });
    assert.deepEqual((await meta).pages.map((p) => p.name), ["Page 1", "Practice"]);
    meta = once(a, "room:meta");
    a.emit("page:rename", { id: "page-two", name: "Worked examples" });
    assert.equal((await meta).pages[1].name, "Worked examples");

    a.emit("op", { op: { kind: "add", items: [rect("page-two")] } });
    meta = once(a, "room:meta");
    const removed = once(a, "op");
    a.emit("page:delete", { id: "page-two" });
    assert.equal((await meta).pages.length, 1);
    assert.equal((await removed).op.kind, "remove");
  });

  test("history entries are reversible and ordered", async () => {
    const a = connect(ctx.port);
    await join(a, { room: "hist", name: "A", clientId: "hist-a" });
    const item = rect();
    a.emit("op", { op: { kind: "add", items: [item] } });
    a.emit("op", { op: { kind: "update", items: [{ ...item, x1: 999 }] } });
    a.emit("op", { op: { kind: "remove", ids: [item.id] } });
    await new Promise((r) => setTimeout(r, 100));
    const history = await new Promise((resolve) => a.emit("history:get", resolve));
    assert.deepEqual(history.entries.map((e) => e.op.t), ["add", "update", "remove"]);
    assert.equal(history.entries[1].op.before[0].x1, 100);
    assert.equal(history.entries[1].op.after[0].x1, 999);
    assert.equal(history.entries[2].op.items[0].id, item.id);
    assert.ok(history.entries.every((e, i, all) => i === 0 || e.seq > all[i - 1].seq));
  });
});

describe("rate limiting", () => {
  const ctx = harness({ rate: { capacity: 20, perSecond: 5 } });

  test("floods are dropped and the sender is told", async () => {
    const a = connect(ctx.port);
    const b = connect(ctx.port);
    await join(a, { room: "flood", name: "A", clientId: "flood-a" });
    await join(b, { room: "flood", name: "B", clientId: "flood-b" });
    let delivered = 0;
    b.on("op", () => delivered++);
    const limited = once(a, "rate:limited");
    for (let i = 0; i < 100; i++) a.emit("op", { op: { kind: "add", items: [rect()] } });
    await limited;
    await new Promise((r) => setTimeout(r, 200));
    assert.ok(delivered < 30, `expected most of the flood dropped, got ${delivered}`);
  });
});

describe("persistence", () => {
  test("boards survive a server restart", async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "board-"));
    const dbPath = path.join(dir, "test.db");
    const item = rect();

    let server = createServer({ dbPath, log: quiet });
    let port = await server.listen(0, "127.0.0.1");
    const a = connect(port);
    await join(a, { room: "durable", name: "A", clientId: "durable-a", code: "keep" });
    a.emit("page:add", { id: "page-saved", name: "Saved page" });
    a.emit("op", { op: { kind: "add", items: [item] } });
    await new Promise((r) => setTimeout(r, 100));
    a.disconnect();
    await server.close();

    server = createServer({ dbPath, log: quiet });
    port = await server.listen(0, "127.0.0.1");
    const b = connect(port);
    const refused = await join(b, { room: "durable", name: "B", clientId: "durable-b" });
    assert.equal(refused.error.code, "code-required", "the access code survives restart");
    const joined = await join(b, { room: "durable", name: "B", clientId: "durable-b", code: "keep" });
    assert.deepEqual(joined.items.map((i) => i.id), [item.id]);
    assert.deepEqual(joined.meta.pages.map((p) => p.name), ["Page 1", "Saved page"]);
    b.disconnect();
    await server.close();
    fs.rmSync(dir, { recursive: true, force: true });
  });
});
