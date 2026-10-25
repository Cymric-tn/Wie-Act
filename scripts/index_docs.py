"""Chunk DOCS_DIR/*.md and write the local store (RAG_INDEX_PATH). No LLM calls.

    ENV_FILE=.env.ollama python -m scripts.index_docs
"""
from __future__ import annotations

from collections import Counter

from config import load_settings
from rag.chunker import chunk_dir
from rag.store import save_chunks


def main() -> None:
    s = load_settings()
    drafts: list[str] = []
    chunks = chunk_dir(s.docs_dir, s.chunk_max_tokens, skipped=drafts)
    save_chunks(s.rag_index_path, chunks, s.docs_dir)
    sizes = [c.tokens for c in chunks]
    print(f"indexed {len(chunks)} chunks from {s.docs_dir} -> {s.rag_index_path}")
    print(f"  tokens (estimated, conservative): min {min(sizes)} / avg {sum(sizes) // len(sizes)} / max {max(sizes)}"
          f" (limit {s.chunk_max_tokens})")
    for src, n in sorted(Counter(c.source for c in chunks).items()):
        print(f"  {n} x {src}")
    for d in drafts:
        print(f"  skipped (status: draft, waiting for review): {d}")


if __name__ == "__main__":
    main()
