"""
Exact cache (SQLite). Key = hash(task, model_class, normalized prompt, language, level, style),
as specified in CLAUDE.md, plus the prompt file version so a prompt edit invalidates old answers.
No semantic cache: FelCloud lists no embedding model (see DECISIONS.md), so it was not built.

Negative markers (table `negative`): an explain answer that failed a deterministic check (wrong math,
ungrounded formula, truncated loop) is not cached, but its key is marked for a short TTL so a repeat is
served in fast mode instead of sending the identical request again.

Normalization collapses whitespace and applies Unicode NFC. It does NOT lowercase: in math and
code, X and x are different questions (the legacy gateway cache lowercased; dropped on purpose).

Only clean results go in: put() refuses anything degraded, fallback or safe-default, so a
transient outage can never be served back from the cache for a week.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import unicodedata
from typing import Any, Callable, Iterable, Mapping


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def cache_key(task: str, model_class: str, messages: Iterable[Mapping[str, str]], language: str | None,
              level: str | None, style: str | None, prompt_version: str = "", extra: Iterable[str] = ()) -> str:
    """extra: further identity, e.g. the ids of the retrieved chunks for grounded tasks."""
    prompt = [[m.get("role", ""), normalize(m.get("content") or "")] for m in messages]
    raw = json.dumps({"task": task, "v": prompt_version, "class": model_class, "prompt": prompt,
                      "language": (language or "").upper(), "level": (level or "").lower(),
                      "style": (style or "").lower(), "extra": list(extra)},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


class NotCacheable(ValueError):
    pass


class ExactCache:
    def __init__(self, path: str, ttl_s: float, clock: Callable[[], float] = time.time):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.ttl_s, self._clock = ttl_s, clock
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS exact (k TEXT PRIMARY KEY, task TEXT, v TEXT, ts REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS negative (k TEXT PRIMARY KEY, task TEXT, reasons TEXT, "
                        "expires REAL)")
        self.db.commit()
        self.hits = self.misses = self.negative_hits = 0

    def get(self, key: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT v, ts FROM exact WHERE k=?", (key,)).fetchone()
        if row and self._clock() - row[1] <= self.ttl_s:
            self.hits += 1
            return json.loads(row[0])
        if row:                                     # expired: drop it so the table does not grow
            self.db.execute("DELETE FROM exact WHERE k=?", (key,))
            self.db.commit()
        self.misses += 1
        return None

    def put(self, key: str, task: str, value: Mapping[str, Any]) -> None:
        if value.get("degraded") or value.get("fallback") or value.get("safe_default"):
            raise NotCacheable("degraded / fallback / default results are never cached")
        self.db.execute("INSERT OR REPLACE INTO exact VALUES (?,?,?,?)",
                        (key, task, json.dumps(value, ensure_ascii=False), self._clock()))
        self.db.commit()

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM exact WHERE k=?", (key,))
        self.db.commit()

    def mark_negative(self, key: str, task: str, reasons: list[str], ttl_s: float) -> None:
        """Remember for ttl_s that this key produced an answer that failed a deterministic check."""
        if ttl_s <= 0:
            return
        self.db.execute("INSERT OR REPLACE INTO negative VALUES (?,?,?,?)",
                        (key, task, json.dumps(reasons), self._clock() + ttl_s))
        self.db.commit()

    def get_negative(self, key: str) -> list[str] | None:
        """The degrade reasons of a live marker, or None."""
        row = self.db.execute("SELECT reasons, expires FROM negative WHERE k=?", (key,)).fetchone()
        if row and self._clock() < row[1]:
            self.negative_hits += 1
            return json.loads(row[0])
        if row:
            self.db.execute("DELETE FROM negative WHERE k=?", (key,))
            self.db.commit()
        return None

    def purge_expired(self) -> int:
        self.db.execute("DELETE FROM negative WHERE expires <= ?", (self._clock(),))
        n = self.db.execute("DELETE FROM exact WHERE ts < ?", (self._clock() - self.ttl_s,)).rowcount
        self.db.commit()
        return n

    def close(self) -> None:
        self.db.close()
