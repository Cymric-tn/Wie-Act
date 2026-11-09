// The diagram routes of the server (/api/diagrams): exact maths, no LLM. And the hand-over from the generator page to the board.
import { SERVER_URL } from "../api.js";
import { clientId } from "../identity.js";
import { ENGINE_COURSE } from "../engine.js";

const PENDING_KEY = "ednova-pending-diagram";

export class DiagramError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function post(path, body) {
  let res;
  try {
    res = await fetch(SERVER_URL + path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new DiagramError("offline", 0);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new DiagramError(data.error || `HTTP ${res.status}`, res.status);
  return data;
}

export const buildDiagram = (text, { lang, practice } = {}) =>
  post("/api/diagrams", { text, lang, practice: practice || undefined, course_id: ENGINE_COURSE, learner_id: clientId() }).then((r) => r.diagram);

export const checkDiagram = (spec, answer, lang) => post("/api/diagrams/check", { spec, answer, lang });

/** Kept in this tab until the board has opened, so the lobby can come in between. */
export function setPendingDiagram(primitives) {
  try {
    sessionStorage.setItem(PENDING_KEY, JSON.stringify(primitives));
    return true;
  } catch {
    return false;
  }
}

export function takePendingDiagram() {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    sessionStorage.removeItem(PENDING_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
