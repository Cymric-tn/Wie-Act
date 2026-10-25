"""
Exercise bank (data/exercises/*.yaml): deterministic selection, no LLM.

  pick(concepts, difficulty, exclude)  first exercise by id among the requested concepts, at the requested
                                       difficulty (nearest level if none), skipping already-served ids
  diagnostic(exclude)                  three exercises of levels 1, 2, 3 on three different concepts
  answer_matches(answer, expected)     normalized comparison: spaces, case, unicode powers (x³ = x^3 = x3),
                                       minus signs, "f'(x) =" / "y =" prefixes

Grading compares to the stored solution and common mistakes first, so the most frequent answers are
judged without a request; only other answers go to the compact grade task.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import yaml

from rag.normalize import tokenize

REQUIRED = ("id", "concept", "level", "solution", "hints", "common_mistakes")
PHRASE_LANGUAGES = ("fr", "en", "ar")


def phrase_language(learner_language: str | None) -> str:
    """ENGLISH -> en, ARABIC -> ar (MSA); FRENCH, DARIJA, ARABIZI -> fr (the course language)."""
    return {"ENGLISH": "en", "ARABIC": "ar"}.get(learner_language or "", "fr")


@dataclass(frozen=True)
class Exercise:
    id: str
    concept: str
    level: int
    solution: str
    hints: tuple[str, ...]
    common_mistakes: tuple[tuple[str, str], ...]          # (expression, feedback)
    template_key: str = ""
    expression: str = ""
    params: tuple[tuple[str, str], ...] = ()              # a, condition...
    free_text: tuple[tuple[str, str], ...] = ()           # (lang, statement) for exercises that fit no template
    phrases: tuple[tuple[str, str], ...] = ()             # (lang, phrase template), from templates.yaml

    def render(self, lang: str = "fr") -> str:
        free = dict(self.free_text)
        if lang in free:
            return free[lang]
        phrase = dict(self.phrases).get(lang) or dict(self.phrases).get("fr") or free.get("fr", "")
        return phrase.format(expr=self.expression, **dict(self.params))

    @property
    def statement(self) -> str:                           # the course-language statement
        return self.render("fr")

    def public(self, lang: str = "fr") -> dict:
        """What may be shown to the learner (no solution, no hints)."""
        return {"id": self.id, "concept": self.concept, "level": self.level, "statement": self.render(lang),
                "expression": self.expression}


@dataclass
class ExerciseBank:
    exercises: list[Exercise] = field(default_factory=list)

    @classmethod
    def load(cls, path: str, templates_path: str = "data/templates.yaml") -> "ExerciseBank":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        phrases_by_key = {}
        if templates_path:
            with open(templates_path, encoding="utf-8") as f:
                phrases_by_key = (yaml.safe_load(f) or {}).get("exercise") or {}
        out, seen = [], set()
        for e in data.get("exercises") or []:
            missing = [k for k in REQUIRED if k not in e]
            free = e.get("statement") or {}
            key = e.get("template_key", "")
            ok_text = bool(free) or (key in phrases_by_key and e.get("expression"))
            if missing or len(e["hints"]) != 3 or not e["common_mistakes"] or e["id"] in seen or not ok_text:
                raise ValueError(f"{path}: exercise {e.get('id')!r} invalid (missing {missing}, 3 hints, "
                                 f"common mistakes, unique id, a known template_key + expression or a statement)")
            seen.add(e["id"])
            params = tuple((k, str(e[k])) for k in ("a", "condition") if k in e)
            out.append(Exercise(str(e["id"]), str(e["concept"]), int(e["level"]), str(e["solution"]),
                                tuple(map(str, e["hints"])),
                                tuple((str(m["expr"]), str(m["feedback"])) for m in e["common_mistakes"]),
                                key, str(e.get("expression", "")), params,
                                tuple((k, str(v)) for k, v in free.items()),
                                tuple((k, str(v)) for k, v in (phrases_by_key.get(key) or {}).items())))
        return cls(sorted(out, key=lambda x: x.id))

    def get(self, exercise_id: str) -> Exercise | None:
        return next((e for e in self.exercises if e.id == exercise_id), None)

    @property
    def concepts(self) -> list[str]:
        return list(dict.fromkeys(e.concept for e in self.exercises))

    def concepts_in(self, text: str, extra_terms: tuple[str, ...] = ()) -> list[str]:
        """Bank concepts named in a learner message (directly or through glossary course terms)."""
        toks = set(tokenize(text)) | {t for term in extra_terms for t in tokenize(term)}
        return [c for c in self.concepts if set(tokenize(c)) <= toks]

    def pick(self, concepts: list[str] | None = None, difficulty: int = 1,
             exclude: tuple[str, ...] = (), strict: bool = False) -> Exercise | None:
        """strict: only the requested concepts (explain's practice item); else any concept when exhausted."""
        pool = [e for e in self.exercises if e.id not in exclude and (not concepts or e.concept in concepts)]
        if not pool and concepts and not strict:          # concept exhausted: any concept
            pool = [e for e in self.exercises if e.id not in exclude]
        if not pool:
            return None
        best = min(abs(e.level - difficulty) for e in pool)
        return next(e for e in pool if abs(e.level - difficulty) == best)

    def diagnostic(self, exclude: tuple[str, ...] = ()) -> list[Exercise]:
        chosen: list[Exercise] = []
        for level in (1, 2, 3):
            for e in self.exercises:
                if e.level == level and e.id not in exclude and e.concept not in {c.concept for c in chosen}:
                    chosen.append(e)
                    break
        return chosen


_SUP = str.maketrans({"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8",
                      "⁹": "9", "⁻": "-", "ⁿ": "n"})


def normalize_answer(s: str) -> str:
    t = unicodedata.normalize("NFKC", str(s).translate(_SUP)).lower()
    t = t.replace("−", "-").replace("–", "-").replace("^", "").replace("·", "").replace("×", "*").replace("*", "")
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"^(f'\(x\)|f'\([^)]*\)|y)=", "", t)      # "f'(x) = 4x³" == "4x3"; "y = 4x - 4" == "4x-4"
    return t.rstrip(".")


def answer_matches(answer: str, expected: str) -> bool:
    return bool(normalize_answer(answer)) and normalize_answer(answer) == normalize_answer(expected)
