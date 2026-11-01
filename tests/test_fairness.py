"""v1.3 fairness: one waiting INTERACTIVE request per learner (the newer replaces the older)."""
import asyncio

from engine.result import Result
from gatekeeper.errors import Superseded
from gatekeeper.queue import Priority
from gatekeeper.retry import CallContext
from metrics import identity
from tests.test_queue import GatedProvider, _settle, caller, send, sent_labels


def send_as(rc, label, priority, learner):
    async def go():
        identity.set({"learner_id": learner} if learner else {})
        from config import ModelClass
        from gatekeeper.client import ChatRequest
        req = ChatRequest(ModelClass.COMPACT, ({"role": "user", "content": label},))
        return await rc.chat(req, CallContext(priority=priority))
    return asyncio.create_task(go())


async def test_newer_interactive_of_the_same_learner_replaces_the_queued_older():
    fake = GatedProvider()
    rc = caller(fake, MAX_CONCURRENT=1)
    hold = send(rc, "holder", Priority.INTERACTIVE)              # takes the only slot
    await _settle()
    old = send_as(rc, "old", Priority.INTERACTIVE, "L1")
    other = send_as(rc, "other-learner", Priority.INTERACTIVE, "L2")
    await _settle()
    new = send_as(rc, "new", Priority.INTERACTIVE, "L1")
    await _settle()
    assert old.done() and isinstance(old.exception(), Superseded)
    assert not other.done() and not new.done()
    for _ in range(3):                                           # open every gate that appears
        await _settle()
        for g in fake.gates:
            g.set()
    await asyncio.gather(hold, other, new)
    assert "old" not in sent_labels(fake) and {"new", "other-learner"} <= set(sent_labels(fake))


async def test_background_and_granted_requests_are_never_superseded():
    fake = GatedProvider()
    rc = caller(fake, MAX_CONCURRENT=1)
    running = send_as(rc, "running", Priority.INTERACTIVE, "L1")  # already holds the slot
    await _settle()
    bg1 = send_as(rc, "bg1", Priority.BACKGROUND, "L1")
    bg2 = send_as(rc, "bg2", Priority.BACKGROUND, "L1")
    newer = send_as(rc, "newer", Priority.INTERACTIVE, "L1")
    await _settle()
    assert not running.done() and not bg1.done() and not bg2.done()
    for _ in range(6):
        await _settle()
        for g in fake.gates:
            g.set()
    await asyncio.gather(running, bg1, bg2, newer)


async def test_engine_answers_the_superseded_request_degraded(vt):
    import json
    from tests.conftest import chat_ok
    from tests.test_engine import GOOD, make_engine
    gate = asyncio.Event()
    eng, fake, sink = make_engine(vt, lambda body: chat_ok(json.dumps(GOOD)), MAX_CONCURRENT=1)
    # occupy the only slot with someone else's request, held by a gate on the limiter
    await eng.caller.limiter.acquire("chat")
    a = asyncio.create_task(eng.run("classify", {"message": "première question sur les dérivées"},
                                    {"learner_id": "L1"}))
    await _settle()
    b = asyncio.create_task(eng.run("classify", {"message": "deuxième question sur les limites"},
                                    {"learner_id": "L1"}))
    await _settle()
    ra = await a
    assert isinstance(ra, Result) and ra.degraded and ra.degrade_reason == "superseded"
    assert ra.eta_seconds is None and len(fake.requests) == 0
    eng.caller.limiter.release()
    rb = await b
    assert not rb.degraded
    assert any(r.degrade_reason == "superseded" and r.learner_id == "L1" for r in sink.records if r.kind == "run")


async def test_second_request_of_a_room_is_room_busy_with_an_eta(vt):
    import json
    from tests.conftest import chat_ok
    from tests.test_engine import GOOD, make_engine
    eng, fake, sink = make_engine(vt, lambda body: chat_ok(json.dumps(GOOD)), MAX_CONCURRENT=1)
    await eng.caller.limiter.acquire("chat")                     # keep the first request in flight
    first = asyncio.create_task(eng.run("classify", {"message": "question de la salle"}, {"room_id": "R9"}))
    await _settle()
    second = await eng.run("classify", {"message": "autre question de la salle"}, {"room_id": "R9"})
    assert second.degraded and second.degrade_reason == "room_busy" and second.eta_seconds >= 1.0
    assert second.event_names == ["room_busy"] and second.data is None and second.text is None
    other_room = asyncio.create_task(eng.run("classify", {"message": "salle différente"}, {"room_id": "R10"}))
    await _settle()
    assert not other_room.done()                                 # not refused: it waits for the slot like anyone
    eng.caller.limiter.release()
    await asyncio.gather(first, other_room)
    third = await eng.run("classify", {"message": "encore une question"}, {"room_id": "R9"})
    assert not third.degraded                                    # the room is free again
    assert len(fake.requests) == 3
    assert any(r.degrade_reason == "room_busy" and r.room_id == "R9" for r in sink.records if r.kind == "run")
