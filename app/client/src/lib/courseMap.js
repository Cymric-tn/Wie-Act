// The learner's course map: the engine's concepts with the learner's real mastery. One shared copy, so the progress
// map, the dashboard and the prep page agree; it refreshes when an exercise is graded (refreshCourseMap()).
import { useMemo, useSyncExternalStore } from "react";
import { buildCourse } from "./demoData.js";
import { clientId } from "./identity.js";
import { SERVER_URL } from "./api.js";
import { useI18n } from "./i18n";

let mastery = {};
let dataState = "pending"; // "pending" until the first answer, then "live" or "failed"
const listeners = new Set();

export async function refreshCourseMap(course = "analyse") {
  try {
    const res = await fetch(`${SERVER_URL}/api/ai/mastery?learner_id=${encodeURIComponent(clientId())}&course_id=${course}`);
    if (!res.ok) throw new Error(String(res.status));
    const data = await res.json();
    mastery = Object.fromEntries(Object.entries(data.mastery || {}).map(([k, v]) => [k, v.value]));
    dataState = "live";
  } catch {
    // offline: the map stays as it was
    if (dataState !== "live") dataState = "failed";
  }
  listeners.forEach((fn) => fn());
}

const subscribe = (fn) => {
  listeners.add(fn);
  return () => listeners.delete(fn);
};
const snapshot = () => mastery;

/** "failed" means the map is showing no real results for this learner: that is when it should say so. */
export const useCourseDataState = () => useSyncExternalStore(subscribe, () => dataState);

/** [course] for the learner, named in the UI language. */
export function useCourses() {
  const { lang } = useI18n();
  const m = useSyncExternalStore(subscribe, snapshot);
  return useMemo(() => [buildCourse(m, lang)], [m, lang]);
}
