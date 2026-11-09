// The exams a learner adds on the Assessment Prep page. Kept in this browser, like the preview accounts, until accounts exist
// on a server. Plans keep the engine's own shape; nothing here is sent anywhere.
import { readStored, store } from "./identity.js";

const KEY = "ednova-exams";
const DAY_MS = 86_400_000;

function read() {
  try {
    const list = JSON.parse(readStored(KEY, "[]"));
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}
const write = (list) => store(KEY, JSON.stringify(list));

export const listExams = () => read().sort((a, b) => a.date.localeCompare(b.date));
export const getExam = (id) => read().find((e) => e.id === id) || null;

export function addExam({ title, date, minutes = 40 }) {
  const exam = { id: `exam-${Math.random().toString(36).slice(2, 10)}`, title: title.trim(), date, minutes, plan: null, done: {} };
  write([...read(), exam]);
  return exam;
}

export function updateExam(id, patch) {
  write(read().map((e) => (e.id === id ? { ...e, ...patch } : e)));
  return getExam(id);
}

export const removeExam = (id) => write(read().filter((e) => e.id !== id));

const pad = (n) => String(n).padStart(2, "0");
export const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const midnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());

/** Whole days from today to an ISO date (yyyy-mm-dd); 0 = today, negative = past. */
export function daysLeft(date, now = new Date()) {
  const [y, m, d] = date.split("-").map(Number);
  return Math.round((new Date(y, m - 1, d) - midnight(now)) / DAY_MS);
}

export function inDays(n, now = new Date()) {
  return isoDay(new Date(now.getFullYear(), now.getMonth(), now.getDate() + n));
}
