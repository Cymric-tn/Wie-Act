from __future__ import annotations

import os
from typing import Protocol

from metrics.record import CallRecord


class MetricsSink(Protocol):
    def write(self, record: CallRecord) -> None: ...


class JsonlSink:
    """Append-only JSONL, flushed per record so a crash loses nothing."""

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")

    def write(self, record: CallRecord) -> None:
        self._f.write(record.model_dump_json(exclude_none=True) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


class MemorySink:
    def __init__(self):
        self.records: list[CallRecord] = []

    def write(self, record: CallRecord) -> None:
        self.records.append(record)
