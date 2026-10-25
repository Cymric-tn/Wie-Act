"""
Local chunk store (JSON). Production would point the same interface at FelCloud's team collection;
the BM25 statistics are rebuilt in memory at load time (cheap at this corpus size).
"""
from __future__ import annotations

import json
import os
import time

from rag.chunker import Chunk

FORMAT_VERSION = 2          # 2: chunks carry a concept


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
    if data.get("version") != FORMAT_VERSION:
        raise ValueError(f"{path}: index format {data.get('version')} != {FORMAT_VERSION}; re-run scripts.index_docs")
    return [Chunk(**c) for c in data["chunks"]]
