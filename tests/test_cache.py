import pytest

from gatekeeper.cache import ExactCache, NotCacheable, cache_key

MSGS = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "derivative of x^2"}]


def key(**over):
    args = dict(task="hint", model_class="compact", messages=MSGS, language="FRENCH", level="beginner",
                style="analogy")
    return cache_key(**{**args, **over})


def test_key_is_stable_and_whitespace_insensitive():
    spaced = [{"role": "system", "content": " be   brief "}, {"role": "user", "content": "derivative\nof x^2"}]
    assert key() == key() == key(messages=spaced)


@pytest.mark.parametrize("field,value", [
    ("task", "classify"), ("model_class", "midsize"), ("language", "ARABIC"), ("level", "advanced"),
    ("style", "formal"), ("prompt_version", "2"), ("messages", [{"role": "user", "content": "derivative of x^3"}])])
def test_every_key_component_matters(field, value):
    assert key(**{field: value}) != key()


def test_case_is_preserved_for_math_and_code():
    upper = [{"role": "user", "content": "derivative of X^2"}]
    assert key(messages=upper) != key(messages=[{"role": "user", "content": "derivative of x^2"}])


def test_roundtrip_and_persistence(tmp_path):
    path = str(tmp_path / "c.sqlite")
    c = ExactCache(path, ttl_s=60)
    c.put(key(), "hint", {"text": "Que vaut la pente ?", "model_class": "compact"})
    c.close()
    c2 = ExactCache(path, ttl_s=60)
    assert c2.get(key())["text"] == "Que vaut la pente ?"
    assert (c2.hits, c2.misses) == (1, 0)


def test_ttl_expiry():
    now = [1000.0]
    c = ExactCache(":memory:", ttl_s=10, clock=lambda: now[0])
    c.put(key(), "hint", {"text": "q"})
    now[0] += 10
    assert c.get(key()) is not None
    now[0] += 0.1
    assert c.get(key()) is None and c.misses == 1
    assert c.db.execute("SELECT COUNT(*) FROM exact").fetchone()[0] == 0


@pytest.mark.parametrize("flag", ["degraded", "fallback", "safe_default"])
def test_degraded_or_fallback_never_cached(flag):
    c = ExactCache(":memory:", ttl_s=60)
    with pytest.raises(NotCacheable):
        c.put(key(), "hint", {"text": "q", flag: True})
    assert c.get(key()) is None
