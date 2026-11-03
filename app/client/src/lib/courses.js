// The example curriculum from the EdNova Academic Constellation, so a course's
// study room can show the same name and status colour.

export const STATUS = {
  completed: { label: "Completed", color: "#40d9be" },
  progress: { label: "In progress", color: "#ffc45c" },
  new: { label: "Not started", color: "#a279ff" },
  attention: { label: "Needs attention", color: "#ff7fa8" }
};

export const COURSES = [
  { id: "MATH101", name: "Calculus I", year: 1, status: "completed" },
  { id: "MATH102", name: "Calculus II", year: 2, status: "progress" },
  { id: "MATH201", name: "Advanced Mathematics", year: 3, status: "new" },
  { id: "PHYS101", name: "Physics I", year: 1, status: "completed" },
  { id: "STAT101", name: "Statistics I", year: 1, status: "completed" },
  { id: "CS101", name: "Introduction to CS", year: 1, status: "attention" },
  { id: "CS201", name: "Data Structures", year: 2, status: "new" },
  { id: "CS301", name: "Algorithms", year: 3, status: "new" },
  { id: "EE101", name: "Circuits I", year: 1, status: "completed" },
  { id: "EE201", name: "Electronics", year: 2, status: "new" },
  { id: "ENG101", name: "Technical Writing", year: 1, status: "attention" },
  { id: "HUM101", name: "Humanities", year: 1, status: "new" }
];

export const courseForRoom = (room) =>
  COURSES.find((c) => c.id.toLowerCase() === String(room || "").trim().toLowerCase()) || null;

export const courseColor = (course) => STATUS[course.status].color;
