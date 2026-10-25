"""
Engine configuration. Everything provider-specific comes from env (.env by default,
or the file named by ENV_FILE). Logic refers to ModelClass, never to model names.

    from config import load_settings, ModelClass
    s = load_settings()
    s.model_for(ModelClass.COMPACT)      # -> first name in MODEL_COMPACT
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from dotenv import dotenv_values


class ModelClass(str, Enum):
    COMPACT = "compact"
    MIDSIZE = "midsize"


@dataclass(frozen=True)
class SimSettings:
    """Constraint simulator knobs (only used when SIMULATE_CONSTRAINTS=true)."""
    latency_compact_s: tuple[float, float] = (2.0, 6.0)
    latency_midsize_s: tuple[float, float] = (8.0, 30.0)
    latency_scale: float = 1.0                # 0 in unit tests
    rate_per_min: float = 20.0                # the simulated provider's bucket
    burst: int = 20
    embed_rate_per_min: float = 60.0
    embed_burst: int = 60
    max_concurrent: int = 2
    midsize_slots: int = 1                    # mid-size saturates beyond this many in flight (0 = off)
    saturation_prob: float = 0.15             # random saturation on mid-size
    saturation_dialect: str = "annex"         # "annex" (503) | "litellm" (429 all_deployments_in_cooldown)
    saturation_retry_after_s: tuple[int, int] = (5, 15)
    compact_saturation_prob: float = 0.0      # random saturation on compact (load-test "saturated" profile)
    transient_error_prob: float = 0.0         # random 500
    max_tokens_ceiling: int = 2048
    prompt_tokens_ceiling: int = 2048
    seed: int | None = None


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str = field(repr=False)
    models: Mapping[ModelClass, tuple[str, ...]]   # in-class preference order
    embed_model: str
    rate_per_min: float = 20.0
    rate_burst: int = 20
    embed_rate_per_min: float = 60.0
    embed_burst: int = 60
    max_concurrent: int = 2
    max_tokens: Mapping[ModelClass, int] = field(default_factory=lambda: {
        ModelClass.COMPACT: 300, ModelClass.MIDSIZE: 550})
    input_token_budget: int = 1800
    request_timeout_s: float = 85.0
    max_retry_wait_s: float = 30.0
    max_attempts: int = 4
    saturation_retries: int = 1               # mid-size: then the router falls back to compact
    compact_max_retries: int = 3              # compact: no fallback, so retry harder
    quota_low_threshold: int = 2              # local tokens below this -> mid-size tasks go compact
    json_mode_unsupported: tuple[str, ...] = ()   # models that break on response_format
    cache_path: str = "data/cache.sqlite"
    cache_ttl_s: float = 7 * 86400.0
    negative_cache_ttl_s: float = 600.0       # explain key that failed a check: repeats go to fast mode; 0 = off
    prompts_dir: str = "prompts"
    retriever: str = "bm25"                   # bm25 | embedding
    docs_dir: str = "data/docs"
    rag_index_path: str = "data/index/chunks.json"
    rag_top_k: int = 2                        # explain uses the 2 most relevant chunks after the gate
    rag_min_coverage: float = 0.4             # share of query terms (idf-weighted) a chunk must contain
    chunk_max_tokens: int = 300
    budget_safety_margin: float = 0.1         # assemble prompts within INPUT_TOKEN_BUDGET * (1 - margin)
    glossary_path: str = "data/glossary.yaml"  # learner terms -> course terms; empty = no expansion
    glossary_weight: float = 0.5              # glossary course terms count this much vs question terms
    terms_ar_path: str = "data/terms_ar.yaml"  # Arabic math terms injected into MSA answers
    exercises_path: str = "data/exercises/derivees.yaml"   # exercise bank for quiz / grade / stored hints
    templates_path: str = "data/templates.yaml"            # openers, practice line, generic feedback
    task_frequency_penalty: Mapping[str, float] = field(default_factory=lambda: {"explain": 0.3})
    task_max_tokens: Mapping[str, int] = field(default_factory=lambda: {"explain": 350})
    breaker_threshold: int = 3                # saturations within the window that open the breaker
    breaker_window_s: float = 60.0
    breaker_cooldown_s: float = 30.0          # open this long, then half-open (one probe)
    simulate: bool = False
    sim: SimSettings = field(default_factory=SimSettings)
    metrics_path: str = "metrics/calls.jsonl"

    def model_for(self, cls: ModelClass, index: int = 0) -> str:
        return self.models[cls][index]

    def class_of(self, model: str) -> ModelClass | None:
        for cls, names in self.models.items():
            if model in names:
                return cls
        return None

    def __repr__(self) -> str:   # never print the key
        return (f"Settings(base_url={self.base_url!r}, api_key='***', models={dict(self.models)}, "
                f"embed_model={self.embed_model!r}, simulate={self.simulate})")


class ConfigError(ValueError):
    pass


def _names(v: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in v.split(",") if x.strip())


def _bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


def _range(v: str) -> tuple[float, float]:
    lo, _, hi = v.partition(",")
    return float(lo), float(hi or lo)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """env=None reads ENV_FILE (default .env) overlaid by the process environment."""
    if env is None:
        env = {**dotenv_values(os.environ.get("ENV_FILE", ".env")), **os.environ}
    e = {k: v for k, v in env.items() if v is not None}

    def req(k: str) -> str:
        if not e.get(k):
            raise ConfigError(f"missing required setting {k} (see .env.example)")
        return e[k]

    def get(k: str, cast, default):
        return cast(e[k]) if e.get(k) else default

    d = SimSettings()
    sim = SimSettings(
        latency_compact_s=get("SIM_LATENCY_COMPACT_S", _range, d.latency_compact_s),
        latency_midsize_s=get("SIM_LATENCY_MIDSIZE_S", _range, d.latency_midsize_s),
        latency_scale=get("SIM_LATENCY_SCALE", float, d.latency_scale),
        rate_per_min=get("SIM_RATE_PER_MIN", float, get("RATE_LIMIT_PER_MIN", float, d.rate_per_min)),
        burst=get("SIM_BURST", int, get("RATE_BURST", int, d.burst)),
        embed_rate_per_min=get("SIM_EMBED_RATE_PER_MIN", float, d.embed_rate_per_min),
        embed_burst=get("SIM_EMBED_BURST", int, d.embed_burst),
        max_concurrent=get("SIM_MAX_CONCURRENT", int, get("MAX_CONCURRENT", int, d.max_concurrent)),
        midsize_slots=get("SIM_MIDSIZE_SLOTS", int, d.midsize_slots),
        saturation_prob=get("SIM_SATURATION_PROB", float, d.saturation_prob),
        saturation_dialect=get("SIM_SATURATION_DIALECT", str, d.saturation_dialect),
        compact_saturation_prob=get("SIM_COMPACT_SATURATION_PROB", float, d.compact_saturation_prob),
        transient_error_prob=get("SIM_TRANSIENT_ERROR_PROB", float, d.transient_error_prob),
        max_tokens_ceiling=get("SIM_MAX_TOKENS_CEILING", int, d.max_tokens_ceiling),
        prompt_tokens_ceiling=get("SIM_PROMPT_TOKENS_CEILING", int, d.prompt_tokens_ceiling),
        seed=get("SIM_SEED", int, None),
    )
    if e.get("RETRIEVER") and e["RETRIEVER"].strip().lower() not in ("bm25", "embedding"):
        raise ConfigError("RETRIEVER must be 'bm25' or 'embedding'")
    if sim.saturation_dialect not in ("annex", "litellm"):
        raise ConfigError("SIM_SATURATION_DIALECT must be 'annex' or 'litellm'")

    defaults = Settings(base_url="", api_key="", models={}, embed_model="")
    return Settings(
        base_url=req("LLM_BASE_URL").rstrip("/"),
        api_key=req("LLM_API_KEY"),
        models={ModelClass.COMPACT: _names(req("MODEL_COMPACT")),
                ModelClass.MIDSIZE: _names(req("MODEL_MIDSIZE"))},
        embed_model=e.get("MODEL_EMBED", ""),
        rate_per_min=get("RATE_LIMIT_PER_MIN", float, defaults.rate_per_min),
        rate_burst=get("RATE_BURST", int, defaults.rate_burst),
        embed_rate_per_min=get("EMBED_RATE_LIMIT_PER_MIN", float, defaults.embed_rate_per_min),
        embed_burst=get("EMBED_BURST", int, defaults.embed_burst),
        max_concurrent=get("MAX_CONCURRENT", int, defaults.max_concurrent),
        max_tokens={ModelClass.COMPACT: get("MAX_TOKENS_COMPACT", int, 300),
                    ModelClass.MIDSIZE: get("MAX_TOKENS_MIDSIZE", int, 550)},
        input_token_budget=get("INPUT_TOKEN_BUDGET", int, defaults.input_token_budget),
        request_timeout_s=get("REQUEST_TIMEOUT_S", float, defaults.request_timeout_s),
        max_retry_wait_s=get("MAX_RETRY_WAIT_S", float, defaults.max_retry_wait_s),
        max_attempts=get("MAX_ATTEMPTS", int, defaults.max_attempts),
        saturation_retries=get("SATURATION_RETRIES", int, defaults.saturation_retries),
        compact_max_retries=get("COMPACT_MAX_RETRIES", int, defaults.compact_max_retries),
        quota_low_threshold=get("QUOTA_LOW_THRESHOLD", int, defaults.quota_low_threshold),
        json_mode_unsupported=get("MODELS_WITHOUT_JSON_MODE", _names, ()),
        cache_path=e.get("CACHE_PATH") or defaults.cache_path,
        cache_ttl_s=get("CACHE_TTL_DAYS", float, defaults.cache_ttl_s / 86400) * 86400,
        negative_cache_ttl_s=get("NEGATIVE_CACHE_TTL_S", float, defaults.negative_cache_ttl_s),
        prompts_dir=e.get("PROMPTS_DIR") or defaults.prompts_dir,
        retriever=get("RETRIEVER", lambda v: v.strip().lower(), defaults.retriever),
        docs_dir=e.get("DOCS_DIR") or defaults.docs_dir,
        rag_index_path=e.get("RAG_INDEX_PATH") or defaults.rag_index_path,
        rag_top_k=get("RAG_TOP_K", int, defaults.rag_top_k),
        rag_min_coverage=get("RAG_MIN_COVERAGE", float, defaults.rag_min_coverage),
        chunk_max_tokens=get("CHUNK_MAX_TOKENS", int, defaults.chunk_max_tokens),
        budget_safety_margin=get("BUDGET_SAFETY_MARGIN", float, defaults.budget_safety_margin),
        glossary_path=e.get("GLOSSARY_PATH", defaults.glossary_path).strip(),
        glossary_weight=get("GLOSSARY_WEIGHT", float, defaults.glossary_weight),
        terms_ar_path=e.get("TERMS_AR_PATH", defaults.terms_ar_path).strip(),
        exercises_path=e.get("EXERCISES_PATH", defaults.exercises_path).strip(),
        templates_path=e.get("TEMPLATES_PATH", defaults.templates_path).strip(),
        task_frequency_penalty={"explain": get("EXPLAIN_FREQUENCY_PENALTY", float, 0.3)},
        task_max_tokens={"explain": get("EXPLAIN_MAX_TOKENS", int, 350)},
        breaker_threshold=get("BREAKER_THRESHOLD", int, defaults.breaker_threshold),
        breaker_window_s=get("BREAKER_WINDOW_S", float, defaults.breaker_window_s),
        breaker_cooldown_s=get("BREAKER_COOLDOWN_S", float, defaults.breaker_cooldown_s),
        simulate=get("SIMULATE_CONSTRAINTS", _bool, False),
        sim=sim,
        metrics_path=e.get("METRICS_PATH") or defaults.metrics_path,
    )
