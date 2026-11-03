// Rebuilds earlier board states from the server's reversible history log.
// Entry ops: { t: "add", items } | { t: "remove", items } | { t: "update", before, after }

function revert(state, op) {
  if (op.t === "add") op.items.forEach((i) => state.delete(i.id));
  else if (op.t === "remove") op.items.forEach((i) => state.set(i.id, i));
  else if (op.t === "update") op.before.forEach((i) => state.set(i.id, i));
}

function replay(state, op) {
  if (op.t === "add") op.items.forEach((i) => state.set(i.id, i));
  else if (op.t === "remove") op.items.forEach((i) => state.delete(i.id));
  else if (op.t === "update") op.after.forEach((i) => state.set(i.id, i));
}

/**
 * Works backwards from the current items to the state before the oldest
 * entry, keeping a checkpoint every `every` steps so scrubbing stays fast.
 */
export function buildTimeline(currentItems, entries, every = 200) {
  const base = new Map(currentItems.map((i) => [i.id, i]));
  for (let k = entries.length - 1; k >= 0; k--) revert(base, entries[k].op);

  const checkpoints = [new Map(base)];
  const state = new Map(base);
  entries.forEach((entry, k) => {
    replay(state, entry.op);
    if ((k + 1) % every === 0) checkpoints.push(new Map(state));
  });
  return { entries, checkpoints, every };
}

/** Items after the first `count` entries have been applied. */
export function stateAt(timeline, count) {
  const { entries, checkpoints, every } = timeline;
  const n = Math.max(0, Math.min(count, entries.length));
  const index = Math.min(Math.floor(n / every), checkpoints.length - 1);
  const state = new Map(checkpoints[index]);
  for (let k = index * every; k < n; k++) replay(state, entries[k].op);
  return [...state.values()];
}

export function describeEntry(entry) {
  const { op } = entry;
  const count = op.t === "update" ? op.after.length : op.items.length;
  const noun = count === 1 ? "item" : "items";
  const verb = op.t === "add" ? "added" : op.t === "remove" ? "removed" : "changed";
  return `${entry.by} ${verb} ${count} ${noun}`;
}
