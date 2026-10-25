"""
Prompt assembly under the input budget. FelCloud silently truncates input near 2048 tokens (recon),
so we never send more than INPUT_TOKEN_BUDGET * (1 - BUDGET_SAFETY_MARGIN) estimated tokens, using
the conservative estimator from gatekeeper.client (2 chars/token, an over-estimate on measured text).

Priority (highest first): system prompt, question  ->  top chunks in rank order  ->  history,
newest turn first. History is trimmed first: once a turn does not fit, it and every older turn
are dropped (no gaps in the conversation). A chunk that does not fit is skipped and a lower-ranked,
shorter one may still fit. Everything dropped is returned and logged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Sequence

from gatekeeper.client import estimate_tokens
from rag.chunker import Chunk

log = logging.getLogger("ednova.budget")

Turn = dict          # {"role": "user"|"assistant", "content": str}
Render = Callable[[Sequence[Chunk], Sequence[Turn]], list[dict]]


class BudgetError(ValueError):
    """System prompt + question alone exceed the budget."""


@dataclass
class Assembly:
    messages: list[dict]
    chunk_ids: list[str]
    history_kept: int
    tokens: int
    limit: int
    dropped: list[dict] = field(default_factory=list)

    @property
    def all_chunks_fit(self) -> bool:
        return not any(d["kind"] == "chunk" for d in self.dropped)


def limit_for(input_token_budget: int, safety_margin: float) -> int:
    return int(input_token_budget * (1 - safety_margin))


def assemble(render: Render, chunks: Sequence[Chunk], history: Sequence[Turn], limit: int) -> Assembly:
    base = render([], [])
    if estimate_tokens(base) > limit:
        raise BudgetError(f"system + question need ~{estimate_tokens(base)} tokens > limit {limit}")
    dropped: list[dict] = []
    chosen: list[Chunk] = []
    for c in chunks:
        if estimate_tokens(render([*chosen, c], [])) <= limit:
            chosen.append(c)
        else:
            dropped.append({"kind": "chunk", "id": c.chunk_id, "tokens": c.tokens, "reason": "budget"})
    kept: list[Turn] = []
    for i in range(len(history) - 1, -1, -1):
        if estimate_tokens(render(chosen, [history[i], *kept])) <= limit:
            kept.insert(0, history[i])
        else:
            dropped.extend({"kind": "history", "id": f"turn-{j}", "reason": "budget"} for j in range(i + 1))
            break
    messages = render(chosen, kept)
    a = Assembly(messages, [c.chunk_id for c in chosen], len(kept), estimate_tokens(messages), limit, dropped)
    if dropped:
        log.info("budget %d/%d tokens; dropped %s", a.tokens, limit, [(d["kind"], d["id"]) for d in dropped])
    return a
