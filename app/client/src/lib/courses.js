// The courses with a study room. Phase 1 has one: analyse, the course the engine has content for.

export const STATUS = {
  completed: { label: "Completed", color: "var(--lumi-ok)" },
  progress: { label: "In progress", color: "var(--lumi-warn)" },
  new: { label: "Not started", color: "var(--lumi-iris)" },
  attention: { label: "Needs attention", color: "var(--lumi-bad)" }
};

export const COURSES = [
  { id: "analyse", name: "Analyse : dérivation", year: 1, status: "progress" }
];

export const courseForRoom = (room) =>
  COURSES.find((c) => c.id.toLowerCase() === String(room || "").trim().toLowerCase()) || null;

export const courseColor = (course) => STATUS[course.status].color;
