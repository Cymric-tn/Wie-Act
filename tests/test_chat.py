"""scripts/chat.py driven line by line against a fake provider (no LLM)."""
import json
import random

import pytest

from engine import Engine
from gatekeeper.cache import ExactCache
from gatekeeper.client import LLMClient
from metrics import MemorySink
from rag.chunker import chunk_dir
from rag.store import save_chunks
from scripts.chat import Console, TeeSink
from tests.conftest import FnProvider, chat_ok, make_settings


def label(intent, language="FRENCH"):
    return json.dumps({"intent": intent, "language": language, "confusion": "LOW", "complexity": 2,
                       "confidence": 0.9})


@pytest.fixture
def console(vt, tmp_path):
    index = str(tmp_path / "chunks.json")
    save_chunks(index, chunk_dir("data/docs"))
    intent = {"value": "EXPLAIN"}

    def fn(body):
        system = body["messages"][0]["content"]
        if system.startswith("You label ONE message"):
            return chat_ok(label(intent["value"]))
        return chat_ok("Que vaut f(1) ?" if "guiding question" in system else "La tangente a pour pente f'(a).")

    s = make_settings(RAG_INDEX_PATH=index)
    sink = TeeSink(MemorySink())
    eng = Engine.create(s, client=LLMClient(s, FnProvider(fn)), sink=sink,
                        cache=ExactCache(":memory:", s.cache_ttl_s), clock=vt.clock, sleep=vt.sleep,
                        rng=random.Random(1))
    lines = []
    return Console(eng, sink, out=lines.append), lines, intent


async def test_plain_line_is_a_turn_classify_then_explain(console):
    c, lines, _ = console
    assert await c.handle("Comment trouver l'équation de la tangente ?")
    text = "\n".join(lines)
    assert "-> classify" in text and "-> explain" in text
    assert "La tangente a pour pente f'(a)." in text and "sources: derivee-definition.fr#" in text
    assert "· retrieving" in text and "· generating" in text                 # live progress events
    turn_ids = {r.turn_id for r in c.sink.records if r.kind == "run"}
    assert len(turn_ids) == 1                                                # both calls, one turn


async def test_answer_attempt_routes_to_hint_and_chitchat_stops(console):
    c, lines, intent = console
    intent["value"] = "ANSWER_ATTEMPT"
    await c.handle("Résoudre 2x + 6 = 0 : j'ai trouvé x = 3")
    assert "-> hint" in "\n".join(lines) and "Que vaut f(1) ?" in "\n".join(lines)
    lines.clear()
    await c.handle("Merci !")
    assert "answered_by_rules" in "\n".join(lines) and "no tutoring follow-up" in "\n".join(lines)


async def test_commands(console):
    c, lines, _ = console
    await c.handle("/lang arabizi")
    assert c.language == "ARABIZI"
    await c.handle("/lang klingon")
    assert c.language == "ARABIZI" and "languages:" in "\n".join(lines)
    await c.handle("/level lycée")
    await c.handle("/bg")
    assert c.level == "lycée" and c.priority.value == "BACKGROUND"
    await c.handle("/hint Dériver f(x) = x(x + 1) :: f'(x) = 1")
    hint_runs = [r for r in c.sink.records if r.kind == "run" and r.task == "hint"]
    assert hint_runs and hint_runs[0].priority == "BACKGROUND"
    await c.handle("/hint")
    assert "incomplete command" in lines[-1]
    await c.handle("/stats")
    assert lines[-1].strip().startswith("turns 1 ") and "provider requests" in lines[-1]   # only the /hint
    assert await c.handle("/quit") is False
