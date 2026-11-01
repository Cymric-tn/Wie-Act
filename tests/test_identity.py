"""learner_id / room_id / course_id / class_id: copied into every metrics record, never into a cache key."""
import json

from tests.test_engine import GOOD, MSG, always, make_engine
from tests.conftest import chat_ok

IDS = {"learner_id": "L1", "room_id": "R1", "course_id": "calc1", "class_id": "C7"}


async def test_ids_reach_every_record_of_a_run(vt):
    eng, fake, sink = make_engine(vt, always(lambda: chat_ok(json.dumps(GOOD))))
    await eng.run("classify", MSG, IDS)
    assert sink.records
    for rec in sink.records:
        assert (rec.learner_id, rec.room_id, rec.course_id, rec.class_id) == ("L1", "R1", "calc1", "C7"), rec


async def test_ids_are_not_part_of_the_cache_key(vt):
    eng, fake, sink = make_engine(vt, always(lambda: chat_ok(json.dumps(GOOD))))
    await eng.run("classify", MSG, IDS)
    r2 = await eng.run("classify", MSG, {**IDS, "learner_id": "L2", "room_id": None})
    assert r2.from_cache and len(fake.requests) == 1
    hit = [x for x in sink.records if x.outcome == "cache_hit"][0]
    assert hit.learner_id == "L2" and hit.room_id is None


async def test_ids_do_not_leak_between_runs_and_default_to_none(vt):
    eng, fake, sink = make_engine(vt, always(lambda: chat_ok(json.dumps(GOOD))))
    await eng.run("classify", MSG, IDS)
    n = len(sink.records)
    await eng.run("classify", {"message": "autre question sur les limites"})
    assert all(r.learner_id is None for r in sink.records[n:])
