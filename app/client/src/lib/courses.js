// The courses with a study room. Phase 1 has one: analyse, the course the engine has content for.

export const STATUS = {
  completed: { label: "Completed", color: "#40d9be" },
  progress: { label: "In progress", color: "#ffc45c" },
  new: { label: "Not started", color: "#a279ff" },
  attention: { label: "Needs attention", color: "#ff7fa8" }
};

export const COURSES = [
  { id: "analyse", name: "Analyse : dérivation", year: 1, status: "progress" }
];

export const courseForRoom = (room) =>
  COURSES.find((c) => c.id.toLowerCase() === String(room || "").trim().toLowerCase()) || null;

export const courseColor = (course) => STATUS[course.status].color;
