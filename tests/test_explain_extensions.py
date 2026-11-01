"""explain: `passage` (this confuses me, on a prep page) and `board_text` (Ask AI on the whiteboard)."""
import pytest

from engine.core import trim_to_tokens
from gatekeeper.client import estimate_tokens
from tests.conftest import chat_ok
from tests.test_engine import _index, make_engine  # noqa: F401  (_index: the autouse course index)

BODY = "Une constante ne varie pas, donc sa dérivée est nulle : le terme disparaît quand on dérive."
PASSAGE = ("Une constante ne varie pas, donc sa dérivée est 0. Dans f(x) = x² + 7, le 7 disparaît : f'(x) = 2x. "
           "Beaucoup d'élèves écrivent f'(x) = 2x + 7, et c'est faux.")
GENERIC = {"question": "explique-moi ça"}                         # names no topic: retrieval needs the passage


def seen(fake):
    return [r["body"]["messages"][-1]["content"] for r in fake.requests if r["body"]["model"] == "mid-a"]


async def test_the_passage_is_in_the_prompt_and_retrieval_runs_on_it(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 900, 40))
    alone = await eng.run("explain", GENERIC, {"language": "FRENCH"})
    assert alone.notes.get("retrieval") == "no_relevant_chunks" and fake.requests == []     # nothing to search with
    r = await eng.run("explain", {**GENERIC, "passage": PASSAGE}, {"language": "FRENCH"})
    assert not r.degraded and BODY in r.text and r.notes["retrieval_on"] == "passage"
    assert r.notes["sources"] and "derivee-erreurs.fr#oublier-que-la-derivee-d-une-constante-est-nulle" in r.notes["sources"]
    user = seen(fake)[0]
    assert "says it confuses them" in user and PASSAGE in user and "Question: explique-moi ça" in user


async def test_the_cache_key_includes_a_hash_of_the_whole_passage(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 900, 40))
    ctx = {"language": "FRENCH"}
    a1 = await eng.run("explain", {**GENERIC, "passage": PASSAGE}, ctx)
    a2 = await eng.run("explain", {**GENERIC, "passage": "  " + PASSAGE + "  "}, ctx)       # same text, other whitespace
    assert not a1.from_cache and a2.from_cache and len(seen(fake)) == 1
    b = await eng.run("explain", {**GENERIC, "passage": PASSAGE.replace("7", "9")}, ctx)
    assert not b.from_cache and len(seen(fake)) == 2                                          # another passage: a miss
    # passages that differ only AFTER the trim point are still different keys
    base = PASSAGE + " " + "mot " * 400
    c1 = await eng.run("explain", {**GENERIC, "passage": base + "A"}, ctx)
    c2 = await eng.run("explain", {**GENERIC, "passage": base + "B"}, ctx)
    assert not c2.from_cache and len(seen(fake)) == 4 and "trimming_input" in c1.event_names
    q = await eng.run("explain", {"question": "c'est quoi la dérivée d'une constante ?"}, ctx)   # no passage: its own key
    assert not q.from_cache


async def test_board_text_is_trimmed_to_its_share_of_the_budget_and_the_request_still_fits(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 900, 40))
    board = "f(x) = x² + 7\nf'(x) = 2x + 7\n" + "je réfléchis à la dérivée de la constante. " * 400    # a whiteboard full of notes
    r = await eng.run("explain", {"question": "c'est quoi la dérivée d'une constante ?", "board_text": board},
                      {"language": "FRENCH"})
    assert not r.degraded and r.degrade_reason is None and len(seen(fake)) == 1
    ev = next(e for e in r.events if e.name == "trimming_input")
    assert ev.data["input"] == "board_text" and ev.data["from_chars"] == len(board.strip())
    share_chars = int(eng.limit * eng.settings.board_budget_share * 2.0)                # the tests estimate at 2 chars/token
    assert ev.data["to_chars"] <= share_chars + 5
    user = seen(fake)[0]
    assert "whiteboard" in user and "f'(x) = 2x + 7" in user and user.rstrip().endswith("[…]\"") or "[…]" in user
    sent = fake.requests[0]["body"]["messages"]
    assert estimate_tokens(sent) <= eng.limit + 80                                          # the whole prompt is in budget


async def test_board_text_is_part_of_the_cache_key(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 900, 40))
    q, ctx = {"question": "c'est quoi la dérivée d'une constante ?"}, {"language": "FRENCH"}
    first = await eng.run("explain", {**q, "board_text": "f(x) = x² + 7\nf'(x) = 2x + 7"}, ctx)
    same = await eng.run("explain", {**q, "board_text": "f(x) = x² + 7\nf'(x) = 2x + 7 "}, ctx)
    other = await eng.run("explain", {**q, "board_text": "f(x) = x² + 7\nf'(x) = 2x"}, ctx)
    plain = await eng.run("explain", q, ctx)
    assert same.from_cache and not first.from_cache and not other.from_cache and not plain.from_cache
    assert len(seen(fake)) == 3
    assert "whiteboard" in seen(fake)[0] and "whiteboard" not in seen(fake)[2]


async def test_a_generic_question_falls_back_to_the_whiteboard_for_retrieval(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 900, 40))
    r = await eng.run("explain", {"question": "qu'est-ce qui ne va pas ici ?",
                                  "board_text": "f(x) = x² + 7, donc f'(x) = 2x + 7 : la dérivée d'une constante ?"},
                      {"language": "FRENCH"})
    assert not r.degraded and r.notes["retrieval_on"] == "board_text" and r.notes["sources"]
    off = await eng.run("explain", {"question": "qu'est-ce qui ne va pas ici ?", "board_text": "recette de couscous: semoule, eau"},
                        {"language": "FRENCH"})
    assert off.notes.get("retrieval") == "no_relevant_chunks"                                # the gate still applies


async def test_both_together_and_the_ordinary_call_is_unchanged(vt):
    eng, fake, _ = make_engine(vt, lambda b: chat_ok(BODY, 900, 40))
    r = await eng.run("explain", {**GENERIC, "passage": PASSAGE, "board_text": "f'(x) = 2x + 7 ?"}, {"language": "FRENCH"})
    user = seen(fake)[0]
    assert user.index("confuses them") < user.index("whiteboard") and not r.degraded
    plain = await eng.run("explain", {"question": "c'est quoi une dérivée ?"}, {"language": "FRENCH"})
    assert "confuses" not in seen(fake)[-1] and "whiteboard" not in seen(fake)[-1] and not plain.degraded
    assert "retrieval_on" not in plain.notes


async def test_fast_mode_extracts_follow_the_passage(vt):
    from tests.conftest import error
    eng, fake, _ = make_engine(vt, lambda b: error(503, "model_saturated", "busy", {"Retry-After": "4"}),
                               SATURATION_RETRIES=0, COMPACT_MAX_RETRIES=0)
    r = await eng.run("explain", {**GENERIC, "passage": PASSAGE}, {"language": "FRENCH"})
    assert r.mode == "fast_extractive" and any("constante" in e["text"] for e in r.data["excerpts"])


def test_trim_to_tokens_keeps_the_head_on_a_boundary_and_marks_the_cut():
    short = "Une phrase courte."
    assert trim_to_tokens(short, 100) == short
    text = "Première phrase du texte. Deuxième phrase du texte. " * 50
    out = trim_to_tokens(text, 60)
    assert out.endswith("[…]") and len(out) < len(text) and text.startswith(out[:-4].rstrip())
    assert out[:-4].rstrip().endswith(".")                                  # cut on a sentence end
    assert trim_to_tokens("x" * 5000, 10).endswith("[…]")                   # no boundary at all: still cut
