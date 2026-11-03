// Feature-layer tables that live next to the boards: course materials, a log of
// AI runs (ids only, never names), answers flagged for professor review, and
// practice attempts (the mastery behind the Progress Map and the prep plan).

const RUNS_KEPT = 20_000;

function openAiStore(db) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS materials (
      id TEXT PRIMARY KEY,
      course_id TEXT NOT NULL,
      title TEXT NOT NULL,
      filename TEXT NOT NULL,
      mime TEXT NOT NULL,
      size INTEGER NOT NULL,
      visibility TEXT NOT NULL,
      class_id TEXT,
      owner_id TEXT NOT NULL,
      owner_name TEXT NOT NULL,
      source TEXT NOT NULL,
      status TEXT NOT NULL,
      chunks TEXT NOT NULL DEFAULT '[]',
      detail TEXT,
      created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ai_runs (
      request_id TEXT PRIMARY KEY,
      task TEXT NOT NULL,
      backend TEXT NOT NULL,
      learner_id TEXT,
      room_id TEXT,
      course_id TEXT,
      language TEXT,
      degraded INTEGER NOT NULL,
      degrade_reason TEXT,
      mode TEXT,
      model_class TEXT,
      from_cache INTEGER NOT NULL,
      latency_ms INTEGER NOT NULL,
      question TEXT,
      answer TEXT,
      created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ai_runs_time ON ai_runs (created_at);
    CREATE TABLE IF NOT EXISTS ai_flags (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      request_id TEXT NOT NULL,
      task TEXT NOT NULL,
      reason TEXT NOT NULL,
      note TEXT,
      flagged_by TEXT NOT NULL,
      course_id TEXT,
      question TEXT,
      answer TEXT,
      status TEXT NOT NULL DEFAULT 'open',
      resolved_at INTEGER,
      created_at INTEGER NOT NULL,
      UNIQUE (request_id, flagged_by)
    );
    CREATE TABLE IF NOT EXISTS attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      learner_id TEXT NOT NULL,
      course_id TEXT NOT NULL,
      exercise_id TEXT NOT NULL,
      concept TEXT,
      verdict TEXT NOT NULL,
      hints_used INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS attempts_learner ON attempts (learner_id, course_id);
  `);

  const q = {
    putMaterial: db.prepare(`INSERT INTO materials (id, course_id, title, filename, mime, size, visibility, class_id, owner_id, owner_name, source, status, chunks, detail, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`),
    updateMaterial: db.prepare("UPDATE materials SET status = ?, chunks = ?, detail = ? WHERE id = ?"),
    getMaterial: db.prepare("SELECT * FROM materials WHERE id = ?"),
    listMaterials: db.prepare("SELECT * FROM materials WHERE (? IS NULL OR course_id = ?) ORDER BY source = 'professor' DESC, created_at DESC LIMIT 500"),
    deleteMaterial: db.prepare("DELETE FROM materials WHERE id = ?"),
    putRun: db.prepare(`INSERT OR REPLACE INTO ai_runs (request_id, task, backend, learner_id, room_id, course_id, language, degraded, degrade_reason, mode, model_class, from_cache, latency_ms, question, answer, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`),
    getRun: db.prepare("SELECT * FROM ai_runs WHERE request_id = ?"),
    pruneRuns: db.prepare("DELETE FROM ai_runs WHERE created_at < (SELECT created_at FROM ai_runs ORDER BY created_at DESC LIMIT 1 OFFSET ?)"),
    stats: db.prepare(`SELECT task, COUNT(*) AS runs, SUM(degraded) AS degraded, SUM(from_cache) AS cached,
      SUM(CASE WHEN model_class IS NULL THEN 1 ELSE 0 END) AS zero_request, CAST(AVG(latency_ms) AS INTEGER) AS avg_ms
      FROM ai_runs WHERE created_at > ? GROUP BY task ORDER BY runs DESC`),
    reasons: db.prepare("SELECT degrade_reason AS reason, COUNT(*) AS n FROM ai_runs WHERE created_at > ? AND degraded = 1 GROUP BY degrade_reason ORDER BY n DESC"),
    putFlag: db.prepare(`INSERT OR IGNORE INTO ai_flags (request_id, task, reason, note, flagged_by, course_id, question, answer, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`),
    listFlags: db.prepare("SELECT * FROM ai_flags WHERE (? IS NULL OR status = ?) ORDER BY created_at DESC LIMIT 200"),
    resolveFlags: db.prepare("UPDATE ai_flags SET status = ?, resolved_at = ? WHERE request_id = ? AND status = 'open'"),
    getFlag: db.prepare("SELECT * FROM ai_flags WHERE id = ?"),
    putAttempt: db.prepare("INSERT INTO attempts (learner_id, course_id, exercise_id, concept, verdict, hints_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"),
    attempts: db.prepare("SELECT concept, verdict, hints_used FROM attempts WHERE learner_id = ? AND course_id = ? ORDER BY created_at")
  };

  const material = (row) => row && { ...row, chunks: JSON.parse(row.chunks || "[]") };

  return {
    addMaterial(m) {
      q.putMaterial.run(m.id, m.course_id, m.title, m.filename, m.mime, m.size, m.visibility, m.class_id ?? null, m.owner_id, m.owner_name, m.source, m.status, JSON.stringify(m.chunks || []), m.detail ?? null, m.created_at ?? Date.now());
    },
    updateMaterial(id, { status, chunks, detail }) {
      q.updateMaterial.run(status, JSON.stringify(chunks || []), detail ?? null, id);
    },
    getMaterial: (id) => material(q.getMaterial.get(id)),
    listMaterials: (courseId = null) => q.listMaterials.all(courseId, courseId).map(material),
    deleteMaterial: (id) => q.deleteMaterial.run(id),

    logRun(r) {
      q.putRun.run(r.request_id, r.task, r.backend, r.learner_id ?? null, r.room_id ?? null, r.course_id ?? null, r.language ?? null,
        r.degraded ? 1 : 0, r.degrade_reason ?? null, r.mode ?? null, r.model_class ?? null, r.from_cache ? 1 : 0, Math.round(r.latency_ms || 0),
        r.question ?? null, r.answer ?? null, Date.now());
      if (Math.random() < 0.01) q.pruneRuns.run(RUNS_KEPT);
    },
    getRun: (id) => q.getRun.get(id),
    stats(sinceMs = 24 * 3600 * 1000) {
      const since = Date.now() - sinceMs;
      return { tasks: q.stats.all(since), reasons: q.reasons.all(since) };
    },

    flag(f) {
      q.putFlag.run(f.request_id, f.task, f.reason, f.note ?? null, f.flagged_by, f.course_id ?? null, f.question ?? null, f.answer ?? null, Date.now());
    },
    listFlags: (status = null) => q.listFlags.all(status, status),
    getFlag: (id) => q.getFlag.get(id),
    resolveFlag: (requestId, status) => q.resolveFlags.run(status, Date.now(), requestId),

    addAttempt(a) {
      q.putAttempt.run(a.learner_id, a.course_id, a.exercise_id, a.concept ?? null, a.verdict, a.hints_used || 0, Date.now());
    },
    /** Mastery per concept: exponentially weighted correctness, hints count half. */
    mastery(learnerId, courseId) {
      const out = {};
      q.attempts.all(learnerId, courseId).forEach((a) => {
        if (!a.concept || a.verdict === "UNKNOWN") return;
        const score = a.verdict === "CORRECT" ? (a.hints_used ? 0.6 : 1) : a.verdict === "PARTIAL" ? 0.5 : 0;
        const prev = out[a.concept];
        out[a.concept] = prev
          ? { value: prev.value * 0.6 + score * 0.4, attempts: prev.attempts + 1 }
          : { value: score * 0.7, attempts: 1 };
      });
      Object.values(out).forEach((m) => { m.value = Math.round(m.value * 100) / 100; });
      return out;
    }
  };
}

module.exports = { openAiStore };
