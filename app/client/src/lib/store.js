// The local copy of the board. Operations use the same shapes the server speaks:
//   { kind: "add", items } | { kind: "remove", ids } | { kind: "update", items } | { kind: "clear", page }
// apply() returns the inverse operation, which is what undo sends.

export class BoardStore {
  constructor(items = []) {
    this.items = new Map();
    this.version = 0;
    this.load(items);
  }

  load(items) {
    this.items = new Map(items.map((i) => [i.id, i]));
    this.version++;
  }

  get(id) {
    return this.items.get(id);
  }

  apply(op) {
    let inverse = null;
    if (op.kind === "add") {
      const added = op.items.filter((i) => !this.items.has(i.id));
      added.forEach((i) => this.items.set(i.id, i));
      inverse = { kind: "remove", ids: added.map((i) => i.id) };
    } else if (op.kind === "remove") {
      const removed = op.ids.map((id) => this.items.get(id)).filter(Boolean);
      removed.forEach((i) => this.items.delete(i.id));
      inverse = { kind: "add", items: removed };
    } else if (op.kind === "update") {
      const before = [];
      op.items.forEach((i) => {
        const old = this.items.get(i.id);
        if (!old) return;
        before.push(old);
        this.items.set(i.id, i);
      });
      inverse = { kind: "update", items: before };
    } else if (op.kind === "clear") {
      const removed = [...this.items.values()].filter((i) => i.page === op.page);
      removed.forEach((i) => this.items.delete(i.id));
      inverse = { kind: "add", items: removed };
    }
    this.version++;
    return inverse;
  }

  /** Items on a page in paint order (z, then id for stable ties). */
  pageItems(page) {
    return [...this.items.values()]
      .filter((i) => i.page === page)
      .sort((a, b) => a.z - b.z || (a.id < b.id ? -1 : 1));
  }

  maxZ(page) {
    let max = 0;
    this.items.forEach((i) => {
      if (i.page === page && i.z > max) max = i.z;
    });
    return max;
  }

  minZ(page) {
    let min = 0;
    this.items.forEach((i) => {
      if (i.page === page && i.z < min) min = i.z;
    });
    return min;
  }

  countByPage() {
    const counts = {};
    this.items.forEach((i) => {
      counts[i.page] = (counts[i.page] || 0) + 1;
    });
    return counts;
  }
}

/** True when an operation would change nothing (e.g. undoing an already-removed item). */
export function isEmptyOp(op) {
  if (!op) return true;
  if (op.kind === "add" || op.kind === "update") return op.items.length === 0;
  if (op.kind === "remove") return op.ids.length === 0;
  return false;
}

/** Local undo/redo of this user's own operations. */
export class UndoHistory {
  constructor(limit = 200) {
    this.limit = limit;
    this.undoStack = [];
    this.redoStack = [];
  }

  push(forward, backward) {
    if (isEmptyOp(forward)) return;
    this.undoStack.push({ forward, backward });
    if (this.undoStack.length > this.limit) this.undoStack.shift();
    this.redoStack = [];
  }

  takeUndo() {
    const entry = this.undoStack.pop();
    if (entry) this.redoStack.push(entry);
    return entry ? entry.backward : null;
  }

  takeRedo() {
    const entry = this.redoStack.pop();
    if (entry) this.undoStack.push(entry);
    return entry ? entry.forward : null;
  }

  reset() {
    this.undoStack = [];
    this.redoStack = [];
  }

  get canUndo() {
    return this.undoStack.length > 0;
  }

  get canRedo() {
    return this.redoStack.length > 0;
  }
}
