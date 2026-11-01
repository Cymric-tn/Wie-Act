"""
Local chunk store (JSON). Production would point the same interface at FelCloud's team collection;
the BM25 statistics are rebuilt in memory at load time (cheap at this corpus size).
"""
from __future__ import annotations

import json
import os
import time

from rag.chunker import Chunk

FORMAT_VERSION = 3          # 2: chunks carry a concept; 3: course scope (visibility, owner, origin ...)
READABLE = (2, 3)           # a v2 file loads: the new fields take their defaults


def save_chunks(path: str, chunks: list[Chunk], docs_dir: str = "") -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": FORMAT_VERSION, "built_at": time.time(), "docs_dir": docs_dir,
                   "chunks": [c.to_dict() for c in chunks]}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)                       # atomic: a running engine never reads a half file


def load_chunks(path: str) -> list[Chunk]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("version") not in READABLE:
        raise ValueError(f"{path}: index format {data.get('version')} not in {READABLE}; re-run scripts.index_docs")
    return [Chunk(**c) for c in data["chunks"]]


class CourseStore:
    """Runtime material of each course (uploads, professor documents), one file per course: <dir>/<course_id>.json.
    The course docs of data/docs stay in the base index; this holds what was added while the engine runs, so it
    survives a restart. Same file format as the base index."""

    def __init__(self, directory: str):
        self.dir = directory

    def path(self, course_id: str) -> str:
        if not course_id or "/" in course_id or "\\" in course_id or course_id.startswith("."):
            raise ValueError(f"bad course id {course_id!r}")
        return os.path.join(self.dir, f"{course_id}.json")

    def load(self, course_id: str) -> list[Chunk]:
        return load_chunks(self.path(course_id))

    def save(self, course_id: str, chunks: list[Chunk]) -> None:
        if chunks:
            save_chunks(self.path(course_id), chunks)
        elif os.path.exists(self.path(course_id)):
            os.remove(self.path(course_id))

    def courses(self) -> list[str]:
        import glob
        return sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(self.dir, "*.json")))

    def load_all(self) -> list[Chunk]:
        return [c for course in self.courses() for c in self.load(course)]
