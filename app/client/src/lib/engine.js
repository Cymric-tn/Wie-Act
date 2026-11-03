// The web app's side of the EdNova engine. Every call goes to the server's
// feature layer (/api/ai/*), which adds ids, keeps exercise solutions private
// and streams the engine's progress events back as NDJSON.
import { SERVER_URL } from "./api.js";
import { clientId, readStored, store } from "./identity.js";

export const ENGINE_COURSE = "MATH101"; // the course the engine has content for
export const AI_LANGUAGES = ["auto", "FRENCH", "ENGLISH", "ARABIC", "DARIJA", "ARABIZI"];

/** UI language → engine language (used for templates and quiz statements). */
export const engineLanguage = (lang) => ({ fr: "FRENCH", en: "ENGLISH", ar: "ARABIC" })[lang] || "FRENCH";

/** The learner's reply-language preference; "auto" lets the engine detect it from the text. */
export function replyLanguage() {
  const v = readStored("ednova-ai-lang", "auto");
  return AI_LANGUAGES.includes(v) ? v : "auto";
}
export const setReplyLanguage = (v) => store("ednova-ai-lang", v);

export class AiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/** POSTs and reads an NDJSON stream; onEvent gets each engine event as it happens. */
async function stream(path, body, { onEvent, onStage, signal } = {}) {
  let res;
  try {
    res = await fetch(SERVER_URL + path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new AiError("offline", 0);
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new AiError(data.error || `HTTP ${res.status}`, res.status);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final = null;
  const handle = (line) => {
    if (!line.trim()) return;
    const msg = JSON.parse(line);
    if (msg.type === "event") onEvent?.(msg.event, msg.stage);
    else if (msg.type === "stage") onStage?.(msg);
    else if (msg.type === "result") final = msg;
    else if (msg.type === "error") throw new AiError(msg.error, 500);
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let nl;
    while ((nl = buffer.indexOf("\n")) >= 0) {
      handle(buffer.slice(0, nl));
      buffer = buffer.slice(nl + 1);
    }
  }
  handle(buffer);
  if (!final) throw new AiError("no result", 500);
  return final;
}

async function json(path, options = {}) {
  let res;
  try {
    res = await fetch(SERVER_URL + path, {
      ...options,
      headers: options.body ? { "content-type": "application/json" } : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined
    });
  } catch {
    throw new AiError("offline", 0);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new AiError(data.error || `HTTP ${res.status}`, res.status);
  return data;
}

function body(extra = {}, context = {}) {
  const lang = context.language ?? replyLanguage();
  return {
    learner_id: clientId(),
    ...extra,
    context: { course_id: ENGINE_COURSE, ...context, language: lang === "auto" ? undefined : lang }
  };
}

export const ai = {
  /** One engine task. Resolves to the (public) Result. */
  run: (task, inputs, { context, priority, onEvent, signal } = {}) =>
    stream("/api/ai/run", body({ task, inputs, priority }, context), { onEvent, signal }).then((r) => r.result),

  /** One tutor turn: classify → follow-up task. Resolves to { classification, follow, result }. */
  turn: (message, { history, exclude, exerciseId, hintsUsed, context, onEvent, onStage, signal } = {}) =>
    stream("/api/ai/turn", body({ message, history, exclude, exercise_id: exerciseId, hints_used: hintsUsed }, context), { onEvent, onStage, signal }),

  /** answer | hint | reveal for a bank exercise. */
  exercise: (id, action, extra = {}, { context, onEvent } = {}) =>
    stream(`/api/ai/exercises/${encodeURIComponent(id)}/${action}`, body(extra, context), { onEvent }),

  status: () => json("/api/ai/status"),
  mastery: (course = ENGINE_COURSE) => json(`/api/ai/mastery?learner_id=${encodeURIComponent(clientId())}&course_id=${course}`),
  course: (course = ENGINE_COURSE) => json(`/api/ai/courses/${course}`).then((r) => r.course),
  report: (requestId, note) => json("/api/ai/flags", { method: "POST", body: { request_id: requestId, learner_id: clientId(), note } }),
  flags: (status = "open") => json(`/api/ai/flags?status=${status}`).then((r) => r.flags),
  resolve: (requestId, action) => json(`/api/ai/flags/${encodeURIComponent(requestId)}/resolve`, { method: "POST", body: { action, role: "professor" } })
};

const MIME = { pdf: "application/pdf", md: "text/markdown", markdown: "text/markdown", txt: "text/plain", docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" };
export const MATERIAL_ACCEPT = ".pdf,.md,.markdown,.txt,.docx";
export const mimeFor = (file) => MIME[file.name.split(".").pop().toLowerCase()] || file.type;

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

/** Text layer of a PDF, so even the built-in engine can index it. Scanned pages have none. */
async function pdfText(buffer) {
  const pdfjs = await import("pdfjs-dist");
  const worker = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
  pdfjs.GlobalWorkerOptions.workerSrc = worker.default;
  const task = pdfjs.getDocument({ data: buffer.slice(0) });
  try {
    const doc = await task.promise;
    const pages = [];
    for (let i = 1; i <= Math.min(doc.numPages, 200); i++) {
      const content = await (await doc.getPage(i)).getTextContent();
      pages.push(content.items.map((it) => it.str).join(" "));
    }
    return pages.map((p, i) => `## Page ${i + 1}\n${p}`).join("\n\n");
  } finally {
    await task.destroy();
  }
}

export const materials = {
  list: (user) => json(`/api/materials?course_id=${ENGINE_COURSE}&learner_id=${encodeURIComponent(clientId())}&role=${user.role}`).then((r) => r.materials),
  async upload(file, { title, visibility, user }) {
    const mime = mimeFor(file);
    const buffer = await file.arrayBuffer();
    let text = "";
    if (mime === "application/pdf") text = await pdfText(buffer).catch(() => "");
    return json("/api/materials", {
      method: "POST",
      body: { title, filename: file.name, mime, data: toBase64(buffer), text, course_id: ENGINE_COURSE, visibility, owner_id: clientId(), owner_name: user.name, role: user.role }
    }).then((r) => r.material);
  },
  remove: (id, user) => json(`/api/materials/${id}`, { method: "DELETE", body: { learner_id: clientId(), role: user.role } }),
  confirmTag: (id, chunkId, concept) => json(`/api/materials/${id}/chunks/${encodeURIComponent(chunkId)}`, { method: "PATCH", body: { concept, role: "professor" } }),
  fileUrl: (m, user) => `${SERVER_URL}${m.url}?learner_id=${encodeURIComponent(clientId())}&role=${user.role}`
};
