// Example data for the preview dashboards, until the real backend exists.
// Mastery is 0–1. Course ids match the study-room names on the whiteboard.

export const MASTERY = {
  mastered: { color: "#40d9be", key: "mastery.mastered" },
  learning: { color: "#ffc45c", key: "mastery.learning" },
  weak: { color: "#ff7fa8", key: "mastery.weak" },
  new: { color: "#6f7ea8", key: "mastery.new" }
};

export function masteryLevel(value) {
  if (value >= 0.75) return "mastered";
  if (value >= 0.4) return "learning";
  if (value > 0) return "weak";
  return "new";
}

const unit = (name, lessons) => ({
  name,
  lessons: lessons.map(([lesson, mastery]) => ({ name: lesson, mastery })),
  get mastery() {
    return this.lessons.reduce((s, l) => s + l.mastery, 0) / this.lessons.length;
  }
});

export const COURSES = [
  {
    id: "MATH102",
    name: "Calculus II",
    color: "#a68bff",
    professor: "Dr. Ben Salah",
    units: [
      unit("Integration techniques", [["Substitution", 0.92], ["Integration by parts", 0.81], ["Partial fractions", 0.66]]),
      unit("Improper integrals", [["Infinite bounds", 0.55], ["Comparison test", 0.38]]),
      unit("Sequences & series", [["Convergence tests", 0.24], ["Power series", 0.3], ["Taylor series", 0.12]]),
      unit("Parametric & polar", [["Parametric curves", 0.4], ["Polar area", 0]]),
      unit("Differential equations", [["Separable ODEs", 0], ["Linear first order", 0]])
    ]
  },
  {
    id: "CS201",
    name: "Data Structures",
    color: "#61d9f2",
    professor: "Dr. Trabelsi",
    units: [
      unit("Arrays & lists", [["Dynamic arrays", 0.95], ["Linked lists", 0.88]]),
      unit("Stacks & queues", [["Stacks", 0.9], ["Queues", 0.78], ["Deques", 0.6]]),
      unit("Trees", [["Binary search trees", 0.62], ["AVL trees", 0.31], ["Heaps", 0.45]]),
      unit("Hash tables", [["Hashing", 0.5], ["Collisions", 0.22]]),
      unit("Graphs", [["Representations", 0.2], ["BFS & DFS", 0]])
    ]
  },
  {
    id: "PHYS101",
    name: "Physics I",
    color: "#f8bc68",
    professor: "Dr. Gharbi",
    units: [
      unit("Kinematics", [["Motion in 1D", 1], ["Projectiles", 0.9]]),
      unit("Newton’s laws", [["Forces", 0.86], ["Friction", 0.74]]),
      unit("Energy", [["Work", 0.7], ["Conservation", 0.52]]),
      unit("Momentum", [["Impulse", 0.35], ["Collisions", 0.18]])
    ]
  }
];

export const courseMastery = (course) =>
  course.units.reduce((s, u) => s + u.mastery, 0) / course.units.length;

/** The lowest-mastery lessons that have been started, across all courses. */
export function weakTopics(limit = 5) {
  return COURSES.flatMap((c) => c.units.flatMap((u) => u.lessons.map((l) => ({ ...l, course: c }))))
    .filter((l) => l.mastery > 0 && l.mastery < 0.4)
    .sort((a, b) => a.mastery - b.mastery)
    .slice(0, limit);
}

export const PEOPLE = {
  amira: { name: "Amira K.", color: "#a279ff" },
  youssef: { name: "Youssef B.", color: "#75e8ff" },
  salma: { name: "Salma T.", color: "#40d9be" },
  nour: { name: "Nour H.", color: "#ffc45c" },
  karim: { name: "Karim M.", color: "#ff7fa8" },
  ines: { name: "Inès D.", color: "#8dbdff" }
};

export const ROOMS = [
  { code: "MATH102", name: "Calculus II · Series workshop", course: "MATH102", assignedBy: "Dr. Ben Salah", online: ["amira", "youssef", "salma"], live: true },
  { code: "algo-night", name: "Algo night", course: "CS201", online: ["karim", "ines"], live: true },
  { code: "PHYS101", name: "Physics I · Lab 3 report", course: "PHYS101", assignedBy: "Dr. Gharbi", online: [], live: false }
];

export const EXAMS = [
  { title: "Quiz 4 · Series convergence", course: "MATH102", inDays: 1, readiness: 0.38 },
  { title: "Midterm · Trees & hashing", course: "CS201", inDays: 6, readiness: 0.57 },
  { title: "Lab exam · Energy", course: "PHYS101", inDays: 11, readiness: 0.71 }
];

export const MATERIALS = [
  { title: "Chapter 7 — Infinite series (slides)", course: "MATH102", type: "PDF", official: true, by: "Dr. Ben Salah" },
  { title: "Worked examples: ratio test", course: "MATH102", type: "PDF", official: true, by: "Dr. Ben Salah" },
  { title: "AVL rotations cheat sheet", course: "CS201", type: "PNG", official: false, by: "Karim M." },
  { title: "Lab 3 — energy measurements", course: "PHYS101", type: "DOCX", official: true, by: "Dr. Gharbi" },
  { title: "Taylor series summary (Darija notes)", course: "MATH102", type: "PDF", official: false, by: "Salma T." }
];

// ---------- professor view: MATH102 ----------
export const CLASS = {
  course: "MATH102",
  students: 48,
  materials: 14,
  groups: [
    { name: "Group A · Series", members: 5, mastery: 0.71, online: 3 },
    { name: "Group B · Series", members: 5, mastery: 0.58, online: 2 },
    { name: "Group C · Integrals", members: 6, mastery: 0.64, online: 0 },
    { name: "Group D · Integrals", members: 5, mastery: 0.42, online: 1 },
    { name: "Group E · Polar", members: 6, mastery: 0.35, online: 0 }
  ],
  roster: [
    { name: "Amira Khelifi", group: "A", mastery: 0.82, active: 0, weak: "Taylor series" },
    { name: "Youssef Bouazizi", group: "A", mastery: 0.74, active: 0, weak: "Comparison test" },
    { name: "Salma Toumi", group: "B", mastery: 0.66, active: 2, weak: "Power series" },
    { name: "Karim Mansouri", group: "D", mastery: 0.39, active: 26, weak: "Convergence tests" },
    { name: "Inès Dridi", group: "C", mastery: 0.61, active: 5, weak: "Partial fractions" },
    { name: "Nour Haddad", group: "E", mastery: 0.28, active: 72, weak: "Polar area" }
  ],
  weakPoints: [
    { topic: "Convergence tests", share: 0.62 },
    { topic: "Taylor series", share: 0.54 },
    { topic: "Polar area", share: 0.47 },
    { topic: "Comparison test", share: 0.36 },
    { topic: "Partial fractions", share: 0.22 }
  ]
};
