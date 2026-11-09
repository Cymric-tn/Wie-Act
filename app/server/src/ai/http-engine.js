// Client for the Python bridge (app/bridge/bridge.py), the one service that hosts the EdNova engine: one Engine per
// process, as the engine guide requires. Results stream back as NDJSON — one {"type":"event"} line per engine Event, then
// one {"type":"result"} line — so the UI can show staged progress. Every call carries the shared secret in X-Engine-Token.
//
// Like engine.run itself, run() never throws a provider/transport error: when the bridge is down it returns a degraded
// Result (service_unavailable). A bad task or missing input still throws RangeError (the engine's ValueError).

const crypto = require("crypto");

const RUN_TIMEOUT_MS = 120_000; // explain is bounded at 25 s; OCR at 40 s; background prep can queue longer
const TOKEN_HEADER = "x-engine-token";
const DIAGRAM_TIMEOUT_MS = 30_000; // the bridge stops the maths at 10 s; this also covers waiting behind another diagram

function createHttpEngine({ url, token = "", fetchImpl = fetch, timeoutMs = RUN_TIMEOUT_MS } = {}) {
  const base = String(url || "http://127.0.0.1:8765").replace(/\/+$/, "");
  const headers = (extra) => ({ ...(token ? { [TOKEN_HEADER]: token } : {}), ...extra });

  async function call(path, { method = "GET", body, timeout = 8000 } = {}) {
    const res = await fetchImpl(base + path, {
      method,
      headers: headers(body ? { "content-type": "application/json" } : undefined),
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(timeout)
    });
    const data = await res.json().catch(() => ({}));
    if (res.status === 400) throw new RangeError(data.error || "bad request");
    if (!res.ok) {
      const error = new Error(data.error || `bridge HTTP ${res.status}`);
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function unavailable(task, started, detail) {
    return {
      request_id: crypto.randomUUID(), task, text: null, data: null, model_class_used: null, model: null,
      from_cache: false, degraded: true, degrade_reason: "service_unavailable", eta_seconds: 15, mode: null,
      followup_available: false, events: [], latency_ms: Date.now() - started, tokens_in: 0, tokens_out: 0,
      repaired: false, safe_default: false, route_reason: "bridge unreachable", notes: { bridge_error: detail }
    };
  }

  /** POST to a streaming route; resolves to the final result object, or a degraded one when the bridge is unreachable. */
  async function stream(path, body, task, onEvent) {
    const started = Date.now();
    let res;
    try {
      res = await fetchImpl(base + path, {
        method: "POST",
        headers: headers({ "content-type": "application/json", accept: "application/x-ndjson" }),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs)
      });
    } catch (error) {
      return unavailable(task, started, error.message);
    }
    if (res.status === 400) {
      const data = await res.json().catch(() => ({}));
      throw new RangeError(data.error || "bad request");
    }
    if (!res.ok || !res.body) return unavailable(task, started, res.status === 401 ? "the bridge refused the token (ENGINE_SERVICE_TOKEN differs)" : `HTTP ${res.status}`);

    let result = null;
    let buffer = "";
    const decoder = new TextDecoder();
    const handle = (line) => {
      if (!line.trim()) return;
      let msg;
      try { msg = JSON.parse(line); } catch { return; }
      if (msg.type === "event") {
        try { onEvent?.(msg.event); } catch { /* UI callbacks never break a run */ }
      } else if (msg.type === "result") result = msg.result;
      else if (msg.type === "error" && msg.kind === "value") throw new RangeError(msg.error);
    };
    try {
      for await (const chunk of res.body) {
        buffer += decoder.decode(chunk, { stream: true });
        let nl;
        while ((nl = buffer.indexOf("\n")) >= 0) {
          handle(buffer.slice(0, nl));
          buffer = buffer.slice(nl + 1);
        }
      }
      handle(buffer);
    } catch (error) {
      if (error instanceof RangeError) throw error;
      return unavailable(task, started, error.message);
    }
    return result || unavailable(task, started, "stream ended without a result");
  }

  return {
    kind: "engine",
    url: base,
    run: (task, inputs = {}, context = {}, priority = "INTERACTIVE", onEvent) => stream("/run", { task, inputs, context, priority }, task, onEvent),
    /** Engine.generate_page: one prep page (explanation, worked example, quiz items). Resolves to the page, or a degraded Result. */
    generatePage: (focusPoint, context = {}, onEvent) => stream("/generate_page", { focus_point: focusPoint, context }, "generate_page", onEvent),
    followUp: (classification, hasOpenExercise = false) => call("/follow_up", { method: "POST", body: { classification, has_open_exercise: Boolean(hasOpenExercise) } }),
    getExercise: (id) => call(`/exercises/${encodeURIComponent(id)}`).then((r) => r.exercise || null).catch((e) => (e.status === 404 ? null : Promise.reject(e))),
    course: (id) => call(`/courses/${encodeURIComponent(id)}`).then((r) => r.course || null).catch((e) => (e.status === 404 ? null : Promise.reject(e))),
    addMaterial: (material) => call("/materials", { method: "POST", body: material, timeout: 180_000 }),
    removeMaterial: (id, course_id, owner) => call(`/materials/${encodeURIComponent(id)}?course_id=${encodeURIComponent(course_id || "")}&owner=${encodeURIComponent(owner || "")}`, { method: "DELETE" }),
    confirmChunk: (chunkId, concept) => call(`/materials/chunks/${encodeURIComponent(chunkId)}`, { method: "PATCH", body: { concept } }),
    invalidate: (requestId) => call("/cache/invalidate", { method: "POST", body: { request_id: requestId } }),
    metrics: (since) => call(`/metrics/summary${since ? `?since=${encodeURIComponent(since)}` : ""}`),
    /** Diagrams: exact maths, no LLM. A refusal is a RangeError whose message is written for the learner. */
    diagram: (request) => call("/diagrams", { method: "POST", body: request, timeout: DIAGRAM_TIMEOUT_MS }).then((r) => r.spec),
    checkDiagram: (spec, answer, lang) => call("/diagrams/check", { method: "POST", body: { spec, answer, lang }, timeout: DIAGRAM_TIMEOUT_MS }),
    health: () => call("/health", { timeout: 2500 }),
    async close() {}
  };
}

module.exports = { createHttpEngine };
