"""
Task registry: task -> prompt file, model class, output schema, max_tokens (+ task-specific hooks).
The model class is read from router.policy.ROUTES so the routing table exists in one place only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config import ModelClass
from engine import language as L
from engine.prompts import PromptTemplate
from gatekeeper.client import estimate_tokens
from rag.normalize import tokenize
from router.policy import ROUTES

Messages = list[dict[str, str]]


@dataclass(frozen=True)
class TaskContext:
    language: str | None = None          # one of language.LANGUAGES, upper-case
    level: str | None = None
    style: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)   # escalate, complexity, ...

    @classmethod
    def from_mapping(cls, m: Mapping[str, Any]) -> "TaskContext":
        rest = {k: v for k, v in m.items() if k not in ("language", "level", "style")}
        return cls(L.normalize_label(m.get("language")), m.get("level"), m.get("style"), rest)

    def as_mapping(self) -> dict[str, Any]:
        return {**self.extra, "language": self.language, "level": self.level, "style": self.style}


@dataclass(frozen=True)
class TaskSpec:
    name: str
    prompt: str                                          # prompts/<prompt>.md
    max_tokens: int
    temperature: float = 0.2
    output_schema: type[BaseModel] | None = None         # JSON task when set
    required_inputs: tuple[str, ...] = ()
    build: Callable[[PromptTemplate, Mapping, TaskContext, int], Messages] | None = None    # int: token limit
    prepare: Callable[[Mapping, TaskContext], TaskContext] | None = None
    shortcut: Callable[[Mapping, TaskContext], dict | None] | None = None      # answer without the LLM
    finalize_data: Callable[[dict, Mapping, TaskContext], tuple[dict, dict]] | None = None
    finalize_text: Callable[[str, PromptTemplate, Mapping, TaskContext], tuple[str, str | None]] | None = None
    safe_default: Callable[[Mapping, TaskContext], dict] | None = None        # JSON tasks
    fallback_text: Callable[[PromptTemplate, Mapping, TaskContext], str | None] | None = None
    # grounded tasks: retrieve -> budget (-> compress) -> generate; render(prompt, inputs, ctx, chunks, history)
    grounded: bool = False
    render_grounded: Callable[[PromptTemplate, Mapping, TaskContext, Sequence, Sequence], Messages] | None = None
    not_found_text: Callable[[PromptTemplate, Mapping, TaskContext], str] | None = None
    # local tasks: the answer comes from data (exercise bank), not from a model; "_text" = body to show
    local: Callable[[Mapping, TaskContext], dict] | None = None
    # deterministic scaffolding around the model's text (opener, practice exercise): applied to generated,
    # cached and fast-mode answers alike; the cache stores the model's text only. (text, inputs, ctx, opener)
    decorate: Callable[[str, Mapping, TaskContext, bool], tuple[str, dict]] | None = None
    # every equation in the model's text must come from the retrieved chunks / appended exercise / question
    check_grounding: bool = False
    # re-apply finalize_data on cache hits, so rule changes (caps, floors, language checks) reach old entries;
    # only for idempotent rules
    rules_on_hit: bool = False

    @property
    def model_class(self) -> ModelClass:
        return ROUTES[self.name].default


class Registry:
    def __init__(self, specs: list[TaskSpec] = ()):
        self._specs: dict[str, TaskSpec] = {}
        for s in specs:
            self.register(s)

    def register(self, spec: TaskSpec) -> None:
        if spec.name not in ROUTES:
            raise ValueError(f"task {spec.name!r} has no row in router.policy.ROUTES")
        self._specs[spec.name] = spec

    def get(self, name: str) -> TaskSpec:
        if name not in self._specs:
            raise ValueError(f"unknown or not yet implemented task {name!r}; have {sorted(self._specs)}")
        return self._specs[name]

    def __iter__(self) -> Iterator[TaskSpec]:
        return iter(self._specs.values())


# ====================================================================== classify
Intent = Literal["EXPLAIN", "HINT", "QUIZ", "ANSWER_ATTEMPT", "DIAGNOSTIC", "OFF_TOPIC", "CHITCHAT"]
Language = Literal["DARIJA", "ARABIZI", "FRENCH", "ARABIC", "ENGLISH"]


class Classification(BaseModel):
    model_config = ConfigDict(extra="ignore")
    intent: Intent
    language: Language
    confusion: Literal["LOW", "MEDIUM", "HIGH"]
    complexity: int = Field(ge=1, le=5)
    confidence: float = Field(ge=0.0, le=1.0)

    # normalization, not repair: small models write "explain" or "answer attempt" or 4.0
    @field_validator("intent", "language", "confusion", mode="before")
    @classmethod
    def _upper(cls, v):
        return re.sub(r"[\s-]+", "_", v.strip().upper()) if isinstance(v, str) else v

    @field_validator("complexity", mode="before")
    @classmethod
    def _int(cls, v):
        try:
            return min(5, max(1, round(float(v))))
        except (TypeError, ValueError):
            return v

    @field_validator("confidence", mode="before")
    @classmethod
    def _unit(cls, v):
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return v


_GREETINGS = {"salut", "bonjour", "hello", "hi", "aslema", "3aslema", "merci", "ok", "ok merci", "thanks",
              "thank you", "yaatik saha", "ya3tik sa7a", "سلام", "عسلامة", "شكرا", "مرحبا"}


def _classify_build(p: PromptTemplate, inputs: Mapping, ctx: TaskContext, limit: int = 0) -> Messages:
    return [{"role": "system", "content": p.render("system")},
            {"role": "user", "content": p.render("user", message=str(inputs["message"]).strip())}]


def _classify_shortcut(inputs: Mapping, ctx: TaskContext) -> dict | None:
    """Bare greetings / thanks cost no request (exact match only)."""
    msg = str(inputs["message"])
    if msg.lower().strip(" !.?،") in _GREETINGS:
        return Classification(intent="CHITCHAT", language=L.detect(msg)[0], confusion="LOW",
                              complexity=1, confidence=1.0).model_dump()
    return None


MODEL_MAX_CONFIDENCE = 0.9      # 1.0 is reserved for rule-based answers (greetings shortcut)


def _classify_finalize(data: dict, inputs: Mapping, ctx: TaskContext) -> tuple[dict, dict]:
    final, source = L.reconcile(data["language"], str(inputs["message"]))
    notes = {"model_language": data["language"], "language_source": source}
    if data["confidence"] > MODEL_MAX_CONFIDENCE:
        notes["model_confidence"] = data["confidence"]
    confusion = data["confusion"]
    if confusion != "HIGH" and L.says_lost(str(inputs["message"])):   # "ma fhemt chay": the learner said it
        notes["model_confusion"] = confusion
        confusion = "HIGH"
    return {**data, "language": final, "confidence": min(data["confidence"], MODEL_MAX_CONFIDENCE),
            "confusion": confusion}, notes


def _classify_default(inputs: Mapping, ctx: TaskContext) -> dict:
    return Classification(intent="EXPLAIN", language=L.detect(str(inputs["message"]))[0], confusion="MEDIUM",
                          complexity=2, confidence=0.0).model_dump()


CLASSIFY = TaskSpec(
    name="classify", prompt="classify", max_tokens=80, temperature=0.0, output_schema=Classification,
    required_inputs=("message",), build=_classify_build, shortcut=_classify_shortcut,
    finalize_data=_classify_finalize, safe_default=_classify_default, rules_on_hit=True)


# ====================================================================== hint
MAX_ARABIC_TERMS = 12


def load_templates(path: str) -> dict:
    """data/templates.yaml (openers, practice line, generic grading feedback); {} when absent."""
    import os
    import yaml
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_terms_ar(path: str) -> tuple[tuple[str, str], ...]:
    """data/terms_ar.yaml -> ((french, arabic), ...); empty when the file is absent."""
    import os
    import yaml
    if not path or not os.path.exists(path):
        return ()
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return tuple((str(t["fr"]), str(t["ar"])) for t in data.get("terms") or [])


def terms_rule(ctx: TaskContext, *texts: str) -> str:
    """ARABIC answers: the data/terms_ar.yaml entries whose French term occurs in the question / material."""
    if ctx.language != "ARABIC" or not ctx.extra.get("terms_ar"):
        return ""
    words = set().union(*(L.latin_words(t) for t in texts)) if texts else set()
    toks = set().union(*(tokenize(t) for t in texts)) if texts else set()     # Arabic side, normalized
    picked = [f"{ar} ({fr})" for fr, ar in ctx.extra["terms_ar"]
              if L.latin_words(fr) <= words or set(tokenize(ar)) <= toks][:MAX_ARABIC_TERMS]
    return ("\n- Arabic math terms to use: " + ", ".join(picked) + ".") if picked else ""


def allowed_latin(ctx: TaskContext, *texts: str) -> set[str]:
    """Latin words an Arabic-script answer may contain: French course terms + the words of its sources."""
    return set(ctx.extra.get("course_words") or ()) | set().union(*(L.latin_words(t) for t in texts))


def contamination_guard(text: str, prefix: str, ctx: TaskContext, *sources: str):
    bad = L.contamination(text, allowed_latin(ctx, *sources))
    return (f"{prefix}:contamination", {"contamination": bad}) if bad else (None, None)


def _hint_prepare(inputs: Mapping, ctx: TaskContext) -> TaskContext:
    if ctx.language:
        return ctx
    text = f"{inputs.get('problem', '')} {inputs.get('attempt') or ''}"
    return replace(ctx, language=L.detect(text)[0])


def _hint_user(p: PromptTemplate, problem: str, attempt: str | None) -> str:
    return p.render("user", problem=problem.strip(), attempt=(attempt or "").strip() or "(none yet)")


def _hint_build(p: PromptTemplate, inputs: Mapping, ctx: TaskContext, limit: int = 0) -> Messages:
    lang = ctx.language or "FRENCH"
    sources = (str(inputs["problem"]), str(inputs.get("attempt") or ""))
    system = p.render("system", level=ctx.level or "not specified", language_rule=p.sections[f"lang.{lang}"],
                      terms_rule=terms_rule(ctx, *sources))
    ex = p.fields(f"example.{lang}")
    return [{"role": "system", "content": system},
            {"role": "user", "content": _hint_user(p, ex["problem"], ex.get("attempt"))},
            {"role": "assistant", "content": ex["hint"]},
            {"role": "user", "content": _hint_user(p, str(inputs["problem"]), inputs.get("attempt"))}]


_LABEL = re.compile(r"^\s*(hint|indice|astuce|tip|question)\s*:\s*", re.I)


def reveals_answer(hint: str, solution: str | None) -> bool:
    """True if the hint contains the stored solution (verbatim, or as '= value')."""
    if not solution or not str(solution).strip():
        return False
    squash = lambda s: re.sub(r"\s+", "", str(s).lower())
    sol, h = squash(solution), squash(hint)
    if len(sol) >= 3 and sol in h:
        return True
    m = re.search(r"(-?\d+(?:[.,]\d+)?)$", sol)
    return bool(m and re.search("=" + re.escape(m.group(1)) + r"(?![\d.,])", h))


MAX_HINT_WORDS = 40          # the prompt asks for 25; beyond 40 the model is rambling or looping


LOOP_MAX_GAP = 12      # words between two occurrences of the repeated phrase


def loop_start(text: str) -> int | None:
    """Word index where a repetition loop starts, or None. A loop = some 3-word phrase occurring 3+ times IN A
    ROW, each within LOOP_MAX_GAP words of the previous ("la mélange des concepts de la mélange des...").
    A phrase used three times across a normal explanation ("le taux d'accroissement") is not a loop: the
    first detector (any 3-gram 3+ times) sent good answers to fast mode."""
    w = text.lower().split()
    where: dict[str, list[int]] = {}
    for i in range(len(w) - 2):
        where.setdefault(" ".join(w[i:i + 3]), []).append(i)
    starts = [pos[i] for pos in where.values() for i in range(len(pos) - 2)
              if pos[i + 1] - pos[i] <= LOOP_MAX_GAP and pos[i + 2] - pos[i + 1] <= LOOP_MAX_GAP]
    return min(starts) if starts else None


def degenerate(text: str) -> bool:
    """Repetition loop (small models do this, in Arabizi and in long explanations)."""
    return loop_start(text) is not None


LOOP_SALVAGE_MIN_WORDS = 40


def salvage_before_loop(text: str) -> str | None:
    """The text before a repetition loop, cut at the last complete sentence, if it has 40+ words; else None."""
    spans = list(re.finditer(r"\S+", text))
    start = loop_start(text)
    if start is None:
        return None
    prefix = text[:spans[start].start()]
    cut = max(prefix.rfind(c) for c in ".!?؟")
    if cut < 0:
        return None
    kept = prefix[:cut + 1].strip()
    return kept if len(kept.split()) >= LOOP_SALVAGE_MIN_WORDS else None


def _first_question(text: str) -> str | None:
    m = re.match(r"(.+?[?؟])(\s|$)", text)
    return m.group(1).strip() if m and len(m.group(1).split()) <= MAX_HINT_WORDS else None


def _hint_finalize(text: str, p: PromptTemplate, inputs: Mapping, ctx: TaskContext) -> tuple[str, str | None]:
    """(hint, guard reason or None). A guarded hint is replaced by the language's safe template."""
    t = _LABEL.sub("", " ".join(text.split())).strip().strip('"“”«»').strip()
    if degenerate(t):
        return _hint_fallback(p, inputs, ctx), "hint_guard:degenerate"
    if len(t.split()) > MAX_HINT_WORDS:                     # keep a complete leading question if there is one
        q = _first_question(t)
        if q is None:
            return _hint_fallback(p, inputs, ctx), "hint_guard:too_long"
        t = q
    if reveals_answer(t, inputs.get("solution")):
        return _hint_fallback(p, inputs, ctx), "hint_guard:answer_leak"
    if L.drifted(t, ctx.language or "FRENCH"):
        return _hint_fallback(p, inputs, ctx), "hint_guard:language_drift"
    guard, detail = contamination_guard(t, "hint_guard", ctx, str(inputs["problem"]), str(inputs.get("attempt") or ""))
    return (t, guard, detail) if guard else (t, None)


def _hint_fallback(p: PromptTemplate, inputs: Mapping, ctx: TaskContext) -> str:
    return p.sections[f"fallback.{ctx.language or 'FRENCH'}"]


def _hint_stored(inputs: Mapping, ctx: TaskContext) -> dict | None:
    """A bank exercise carries graded hints: serve them in order, no request. hint_level is 1-based."""
    hints = list(inputs.get("stored_hints") or [])
    if not hints:
        return None
    n = min(max(int(inputs.get("hint_level") or 1), 1), len(hints))
    return {"_text": hints[n - 1], "hint_level": n, "hints_total": len(hints), "source": "bank"}


HINT = TaskSpec(
    name="hint", prompt="hint", max_tokens=80, temperature=0.3, required_inputs=("problem",),
    build=_hint_build, prepare=_hint_prepare, shortcut=_hint_stored, finalize_text=_hint_finalize,
    fallback_text=_hint_fallback)


# ====================================================================== compress (extractive)
_SENT_SPLIT = re.compile(r"(?<=[.!?؟])\s+|\n+")


class Compression(BaseModel):
    model_config = ConfigDict(extra="ignore")
    keep: list[int]

    @field_validator("keep", mode="before")
    @classmethod
    def _ints(cls, v):
        if isinstance(v, str):
            v = re.findall(r"\d+", v)
        if isinstance(v, list):
            out = []
            for x in v:
                try:
                    out.append(int(x))
                except (TypeError, ValueError):
                    return v
            return sorted(set(out))
        return v


def _chunk_parts(c) -> tuple[str, str]:
    return (c["chunk_id"], c["text"]) if isinstance(c, Mapping) else (c.chunk_id, c.text)


def compress_sentences(inputs: Mapping) -> list[tuple[str, str]]:
    """(chunk_id, sentence) for every sentence of every chunk, in rank order. The first line of a
    chunk is its "title — section" header, not evidence, so it is not numbered."""
    out = []
    for c in inputs["chunks"]:
        cid, text = _chunk_parts(c)
        body = text.split("\n", 1)[1] if "\n" in text else text
        out.extend((cid, s.strip(" -")) for s in _SENT_SPLIT.split(body) if len(s.strip(" -")) > 2)
    return out


def _compress_build(p: PromptTemplate, inputs: Mapping, ctx: TaskContext, limit: int = 0) -> Messages:
    """Numbered sentences, as many as fit in the limit (lowest-ranked chunks are cut first)."""
    system = {"role": "system", "content": p.render("system")}
    lines: list[str] = []
    for i, (_, sent) in enumerate(compress_sentences(inputs), 1):
        cand = [*lines, f"[{i}] {sent}"]
        user = p.render("user", question=str(inputs["question"]).strip(), sentences="\n".join(cand))
        if limit and lines and estimate_tokens([system, {"role": "user", "content": user}]) > limit:
            break
        lines = cand
    return [system, {"role": "user", "content": p.render("user", question=str(inputs["question"]).strip(),
                                                          sentences="\n".join(lines))}]


def _lexical_pick(inputs: Mapping, max_sentences: int = 6) -> list[int]:
    """No-LLM fallback: sentences sharing the most question terms, original order kept."""
    q = set(tokenize(str(inputs["question"])))
    sents = compress_sentences(inputs)
    scored = [(len(q & set(tokenize(s))), i) for i, (_, s) in enumerate(sents, 1)]
    best = sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))[:max_sentences]
    return sorted(i for _, i in best)


def best_sentence(question: str, chunk) -> str | None:
    """The sentence of one chunk sharing the most question terms (None if it shares none)."""
    inp = {"question": question, "chunks": [chunk]}
    pick = _lexical_pick(inp, 1)
    return compress_sentences(inp)[pick[0] - 1][1] if pick else None


FAST_MAX_SENTENCES = 5


def fast_excerpts(question: str, chunks: Sequence, n: int = FAST_MAX_SENTENCES) -> list[dict]:
    """Fast mode: the n sentences sharing most question terms, in course order, verbatim.
    If none shares a term (the glossary matched instead), the opening sentences of the top chunk."""
    inp = {"question": question, "chunks": list(chunks)}
    sents = compress_sentences(inp)
    keep = _lexical_pick(inp, n) or list(range(1, min(3, len(sents)) + 1))
    return [{"chunk_id": sents[i - 1][0], "text": sents[i - 1][1]} for i in keep]


def _compress_expand(keep: list[int], inputs: Mapping, method: str) -> dict:
    sents = compress_sentences(inputs)
    keep = [i for i in keep if 1 <= i <= len(sents)]
    return {"keep": keep, "method": method,
            "sentences": [{"chunk_id": sents[i - 1][0], "text": sents[i - 1][1]} for i in keep]}


def _compress_finalize(data: dict, inputs: Mapping, ctx: TaskContext) -> tuple[dict, dict]:
    out = _compress_expand(data["keep"], inputs, "llm")
    if not out["keep"]:                     # the model kept nothing: trust retrieval over an empty pick
        return _compress_expand(_lexical_pick(inputs), inputs, "lexical"), {"note": "empty_keep"}
    return out, {}


def _compress_default(inputs: Mapping, ctx: TaskContext) -> dict:
    return _compress_expand(_lexical_pick(inputs), inputs, "lexical")


COMPRESS = TaskSpec(
    name="compress", prompt="compress", max_tokens=60, temperature=0.0, output_schema=Compression,
    required_inputs=("question", "chunks"), build=_compress_build, finalize_data=_compress_finalize,
    safe_default=_compress_default)


# ====================================================================== explain (grounded)
def _explain_prepare(inputs: Mapping, ctx: TaskContext) -> TaskContext:
    return ctx if ctx.language else replace(ctx, language=L.detect(str(inputs.get("question", "")))[0])


def _material(chunks: Sequence) -> str:
    if not chunks:
        return "(none)"
    return "\n\n".join(f"[{i}] {_chunk_parts(c)[1]}" for i, c in enumerate(chunks, 1))


def _explain_render(p: PromptTemplate, inputs: Mapping, ctx: TaskContext, chunks: Sequence,
                    history: Sequence) -> Messages:
    lang = ctx.language or "FRENCH"
    style = f" Style: {ctx.style}." if ctx.style else ""
    terms = terms_rule(ctx, str(inputs["question"]), *(_chunk_parts(c)[1] for c in chunks))
    msgs = [{"role": "system", "content": p.render("system", level=ctx.level or "not specified", style_rule=style,
                                                   language_rule=p.sections[f"lang.{lang}"], terms_rule=terms)}]
    msgs += [{"role": "assistant" if t.get("role") == "assistant" else "user", "content": str(t.get("content", ""))}
             for t in history]
    msgs.append({"role": "user", "content": p.render("user", material=_material(chunks),
                                                     question=str(inputs["question"]).strip())})
    return msgs


def _explain_finalize(text: str, p: PromptTemplate, inputs: Mapping, ctx: TaskContext) -> tuple[str, str | None]:
    t = text.strip()
    if not t:
        return _explain_unavailable(p, inputs, ctx), "explain_guard:empty"
    if degenerate(t):          # repetition loop (seen live: llama3.1:8b, 350 tokens of one phrase)
        kept = salvage_before_loop(t)
        return (kept, "explain_guard:loop_truncated") if kept else (t, "explain_guard:degenerate")
    lang = ctx.language or "FRENCH"
    # language policy: the model writes one language: MSA for ARABIC, French for FRENCH / DARIJA / ARABIZI
    # (their Tunisian lines are templates added later), English for ENGLISH
    if (L.arabic_ratio(t) >= 0.5) != (lang == "ARABIC"):
        return t, "explain_guard:language_drift"
    guard, detail = contamination_guard(t, "explain_guard", ctx, str(inputs["question"]),
                                        str(ctx.extra.get("material") or ""))
    return (t, guard, detail) if guard else (t, None)


def _explain_unavailable(p: PromptTemplate, inputs: Mapping, ctx: TaskContext) -> str:
    return p.sections[f"unavailable.{ctx.language or 'FRENCH'}"]


def _explain_not_found(p: PromptTemplate, inputs: Mapping, ctx: TaskContext) -> str:
    return p.sections[f"notfound.{ctx.language or 'FRENCH'}"]


CONFUSION_LEVELS = ("LOW", "MEDIUM", "HIGH")


def _explain_decorate(body: str, inputs: Mapping, ctx: TaskContext, opener: bool = True) -> tuple[str, dict]:
    """[opener template] + [model body] + [bank exercise]. The opener depends on language x confusion
    (data/templates.yaml); the exercise matches the top chunk's concept and the learner's difficulty."""
    t = ctx.extra.get("templates") or {}
    lang = ctx.language or "FRENCH"
    conf = str(ctx.extra.get("confusion") or "LOW").upper()
    conf = conf if conf in CONFUSION_LEVELS else "LOW"
    parts, data = [], {"body": body}
    if opener and t.get("opener"):
        data["opener"] = t["opener"][lang][conf]
        parts.append(data["opener"])
    parts.append(body)
    bank, concept = ctx.extra.get("exercise_bank"), ctx.extra.get("concept")
    if bank is not None and concept:
        exclude = tuple(inputs.get("exclude_exercises") or ())
        ex = bank.pick([concept], int(ctx.extra.get("difficulty") or 1), exclude, strict=True)
        if ex is not None:
            from engine.exercises import phrase_language
            data["exercise"] = ex.public(phrase_language(lang))
            parts.append(f"{t.get('practice', {}).get(lang, 'À toi :')} {data['exercise']['statement']}")
    return "\n\n".join(parts), data


EXPLAIN = TaskSpec(
    name="explain", prompt="explain", max_tokens=350, temperature=0.3, required_inputs=("question",),
    prepare=_explain_prepare, grounded=True, render_grounded=_explain_render, finalize_text=_explain_finalize,
    fallback_text=_explain_unavailable, not_found_text=_explain_not_found, decorate=_explain_decorate,
    check_grounding=True)


# ====================================================================== quiz (exercise bank, no LLM)
def _quiz_local(inputs: Mapping, ctx: TaskContext) -> dict:
    """Deterministic pick from the exercise bank. inputs: message (learner request), concept, difficulty,
    exclude (ids already served), diagnostic (3 exercises of increasing level)."""
    bank = ctx.extra.get("exercise_bank")
    if bank is None:
        raise ValueError("quiz needs an exercise bank (EXERCISES_PATH)")
    exclude = tuple(inputs.get("exclude") or ())
    if inputs.get("diagnostic"):
        chosen, concepts = bank.diagnostic(exclude), []
    else:
        concepts = [str(inputs["concept"])] if inputs.get("concept") else \
            bank.concepts_in(str(inputs.get("message") or ""), tuple(ctx.extra.get("expansion_terms") or ()))
        ex = bank.pick(concepts or None, int(inputs.get("difficulty") or 1), exclude)
        chosen = [ex] if ex else []
    from engine.exercises import phrase_language
    lang = phrase_language(ctx.language)
    numbered = len(chosen) > 1
    body = "\n".join((f"{i}) " if numbered else "") + e.render(lang) for i, e in enumerate(chosen, 1))
    return {"_text": body, "exercises": [e.public(lang) for e in chosen], "requested_concepts": concepts,
            "diagnostic": bool(inputs.get("diagnostic"))}


QUIZ = TaskSpec(name="quiz", prompt="quiz", max_tokens=40, temperature=0.2, local=_quiz_local)


# ====================================================================== grade (rules first, then compact JSON)
class Grading(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verdict: Literal["CORRECT", "INCORRECT", "PARTIAL"]
    mistake: str | None = None
    feedback: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("feedback", mode="before")
    @classmethod
    def _text(cls, v):
        return " ".join(str(v or "").split())[:300]


def _mistakes(inputs: Mapping) -> list[tuple[str, str]]:
    """(expression, feedback) pairs; accepts {expr, feedback} dicts or 2-item lists."""
    out = []
    for m in inputs.get("common_mistakes") or []:
        out.append((str(m["expr"]), str(m["feedback"])) if isinstance(m, Mapping) else (str(m[0]), str(m[1])))
    return out


def _grade_sympy(inputs: Mapping, ctx: TaskContext) -> dict | None:
    """Deterministic path (no request): when the answer and the solution both parse, decide by symbolic
    equivalence; a wrong answer equivalent to a known mistake gets that mistake's feedback, otherwise the
    generic template. Unparseable (a sentence, text solutions): None -> the compact LLM grader."""
    from engine.mathcheck import equivalent, parse_answer
    solution, attempt = parse_answer(str(inputs["solution"])), parse_answer(str(inputs["attempt"]))
    if solution is None or attempt is None:
        return None
    if equivalent(attempt, solution):
        return {"verdict": "CORRECT", "mistake": None, "feedback": "", "graded_by": "sympy"}
    for expr, feedback in _mistakes(inputs):
        m = parse_answer(expr)
        if m is not None and equivalent(attempt, m):
            return {"verdict": "INCORRECT", "mistake": expr, "feedback": feedback, "graded_by": "sympy"}
    generic = (ctx.extra.get("templates") or {}).get("grade_wrong", {}).get(ctx.language or "FRENCH", "")
    return {"verdict": "INCORRECT", "mistake": None, "feedback": generic, "graded_by": "sympy"}


def _grade_build(p: PromptTemplate, inputs: Mapping, ctx: TaskContext, limit: int = 0) -> Messages:
    lang = ctx.language or "FRENCH"
    mistakes = "; ".join(f"{a} ({d})" for a, d in _mistakes(inputs)) or "none listed"
    return [{"role": "system", "content": p.render("system", language_rule=p.sections[f"lang.{lang}"])},
            {"role": "user", "content": p.render("user", problem=str(inputs["problem"]), solution=str(inputs["solution"]),
                                                 mistakes=mistakes, attempt=str(inputs["attempt"]))}]


def _grade_finalize(data: dict, inputs: Mapping, ctx: TaskContext) -> tuple[dict, dict]:
    if reveals_answer(data.get("feedback") or "", str(inputs["solution"])):     # never leak the solution
        return {**data, "feedback": "", "graded_by": "llm"}, {"guard": "feedback_leaked_solution"}
    return {**data, "graded_by": "llm"}, {}


def _grade_default(inputs: Mapping, ctx: TaskContext) -> dict:
    return {"verdict": "UNKNOWN", "mistake": None, "feedback": "", "graded_by": "llm"}


GRADE = TaskSpec(
    name="grade", prompt="grade", max_tokens=120, temperature=0.0, output_schema=Grading,
    required_inputs=("problem", "solution", "attempt"), build=_grade_build, shortcut=_grade_sympy,
    finalize_data=_grade_finalize, safe_default=_grade_default)


def default_registry() -> Registry:
    return Registry([CLASSIFY, HINT, COMPRESS, EXPLAIN, QUIZ, GRADE])
