import { useEffect, useMemo, useRef, useState } from "react";
import { socket } from "../socket";
import Icon from "./Icons";
import ToolDock, { TOOLS, SHAPES, EDIT_TOOLS } from "./ToolDock";
import ContextDock from "./ContextDock";
import TopBar from "./TopBar";
import SidePanel from "./SidePanel";
import BoardAI from "./BoardAI";
import PageBar from "./PageBar";
import Minimap from "./Minimap";
import { TextEditor, NoteEditor, EquationEditor } from "./Editors";
import { BoardStore, UndoHistory, isEmptyOp } from "../lib/store";
import * as G from "../lib/geometry";
import { drawItem, drawBackground, drawLaser, setAssetListener, LASER_FADE_MS } from "../lib/render";
import { texToSvg } from "../lib/math";
import { imageFileToData, pdfFileToPages, isPdf, isImage } from "../lib/media";
import { exportPng, exportSvg, exportPdf, copyPng } from "../lib/exporters";
import { buildTemplate } from "../lib/templates";
import { LumiSpark } from "./LumiSpark";
import { lumiColors } from "../lib/lumiColors";
import { toBoardItems } from "../lib/diagrams/toBoard";
import { takePendingDiagram } from "../lib/diagrams/api";
import { buildTimeline, stateAt, describeEntry } from "../lib/history";
import { courseForRoom } from "../lib/courses";
import { NOTE_COLORS, STARLIGHT, INK, inkFor, isLightBackground } from "../lib/theme";
import { readStored, store as remember } from "../lib/identity";

const POINT_FLUSH_MS = 30;
const CURSOR_MS = 40;
const LASER_FLUSH_MS = 40;
const VIEW_SEND_MS = 90;
const HANDLE = 9; // px
const PATH_TOOLS = new Set(["pen", "highlighter"]);
const ASPECT_TYPES = new Set(["image", "equation", "text"]);
const NOTE_SIZE = { w: 220, h: 160 };
const PASTE_MARKER = "[Lumi board items]";

const REJECTIONS = {
  locked: "The host has locked the board.",
  "host-only": "Only the host can do that.",
  "room-full": "This room has reached its storage limit.",
  "bad-item": "That change couldn’t be saved — the board was refreshed.",
  "bad-op": "That change couldn’t be saved — the board was refreshed."
};

const textSizeFor = (width) => 12 + width * 4;
const equationScaleFor = (width) => 0.5 + width / 8;

export default function Board({ session, status, onLeave }) {
  const { room, me } = session;
  const course = useMemo(() => courseForRoom(room), [room]);

  // ---------- refs: the hot path never waits on React ----------
  const stageRef = useRef(null);
  const canvasRef = useRef(null);
  const bgRef = useRef(null);
  const storeRef = useRef(null);
  if (!storeRef.current) storeRef.current = new BoardStore(session.items);
  const undoRef = useRef(new UndoHistory());
  const activeRef = useRef(new Map(session.active.map((s) => [s.id, { ...s, points: [...s.points] }])));
  const viewRef = useRef({ x: -600, y: -350, zoom: 1 });
  const sizeRef = useRef({ w: 0, h: 0, dpr: 1 });
  const dirtyRef = useRef(true);
  const bgDirtyRef = useRef(true);
  const interactionRef = useRef(null);
  const pointersRef = useRef(new Map());
  const lasersRef = useRef(new Map()); // userId -> { page, color, points: [{ x, y, t }] }
  const laserBufRef = useRef([]);
  const lastLaserRef = useRef(0);
  const pendingRef = useRef([]);
  const lastFlushRef = useRef(0);
  const lastCursorRef = useRef(0);
  const lastPointerRef = useRef(null);
  const spaceRef = useRef(false);
  const clipboardRef = useRef([]);
  const pageViewsRef = useRef(new Map());
  const bboxCache = useRef(new WeakMap());
  const pageCacheRef = useRef({ version: -1, page: null, items: [] });
  const historyRef = useRef(null); // { timeline, items }
  const playRef = useRef(null);
  const viewSendRef = useRef({ last: 0, timer: null });
  const firstSessionRef = useRef(session);

  // ---------- UI state ----------
  const [meta, setMeta] = useState(session.meta);
  const [timerOffset, setTimerOffset] = useState(session.meta.serverNow - Date.now());
  const [pageId, setPageIdState] = useState(session.meta.pages[0].id);
  const [view, setViewState] = useState(viewRef.current);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [tool, setToolState] = useState("pen");
  const [shapeKind, setShapeKind] = useState("rect");
  const [background, setBackground] = useState(() => readStored("lumi-board-surface", "grid"));
  const [color, setColor] = useState(() => inkFor(readStored("lumi-board-surface", "grid")));
  const [width, setWidth] = useState(4);
  const [fill, setFill] = useState(false);
  const [noteColor, setNoteColor] = useState(NOTE_COLORS[0].value);
  const [shapeAssist, setShapeAssist] = useState(false);
  const [selection, setSelectionState] = useState([]);
  const [users, setUsers] = useState(session.users);
  const [cursors, setCursors] = useState({});
  const [reactions, setReactions] = useState([]);
  const [editor, setEditorState] = useState(null);
  const [rev, setRev] = useState(0);
  const [toast, setToast] = useState(null);
  const [confirm, setConfirm] = useState(null);
  // The dashboard's "Ask the AI" / "Fact-check" buttons open the board on its AI tab.
  const [initialTab] = useState(() => {
    const wanted = readStored("ednova-board-tab", "");
    remember("ednova-board-tab", "");
    return wanted === "ai" ? "ai" : "people";
  });
  const [panelOpen, setPanelOpen] = useState(() => initialTab === "ai" || window.innerWidth > 1180);
  const [tab, setTab] = useState(initialTab);
  const [following, setFollowing] = useState(true);
  const [history, setHistory] = useState({ state: "idle" });
  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(null);

  const pageRef = useRef(pageId);
  const toolRef = useRef(tool);
  const selectionRef = useRef(selection);
  const editorRef = useRef(editor);
  const metaRef = useRef(meta);
  const usersRef = useRef(users);
  const followingRef = useRef(following);
  const backgroundRef = useRef(background);
  pageRef.current = pageId;
  toolRef.current = tool;
  selectionRef.current = selection;
  editorRef.current = editor;
  metaRef.current = meta;
  usersRef.current = users;
  followingRef.current = following;
  backgroundRef.current = background;

  const store = storeRef.current;
  const isHost = meta.host === me.clientId;
  const isHostRef = useRef(isHost);
  isHostRef.current = isHost;
  const inHistory = history.state === "ready";
  const locked = meta.locked && !isHost;
  const readOnly = locked || inHistory;
  const readOnlyRef = useRef(readOnly);
  readOnlyRef.current = readOnly;

  // ---------- small helpers ----------
  const markDirty = () => { dirtyRef.current = true; };
  const aiBusyRef = useRef(false);
  const onAiBusy = (on) => { aiBusyRef.current = on; markDirty(); };
  const bump = () => { setRev((r) => r + 1); dirtyRef.current = true; };
  const showToast = (text) => setToast({ text, key: Date.now() + Math.random() });

  function cachedBox(item) {
    let b = bboxCache.current.get(item);
    if (!b) {
      b = G.bbox(item);
      bboxCache.current.set(item, b);
    }
    return b;
  }

  function pageItems(page = pageRef.current) {
    const cache = pageCacheRef.current;
    if (cache.version !== store.version || cache.page !== page) {
      pageCacheRef.current = { version: store.version, page, items: store.pageItems(page) };
    }
    return pageCacheRef.current.items;
  }

  /** What the canvas shows: the live page, or the history snapshot. */
  function renderItems() {
    const h = historyRef.current;
    if (h?.items) return h.items.filter((i) => i.page === pageRef.current).sort((a, b) => a.z - b.z || (a.id < b.id ? -1 : 1));
    return pageItems();
  }

  function setSelection(ids) {
    selectionRef.current = ids;
    setSelectionState(ids);
    markDirty();
  }

  function setEditor(next) {
    editorRef.current = next;
    setEditorState(next);
    markDirty();
  }

  function stopFollowing() {
    if (metaRef.current.presenting && !isHostRef.current && followingRef.current) {
      followingRef.current = false;
      setFollowing(false);
    }
  }

  function setView(next, { user = false } = {}) {
    viewRef.current = next;
    setViewState(next);
    bgDirtyRef.current = true;
    dirtyRef.current = true;
    if (user) stopFollowing();
  }

  function defaultViewFor(page) {
    const { w, h } = sizeRef.current;
    const items = store.pageItems(page);
    if (!items.length) return { x: -w / 2, y: -h / 2, zoom: 1 };
    return G.fitView(G.unionBox(items.map(cachedBox)), w, h, 80, 1.25);
  }

  function setPage(id, { user = false, keepView = false } = {}) {
    if (id === pageRef.current) return;
    pageViewsRef.current.set(pageRef.current, viewRef.current);
    pageRef.current = id;
    setPageIdState(id);
    setSelection([]);
    if (!keepView) setView(pageViewsRef.current.get(id) || defaultViewFor(id));
    socket.emit("cursor:move", null);
    if (user) stopFollowing();
  }

  function worldPoint(e) {
    const rect = canvasRef.current.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    return { sx, sy, ...G.screenToWorld(viewRef.current, sx, sy) };
  }

  const nextZ = () => store.maxZ(pageRef.current) + 1;

  // ---------- operations ----------
  function send(op) {
    socket.emit("op", { op, opId: G.makeId("op") });
  }

  function commitOp(op, { undoable = true } = {}) {
    if (isEmptyOp(op)) return;
    const inverse = store.apply(op);
    if (undoable) undoRef.current.push(op, inverse);
    send(op);
    if (op.kind === "remove" || op.kind === "clear") pruneSelection();
    bump();
  }

  function pruneSelection() {
    const kept = selectionRef.current.filter((id) => store.get(id));
    if (kept.length !== selectionRef.current.length) setSelection(kept);
  }

  function replay(op) {
    if (isEmptyOp(op)) return bump();
    store.apply(op);
    send(op);
    pruneSelection();
    bump();
  }

  const undo = () => replay(undoRef.current.takeUndo());
  const redo = () => replay(undoRef.current.takeRedo());

  function resync() {
    socket.emit("board:resync", (data) => {
      if (!data) return;
      store.load(data.items);
      setMeta(data.meta);
      pruneSelection();
      bump();
    });
  }

  // ---------- canvas sizing ----------
  useEffect(() => {
    const stage = stageRef.current;
    let first = true;
    const observer = new ResizeObserver(([entry]) => {
      const w = Math.round(entry.contentRect.width);
      const h = Math.round(entry.contentRect.height);
      const dpr = window.devicePixelRatio || 1;
      sizeRef.current = { w, h, dpr };
      [canvasRef.current, bgRef.current].forEach((c) => {
        c.width = Math.max(1, Math.round(w * dpr));
        c.height = Math.max(1, Math.round(h * dpr));
      });
      setSize({ w, h });
      if (first && w > 0) {
        first = false;
        const m = metaRef.current;
        if (m.presenting && m.view && !isHostRef.current) followView(m.view);
        else setView(defaultViewFor(pageRef.current));
      }
      bgDirtyRef.current = true;
      dirtyRef.current = true;
    });
    observer.observe(stage);
    return () => observer.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bgDirtyRef.current = true;
    markDirty(); // the default inks are redrawn for the new surface
  }, [background]);

  // ---------- render loop ----------
  const renderRef = useRef(null);
  renderRef.current = function render(now) {
    const canvas = canvasRef.current;
    const { w, h, dpr } = sizeRef.current;
    if (!canvas || !w) return;
    const v = viewRef.current;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    ctx.setTransform(dpr * v.zoom, 0, 0, dpr * v.zoom, -v.x * v.zoom * dpr, -v.y * v.zoom * dpr);

    const margin = 40 / v.zoom;
    const visible = { x: v.x - margin, y: v.y - margin, w: w / v.zoom + margin * 2, h: h / v.zoom + margin * 2 };
    const inter = interactionRef.current;
    const moving = inter?.preview ? new Set(inter.ids) : null;
    const editingId = editorRef.current?.id;

    renderItems().forEach((item) => {
      if (item.id === editingId) return;
      const shown = moving?.has(item.id) ? G.transformItem(item, inter.preview) : item;
      if (!G.boxesIntersect(moving?.has(item.id) ? G.bbox(shown) : cachedBox(item), visible)) return;
      drawItem(ctx, shown, inter?.kind === "erase" && inter.ids.has(item.id) ? { alpha: 0.16 } : undefined);
    });

    activeRef.current.forEach((stroke) => {
      if (stroke.page === pageRef.current) drawItem(ctx, stroke, { live: true });
    });
    if (inter?.kind === "shape" && inter.item) drawItem(ctx, inter.item);

    let lasersAlive = false;
    lasersRef.current.forEach((laser) => {
      laser.points = laser.points.filter((p) => now - p.t < LASER_FADE_MS);
      if (!laser.points.length || laser.page !== pageRef.current) return;
      lasersAlive = true;
      drawLaser(ctx, laser.points, now, laser.color, v.zoom);
    });

    // Screen-space overlays: selection frame, handles, marquee.
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const light = isLightBackground(backgroundRef.current);
    const { iris, glow, void: voidColor } = lumiColors();
    const accent = aiBusyRef.current ? glow : iris; // Glow while the AI is checking the selection
    const box = selectionBox();
    if (box && !historyRef.current) {
      const a = G.worldToScreen(v, box.x, box.y);
      const bw = box.w * v.zoom;
      const bh = box.h * v.zoom;
      ctx.save();
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(a.x - 4, a.y - 4, bw + 8, bh + 8);
      ctx.setLineDash([]);
      if (!readOnlyRef.current) {
        handlePoints(box).forEach(([, hx, hy]) => {
          const p = G.worldToScreen(v, hx, hy);
          ctx.fillStyle = light ? "#ffffff" : voidColor;
          ctx.fillRect(p.x - HANDLE / 2, p.y - HANDLE / 2, HANDLE, HANDLE);
          ctx.strokeRect(p.x - HANDLE / 2, p.y - HANDLE / 2, HANDLE, HANDLE);
        });
      }
      ctx.restore();
    }
    if (inter?.kind === "marquee" && inter.rect) {
      const r = inter.rect;
      const a = G.worldToScreen(v, r.x, r.y);
      ctx.fillStyle = `${iris}18`;
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1;
      ctx.fillRect(a.x, a.y, r.w * v.zoom, r.h * v.zoom);
      ctx.strokeRect(a.x + 0.5, a.y + 0.5, r.w * v.zoom, r.h * v.zoom);
    }
    if (toolRef.current === "eraser" && lastPointerRef.current && !readOnlyRef.current) {
      const p = G.worldToScreen(v, lastPointerRef.current.x, lastPointerRef.current.y);
      ctx.strokeStyle = accent;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 10, 0, Math.PI * 2);
      ctx.stroke();
    }
    return lasersAlive;
  };

  useEffect(() => {
    setAssetListener(() => {
      dirtyRef.current = true;
      bboxCache.current = new WeakMap();
    });
    let frame;
    let lasersAlive = false;
    const loop = (now) => {
      flushStroke(now);
      flushLaser(now);
      const { w, h, dpr } = sizeRef.current;
      if (bgDirtyRef.current && bgRef.current && w) {
        const ctx = bgRef.current.getContext("2d");
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        drawBackground(ctx, backgroundRef.current, viewRef.current, w, h);
        bgDirtyRef.current = false;
      }
      if (dirtyRef.current || lasersAlive) {
        dirtyRef.current = false;
        lasersAlive = renderRef.current(now);
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function flushStroke(now) {
    const inter = interactionRef.current;
    if (inter?.kind === "stroke" && pendingRef.current.length && now - lastFlushRef.current > POINT_FLUSH_MS) {
      socket.emit("stroke:points", { id: inter.id, points: pendingRef.current });
      pendingRef.current = [];
      lastFlushRef.current = now;
    }
  }

  function flushLaser(now) {
    if (laserBufRef.current.length && now - lastLaserRef.current > LASER_FLUSH_MS) {
      socket.emit("laser", { page: pageRef.current, points: laserBufRef.current });
      laserBufRef.current = [];
      lastLaserRef.current = now;
    }
  }

  // ---------- selection geometry ----------
  function selectedItems() {
    return selectionRef.current.map((id) => store.get(id)).filter(Boolean);
  }

  function selectionBox() {
    const items = selectedItems();
    if (!items.length) return null;
    const inter = interactionRef.current;
    const boxes = items.map((i) => (inter?.preview ? G.bbox(G.transformItem(i, inter.preview)) : cachedBox(i)));
    return G.unionBox(boxes);
  }

  function handlePoints(box) {
    return [
      ["nw", box.x, box.y, box.x + box.w, box.y + box.h],
      ["ne", box.x + box.w, box.y, box.x, box.y + box.h],
      ["se", box.x + box.w, box.y + box.h, box.x, box.y],
      ["sw", box.x, box.y + box.h, box.x + box.w, box.y]
    ];
  }

  function handleAt(sx, sy) {
    const box = selectionBox();
    if (!box || readOnlyRef.current) return null;
    const v = viewRef.current;
    return handlePoints(box).find(([, hx, hy]) => {
      const p = G.worldToScreen(v, hx, hy);
      return Math.abs(p.x - sx) <= HANDLE && Math.abs(p.y - sy) <= HANDLE;
    }) || null;
  }

  // ---------- pointer input ----------
  function sendCursor(p) {
    const now = performance.now();
    if (now - lastCursorRef.current < CURSOR_MS) return;
    lastCursorRef.current = now;
    socket.emit("cursor:move", { x: Math.round(p.x), y: Math.round(p.y), page: pageRef.current });
  }

  function cancelStroke() {
    const inter = interactionRef.current;
    if (inter?.kind === "stroke") {
      activeRef.current.delete(inter.id);
      socket.emit("stroke:cancel", { id: inter.id });
    }
    interactionRef.current = null;
    markDirty();
  }

  function startPinch() {
    cancelStroke();
    const [a, b] = [...pointersRef.current.values()];
    interactionRef.current = {
      kind: "pinch",
      d0: G.dist(a.sx, a.sy, b.sx, b.sy) || 1,
      mid0: { x: (a.sx + b.sx) / 2, y: (a.sy + b.sy) / 2 },
      view0: viewRef.current
    };
  }

  function onPointerDown(e) {
    if (editorRef.current) return; // the editor's blur commits it first
    const p = worldPoint(e);
    try {
      canvasRef.current.setPointerCapture(e.pointerId);
    } catch {
      // The pointer may already be gone (e.g. a very quick tap); capture is optional.
    }
    pointersRef.current.set(e.pointerId, { sx: p.sx, sy: p.sy, type: e.pointerType });
    lastPointerRef.current = p;

    if (pointersRef.current.size === 2 && e.pointerType !== "mouse") {
      startPinch();
      return;
    }
    if (pointersRef.current.size > 1) return;

    const current = toolRef.current;
    const panning = spaceRef.current || e.button === 1 || current === "hand";
    if (panning || (e.button !== 0 && e.pointerType === "mouse")) {
      interactionRef.current = { kind: "pan", sx: p.sx, sy: p.sy, view0: viewRef.current };
      stageRef.current.classList.add("grabbing");
      return;
    }
    if (inHistoryNow()) return;

    const items = pageItems();
    const tol = 6 / viewRef.current.zoom;

    if (current === "select") {
      const handle = handleAt(p.sx, p.sy);
      if (handle) {
        interactionRef.current = { kind: "resize", handle, ids: [...selectionRef.current], box0: selectionBox() };
        return;
      }
      const hit = G.topItemAt(items, p.x, p.y, tol);
      if (hit?.type === "note" && G.pointInBox(p.x, p.y, G.voteBadgeBox(hit), 2)) {
        socket.emit("note:vote", { id: hit.id });
        return;
      }
      if (readOnlyRef.current) return;
      if (hit) {
        let ids = selectionRef.current;
        if (e.shiftKey) {
          ids = ids.includes(hit.id) ? ids.filter((id) => id !== hit.id) : [...ids, hit.id];
          setSelection(ids);
          return;
        }
        if (!ids.includes(hit.id)) {
          ids = [hit.id];
          setSelection(ids);
        }
        interactionRef.current = { kind: "move", start: p, ids: [...ids], moved: false };
      } else {
        interactionRef.current = { kind: "marquee", start: p, additive: e.shiftKey, base: e.shiftKey ? selectionRef.current : [] };
        if (!e.shiftKey) setSelection([]);
      }
      return;
    }

    if (readOnlyRef.current) return;

    if (PATH_TOOLS.has(current)) {
      const id = G.makeId("s");
      const hasPressure = e.pointerType === "pen";
      const point = hasPressure ? [round(p.x), round(p.y), e.pressure || 0.5] : [round(p.x), round(p.y)];
      const stroke = {
        id, type: "path", page: pageRef.current, z: nextZ(), owner: me.clientId,
        tool: current, color, width, pressure: hasPressure, points: [point]
      };
      activeRef.current.set(id, stroke);
      pendingRef.current = [];
      interactionRef.current = { kind: "stroke", id, hasPressure };
      socket.emit("stroke:begin", { id, tool: current, color, width, pressure: hasPressure, page: stroke.page, z: stroke.z, point });
    } else if (current === "eraser") {
      interactionRef.current = { kind: "erase", ids: new Set(), last: p };
      eraseAt(p, p);
    } else if (current === "laser") {
      interactionRef.current = { kind: "laser" };
      addLaserPoint(p);
    } else if (current === "shape") {
      interactionRef.current = {
        kind: "shape",
        start: p,
        item: {
          id: G.makeId("sh"), type: "shape", shape: shapeKind, page: pageRef.current, z: nextZ(), owner: me.clientId,
          x0: round(p.x), y0: round(p.y), x1: round(p.x), y1: round(p.y), color, width, fill: fill && (shapeKind === "rect" || shapeKind === "ellipse")
        }
      };
    } else if (current === "text" || current === "note" || current === "equation") {
      e.preventDefault();
      const hit = G.topItemAt(items, p.x, p.y, tol);
      if (hit && hit.type === current) openEditorFor(hit);
      else openNewEditor(current, p);
    }
    markDirty();
  }

  const round = (n) => Math.round(n * 10) / 10;
  const inHistoryNow = () => Boolean(historyRef.current);

  function eraseAt(from, to) {
    const inter = interactionRef.current;
    const tol = 9 / viewRef.current.zoom;
    const steps = Math.max(1, Math.ceil(G.dist(from.x, from.y, to.x, to.y) / (4 / viewRef.current.zoom)));
    const items = pageItems();
    for (let s = 0; s <= steps; s++) {
      const x = from.x + ((to.x - from.x) * s) / steps;
      const y = from.y + ((to.y - from.y) * s) / steps;
      items.forEach((item) => {
        if (!inter.ids.has(item.id) && G.pointInBox(x, y, cachedBox(item), tol) && G.hitTest(item, x, y, tol)) inter.ids.add(item.id);
      });
    }
  }

  function addLaserPoint(p) {
    const now = performance.now();
    let laser = lasersRef.current.get(me.id);
    if (!laser || laser.page !== pageRef.current) {
      laser = { page: pageRef.current, color: me.color, points: [] };
      lasersRef.current.set(me.id, laser);
    }
    laser.points.push({ x: p.x, y: p.y, t: now });
    laserBufRef.current.push([round(p.x), round(p.y)]);
    markDirty();
  }

  function constrain(start, end, shiftKey, kind) {
    if (!shiftKey) return end;
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    if (kind === "rect" || kind === "ellipse") {
      const s = Math.max(Math.abs(dx), Math.abs(dy));
      return { x: start.x + Math.sign(dx || 1) * s, y: start.y + Math.sign(dy || 1) * s };
    }
    const angle = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
    const len = Math.hypot(dx, dy);
    return { x: start.x + Math.cos(angle) * len, y: start.y + Math.sin(angle) * len };
  }

  function onPointerMove(e) {
    const p = worldPoint(e);
    const tracked = pointersRef.current.get(e.pointerId);
    if (tracked) {
      tracked.sx = p.sx;
      tracked.sy = p.sy;
    }
    const prev = lastPointerRef.current;
    lastPointerRef.current = p;
    sendCursor(p);
    const inter = interactionRef.current;

    if (!inter) {
      if (toolRef.current === "select") {
        const handle = handleAt(p.sx, p.sy);
        canvasRef.current.style.cursor = handle ? (handle[0] === "nw" || handle[0] === "se" ? "nwse-resize" : "nesw-resize") : "";
      }
      if (toolRef.current === "eraser") markDirty();
      return;
    }

    switch (inter.kind) {
      case "pinch": {
        if (pointersRef.current.size < 2) return;
        const [a, b] = [...pointersRef.current.values()];
        const d = G.dist(a.sx, a.sy, b.sx, b.sy) || 1;
        const mid = { x: (a.sx + b.sx) / 2, y: (a.sy + b.sy) / 2 };
        const zoom = G.clamp(inter.view0.zoom * (d / inter.d0), G.MIN_ZOOM, G.MAX_ZOOM);
        const anchor = G.screenToWorld(inter.view0, inter.mid0.x, inter.mid0.y);
        setView({ x: anchor.x - mid.x / zoom, y: anchor.y - mid.y / zoom, zoom }, { user: true });
        return;
      }
      case "pan": {
        const v0 = inter.view0;
        setView({ ...v0, x: v0.x - (p.sx - inter.sx) / v0.zoom, y: v0.y - (p.sy - inter.sy) / v0.zoom }, { user: true });
        return;
      }
      case "stroke": {
        const stroke = activeRef.current.get(inter.id);
        if (!stroke) return;
        const last = stroke.points[stroke.points.length - 1];
        if (G.dist(p.x, p.y, last[0], last[1]) < 1.2 / viewRef.current.zoom) return;
        const coalesced = e.nativeEvent.getCoalescedEvents?.() || [];
        const samples = coalesced.length > 1 ? coalesced.map((c) => ({ ...worldPoint(c), pressure: c.pressure })) : [{ ...p, pressure: e.pressure }];
        samples.forEach((s) => {
          const point = inter.hasPressure ? [round(s.x), round(s.y), s.pressure || 0.5] : [round(s.x), round(s.y)];
          stroke.points.push(point);
          pendingRef.current.push(point);
        });
        break;
      }
      case "erase":
        eraseAt(prev || p, p);
        break;
      case "laser":
        addLaserPoint(p);
        break;
      case "shape": {
        const end = constrain(inter.start, p, e.shiftKey, inter.item.shape);
        inter.item = { ...inter.item, x1: round(end.x), y1: round(end.y) };
        break;
      }
      case "move": {
        const dx = p.x - inter.start.x;
        const dy = p.y - inter.start.y;
        if (!inter.moved && Math.hypot(dx, dy) * viewRef.current.zoom < 3) return;
        inter.moved = true;
        inter.preview = { dx, dy };
        break;
      }
      case "resize": {
        const [, , , ax, ay] = inter.handle;
        const [, hx, hy] = handlePoints(inter.box0).find(([n]) => n === inter.handle[0]);
        let sx = (p.x - ax) / (hx - ax || 1);
        let sy = (p.y - ay) / (hy - ay || 1);
        const aspect = inter.ids.some((id) => ASPECT_TYPES.has(store.get(id)?.type));
        if (e.shiftKey !== aspect) {
          const s = Math.max(Math.abs(sx), Math.abs(sy));
          sx = Math.sign(sx || 1) * s;
          sy = Math.sign(sy || 1) * s;
        }
        const minScale = 0.05;
        if (Math.abs(sx) < minScale) sx = Math.sign(sx || 1) * minScale;
        if (Math.abs(sy) < minScale) sy = Math.sign(sy || 1) * minScale;
        inter.preview = { sx, sy, ox: ax, oy: ay };
        break;
      }
      case "marquee": {
        const r = {
          x: Math.min(inter.start.x, p.x),
          y: Math.min(inter.start.y, p.y),
          w: Math.abs(p.x - inter.start.x),
          h: Math.abs(p.y - inter.start.y)
        };
        inter.rect = r;
        const inside = pageItems().filter((i) => G.boxContains(r, cachedBox(i))).map((i) => i.id);
        const ids = inter.additive ? [...new Set([...inter.base, ...inside])] : inside;
        if (ids.length !== selectionRef.current.length || ids.some((id, k) => id !== selectionRef.current[k])) setSelection(ids);
        break;
      }
      default:
        break;
    }
    markDirty();
  }

  function onPointerUp(e) {
    pointersRef.current.delete(e.pointerId);
    stageRef.current.classList.remove("grabbing");
    const inter = interactionRef.current;
    if (!inter) return;
    if (inter.kind === "pinch") {
      if (pointersRef.current.size < 2) interactionRef.current = null;
      return;
    }
    interactionRef.current = null;

    switch (inter.kind) {
      case "stroke": {
        const stroke = activeRef.current.get(inter.id);
        activeRef.current.delete(inter.id);
        if (!stroke) break;
        const shape = shapeAssist && stroke.tool === "pen" ? G.recognizeShape(stroke.points) : null;
        if (shape) {
          pendingRef.current = [];
          socket.emit("stroke:cancel", { id: stroke.id });
          commitOp({
            kind: "add",
            items: [{
              id: G.makeId("sh"), type: "shape", page: stroke.page, z: stroke.z, owner: me.clientId,
              ...mapShape(shape), color: stroke.color, width: stroke.width, fill: false
            }]
          });
          break;
        }
        if (pendingRef.current.length) {
          socket.emit("stroke:points", { id: stroke.id, points: pendingRef.current });
          pendingRef.current = [];
        }
        socket.emit("stroke:end", { id: stroke.id });
        // The server commits streamed strokes itself, so record it locally without resending.
        const add = { kind: "add", items: [stroke] };
        undoRef.current.push(add, store.apply(add));
        bump();
        break;
      }
      case "erase":
        if (inter.ids.size) commitOp({ kind: "remove", ids: [...inter.ids] });
        break;
      case "laser":
        break;
      case "shape": {
        const s = inter.item;
        if (Math.hypot(s.x1 - s.x0, s.y1 - s.y0) * viewRef.current.zoom > 4) commitOp({ kind: "add", items: [s] });
        break;
      }
      case "move":
      case "resize":
        if (inter.preview && (inter.kind === "resize" || inter.moved)) {
          const items = inter.ids.map((id) => store.get(id)).filter(Boolean).map((i) => G.transformItem(i, inter.preview));
          commitOp({ kind: "update", items });
        }
        break;
      default:
        break;
    }
    markDirty();
  }

  function mapShape(shape) {
    return { shape: shape.shape, x0: round(shape.x0), y0: round(shape.y0), x1: round(shape.x1), y1: round(shape.y1) };
  }

  function onDoubleClick(e) {
    if (readOnlyRef.current) return;
    const p = worldPoint(e);
    const hit = G.topItemAt(pageItems(), p.x, p.y, 6 / viewRef.current.zoom);
    if (hit && (hit.type === "text" || hit.type === "note" || hit.type === "equation")) openEditorFor(hit);
    else if (!hit && toolRef.current === "select") openNewEditor("text", p);
  }

  // Wheel: pan, or zoom with Ctrl/⌘ (trackpad pinch arrives as Ctrl+wheel).
  const wheelRef = useRef(null);
  wheelRef.current = (e) => {
    e.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const v = viewRef.current;
    if (e.ctrlKey || e.metaKey) {
      setView(G.zoomAt(v, Math.exp(-e.deltaY * 0.0085), sx, sy), { user: true });
    } else {
      const dx = e.shiftKey && !e.deltaX ? e.deltaY : e.deltaX;
      const dy = e.shiftKey && !e.deltaX ? 0 : e.deltaY;
      setView({ ...v, x: v.x + dx / v.zoom, y: v.y + dy / v.zoom }, { user: true });
    }
  };
  useEffect(() => {
    const canvas = canvasRef.current;
    const handler = (e) => wheelRef.current(e);
    canvas.addEventListener("wheel", handler, { passive: false });
    return () => canvas.removeEventListener("wheel", handler);
  }, []);

  function zoomBy(factor) {
    const { w, h } = sizeRef.current;
    setView(G.zoomAt(viewRef.current, factor, w / 2, h / 2), { user: true });
  }

  function fitToContent() {
    setView(defaultViewFor(pageRef.current), { user: true });
  }

  function resetZoom() {
    const { w, h } = sizeRef.current;
    const v = viewRef.current;
    const c = G.screenToWorld(v, w / 2, h / 2);
    setView({ x: c.x - w / 2, y: c.y - h / 2, zoom: 1 }, { user: true });
  }

  function centerOn(x, y, zoom = viewRef.current.zoom) {
    const { w, h } = sizeRef.current;
    setView({ x: x - w / 2 / zoom, y: y - h / 2 / zoom, zoom }, { user: true });
  }

  function viewCenter() {
    const { w, h } = sizeRef.current;
    return G.screenToWorld(viewRef.current, w / 2, h / 2);
  }

  // ---------- AI results onto the board ----------
  const clip = (text) => (text.length > 500 ? `${text.slice(0, 499)}…` : text);

  function pinNote(text) {
    if (readOnlyRef.current) return showToast(REJECTIONS.locked);
    const w = 280;
    const h = Math.min(320, 110 + Math.ceil(text.length / 34) * 18);
    // Beside what the AI looked at, else in the middle of the view.
    const box = selectionBox();
    const c = viewCenter();
    const x = box ? box.x + box.w + 40 : c.x - w / 2;
    const y = box ? box.y : c.y - h / 2;
    commitOp({ kind: "add", items: [{ id: G.makeId("nt"), type: "note", page: pageRef.current, z: nextZ(), owner: me.clientId, x: round(x), y: round(y), w, h, color: NOTE_COLORS[2]?.value || noteColor, text: clip(text), votes: [] }] });
    showToast("Pinned to the board");
  }

  function addTextItem(text) {
    if (readOnlyRef.current) return showToast(REJECTIONS.locked);
    const c = viewCenter();
    commitOp({ kind: "add", items: [{ id: G.makeId("tx"), type: "text", page: pageRef.current, z: nextZ(), owner: me.clientId, x: round(c.x - 120), y: round(c.y), size: textSizeFor(width), color, text: clip(text) }] });
  }

  // ---------- editors (text, notes, equations) ----------
  function openNewEditor(kind, p) {
    if (kind === "text") setEditor({ kind, x: round(p.x), y: round(p.y - textSizeFor(width) * 0.5), size: textSizeFor(width), color });
    else if (kind === "note") setEditor({ kind, x: round(p.x - NOTE_SIZE.w / 2), y: round(p.y - NOTE_SIZE.h / 2), ...NOTE_SIZE, color: noteColor });
    else setEditor({ kind, x: round(p.x), y: round(p.y), color });
  }

  function openEditorFor(item) {
    setSelection([]);
    if (item.type === "text") setEditor({ kind: "text", id: item.id, x: item.x, y: item.y, size: item.size, color: item.color, initial: item.text });
    if (item.type === "note") setEditor({ kind: "note", id: item.id, x: item.x, y: item.y, w: item.w, h: item.h, color: item.color, initial: item.text });
    if (item.type === "equation") setEditor({ kind: "equation", id: item.id, x: item.x, y: item.y, color: item.color, initial: item.latex });
  }

  function commitText(value) {
    const ed = editorRef.current;
    setEditor(null);
    if (!ed) return;
    const text = value.replace(/\s+$/, "");
    const existing = ed.id && store.get(ed.id);
    if (existing) {
      if (!text.trim()) commitOp({ kind: "remove", ids: [existing.id] });
      else if (text !== existing.text) commitOp({ kind: "update", items: [{ ...existing, text }] });
    } else if (text.trim()) {
      commitOp({ kind: "add", items: [{ id: G.makeId("tx"), type: "text", page: pageRef.current, z: nextZ(), owner: me.clientId, x: ed.x, y: ed.y, size: ed.size, color: ed.color, text }] });
    }
  }

  function commitNote(value) {
    const ed = editorRef.current;
    setEditor(null);
    if (!ed) return;
    const text = value.trim();
    const existing = ed.id && store.get(ed.id);
    if (existing) {
      if (text !== existing.text) commitOp({ kind: "update", items: [{ ...existing, text }] });
    } else if (text) {
      commitOp({ kind: "add", items: [{ id: G.makeId("nt"), type: "note", page: pageRef.current, z: nextZ(), owner: me.clientId, x: ed.x, y: ed.y, w: ed.w, h: ed.h, color: ed.color, text, votes: [] }] });
    }
  }

  async function commitEquation(latex) {
    const ed = editorRef.current;
    setEditor(null);
    if (!ed) return;
    try {
      const existing = ed.id && store.get(ed.id);
      const rendered = await texToSvg(latex, ed.color);
      if (existing) {
        const old = await texToSvg(existing.latex, existing.color).catch(() => null);
        const scale = old ? existing.w / old.w : equationScaleFor(width);
        commitOp({ kind: "update", items: [{ ...existing, latex, w: round(rendered.w * scale), h: round(rendered.h * scale) }] });
      } else {
        const scale = equationScaleFor(width);
        commitOp({
          kind: "add",
          items: [{ id: G.makeId("eq"), type: "equation", page: pageRef.current, z: nextZ(), owner: me.clientId, x: ed.x, y: ed.y, w: round(rendered.w * scale), h: round(rendered.h * scale), color: ed.color, latex }]
        });
      }
    } catch (error) {
      showToast(error.message);
    }
  }

  // ---------- selection actions ----------
  function updateSelected(fn) {
    const items = selectedItems().map(fn).filter(Boolean);
    if (items.length) commitOp({ kind: "update", items });
  }

  const recolor = (c) => updateSelected((i) => (i.type === "image" ? null : { ...i, color: c }));
  const setSelectionWidth = (w) => updateSelected((i) => (i.type === "path" || i.type === "shape" ? { ...i, width: w } : null));
  const toggleSelectionFill = () => {
    const fillable = selectedItems().filter((i) => i.type === "shape" && (i.shape === "rect" || i.shape === "ellipse"));
    const on = !fillable.every((i) => i.fill);
    updateSelected((i) => (fillable.includes(i) ? { ...i, fill: on } : null));
  };
  const deleteSelected = () => commitOp({ kind: "remove", ids: [...selectionRef.current] });

  function duplicateItems(items, offset = 24) {
    if (!items.length) return;
    let z = nextZ();
    const copies = [...items].sort((a, b) => a.z - b.z).map((i) => ({
      ...G.transformItem(i, { dx: offset, dy: offset }),
      id: G.makeId("cp"),
      page: pageRef.current,
      z: z++,
      owner: me.clientId,
      ...(i.type === "note" ? { votes: [] } : {})
    }));
    commitOp({ kind: "add", items: copies });
    setSelection(copies.map((c) => c.id));
    setTool("select");
  }

  function reorder(toFront) {
    const items = selectedItems().sort((a, b) => a.z - b.z);
    let z = toFront ? store.maxZ(pageRef.current) + 1 : store.minZ(pageRef.current) - items.length;
    commitOp({ kind: "update", items: items.map((i) => ({ ...i, z: z++ })) });
  }

  function nudge(dx, dy) {
    updateSelected((i) => G.transformItem(i, { dx, dy }));
  }

  // ---------- tools & style ----------
  function setTool(id, kind) {
    if (readOnlyRef.current && EDIT_TOOLS.has(id)) return;
    toolRef.current = id;
    setToolState(id);
    if (kind) setShapeKind(kind);
    if (id !== "select") setSelection([]);
    if (canvasRef.current) canvasRef.current.style.cursor = "";
    markDirty();
  }

  function changeBackground(id) {
    setBackground(id);
    remember("lumi-board-surface", id); // only a real choice is stored, so the default can change later
    if (isLightBackground(id) && color === STARLIGHT) setColor(INK);
    if (!isLightBackground(id) && color === INK) setColor(STARLIGHT);
  }

  // ---------- files: images & PDFs ----------
  async function insertFiles(files, at) {
    if (readOnlyRef.current) return showToast("The board is locked.");
    const center = at || viewCenter();
    let offset = 0;
    for (const file of files) {
      try {
        if (isPdf(file)) {
          await importPdf(file);
        } else if (isImage(file)) {
          setBusy(`Adding ${file.name}…`);
          const data = await imageFileToData(file);
          const vis = G.visibleBox(viewRef.current, sizeRef.current.w, sizeRef.current.h);
          const s = Math.min(1 / viewRef.current.zoom, (vis.w * 0.6) / data.w, (vis.h * 0.6) / data.h);
          const w = round(data.w * s);
          const h = round(data.h * s);
          commitOp({
            kind: "add",
            items: [{ id: G.makeId("im"), type: "image", page: pageRef.current, z: nextZ(), owner: me.clientId, x: round(center.x - w / 2 + offset), y: round(center.y - h / 2 + offset), w, h, src: data.src, name: file.name.slice(0, 80) }]
          });
          offset += 30 / viewRef.current.zoom;
        } else {
          showToast(`${file.name}: only images and PDFs can be added.`);
        }
      } catch (error) {
        showToast(error.message || "That file couldn’t be added.");
      } finally {
        setBusy(null);
      }
    }
  }

  async function importPdf(file) {
    setBusy("Reading PDF…");
    const { pages, total } = await pdfFileToPages(file, { onProgress: (n, count) => setBusy(`Rendering page ${n} of ${count}…`) });
    const base = file.name.replace(/\.pdf$/i, "").slice(0, 28);
    const startIndex = metaRef.current.pages.findIndex((p) => p.id === pageRef.current) + 1;
    const created = pages.map((pg, k) => ({ id: G.makeId("pg"), name: `${base} · p${pg.number}`.slice(0, 40), index: startIndex + k, pg }));
    created.forEach(({ id, name, index }) => socket.emit("page:add", { id, name, index }));
    setMeta((m) => {
      const list = [...m.pages];
      created.forEach(({ id, name, index }) => list.splice(index, 0, { id, name }));
      return { ...m, pages: list };
    });
    created.forEach(({ id, pg }) => {
      const w = 1200;
      const h = round((pg.h / pg.w) * w);
      commitOp({ kind: "add", items: [{ id: G.makeId("im"), type: "image", page: id, z: 1, owner: me.clientId, x: -w / 2, y: -h / 2, w, h, src: pg.src, name: file.name.slice(0, 80) }] });
    });
    if (created.length) setPage(created[0].id, { user: true });
    showToast(total > pages.length ? `Imported the first ${pages.length} of ${total} pages.` : `Imported ${pages.length} page${pages.length === 1 ? "" : "s"} — draw right on top.`);
  }

  // ---------- pages ----------
  function addPage() {
    const id = G.makeId("pg");
    const index = metaRef.current.pages.findIndex((p) => p.id === pageRef.current) + 1;
    const name = `Page ${metaRef.current.pages.length + 1}`;
    socket.emit("page:add", { id, name, index });
    setMeta((m) => {
      const pages = [...m.pages];
      pages.splice(index, 0, { id, name });
      return { ...m, pages };
    });
    setPage(id, { user: true });
  }

  function renamePage(id, name) {
    socket.emit("page:rename", { id, name });
    setMeta((m) => ({ ...m, pages: m.pages.map((p) => (p.id === id ? { ...p, name } : p)) }));
  }

  function deletePage(id) {
    const page = meta.pages.find((p) => p.id === id);
    setConfirm({
      title: `Delete “${page?.name}”?`,
      body: "Everything on this page is removed for everyone. This can’t be undone.",
      label: "Delete page",
      run: () => {
        if (id === pageRef.current) {
          const other = meta.pages.find((p) => p.id !== id);
          if (other) setPage(other.id, { user: true });
        }
        socket.emit("page:delete", { id });
      }
    });
  }

  function clearPage() {
    const page = meta.pages.find((p) => p.id === pageRef.current);
    setConfirm({
      title: `Clear “${page?.name}” for everyone?`,
      body: `This removes everything on this page for all ${users.length} participant${users.length === 1 ? "" : "s"}. You can undo it.`,
      label: "Clear page",
      run: () => commitOp({ kind: "clear", page: pageRef.current })
    });
  }

  /** Adds ready-made items in one undoable step, selects them and zooms out if they don't fit. */
  function placeItems(built) {
    const items = built.map((i) => ({ ...i, owner: me.clientId, ...(i.type === "note" ? { votes: [] } : {}) }));
    commitOp({ kind: "add", items });
    setTool("select");
    setSelection(items.map((i) => i.id));
    const box = G.unionBox(items.map(G.bbox));
    const { w, h } = sizeRef.current;
    if (box.w * viewRef.current.zoom > w * 0.9 || box.h * viewRef.current.zoom > h * 0.9) setView(G.fitView(box, w, h, 80, 1.2), { user: true });
  }

  function insertTemplate(id) {
    const c = viewCenter();
    placeItems(buildTemplate(id, { cx: round(c.x), cy: round(c.y), page: pageRef.current, z: nextZ(), ink: inkFor(background) }));
  }

  // A diagram sent from the generator page lands here, once the canvas has its size.
  useEffect(() => {
    const timer = setTimeout(() => {
      const primitives = takePendingDiagram();
      if (!primitives) return;
      if (readOnlyRef.current) return showToast(REJECTIONS.locked);
      const c = viewCenter();
      placeItems(toBoardItems(primitives, { cx: round(c.x), cy: round(c.y), page: pageRef.current, z: nextZ(), ink: inkFor(background) }));
      showToast("Diagram added to the board");
    }, 150);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, when the board opens
  }, []);

  function jumpTo(item) {
    setPage(item.page, { user: true });
    const b = G.bbox(item);
    const { w, h } = sizeRef.current;
    setView(G.fitView({ x: b.x - 200, y: b.y - 150, w: b.w + 400, h: b.h + 300 }, w, h, 40, 1.3), { user: true });
    setTool("select");
    setSelection([item.id]);
  }

  // ---------- presenting & following ----------
  function followView(hv) {
    if (!hv) return;
    if (hv.page !== pageRef.current) setPage(hv.page, { keepView: true });
    const { w, h } = sizeRef.current;
    if (!w) return;
    const vw = hv.width / hv.zoom;
    const vh = hv.height / hv.zoom;
    const zoom = G.clamp(Math.min(w / vw, h / vh), G.MIN_ZOOM, G.MAX_ZOOM);
    setView({ x: hv.x + vw / 2 - w / 2 / zoom, y: hv.y + vh / 2 - h / 2 / zoom, zoom });
  }

  function rejoinPresenter() {
    followingRef.current = true;
    setFollowing(true);
    followView(metaRef.current.view);
  }

  // The host broadcasts their view while presenting.
  useEffect(() => {
    if (!meta.presenting || !isHost || !size.w) return;
    const state = viewSendRef.current;
    const sendView = () => {
      state.last = performance.now();
      const v = viewRef.current;
      socket.emit("view:update", { page: pageRef.current, x: v.x, y: v.y, zoom: v.zoom, width: sizeRef.current.w, height: sizeRef.current.h });
    };
    clearTimeout(state.timer);
    const wait = VIEW_SEND_MS - (performance.now() - state.last);
    if (wait <= 0) sendView();
    else state.timer = setTimeout(sendView, wait);
  }, [view, pageId, meta.presenting, isHost, size]);

  // ---------- history timeline ----------
  function loadHistory() {
    setHistory({ state: "loading" });
    socket.emit("history:get", (data) => {
      if (!data) return setHistory({ state: "idle" });
      const timeline = buildTimeline(data.items, data.entries);
      const count = data.entries.length;
      historyRef.current = { timeline, items: stateAt(timeline, count) };
      setSelection([]);
      setEditor(null);
      setHistory({ state: "ready", count, index: count, entry: entryInfo(data.entries[count - 1]), playing: false });
      markDirty();
    });
  }

  const entryInfo = (entry) => (entry ? { ts: entry.ts, label: describeEntry(entry) } : null);

  function scrubHistory(index) {
    const h = historyRef.current;
    if (!h) return;
    h.items = stateAt(h.timeline, index);
    setHistory((s) => ({ ...s, index, entry: entryInfo(h.timeline.entries[index - 1]) }));
    markDirty();
  }

  function togglePlay() {
    if (playRef.current) {
      clearInterval(playRef.current);
      playRef.current = null;
      setHistory((s) => ({ ...s, playing: false }));
      return;
    }
    const h = historyRef.current;
    if (!h) return;
    const count = h.timeline.entries.length;
    let index = history.index >= count ? 0 : history.index;
    setHistory((s) => ({ ...s, playing: true }));
    playRef.current = setInterval(() => {
      index += 1;
      scrubHistory(index);
      if (index >= count) {
        clearInterval(playRef.current);
        playRef.current = null;
        setHistory((s) => ({ ...s, playing: false }));
      }
    }, Math.max(25, Math.min(400, 8000 / Math.max(1, count))));
  }

  function exitHistory() {
    clearInterval(playRef.current);
    playRef.current = null;
    historyRef.current = null;
    setHistory({ state: "idle" });
    markDirty();
  }

  useEffect(() => () => clearInterval(playRef.current), []);

  // ---------- realtime events ----------
  const handlersRef = useRef({});
  handlersRef.current = {
    op({ op }) {
      store.apply(op);
      pruneSelection();
      bump();
    },
    "op:rejected"({ reason }) {
      showToast(REJECTIONS[reason] || "That change couldn’t be saved.");
      resync();
    },
    "stroke:begin"(stroke) {
      activeRef.current.set(stroke.id, { ...stroke, points: [...stroke.points] });
      markDirty();
    },
    "stroke:points"({ id, points }) {
      const stroke = activeRef.current.get(id);
      if (!stroke) return;
      stroke.points.push(...points);
      markDirty();
    },
    "stroke:end"({ id }) {
      const stroke = activeRef.current.get(id);
      if (!stroke) return;
      activeRef.current.delete(id);
      store.apply({ kind: "add", items: [stroke] });
      bump();
    },
    "stroke:cancel"({ id }) {
      activeRef.current.delete(id);
      markDirty();
    },
    "user:joined"(user) {
      setUsers((list) => [...list.filter((u) => u.id !== user.id), user]);
      showToast(`${user.name} joined the board`);
    },
    "user:left"({ id }) {
      const leaving = usersRef.current.find((u) => u.id === id);
      if (leaving) showToast(`${leaving.name} left`);
      setUsers((list) => list.filter((u) => u.id !== id));
      setCursors(({ [id]: _gone, ...rest }) => rest);
      lasersRef.current.delete(id);
    },
    "cursor:move"({ id, x, y, page }) {
      setCursors((c) => {
        if (x === null) {
          const { [id]: _gone, ...rest } = c;
          return rest;
        }
        return { ...c, [id]: { x, y, page } };
      });
    },
    laser({ id, page, points }) {
      const user = usersRef.current.find((u) => u.id === id);
      let laser = lasersRef.current.get(id);
      if (!laser || laser.page !== page) {
        laser = { page, color: user?.color || "#ff7fa8", points: [] };
        lasersRef.current.set(id, laser);
      }
      const now = performance.now();
      points.forEach(([x, y], k) => laser.points.push({ x, y, t: now - (points.length - 1 - k) * 8 }));
      markDirty();
    },
    reaction({ id, emoji, x, y, page }) {
      const key = G.makeId("rx");
      setReactions((list) => [...list, { key, id, emoji, x, y, page }]);
      setTimeout(() => setReactions((list) => list.filter((r) => r.key !== key)), 2200);
    },
    "room:meta"(next) {
      const prev = metaRef.current;
      metaRef.current = next;
      setMeta(next);
      setTimerOffset(next.serverNow - Date.now());
      if (!next.pages.some((p) => p.id === pageRef.current)) setPage(next.pages[0].id);
      if (next.host !== prev.host) {
        if (next.host === me.clientId) showToast("You are now the host of this room.");
        else {
          const host = usersRef.current.find((u) => u.clientId === next.host);
          if (host) showToast(`${host.name} is now the host.`);
        }
      }
      if (next.locked !== prev.locked && next.host !== me.clientId) {
        showToast(next.locked ? "The host locked the board." : "The board is unlocked — draw away.");
        if (next.locked && EDIT_TOOLS.has(toolRef.current)) setTool("select");
      }
      if (next.presenting && !prev.presenting && next.host !== me.clientId) {
        followingRef.current = true;
        setFollowing(true);
        showToast("The host is presenting — you’re following their view.");
        followView(next.view);
      }
      if (next.spotlight !== prev.spotlight && next.spotlight) {
        const who = usersRef.current.find((u) => u.clientId === next.spotlight);
        if (who) showToast(next.spotlight === me.clientId ? "You’re in the spotlight ✦" : `Spotlight on ${who.name}`);
      }
    },
    "view:follow"(hv) {
      if (followingRef.current && metaRef.current.presenting && !isHostRef.current) followView(hv);
    },
    "rate:limited"() {
      showToast("You’re sending changes very fast — some were skipped.");
    }
  };

  useEffect(() => {
    const events = Object.keys(handlersRef.current);
    const bound = events.map((name) => [name, (data) => handlersRef.current[name](data)]);
    bound.forEach(([name, fn]) => socket.on(name, fn));
    return () => bound.forEach(([name, fn]) => socket.off(name, fn));
  }, []);

  // A rejoin after reconnecting delivers a fresh snapshot of the room.
  useEffect(() => {
    if (session === firstSessionRef.current) return;
    store.load(session.items);
    activeRef.current = new Map(session.active.map((s) => [s.id, { ...s, points: [...s.points] }]));
    setUsers(session.users);
    metaRef.current = session.meta;
    setMeta(session.meta);
    if (!session.meta.pages.some((p) => p.id === pageRef.current)) setPage(session.meta.pages[0].id);
    pruneSelection();
    bump();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);

  // ---------- keyboard, clipboard, drag & drop ----------
  const keyRef = useRef(null);
  keyRef.current = (e) => {
    if (e.target.closest?.("input, textarea, select, [contenteditable]")) return;
    const mod = e.metaKey || e.ctrlKey;
    const key = e.key.toLowerCase();

    if (e.key === " " && !e.repeat) {
      spaceRef.current = true;
      stageRef.current.classList.add("grab");
      e.preventDefault();
      return;
    }
    if (e.key === "Escape") {
      if (confirm) setConfirm(null);
      else if (inHistory) exitHistory();
      else setSelection([]);
      return;
    }
    if (mod && key === "z") {
      e.preventDefault();
      if (!readOnly) (e.shiftKey ? redo : undo)();
      return;
    }
    if (mod && key === "y") {
      e.preventDefault();
      if (!readOnly) redo();
      return;
    }
    if (mod && key === "a") {
      e.preventDefault();
      setTool("select");
      setSelection(pageItems().map((i) => i.id));
      return;
    }
    if (mod && key === "d") {
      e.preventDefault();
      if (!readOnly) duplicateItems(selectedItems());
      return;
    }
    if (mod && key === "0") {
      e.preventDefault();
      resetZoom();
      return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && selectionRef.current.length && !readOnly) {
      e.preventDefault();
      deleteSelected();
      return;
    }
    if (e.key.startsWith("Arrow") && selectionRef.current.length && !readOnly) {
      e.preventDefault();
      const step = (e.shiftKey ? 10 : 1) / Math.min(1, viewRef.current.zoom);
      nudge(
        e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0,
        e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0
      );
      return;
    }
    if (e.key === "PageDown" || e.key === "PageUp") {
      e.preventDefault();
      const pages = metaRef.current.pages;
      const i = pages.findIndex((p) => p.id === pageRef.current) + (e.key === "PageDown" ? 1 : -1);
      if (pages[i]) setPage(pages[i].id, { user: true });
      return;
    }
    if (mod || e.altKey) return;
    if (e.shiftKey && e.code === "Digit1") return fitToContent();
    if (e.shiftKey && e.code === "Digit0") return resetZoom();
    if (key === "+" || key === "=") return zoomBy(1.2);
    if (key === "-" || key === "_") return zoomBy(1 / 1.2);
    if (key === "[") return setWidth((w) => Math.max(1, w - 1));
    if (key === "]") return setWidth((w) => Math.min(24, w + 1));
    const t = TOOLS.find((x) => x.key?.toLowerCase() === key);
    if (t) return setTool(t.id);
    const s = SHAPES.find((x) => x.key.toLowerCase() === key);
    if (s) return setTool("shape", s.id);
  };

  useEffect(() => {
    const down = (e) => keyRef.current(e);
    const up = (e) => {
      if (e.key === " ") {
        spaceRef.current = false;
        stageRef.current?.classList.remove("grab");
      }
    };
    const copy = (e) => {
      if (e.target.closest?.("input, textarea")) return;
      const items = selectedItems();
      if (!items.length) return;
      clipboardRef.current = items;
      e.clipboardData.setData("text/plain", PASTE_MARKER);
      e.preventDefault();
      showToast(`Copied ${items.length} item${items.length === 1 ? "" : "s"}`);
    };
    const paste = (e) => {
      if (e.target.closest?.("input, textarea") || readOnlyRef.current) return;
      const files = [...(e.clipboardData?.files || [])];
      const at = lastPointerRef.current || viewCenter();
      if (files.length) {
        e.preventDefault();
        insertFiles(files, at);
        return;
      }
      const text = e.clipboardData?.getData("text/plain") || "";
      if (text === PASTE_MARKER && clipboardRef.current.length) {
        e.preventDefault();
        duplicateItems(clipboardRef.current, 30);
      } else if (text.trim()) {
        e.preventDefault();
        commitOp({ kind: "add", items: [{ id: G.makeId("tx"), type: "text", page: pageRef.current, z: nextZ(), owner: me.clientId, x: round(at.x), y: round(at.y), size: textSizeFor(width), color, text: text.slice(0, 500) }] });
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("copy", copy);
    window.addEventListener("paste", paste);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("copy", copy);
      window.removeEventListener("paste", paste);
    };
  });

  function onDrop(e) {
    e.preventDefault();
    setDragOver(false);
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) insertFiles(files, worldPoint(e));
  }

  // ---------- exports & sharing ----------
  async function onExport(kind) {
    const page = meta.pages.find((p) => p.id === pageId);
    const items = renderItems();
    const name = `${room}-${page?.name || "board"}`;
    try {
      if (kind === "pdf") {
        const pages = meta.pages.map((p) => ({ name: p.name, items: store.pageItems(p.id) })).filter((p) => p.items.length);
        if (!pages.length) return showToast("There’s nothing to export yet.");
        setBusy("Building PDF…");
        await exportPdf(pages, background, room);
        showToast(`Exported ${pages.length} page${pages.length === 1 ? "" : "s"} as PDF`);
        return;
      }
      if (!items.length) return showToast("This page is empty.");
      setBusy("Rendering…");
      if (kind === "png") await exportPng(items, background, name);
      if (kind === "svg") await exportSvg(items, background, name);
      if (kind === "copy") {
        await copyPng(items, background);
        showToast("Board image copied to the clipboard");
      }
    } catch (error) {
      showToast(error.message || "Export failed.");
    } finally {
      setBusy(null);
    }
  }

  async function copyInvite() {
    const url = new URL(window.location.href);
    url.search = "";
    url.searchParams.set("room", room);
    try {
      await navigator.clipboard.writeText(url.toString());
      showToast(meta.hasCode ? "Invite link copied — share the access code separately" : "Invite link copied");
    } catch {
      showToast(url.toString());
    }
  }

  function react(emoji) {
    const p = lastPointerRef.current || viewCenter();
    socket.emit("reaction", { emoji, x: Math.round(p.x), y: Math.round(p.y), page: pageRef.current });
  }

  // ---------- toasts ----------
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(t);
  }, [toast]);

  // ---------- derived view data ----------
  /* eslint-disable react-hooks/exhaustive-deps */
  const livePageItems = useMemo(() => pageItems(), [rev, pageId]);
  const shownItems = inHistory ? renderItems() : livePageItems;
  const counts = useMemo(() => store.countByPage(), [rev]);
  const topNotes = useMemo(() =>
    [...store.items.values()].filter((i) => i.type === "note" && i.votes?.length)
      .sort((a, b) => b.votes.length - a.votes.length).slice(0, 5), [rev]);
  const selectedList = useMemo(() => selection.map((id) => store.get(id)).filter(Boolean), [selection, rev]);
  /* eslint-enable react-hooks/exhaustive-deps */
  const userById = Object.fromEntries(users.map((u) => [u.id, u]));
  const spotUser = users.find((u) => u.clientId === meta.spotlight && u.id !== me.id);
  const spotCursor = spotUser && cursors[spotUser.id];
  const screen = (x, y) => G.worldToScreen(view, x, y);
  const light = isLightBackground(background);
  const pageIndex = meta.pages.findIndex((p) => p.id === pageId);

  const editorNode = (() => {
    if (!editor) return null;
    const p = screen(editor.x, editor.y);
    if (editor.kind === "text") {
      return <TextEditor key={editor.id || "new-text"} left={p.x} top={p.y} fontSize={editor.size * view.zoom} color={editor.color} initial={editor.initial} onCommit={commitText} onCancel={() => setEditor(null)} />;
    }
    if (editor.kind === "note") {
      return (
        <NoteEditor
          key={editor.id || "new-note"}
          left={p.x}
          top={p.y}
          width={editor.w * view.zoom}
          height={editor.h * view.zoom}
          fontSize={G.NOTE_TEXT_SIZE * view.zoom}
          padding={G.NOTE_PADDING * view.zoom}
          background={editor.color}
          initial={editor.initial}
          onCommit={commitNote}
          onCancel={() => setEditor(null)}
        />
      );
    }
    const left = G.clamp(p.x, 12, Math.max(12, size.w - 372));
    const top = G.clamp(p.y, 12, Math.max(12, size.h - 330));
    return <EquationEditor key={editor.id || "new-eq"} left={left} top={top} color={editor.color} initial={editor.initial} onCommit={commitEquation} onCancel={() => setEditor(null)} />;
  })();

  return (
    <div className="app">
      <aside className="rail glass" aria-label="Lumi navigation">
        <div className="logo"><LumiSpark size={34} label="Lumi" /></div>
        <a className="nav" href="/app"><Icon name="home" /><small>Home</small></a>
        {/* The static Universe / Atlas pages (client/public/lumi/) still list the old example curriculum: no links to them in Phase 1. */}
        <span className="nav active" aria-current="page"><Icon name="board" /><small>Board</small></span>
      </aside>

      <main className="main">
        <TopBar
          room={room}
          course={course}
          hasCode={meta.hasCode}
          status={status}
          users={users}
          me={me}
          hostId={meta.host}
          timer={meta.timer}
          timerOffset={timerOffset}
          onTimerFinish={() => showToast("⏰ Time’s up!")}
          presenting={meta.presenting}
          isHost={isHost}
          following={following}
          onRejoin={rejoinPresenter}
          onExport={onExport}
          panelOpen={panelOpen}
          onTogglePanel={() => setPanelOpen((o) => !o)}
          onLeave={onLeave}
          onCopyInvite={copyInvite}
        />

        <div className={"workspace" + (panelOpen ? "" : " panel-closed")}>
          <ToolDock
            tool={tool}
            shapeKind={shapeKind}
            onTool={setTool}
            onImage={(files) => insertFiles(files)}
            onUndo={undo}
            onRedo={redo}
            canUndo={undoRef.current.canUndo}
            canRedo={undoRef.current.canRedo}
            readOnly={readOnly}
          />

          <section className="stage-wrap">
            <div
              ref={stageRef}
              className={`stage tool-${tool}${light ? " light" : ""}${dragOver ? " drag-over" : ""}`}
              onDragOver={(e) => {
                if ([...(e.dataTransfer?.types || [])].includes("Files")) {
                  e.preventDefault();
                  setDragOver(true);
                }
              }}
              onDragLeave={(e) => e.currentTarget === e.target && setDragOver(false)}
              onDrop={onDrop}
            >
              <canvas ref={bgRef} className="layer" aria-hidden="true" />
              <canvas
                ref={canvasRef}
                className="layer board-canvas"
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
                onPointerLeave={() => {
                  socket.emit("cursor:move", null);
                  if (toolRef.current === "eraser") {
                    lastPointerRef.current = null;
                    markDirty();
                  }
                }}
                onDoubleClick={onDoubleClick}
                onContextMenu={(e) => e.preventDefault()}
                aria-label="Shared drawing canvas"
              />

              {Object.entries(cursors).map(([id, c]) => {
                const user = userById[id];
                if (!user || c.page !== pageId) return null;
                const p = screen(c.x, c.y);
                if (p.x < -40 || p.y < -40 || p.x > size.w + 40 || p.y > size.h + 40) return null;
                const spot = user.clientId === meta.spotlight;
                return (
                  <div key={id} className={"cursor" + (spot ? " spotlight" : "")} style={{ left: p.x, top: p.y, "--c": user.color }}>
                    {spot && <i className="spot-ring" />}
                    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true"><path d="M2 2l5.5 14 2.2-5.8L15.5 8z" /></svg>
                    <span>{spot && "✦ "}{user.name}</span>
                  </div>
                );
              })}

              {reactions.filter((r) => r.page === pageId).map((r) => {
                const p = screen(r.x, r.y);
                return (
                  <span key={r.key} className="reaction" style={{ left: p.x, top: p.y }}>
                    {r.emoji}
                    <small style={{ "--c": userById[r.id]?.color }}>{userById[r.id]?.name}</small>
                  </span>
                );
              })}

              {editorNode}

              {!shownItems.length && !editor && !activeRef.current.size && (
                <div className="empty-hint">
                  <div className="eyebrow">{inHistory ? "NOTHING HERE YET" : "A BLANK SKY"}</div>
                  <p>
                    {inHistory
                      ? "Move the timeline forward to see the board fill in."
                      : <>Sketch, drop a PDF, or add a template. Everyone in <b>{room}</b> sees it live.</>}
                  </p>
                </div>
              )}

              <div className="stage-banners">
                {locked && !inHistory && <div className="banner"><Icon name="lock" size={15} /> The host has locked the board — you can still vote and react.</div>}
                {inHistory && (
                  <div className="banner history">
                    <Icon name="history" size={15} /> Viewing history{history.entry ? ` · ${new Date(history.entry.ts).toLocaleTimeString()}` : ""}
                    <button className="chip" onClick={exitHistory}>Back to live</button>
                  </div>
                )}
                {spotUser && spotCursor && spotCursor.page !== pageId && (
                  <div className="banner spot">
                    <Icon name="spotlight" size={15} filled /> Spotlight on {spotUser.name}
                    <button className="chip" onClick={() => { setPage(spotCursor.page, { user: true }); centerOn(spotCursor.x, spotCursor.y); }}>Go there</button>
                  </div>
                )}
              </div>

              {dragOver && <div className="drop-hint"><Icon name="image" size={28} /><b>Drop images or PDFs</b><span>PDF pages become board pages you can annotate</span></div>}
              {busy && <div className="busy"><i className="spinner" />{busy}</div>}

              <PageBar
                pages={meta.pages}
                pageId={pageId}
                onPage={(id) => setPage(id, { user: true })}
                onAddPage={addPage}
                canAdd={!readOnly}
                zoom={view.zoom}
                onZoomIn={() => zoomBy(1.2)}
                onZoomOut={() => zoomBy(1 / 1.2)}
                onFit={fitToContent}
                onResetZoom={resetZoom}
              />
              {size.w > 700 && (
                <Minimap items={shownItems} view={view} size={size} light={light} onNavigate={(x, y) => centerOn(x, y)} />
              )}
            </div>

            <ContextDock
              tool={tool}
              shapeKind={shapeKind}
              color={color}
              setColor={setColor}
              width={width}
              setWidth={setWidth}
              fill={fill}
              setFill={setFill}
              noteColor={noteColor}
              setNoteColor={setNoteColor}
              shapeAssist={shapeAssist}
              setShapeAssist={setShapeAssist}
              selection={selectedList}
              readOnly={readOnly}
              isHost={isHost}
              onClearPage={clearPage}
              onRecolor={recolor}
              onSelectionWidth={setSelectionWidth}
              onSelectionFill={toggleSelectionFill}
              onDuplicate={() => duplicateItems(selectedItems())}
              onFront={() => reorder(true)}
              onBack={() => reorder(false)}
              onDelete={deleteSelected}
            />
          </section>

          <SidePanel
            tab={tab}
            setTab={setTab}
            aiPanel={tab === "ai" ? (
              <BoardAI
                room={room}
                courseId={course?.id}
                pageItems={livePageItems}
                selected={selectedList}
                background={background}
                canEdit={!readOnly}
                onBusyChange={onAiBusy}
                onPin={pinNote}
                onAddText={addTextItem}
              />
            ) : null}
            me={me}
            users={users}
            meta={meta}
            isHost={isHost}
            canEdit={!readOnly}
            onLock={(on) => socket.emit("host:lock", on)}
            onPresent={(on) => socket.emit("host:present", on)}
            onSpotlight={(clientId) => socket.emit("host:spotlight", clientId)}
            onTransfer={(clientId) => socket.emit("host:transfer", clientId)}
            onSetCode={(code) => {
              socket.emit("host:code", code);
              showToast(code ? "Access code set — this room is now private" : "Access code removed");
            }}
            onCopyInvite={copyInvite}
            onReact={react}
            pages={meta.pages}
            pageId={pageId}
            counts={counts}
            onPage={(id) => setPage(id, { user: true })}
            onAddPage={addPage}
            onRenamePage={renamePage}
            onDeletePage={deletePage}
            onClearPage={clearPage}
            timerOffset={timerOffset}
            onTimer={(action, seconds) => socket.emit("timer:control", { action, seconds })}
            onTemplate={insertTemplate}
            background={background}
            onBackground={changeBackground}
            topNotes={topNotes}
            onJumpTo={jumpTo}
            history={history}
            onLoadHistory={loadHistory}
            onScrub={scrubHistory}
            onPlayHistory={togglePlay}
            onExitHistory={exitHistory}
          />
        </div>

        {confirm && (
          <div className="dialog-backdrop" onPointerDown={(e) => e.target === e.currentTarget && setConfirm(null)}>
            <div className="review" role="dialog" aria-labelledby="confirmTitle">
              <button className="close" onClick={() => setConfirm(null)} aria-label="Cancel">×</button>
              <div className="eyebrow">SHARED ACTION</div>
              <h3 id="confirmTitle">{confirm.title}</h3>
              <p>{confirm.body}</p>
              <div className="review-actions">
                <button className="btn btn-secondary" onClick={() => setConfirm(null)}>Cancel</button>
                <button className="btn btn-primary danger" onClick={() => { confirm.run(); setConfirm(null); }}>{confirm.label}</button>
              </div>
            </div>
          </div>
        )}

        {toast && <div key={toast.key} className="toast show" role="status">{toast.text}</div>}
        <span className="sr-only" aria-live="polite">{`Page ${pageIndex + 1} of ${meta.pages.length}`}</span>
      </main>
    </div>
  );
}
