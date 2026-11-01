"""What the professor dashboard reviews: answers a check flagged (wrong math / ungrounded formula) and questions the
course could not answer ("not in the course"), with the professor's decision on each flagged one.

Why not the metrics JSONL: a metrics record is ids and numbers only (no learner text). A review item needs the question,
the answer and the flagged spans, so it lives in its own table, next to the cache (same SQLite file, CACHE_PATH).
Only these two kinds are stored; nothing else a learner wrote is kept here."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Mapping

FLAG_REASONS = ("wrong_math", "ungrounded_formula")
FLAG_TASKS = ("explain", "check_claim", "check_answer")
GAP_TASKS = ("explain", "check_claim")
ACTIONS = ("approve", "reject")
MAX_EXAMPLES = 5
_QUESTION_KEYS = ("question", "claim", "problem")
_NUM = re.compile(r"^\d+(\.\d+)?$")


def parse_since(value: str | float | None) -> float | None:
    """Epoch seconds, or an ISO-8601 date / datetime (no zone = UTC). None / '' = no lower bound."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if _NUM.match(s):
        return float(s)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"since must be epoch seconds or ISO-8601 (2026-11-02, 2026-11-02T08:00:00Z), got {value!r}") from None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def question_of(inputs: Mapping[str, Any]) -> str:
    for k in _QUESTION_KEYS:
        if str(inputs.get(k) or "").strip():
            return str(inputs[k]).strip()
    return ""


def _answer_of(text: str | None, data: Mapping[str, Any] | None) -> str:
    if text:
        return text
    return json.dumps(data, ensure_ascii=False, sort_keys=True) if data else ""


class ReviewStore:
    def __init__(self, path: str = ":memory:", clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS review_items (request_id TEXT, kind TEXT, task TEXT, course_id TEXT, "
                        "class_id TEXT, learner_id TEXT, question TEXT, answer TEXT, spans TEXT, reasons TEXT, "
                        "nearest_concept TEXT, created_at REAL, PRIMARY KEY (request_id, kind))")
        self.db.execute("CREATE INDEX IF NOT EXISTS review_items_kind ON review_items (kind, created_at)")
        self.db.execute("CREATE TABLE IF NOT EXISTS review_decisions (request_id TEXT PRIMARY KEY, action TEXT, "
                        "decided_at REAL)")
        self.db.commit()

    # ---------------------------------------------------------------- write side (called by the engine)
    def observe(self, task: str, inputs: Mapping[str, Any], ids: Mapping[str, Any], result) -> None:
        """Record this result if it is something to review. `result` is an engine Result (or anything with the same
        fields). Cheap for everything else: two set lookups."""
        if task not in FLAG_TASKS and task not in GAP_TASKS:
            return
        notes = result.notes or {}
        reasons = list(notes.get("degrade_reasons") or ([result.degrade_reason] if result.degrade_reason else []))
        flagged = [r for r in reasons if r in FLAG_REASONS]
        verdict = (result.data or {}).get("verdict")
        if flagged and task in FLAG_TASKS and not result.from_cache:
            spans = [*(notes.get("wrong_math") or []), *(notes.get("ungrounded") or [])]
            self._add(result, task, inputs, ids, "flagged", flagged, spans, notes.get("nearest_concept"))
        elif (task == "explain" and notes.get("retrieval") == "no_relevant_chunks") or \
                (task == "check_claim" and verdict == "NOT_IN_COURSE"):
            self._add(result, task, inputs, ids, "gap", [], [], notes.get("nearest_concept"))

    def _add(self, result, task, inputs, ids, kind, reasons, spans, nearest) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO review_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                result.request_id, kind, task, ids.get("course_id"), ids.get("class_id"), ids.get("learner_id"),
                question_of(inputs), _answer_of(result.text, result.data),
                json.dumps(spans, ensure_ascii=False), json.dumps(reasons), nearest or "", self._clock()))
            self.db.commit()

    # ---------------------------------------------------------------- read side (the dashboard)
    @staticmethod
    def _where(kind: str, class_id, course_id, since) -> tuple[str, list]:
        sql, args = ["kind=?"], [kind]
        for col, v in (("class_id", class_id), ("course_id", course_id)):
            if v:
                sql.append(f"{col}=?")
                args.append(v)
        if since is not None:
            sql.append("created_at>=?")
            args.append(since)
        return " AND ".join(sql), args

    def flagged(self, class_id: str | None = None, course_id: str | None = None, since: float | None = None,
                limit: int = 50, offset: int = 0) -> dict:
        """Pending flagged items (no decision yet), newest first."""
        where, args = self._where("flagged", class_id, course_id, since)
        pending = f"{where} AND request_id NOT IN (SELECT request_id FROM review_decisions)"
        with self._lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM review_items WHERE {pending}", args).fetchone()[0]
            rows = self.db.execute(
                f"SELECT request_id, task, course_id, class_id, learner_id, question, answer, spans, reasons, created_at "
                f"FROM review_items WHERE {pending} ORDER BY created_at DESC, request_id DESC LIMIT ? OFFSET ?",
                [*args, limit, offset]).fetchall()
        items = [{"request_id": r[0], "task": r[1], "course_id": r[2], "class_id": r[3], "learner_id": r[4],
                  "question": r[5], "answer": r[6], "spans": json.loads(r[7]), "reasons": json.loads(r[8]),
                  "created_at": iso(r[9])} for r in rows]
        return {"items": items, "total": total, "limit": limit, "offset": offset,
                "next_offset": offset + limit if offset + limit < total else None}

    def decide(self, request_id: str, action: str) -> dict:
        """Record the decision. KeyError: not a flagged item. ValueError: already decided the other way.
        Returns {"request_id", "action", "decided_at", "already"}."""
        if action not in ACTIONS:
            raise ValueError(f"action must be one of {ACTIONS}")
        with self._lock:
            if not self.db.execute("SELECT 1 FROM review_items WHERE request_id=? AND kind='flagged'",
                                   (request_id,)).fetchone():
                raise KeyError(request_id)
            old = self.db.execute("SELECT action, decided_at FROM review_decisions WHERE request_id=?",
                                  (request_id,)).fetchone()
            if old:
                if old[0] != action:
                    raise ValueError(f"already decided: {old[0]}")
                return {"request_id": request_id, "action": action, "decided_at": iso(old[1]), "already": True}
            now = self._clock()
            self.db.execute("INSERT INTO review_decisions VALUES (?,?,?)", (request_id, action, now))
            self.db.commit()
        return {"request_id": request_id, "action": action, "decided_at": iso(now), "already": False}

    def gaps(self, class_id: str | None = None, course_id: str | None = None, since: float | None = None) -> dict:
        """'Not in the course' questions grouped by the nearest concept, most asked first."""
        where, args = self._where("gap", class_id, course_id, since)
        with self._lock:
            rows = self.db.execute(f"SELECT nearest_concept, question, created_at FROM review_items WHERE {where} "
                                   f"ORDER BY created_at DESC, request_id DESC", args).fetchall()
        groups: dict[str, dict] = {}
        for concept, question, ts in rows:
            g = groups.setdefault(concept or "", {"concept": concept or None, "count": 0, "examples": [], "last_at": iso(ts)})
            g["count"] += 1
            if question and question not in g["examples"] and len(g["examples"]) < MAX_EXAMPLES:
                g["examples"].append(question)
        out = sorted(groups.values(), key=lambda g: (-g["count"], g["concept"] or "~"))
        return {"gaps": out, "total": len(rows)}

    def close(self) -> None:
        self.db.close()
