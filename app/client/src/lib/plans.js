// The plans of the business model (Free / Educator / Campus / Enterprise) as the app shows them. The server enforces
// the same table (app/server/src/plans.js); this copy decides what the screens offer. There are no real accounts yet,
// so the plan is a preview setting of this browser, chosen in Settings, exactly like the role.
import { getUser } from "./session.js";

export const PLAN_IDS = ["free", "educator", "campus", "enterprise"];
export const DEFAULT_PLAN = "campus";
const FULL = { boardAi: true, voiceAi: true, aiPerDay: null, exams: null, classTools: true };
export const PLANS = {
  free: { boardAi: false, voiceAi: false, aiPerDay: 30, exams: 1, classTools: false },
  educator: FULL,
  campus: FULL,
  enterprise: FULL
};

export const planId = (user = getUser()) => (PLAN_IDS.includes(user?.plan) ? user.plan : DEFAULT_PLAN);
export const planOf = (user = getUser()) => PLANS[planId(user)];
