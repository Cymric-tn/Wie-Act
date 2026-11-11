// The course graph is the engine's (generated from data/courses/analyse.yaml); mastery is the learner's real practice
// results (/api/ai/mastery). The rooms, materials and class lists below are still example data for the preview
// dashboards, all about the one course the engine has content for: analyse.
import { ANALYSE } from "./analyse.generated.js";

export const COURSE_ID = ANALYSE.id;
export const COURSE_COLOR = "var(--lumi-iris)";

export const MASTERY = {
  mastered: { color: "var(--lumi-spark)", key: "mastery.mastered" },
  learning: { color: "var(--lumi-iris)", key: "mastery.learning" },
  weak: { color: "var(--lumi-bad)", key: "mastery.weak" },
  new: { color: "var(--lumi-slate)", key: "mastery.new" }
};

export function masteryLevel(value) {
  if (value >= 0.75) return "mastered";
  if (value >= 0.4) return "learning";
  if (value > 0) return "weak";
  return "new";
}

const title = (titles, lang) => titles?.[lang] || titles?.fr || titles?.en || "";

/**
 * The course as the progress map draws it: the sun is the course, each CONCEPT is a planet, and a planet's moons are
 * the concepts it builds on (or one "Practice" moon when it builds on nothing). `mastery` = { concept: 0–1 }.
 * Draft concepts (course text still under review) are left out, as the engine's planner leaves them out.
 */
export function buildCourse(mastery = {}, lang = "fr") {
  const concepts = ANALYSE.concepts.filter((c) => c.status !== "draft");
  const byId = Object.fromEntries(ANALYSE.concepts.map((c) => [c.id, c]));
  const level = (id) => Math.max(0, Math.min(1, Number(mastery[id]) || 0));
  const units = concepts.map((c) => {
    const needs = c.requires.filter((r) => byId[r] && byId[r].status !== "draft");
    const lessons = needs.length
      ? needs.map((r) => ({ id: r, name: title(byId[r].titles, lang), mastery: level(r) }))
      : [{ id: c.id, name: lang === "fr" ? "Exercices" : lang === "ar" ? "تمارين" : "Practice", mastery: level(c.id) }];
    return { id: c.id, name: title(c.titles, lang), requires: needs, lessons, mastery: level(c.id) };
  });
  return {
    id: COURSE_ID, name: title(ANALYSE.titles, lang), color: COURSE_COLOR, professor: "", units,
    get mastery() {
      return units.length ? units.reduce((s, u) => s + u.mastery, 0) / units.length : 0;
    }
  };
}

export const courseMastery = (course) =>
  course.units.length ? course.units.reduce((s, u) => s + u.mastery, 0) / course.units.length : 0;

/** The lowest-mastery concepts that have been started. */
export function weakTopics(courses, limit = 5) {
  return courses.flatMap((c) => c.units.map((u) => ({ name: u.name, mastery: u.mastery, course: c })))
    .filter((l) => l.mastery > 0 && l.mastery < 0.4)
    .sort((a, b) => a.mastery - b.mastery)
    .slice(0, limit);
}

export const PEOPLE = {
  amira: { name: "Amira K.", color: "var(--lumi-iris)" },
  youssef: { name: "Youssef B.", color: "var(--lumi-iris-strong)" },
  salma: { name: "Salma T.", color: "var(--lumi-slate)" },
  nour: { name: "Nour H.", color: "var(--lumi-iris)" },
  karim: { name: "Karim M.", color: "var(--lumi-iris-strong)" },
  ines: { name: "Inès D.", color: "var(--lumi-slate)" }
};

export const ROOMS = [
  { code: "analyse", name: "Analyse · Atelier dérivées", course: COURSE_ID, assignedBy: "Dr. Ben Salah", online: ["amira", "youssef", "salma"], live: true },
  { code: "derivees-soir", name: "Dérivées · Révision du soir", course: COURSE_ID, online: ["karim", "ines"], live: true },
  { code: "analyse-tp", name: "Analyse · TD produit et quotient", course: COURSE_ID, assignedBy: "Dr. Ben Salah", online: [], live: false }
];

export const MATERIALS = [
  { title: "Chapitre 1 — La dérivation (cours)", course: COURSE_ID, type: "PDF", official: true, by: "Dr. Ben Salah" },
  { title: "Exemples corrigés : produit et quotient", course: COURSE_ID, type: "PDF", official: true, by: "Dr. Ben Salah" },
  { title: "Fiche : formules de dérivation", course: COURSE_ID, type: "PNG", official: false, by: "Karim M." },
  { title: "Notes : tangente et nombre dérivé (darija)", course: COURSE_ID, type: "PDF", official: false, by: "Salma T." }
];

// ---------- professor view: analyse ----------
export const CLASS = {
  course: COURSE_ID,
  students: 48,
  materials: 14,
  groups: [
    { name: "Groupe A · Dérivées de base", members: 5, mastery: 0.71, online: 3 },
    { name: "Groupe B · Dérivées de base", members: 5, mastery: 0.58, online: 2 },
    { name: "Groupe C · Produit", members: 6, mastery: 0.64, online: 0 },
    { name: "Groupe D · Quotient", members: 5, mastery: 0.42, online: 1 },
    { name: "Groupe E · Variations", members: 6, mastery: 0.35, online: 0 }
  ],
  roster: [
    { name: "Amira Khelifi", group: "A", mastery: 0.82, active: 0, weak: "Tangente" },
    { name: "Youssef Bouazizi", group: "A", mastery: 0.74, active: 0, weak: "Produit" },
    { name: "Salma Toumi", group: "B", mastery: 0.66, active: 2, weak: "Polynôme" },
    { name: "Karim Mansouri", group: "D", mastery: 0.39, active: 26, weak: "Quotient" },
    { name: "Inès Dridi", group: "C", mastery: 0.61, active: 5, weak: "Inverse" },
    { name: "Nour Haddad", group: "E", mastery: 0.28, active: 72, weak: "Extremum" }
  ],
  weakPoints: [
    { topic: "Quotient", share: 0.62 },
    { topic: "Racine carrée", share: 0.54 },
    { topic: "Extremum", share: 0.47 },
    { topic: "Inverse", share: 0.36 },
    { topic: "Produit", share: 0.22 }
  ]
};
