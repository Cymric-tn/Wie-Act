const express = require("express");
const http = require("http");
const crypto = require("crypto");
const cors = require("cors");
const { Server } = require("socket.io");
const os = require("os");
const path = require("path");
const { openDatabase } = require("./db");
const { createEngine } = require("./ai");
const { openAiStore } = require("./ai/store");
const { createAiRouter } = require("./ai/routes");
const V = require("./validate");

const USER_COLORS = [
  "#a279ff", "#75e8ff", "#40d9be", "#ffc45c",
  "#ff7fa8", "#8dbdff", "#c6a0ff", "#8affd3"
];
const MAX_ITEMS_PER_ROOM = 5000;
const MAX_IMAGE_BYTES_PER_ROOM = 40_000_000;
const MAX_PAGES = 50;
const DEFAULT_TIMER = 300;

function hashCode(code, salt) {
  return crypto.scryptSync(code, salt, 32).toString("hex");
}

function newMeta(clientId, code) {
  const meta = {
    owner: clientId,
    host: clientId,
    codeHash: null,
    salt: null,
    locked: false,
    presenting: false,
    spotlight: null,
    pages: [{ id: "page-1", name: "Page 1" }],
    timer: { duration: DEFAULT_TIMER, endsAt: null, remaining: DEFAULT_TIMER },
    seq: 0,
    createdAt: Date.now()
  };
  if (code) setCode(meta, code);
  return meta;
}

function setCode(meta, code) {
  if (!code) {
    meta.codeHash = null;
    meta.salt = null;
    return;
  }
  meta.salt = crypto.randomBytes(16).toString("hex");
  meta.codeHash = hashCode(code, meta.salt);
}

function codeMatches(meta, code) {
  if (!meta.codeHash) return true;
  if (!code) return false;
  const a = Buffer.from(hashCode(code, meta.salt), "hex");
  const b = Buffer.from(meta.codeHash, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

const imageBytes = (item) => (item && item.type === "image" ? item.src.length : 0);

function createServer(options = {}) {
  const {
    dbPath = ":memory:",
    origins = ["http://localhost:5173", "http://127.0.0.1:5173"],
    log = console.log,
    unloadAfterMs = 10 * 60 * 1000,
    rate = { capacity: 400, perSecond: 200 },
    ai = {}
  } = options;

  const db = openDatabase(dbPath);
  const engine = ai.engine || createEngine({ ...ai, log });
  const aiStore = openAiStore(db.handle);
  const uploadsDir = ai.uploadsDir || (dbPath === ":memory:"
    ? path.join(os.tmpdir(), `lumi-uploads-${process.pid}`)
    : path.join(path.dirname(dbPath), "uploads"));
  /** In-memory state for rooms with recent activity; the database is the source of truth. */
  const rooms = new Map();

  const app = express();
  app.use(cors({ origin: origins }));
  app.get("/health", (_req, res) => res.json({ status: "ok", rooms: rooms.size, ai: engine.status() }));
  app.use("/api", createAiRouter({ engine, store: aiStore, uploadsDir, allowForce: ai.allowForce ?? (() => engine.status().backend === "fake"), log }));

  const httpServer = http.createServer(app);
  const io = new Server(httpServer, {
    cors: { origin: origins, methods: ["GET", "POST"] },
    maxHttpBufferSize: 6e6
  });

  // ---------- room state ----------
  function loadRoom(name) {
    if (rooms.has(name)) return rooms.get(name);
    const stored = db.loadRoom(name);
    const room = {
      name,
      meta: stored ? stored.meta : null,
      items: new Map(),
      active: new Map(), // strokes being drawn: id -> { stroke, socketId }
      users: new Map(), // socketId -> user
      view: null,
      imageBytes: 0,
      unloadTimer: null
    };
    if (stored) {
      stored.items.forEach((item) => {
        room.items.set(item.id, item);
        room.imageBytes += imageBytes(item);
      });
    }
    rooms.set(name, room);
    return room;
  }

  function publicMeta(room) {
    const m = room.meta;
    return {
      owner: m.owner,
      host: m.host,
      locked: m.locked,
      presenting: m.presenting,
      spotlight: m.spotlight,
      pages: m.pages,
      timer: m.timer,
      hasCode: Boolean(m.codeHash),
      view: m.presenting ? room.view : null,
      serverNow: Date.now()
    };
  }

  const usersOf = (room) => [...room.users.values()];
  const broadcastMeta = (room) => io.to(room.name).emit("room:meta", publicMeta(room));
  const saveMeta = (room) => db.saveMeta(room.name, room.meta);

  function scheduleUnload(room) {
    clearTimeout(room.unloadTimer);
    room.unloadTimer = setTimeout(() => {
      if (room.users.size === 0) rooms.delete(room.name);
    }, unloadAfterMs);
    room.unloadTimer.unref?.();
  }

  /** Appends a reversible history entry and persists the item changes. */
  function record(room, byName, op, upserts, deletes) {
    room.meta.seq += 1;
    const entry = { seq: room.meta.seq, ts: Date.now(), by: byName, op };
    db.commit(room.name, room.meta, { upserts, deletes, entry });
    return entry;
  }

  function removeItems(room, ids) {
    const removed = [];
    ids.forEach((id) => {
      const item = room.items.get(id);
      if (!item) return;
      room.items.delete(id);
      room.imageBytes -= imageBytes(item);
      removed.push(item);
    });
    return removed;
  }

  /**
   * Applies a client operation. Returns { broadcast } on success or { error }.
   * Client forms: add{items}, remove{ids}, update{items}, clear{page}.
   */
  function applyOp(room, user, op, isHost) {
    if (!op || typeof op !== "object") return { error: "bad-op" };
    const pageIds = new Set(room.meta.pages.map((p) => p.id));

    if (op.kind === "add") {
      if (!Array.isArray(op.items) || op.items.length === 0 || op.items.length > V.LIMITS.itemsPerOp) return { error: "bad-op" };
      const items = op.items.map(V.cleanItem);
      if (items.some((i) => !i || !pageIds.has(i.page) || room.items.has(i.id))) return { error: "bad-item" };
      if (new Set(items.map((i) => i.id)).size !== items.length) return { error: "bad-item" };
      if (room.items.size + items.length > MAX_ITEMS_PER_ROOM) return { error: "room-full" };
      const bytes = items.reduce((sum, i) => sum + imageBytes(i), 0);
      if (room.imageBytes + bytes > MAX_IMAGE_BYTES_PER_ROOM) return { error: "room-full" };
      items.forEach((i) => {
        i.owner = user.clientId;
        if (i.type === "note") i.votes = [];
        room.items.set(i.id, i);
      });
      room.imageBytes += bytes;
      record(room, user.name, { t: "add", items }, items, []);
      return { broadcast: { kind: "add", items } };
    }

    if (op.kind === "remove") {
      if (!Array.isArray(op.ids) || op.ids.length === 0 || op.ids.length > V.LIMITS.idsPerOp || !op.ids.every(V.isId)) return { error: "bad-op" };
      const removed = removeItems(room, op.ids);
      if (!removed.length) return { broadcast: null };
      record(room, user.name, { t: "remove", items: removed }, [], removed.map((i) => i.id));
      return { broadcast: { kind: "remove", ids: removed.map((i) => i.id) } };
    }

    if (op.kind === "update") {
      if (!Array.isArray(op.items) || op.items.length === 0 || op.items.length > V.LIMITS.itemsPerOp) return { error: "bad-op" };
      const before = [];
      const after = [];
      for (const raw of op.items) {
        const clean = V.cleanItem(raw);
        const existing = clean && room.items.get(clean.id);
        if (!existing || existing.type !== clean.type || !pageIds.has(clean.page)) return { error: "bad-item" };
        clean.owner = existing.owner;
        if (existing.type === "note") clean.votes = existing.votes;
        before.push(existing);
        after.push(clean);
      }
      const delta = after.reduce((s, i, k) => s + imageBytes(i) - imageBytes(before[k]), 0);
      if (room.imageBytes + delta > MAX_IMAGE_BYTES_PER_ROOM) return { error: "room-full" };
      after.forEach((i) => room.items.set(i.id, i));
      room.imageBytes += delta;
      record(room, user.name, { t: "update", before, after }, after, []);
      return { broadcast: { kind: "update", items: after } };
    }

    if (op.kind === "clear") {
      if (!isHost) return { error: "host-only" };
      if (!pageIds.has(op.page)) return { error: "bad-op" };
      const ids = [...room.items.values()].filter((i) => i.page === op.page).map((i) => i.id);
      const removed = removeItems(room, ids);
      room.active.forEach((a, id) => a.stroke.page === op.page && room.active.delete(id));
      if (removed.length) record(room, user.name, { t: "remove", items: removed }, [], ids);
      return { broadcast: { kind: "clear", page: op.page }, toAll: true };
    }

    return { error: "bad-op" };
  }

  // ---------- connections ----------
  io.on("connection", (socket) => {
    socket.data = { room: null, user: null, tokens: rate.capacity, last: Date.now(), warnedAt: 0, lastReaction: 0 };

    // Token bucket: refuse floods without disconnecting honest bursts.
    function allow(cost = 1) {
      const now = Date.now();
      const d = socket.data;
      d.tokens = Math.min(rate.capacity, d.tokens + ((now - d.last) / 1000) * rate.perSecond);
      d.last = now;
      if (d.tokens < cost) {
        if (now - d.warnedAt > 2000) {
          d.warnedAt = now;
          socket.emit("rate:limited");
        }
        return false;
      }
      d.tokens -= cost;
      return true;
    }

    function on(event, handler, cost = 1) {
      socket.on(event, (...args) => {
        if (!allow(cost)) return;
        try {
          handler(...args);
        } catch (error) {
          log(`Error handling ${event}:`, error);
        }
      });
    }

    const current = () => {
      const room = socket.data.room && rooms.get(socket.data.room);
      return room ? { room, user: socket.data.user } : null;
    };
    const isHost = (room) => room.meta.host === socket.data.user?.clientId;
    const canEdit = (room) => !room.meta.locked || isHost(room);

    function leave() {
      const ctx = current();
      if (!ctx) return;
      const { room, user } = ctx;
      socket.leave(room.name);
      socket.data.room = null;
      socket.data.user = null;

      // Finish this user's in-flight strokes so nobody keeps a dangling preview.
      for (const [id, active] of room.active) {
        if (active.socketId !== socket.id) continue;
        room.active.delete(id);
        finishStroke(room, user, active.stroke);
      }
      room.users.delete(socket.id);
      socket.to(room.name).emit("user:left", { id: socket.id });

      if (room.meta.host === user.clientId && !usersOf(room).some((u) => u.clientId === user.clientId)) {
        const next = usersOf(room).sort((a, b) => a.joinedAt - b.joinedAt)[0];
        if (next) {
          room.meta.host = next.clientId;
          room.meta.presenting = false;
          saveMeta(room);
        }
      }
      broadcastMeta(room);
      if (room.users.size === 0) scheduleUnload(room);
    }

    function finishStroke(room, user, stroke) {
      const item = V.cleanItem(stroke);
      if (!item || room.items.has(item.id)) {
        socket.to(room.name).emit("stroke:cancel", { id: stroke.id });
        return;
      }
      item.owner = user.clientId;
      room.items.set(item.id, item);
      record(room, user.name, { t: "add", items: [item] }, [item], []);
      socket.to(room.name).emit("stroke:end", { id: stroke.id });
    }

    on("board:join", (payload) => {
      const roomName = V.cleanRoom(payload?.room);
      const name = V.cleanName(payload?.name) || "Guest";
      const clientId = V.isId(payload?.clientId) ? payload.clientId : `anon-${socket.id}`.slice(0, 64);
      const code = typeof payload?.code === "string" ? payload.code.trim().slice(0, 64) : "";
      if (!roomName) {
        socket.emit("board:error", { code: "room-required", message: "A room name is required." });
        return;
      }

      leave();
      const room = loadRoom(roomName);
      if (!room.meta) {
        room.meta = newMeta(clientId, code);
        saveMeta(room);
      } else if (!codeMatches(room.meta, code)) {
        socket.emit("board:error", code
          ? { code: "code-wrong", message: "That access code is not correct." }
          : { code: "code-required", message: "This room is private. Enter its access code." });
        if (room.users.size === 0) scheduleUnload(room);
        return;
      }

      clearTimeout(room.unloadTimer);
      const users = usersOf(room);
      const used = new Set(users.map((u) => u.color));
      const color = USER_COLORS.find((c) => !used.has(c)) || USER_COLORS[users.length % USER_COLORS.length];
      const user = { id: socket.id, clientId, name, color, joinedAt: Date.now() };

      // The room's creator always gets host back; otherwise an empty seat is filled.
      const hostOnline = users.some((u) => u.clientId === room.meta.host);
      if (clientId === room.meta.owner || !hostOnline) {
        if (room.meta.host !== clientId) room.meta.presenting = false;
        room.meta.host = clientId;
        saveMeta(room);
      }

      room.users.set(socket.id, user);
      socket.join(room.name);
      socket.data.room = room.name;
      socket.data.user = user;

      socket.emit("board:joined", {
        room: room.name,
        me: user,
        users: usersOf(room),
        items: [...room.items.values()],
        active: [...room.active.values()].map((a) => a.stroke),
        meta: publicMeta(room)
      });
      socket.to(room.name).emit("user:joined", user);
      // The joiner already has the meta in its snapshot; others may see a host change.
      socket.to(room.name).emit("room:meta", publicMeta(room));
      log(`${name} joined room ${room.name}`);
    });

    on("board:leave", leave);

    on("board:resync", (ack) => {
      const ctx = current();
      if (!ctx || typeof ack !== "function") return;
      ack({ items: [...ctx.room.items.values()], meta: publicMeta(ctx.room) });
    }, 5);

    on("op", (data) => {
      const ctx = current();
      if (!ctx) return;
      const { room, user } = ctx;
      const opId = typeof data?.opId === "string" ? data.opId.slice(0, 64) : null;
      if (!canEdit(room)) {
        socket.emit("op:rejected", { opId, reason: "locked" });
        return;
      }
      const result = applyOp(room, user, data?.op, isHost(room));
      if (result.error) {
        socket.emit("op:rejected", { opId, reason: result.error });
        return;
      }
      if (!result.broadcast) return;
      const target = result.toAll ? io.to(room.name) : socket.to(room.name);
      target.emit("op", { op: result.broadcast, by: user.clientId, byName: user.name });
    });

    // ----- freehand strokes streamed while drawing -----
    on("stroke:begin", (data) => {
      const ctx = current();
      if (!ctx || !canEdit(ctx.room) || !data) return;
      const { room, user } = ctx;
      if (!V.isId(data.id) || room.active.has(data.id) || room.items.has(data.id)) return;
      if (!V.TOOLS.has(data.tool) || !V.isColor(data.color) || !V.isNum(data.width, 1, 400)) return;
      if (!V.isPoint(data.point) || !V.isNum(data.z) || !room.meta.pages.some((p) => p.id === data.page)) return;
      const stroke = {
        id: data.id, type: "path", page: data.page, z: data.z, owner: user.clientId,
        tool: data.tool, color: data.color, width: data.width,
        pressure: Boolean(data.pressure), points: [data.point]
      };
      room.active.set(stroke.id, { stroke, socketId: socket.id });
      socket.to(room.name).emit("stroke:begin", stroke);
    });

    on("stroke:points", (data) => {
      const ctx = current();
      if (!ctx || !data || !V.isId(data.id) || !Array.isArray(data.points)) return;
      const active = ctx.room.active.get(data.id);
      if (!active || active.socketId !== socket.id) return;
      const room = V.LIMITS.pointsPerStroke - active.stroke.points.length;
      const points = data.points.slice(0, Math.max(0, room)).filter(V.isPoint);
      if (!points.length) return;
      active.stroke.points.push(...points);
      socket.to(ctx.room.name).emit("stroke:points", { id: data.id, points });
    });

    on("stroke:end", (data) => {
      const ctx = current();
      if (!ctx || !data || !V.isId(data.id)) return;
      const active = ctx.room.active.get(data.id);
      if (!active || active.socketId !== socket.id) return;
      ctx.room.active.delete(data.id);
      finishStroke(ctx.room, ctx.user, active.stroke);
    });

    // Used when shape assist turns a freehand stroke into a clean shape.
    on("stroke:cancel", (data) => {
      const ctx = current();
      if (!ctx || !data || !V.isId(data.id)) return;
      const active = ctx.room.active.get(data.id);
      if (!active || active.socketId !== socket.id) return;
      ctx.room.active.delete(data.id);
      socket.to(ctx.room.name).emit("stroke:cancel", { id: data.id });
    });

    // ----- sticky-note voting (allowed even when the board is locked) -----
    on("note:vote", (data) => {
      const ctx = current();
      if (!ctx || !data || !V.isId(data.id)) return;
      const { room, user } = ctx;
      const note = room.items.get(data.id);
      if (!note || note.type !== "note") return;
      const votes = new Set(note.votes || []);
      votes.has(user.clientId) ? votes.delete(user.clientId) : votes.add(user.clientId);
      const after = { ...note, votes: [...votes] };
      room.items.set(note.id, after);
      record(room, user.name, { t: "update", before: [note], after: [after] }, [after], []);
      io.to(room.name).emit("op", { op: { kind: "update", items: [after] }, by: user.clientId, byName: user.name });
    });

    // ----- pages -----
    on("page:add", (data) => {
      const ctx = current();
      if (!ctx || !canEdit(ctx.room) || !data || !V.isId(data.id)) return;
      const { room } = ctx;
      if (room.meta.pages.length >= MAX_PAGES || room.meta.pages.some((p) => p.id === data.id)) return;
      const name = V.cleanName(data.name) || `Page ${room.meta.pages.length + 1}`;
      const index = Number.isInteger(data.index) ? Math.max(0, Math.min(data.index, room.meta.pages.length)) : room.meta.pages.length;
      room.meta.pages.splice(index, 0, { id: data.id, name: name.slice(0, V.LIMITS.pageNameLength) });
      saveMeta(room);
      broadcastMeta(room);
    });

    on("page:rename", (data) => {
      const ctx = current();
      if (!ctx || !canEdit(ctx.room) || !data) return;
      const page = ctx.room.meta.pages.find((p) => p.id === data.id);
      const name = V.cleanName(data.name);
      if (!page || !name) return;
      page.name = name.slice(0, V.LIMITS.pageNameLength);
      saveMeta(ctx.room);
      broadcastMeta(ctx.room);
    });

    on("page:delete", (data) => {
      const ctx = current();
      if (!ctx || !isHost(ctx.room) || !data) return;
      const { room, user } = ctx;
      if (room.meta.pages.length <= 1 || !room.meta.pages.some((p) => p.id === data.id)) return;
      const ids = [...room.items.values()].filter((i) => i.page === data.id).map((i) => i.id);
      const removed = removeItems(room, ids);
      room.meta.pages = room.meta.pages.filter((p) => p.id !== data.id);
      if (room.view?.page === data.id) room.view = null;
      record(room, user.name, { t: "remove", items: removed }, [], ids);
      if (ids.length) io.to(room.name).emit("op", { op: { kind: "remove", ids }, by: user.clientId, byName: user.name });
      broadcastMeta(room);
    });

    // ----- teacher controls -----
    function hostAction(event, apply) {
      on(event, (value) => {
        const ctx = current();
        if (!ctx || !isHost(ctx.room)) return;
        if (apply(ctx.room, value) === false) return;
        saveMeta(ctx.room);
        broadcastMeta(ctx.room);
      });
    }

    hostAction("host:lock", (room, value) => { room.meta.locked = Boolean(value); });
    hostAction("host:present", (room, value) => {
      room.meta.presenting = Boolean(value);
      if (!room.meta.presenting) room.view = null;
    });
    hostAction("host:spotlight", (room, clientId) => {
      if (clientId !== null && !usersOf(room).some((u) => u.clientId === clientId)) return false;
      room.meta.spotlight = room.meta.spotlight === clientId ? null : clientId;
    });
    hostAction("host:transfer", (room, clientId) => {
      if (!usersOf(room).some((u) => u.clientId === clientId)) return false;
      room.meta.host = clientId;
      room.meta.presenting = false;
    });
    hostAction("host:code", (room, code) => {
      if (code !== null && (typeof code !== "string" || !code.trim())) return false;
      setCode(room.meta, code ? code.trim().slice(0, 64) : null);
    });
    hostAction("timer:control", (room, data) => {
      const t = room.meta.timer;
      const now = Date.now();
      if (data?.action === "set" && V.isNum(data.seconds, 5, 3 * 3600)) {
        t.duration = Math.round(data.seconds);
        t.remaining = t.duration;
        t.endsAt = null;
      } else if (data?.action === "start" && !t.endsAt && t.remaining > 0) {
        t.endsAt = now + t.remaining * 1000;
      } else if (data?.action === "pause" && t.endsAt) {
        t.remaining = Math.max(0, (t.endsAt - now) / 1000);
        t.endsAt = null;
      } else if (data?.action === "reset") {
        t.endsAt = null;
        t.remaining = t.duration;
      } else {
        return false;
      }
    });

    // The presenter's viewport, relayed to followers.
    on("view:update", (view) => {
      const ctx = current();
      if (!ctx || !isHost(ctx.room) || !ctx.room.meta.presenting || !view) return;
      if (!ctx.room.meta.pages.some((p) => p.id === view.page)) return;
      if (!V.isNum(view.x) || !V.isNum(view.y) || !V.isNum(view.zoom, 0.05, 20)) return;
      if (!V.isNum(view.width, 1, 20000) || !V.isNum(view.height, 1, 20000)) return;
      ctx.room.view = { page: view.page, x: view.x, y: view.y, zoom: view.zoom, width: view.width, height: view.height };
      socket.to(ctx.room.name).volatile.emit("view:follow", ctx.room.view);
    });

    // ----- presence -----
    on("cursor:move", (data) => {
      const ctx = current();
      if (!ctx) return;
      if (data === null) {
        socket.to(ctx.room.name).volatile.emit("cursor:move", { id: socket.id, x: null, y: null });
        return;
      }
      if (!data || !V.isNum(data.x) || !V.isNum(data.y) || !V.isId(data.page)) return;
      socket.to(ctx.room.name).volatile.emit("cursor:move", { id: socket.id, x: data.x, y: data.y, page: data.page });
    });

    on("laser", (data) => {
      const ctx = current();
      if (!ctx || !canEdit(ctx.room) || !data || !V.isId(data.page) || !Array.isArray(data.points)) return;
      const points = data.points.slice(0, 50).filter((p) => V.isPoint(p) && p.length === 2);
      if (!points.length) return;
      socket.to(ctx.room.name).volatile.emit("laser", { id: socket.id, page: data.page, points });
    });

    on("reaction", (data) => {
      const ctx = current();
      if (!ctx || !data || !V.REACTIONS.has(data.emoji) || !V.isNum(data.x) || !V.isNum(data.y) || !V.isId(data.page)) return;
      const now = Date.now();
      if (now - socket.data.lastReaction < 250) return;
      socket.data.lastReaction = now;
      io.to(ctx.room.name).emit("reaction", { id: socket.id, emoji: data.emoji, x: data.x, y: data.y, page: data.page });
    });

    // ----- history timeline -----
    on("history:get", (ack) => {
      const ctx = current();
      if (!ctx || typeof ack !== "function") return;
      ack({ items: [...ctx.room.items.values()], entries: db.history(ctx.room.name) });
    }, 20);

    socket.on("disconnect", () => {
      try {
        leave();
      } catch (error) {
        log("Error on disconnect:", error);
      }
    });
  });

  return {
    app,
    httpServer,
    io,
    rooms,
    engine,
    listen(port = 3000, host = "0.0.0.0") {
      return new Promise((resolve) => httpServer.listen(port, host, () => resolve(httpServer.address().port)));
    },
    async close() {
      rooms.forEach((room) => clearTimeout(room.unloadTimer));
      engine.close?.();
      await new Promise((resolve) => io.close(() => resolve()));
      db.close();
    }
  };
}

module.exports = { createServer };
