// Plans from the business model canvas ("Revenue and packaging"): what each one may use, enforced on the server.
//
//   free        individuals: whiteboard and voice WITHOUT AI, a capped number of AI requests a day, no class tools
//   educator    tutors and small cohorts: everything with AI, class material and answer review
//   campus      institutions: the same, plus institutional administration
//   enterprise  networks: the same, with custom integrations
//
// There are no real accounts yet (the profile lives in the browser), so the plan arrives with the request exactly as
// the role does. What is enforced here is the RULE; binding a plan to a verified account is part of authentication.
// LUMI_DEFAULT_PLAN (default "campus") is what a request without a plan gets, so a demo is not capped by accident.
const FULL = { boardAi: true, voiceAi: true, aiPerDay: null, exams: null, classTools: true };
const PLANS = {
  free: { boardAi: false, voiceAi: false, aiPerDay: 30, exams: 1, classTools: false },
  educator: { ...FULL },
  campus: { ...FULL, admin: true },
  enterprise: { ...FULL, admin: true, customIntegrations: true }
};
const DEFAULT_PLAN = Object.hasOwn(PLANS, process.env.LUMI_DEFAULT_PLAN || "") ? process.env.LUMI_DEFAULT_PLAN : "campus";

const planOf = (value) => (typeof value === "string" && Object.hasOwn(PLANS, value) ? value : DEFAULT_PLAN);

/** Daily AI request counters for capped plans, per learner, in memory (they reset with the UTC day or a restart). */
function createUsage({ now = () => Date.now(), caps = {} } = {}) {
  const used = new Map(); // "learner|day" -> n
  const day = () => new Date(now()).toISOString().slice(0, 10);
  const capOf = (plan) => (caps[plan] !== undefined ? caps[plan] : PLANS[plan].aiPerDay);
  return {
    capOf,
    /** Counts one request. { ok, remaining, cap }; a plan without a cap always passes. */
    take(plan, learner) {
      const cap = capOf(plan);
      if (cap == null) return { ok: true, remaining: null, cap: null };
      const key = `${learner || "anonymous"}|${day()}`;
      if (used.size > 50_000) for (const k of used.keys()) if (!k.endsWith(day())) used.delete(k);
      const n = used.get(key) || 0;
      if (n >= cap) return { ok: false, remaining: 0, cap };
      used.set(key, n + 1);
      return { ok: true, remaining: cap - n - 1, cap };
    },
    remaining(plan, learner) {
      const cap = capOf(plan);
      return cap == null ? null : Math.max(0, cap - (used.get(`${learner || "anonymous"}|${day()}`) || 0));
    }
  };
}

module.exports = { PLANS, DEFAULT_PLAN, planOf, createUsage };
