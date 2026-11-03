"""A minimal stand-in with the EdNova engine's public interface (guide §3), used
only to test the bridge. It is NOT the engine: no LLM, canned answers."""
import asyncio
import enum
import time
import uuid
from dataclasses import dataclass, field

__version__ = "stand-in-1.3"


class Priority(enum.IntEnum):
    INTERACTIVE = 0
    BACKGROUND = 1


@dataclass
class Event:
    name: str
    t_ms: float
    data: dict = field(default_factory=dict)


@dataclass
class Result:
    task: str
    request_id: str
    text: str = None
    data: dict = None
    model_class_used: str = None
    model: str = None
    from_cache: bool = False
    degraded: bool = False
    degrade_reason: str = None
    eta_seconds: float = None
    mode: str = None
    followup_available: bool = False
    events: list = field(default_factory=list)
    latency_ms: float = 0
    tokens_in: int = 0
    tokens_out: int = 0
    repaired: bool = False
    safe_default: bool = False
    route_reason: str = ""
    notes: dict = field(default_factory=dict)


@dataclass
class FollowUp:
    task: str
    reason: str
    overridden: bool = False


class _Router:
    def follow_up(self, c):
        task = {"EXPLAIN": "explain", "HINT": "hint", "QUIZ": "quiz"}.get(c.get("intent"))
        return FollowUp(task=task, reason=f"intent {c.get('intent')} -> {task}")


class _Bank:
    def get(self, exercise_id):
        if exercise_id != "d01":
            return None
        return {"id": "d01", "concept": "puissance", "level": 1, "statement": {"fr": "Calculer la dérivée de x^4."},
                "solution": "4x^3", "hints": ["h1", "h2", "h3"], "common_mistakes": [["x^3", "oubli du coefficient"]]}


class _Cache:
    def __init__(self):
        self.ids = set()

    def invalidate(self, request_id):
        return 1 if request_id in self.ids else 0


class Engine:
    def __init__(self):
        self.router = _Router()
        self.exercises = _Bank()
        self.cache = _Cache()
        self.materials = {}

    @classmethod
    def create(cls, settings=None, **kwargs):
        return cls()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        pass

    async def run(self, task, inputs=None, context=None, priority=Priority.INTERACTIVE, on_event=None):
        if task not in ("classify", "explain", "hint", "grade", "quiz"):
            raise ValueError(f"unknown task: {task}")
        inputs = inputs or {}
        started = time.monotonic()
        events = []

        def emit(name, **data):
            ev = Event(name, (time.monotonic() - started) * 1000, data)
            events.append(ev)
            if on_event:
                on_event(ev)

        rid = str(uuid.uuid4())
        if task == "classify":
            emit("generating", attempt=1, model_class="compact")
            return Result(task, rid, data={"intent": "EXPLAIN", "language": "FRENCH", "confusion": "LOW", "complexity": 1, "confidence": 0.7},
                          model_class_used="compact", events=events)
        if task == "explain":
            if "question" not in inputs:
                raise ValueError("missing input: question")
            emit("retrieving")
            emit("partial_result", sentences=["(u·v)' = u'·v + u·v'"])
            await asyncio.sleep(0.05)
            emit("generating", attempt=1, model_class="midsize")
            self.cache.ids.add(rid)
            return Result(task, rid, text="stand-in explanation", data={"body": "b", "opener": "o", "exercise": None},
                          model_class_used="midsize", events=events, notes={"priority": Priority(priority).name, "ctx": context})
        if task == "grade":
            ok = inputs.get("attempt", "").replace(" ", "") == inputs.get("solution")
            return Result(task, rid, data={"verdict": "CORRECT" if ok else "INCORRECT", "mistake": None, "feedback": "", "graded_by": "sympy"}, events=events)
        return Result(task, rid, text="ok", events=events)

    async def ingest_file(self, path, course_id=None, visibility=None, learner_id=None, priority=None):
        self.materials[path] = course_id
        return {"chunks": [{"id": "m#1", "title": "Intro", "concept": "produit", "concept_confidence": 0.4}]}

    def remove_material(self, material_id, course_id=None):
        return True
