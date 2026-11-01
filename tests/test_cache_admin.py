"""v1.3: a professor rejects a flagged answer: engine.cache.invalidate(request_id)."""
import json

from gatekeeper.cache import ExactCache
from tests.conftest import chat_ok
from tests.test_engine import GOOD, MSG, always, make_engine


def test_invalidate_removes_the_entries_behind_a_request_and_unknown_ids_are_harmless():
    c = ExactCache(":memory:", 1000)
    c.put("k1", "explain", {"text": "a"})
    c.link("req-a", "k1")
    c.mark_negative("k1", "explain", ["wrong_math"], 100)
    assert c.invalidate("nope") == 0 and c.get("k1") is not None
    assert c.invalidate("req-a") == 1
    assert c.get("k1") is None and c.get_negative("k1") is None
    assert c.invalidate("req-a") == 0


async def test_invalidating_a_cache_hit_request_removes_the_entry_it_was_served_from(vt):
    eng, fake, _ = make_engine(vt, always(lambda: chat_ok(json.dumps(GOOD))))
    first = await eng.run("classify", MSG)
    second = await eng.run("classify", MSG)                  # served from the cache: another request id
    assert second.from_cache and second.request_id != first.request_id
    assert eng.cache.invalidate(second.request_id) == 1      # the professor saw the second answer
    third = await eng.run("classify", MSG)
    assert not third.from_cache and len(fake.requests) == 2  # regenerated
    assert eng.cache.invalidate(first.request_id) == 0       # a request id names one generation of the entry
