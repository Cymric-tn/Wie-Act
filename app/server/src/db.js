// SQLite persistence using Node's built-in driver (no native dependency).
const { DatabaseSync } = require("node:sqlite");

const MAX_OPS_PER_ROOM = 5000;

function openDatabase(path = ":memory:") {
  const db = new DatabaseSync(path);
  db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA synchronous = NORMAL;
    CREATE TABLE IF NOT EXISTS rooms (
      name TEXT PRIMARY KEY,
      meta TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS items (
      room TEXT NOT NULL,
      id TEXT NOT NULL,
      data TEXT NOT NULL,
      PRIMARY KEY (room, id)
    );
    CREATE TABLE IF NOT EXISTS ops (
      room TEXT NOT NULL,
      seq INTEGER NOT NULL,
      ts INTEGER NOT NULL,
      by TEXT NOT NULL,
      op TEXT NOT NULL,
      PRIMARY KEY (room, seq)
    );
  `);

  const q = {
    getRoom: db.prepare("SELECT meta FROM rooms WHERE name = ?"),
    putRoom: db.prepare(`INSERT INTO rooms (name, meta, updated_at) VALUES (?, ?, ?)
      ON CONFLICT(name) DO UPDATE SET meta = excluded.meta, updated_at = excluded.updated_at`),
    getItems: db.prepare("SELECT data FROM items WHERE room = ?"),
    putItem: db.prepare(`INSERT INTO items (room, id, data) VALUES (?, ?, ?)
      ON CONFLICT(room, id) DO UPDATE SET data = excluded.data`),
    deleteItem: db.prepare("DELETE FROM items WHERE room = ? AND id = ?"),
    putOp: db.prepare("INSERT INTO ops (room, seq, ts, by, op) VALUES (?, ?, ?, ?, ?)"),
    pruneOps: db.prepare("DELETE FROM ops WHERE room = ? AND seq <= ?"),
    getOps: db.prepare("SELECT seq, ts, by, op FROM ops WHERE room = ? ORDER BY seq DESC LIMIT ?")
  };

  function transaction(fn) {
    db.exec("BEGIN");
    try {
      const result = fn();
      db.exec("COMMIT");
      return result;
    } catch (error) {
      db.exec("ROLLBACK");
      throw error;
    }
  }

  return {
    loadRoom(name) {
      const row = q.getRoom.get(name);
      if (!row) return null;
      const items = q.getItems.all(name).map((r) => JSON.parse(r.data));
      return { meta: JSON.parse(row.meta), items };
    },

    saveMeta(name, meta) {
      q.putRoom.run(name, JSON.stringify(meta), Date.now());
    },

    /** Writes item changes and one reversible history entry atomically. */
    commit(name, meta, { upserts = [], deletes = [], entry }) {
      transaction(() => {
        upserts.forEach((item) => q.putItem.run(name, item.id, JSON.stringify(item)));
        deletes.forEach((id) => q.deleteItem.run(name, id));
        if (entry) {
          q.putOp.run(name, entry.seq, entry.ts, entry.by, JSON.stringify(entry.op));
          if (entry.seq > MAX_OPS_PER_ROOM) q.pruneOps.run(name, entry.seq - MAX_OPS_PER_ROOM);
        }
        q.putRoom.run(name, JSON.stringify(meta), Date.now());
      });
    },

    /** Most recent history entries, oldest first. */
    history(name, limit = MAX_OPS_PER_ROOM) {
      return q.getOps.all(name, limit).reverse().map((r) => ({
        seq: r.seq, ts: r.ts, by: r.by, op: JSON.parse(r.op)
      }));
    },

    /** The open database, for the AI feature tables (src/ai/store.js). */
    handle: db,

    close() {
      db.close();
    }
  };
}

module.exports = { openDatabase, MAX_OPS_PER_ROOM };
