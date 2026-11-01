# CLAUDE.md — EdNova LLM Engine

## Current status (2026-11-01, engine v1.3 on branch `engine-v1.3`, NOT merged, waiting for review; v1.2 merged into `main` 2026-11-01)

> Resume point for new sessions. Update this at the end of every round; evidence lives in `DECISIONS.md` ("Engine v1 summary", "Engine v1.1", "Engine v1.2", "Engine v1.3").

00. **v1.3 (2026-11-01, `engine-v1.3`, from the team's contracts document; evidence in `DECISIONS.md` "Engine v1.3", report `reports/engine-v1.3.md`):** the integration contract is `docs/ENGINE_API.md` (generated from real engine output by `python -m scripts.gen_api_examples --doc`; tests fail on drift) and `engine.fake.FakeEngine` (same `run` signature, every state, no LLM). New: ids `learner_id/room_id/course_id/class_id` in every metrics record; fairness (`superseded`, `room_busy`); BACKGROUND reserve (`waiting_reserve`); `cache.invalidate(request_id)`; vision class (`MODEL_VISION`, images <= 1 MP, `TIMEOUT_VISION_S`); tasks `ocr_transcribe`, `check_claim`, `check_answer`, `prep_plan` and `Engine.generate_page`; explain `passage` / `board_text`; course graph `data/courses/analyse.yaml`; dynamic retrieval with visibility and professor boost; `rag/ingest.py` + `Engine.ingest_file` (OCR of scanned pages, 20 per file). **Not verified (no FelCloud slot on a Sunday):** live smoke incl. one OCR call, the check_claim accuracy (30-claim set), the mixed load on the real API, whether vision counts in the 20/min window; PyMuPDF not installable offline (PDF reader tested through a stand-in). Simulated mixed load passes (`reports/load-mixed-virtual*-v13.md`).
0. **v1.1 (2026-10-26, from the FelCloud probes; evidence in `DECISIONS.md` "Engine v1.1"):** sliding-window rate limiter (20 sends in any 60 s, own window for embeddings, phantom entries from the quota header), quota 429 delay = `retry-after` header only (BACKGROUND waits up to 65 s, INTERACTIVE up to 30 s, else degraded `quota_exhausted` with eta), concurrency 429 waits 2 s, output cap sent as `options.num_predict` + `max_tokens` (`OUTPUT_CAP_MODE`) with per-task caps and sentence-trim on cut output, estimator 2.3 chars/token, `CONTEXT_WINDOW` 4096 (prompt + cap <= 85%), `INPUT_TOKEN_BUDGET` 2800, silent input-truncation detector (degraded `input_truncated`, not cached). Both 429 shapes are CONFIRMED and have real fixtures. Verified on FelCloud 2026-10-26 (`reports/engine-v1.1-felcloud.md`): smoke within every cap, load n=60 at 40/min: 0 server 429s, 0 exceptions, 0 repeats, 0 input_truncated, densest 60 s window = 20 sends. Not exercised live: the output-cut path and a server 429. Review before merging into `main`.
1. **v1 frozen, then reopened:** Build steps 1–9 are done and the engine was frozen after the "last engine round"; v1.1, v1.2 and v1.3 were each started on an explicit go-ahead. No engine change without one. Next work belongs to the feature layer, which calls `engine.run`. Integration + technical guide: `docs/engine_guide/ednova_engine_guide.pdf` (LaTeX source alongside; `pdflatex` twice).
2. **Built:** config + env profiles, gatekeeper (client, limiter, retry, errors, priority queue, exact SQLite cache + coalescing + **negative cache**), simulator + profiles, router + mid-size breaker, `engine.run` with events, BM25 RAG + glossary, tasks classify/hint/explain/compress/grade/quiz, deterministic scaffolding (templates, bank exercises, sympy grading, formula correctness check, loop salvage, fast mode).
3. **Tests:** `pytest -q` → 765 passed (v1.3); `-m integration` → 5 passed (Ollama; not rerun for v1.2/v1.3, Ollama stays off on this laptop). Git repo initialised 2026-10-25 (branch `main`, one initial commit, no remote yet). Real keys live only in git-ignored `.env.real`; `recon_log.jsonl` (real platform data) is also unpublished, and `tests/test_errors.py` skips its replay when absent.
4. **Classify (llama3.2:3b, v4):** intent 87%, language 93%, manual set 4/4. **Retrieval:** hit@1 13/15, hit@3 14/15, off-topic 4/4, cross-language 6/8.
5. **Explain eval (91 answers):** 10 with new examples, wrong_math 1, ungrounded_formula 2; loop rate 2/50 (4%), salvage 1/2.
6. **Load v1 (`reports/load-engine-v1.md`, normal/saturated/quota_low/capacity):** 0 exceptions; compact 90/100/86/75%; cache 44/28/40/61%; quota refusals 7/7 waited the stated delay; 2.00 requests per cold turn; locally the binding limit is concurrency (~10.4 req/min).
7. **Negative cache:** an explain answer that failed a check (ungrounded/wrong_math/loop_truncated/degenerate) marks its key for `NEGATIVE_CACHE_TTL_S` (600 s); a repeat gets fast mode with 0 requests. The t011/t016 repeat is fixed in a replay test. The live rerun showed 0 repeats but didn't recreate the case (`reports/load-capacity-negcache.md`).
8. **Open, platform:** the quota-exhausted shape is CONFIRMED (v1.1); FelCloud has no embedding model, so there is no semantic cache; latency and capacity need a FelCloud-slot rerun (local mid-size is 8B on CPU).
9. **Open, quality:** Darija generation is weak (the answer body is in French); azi-1 is still labeled DIAGNOSTIC (the router override handles it); quotient and x(x+1) retrieval misses; the saturated-profile priority inversion is unexplained.
10. **Pending review:** drafts `derivee-definition.en.md`, `limite.fr.md` (`limite` needs bank exercises first) and `derivee-quotient-inverse-racine.fr.md` (new in v1.3: course text for the bank's quotient / inverse / racine carrée); the exercise bank is French-only. Legacy root `cache.sqlite` and `metrics*.jsonl` moved to `_legacy/`; the research PDF moved to `docs/research/`.

## What this repo is

The **LLM/platform layer** of EdNova, a GenAI peer learning companion built for the
IEEE WIE ACT 5.0 EdNova Technical Challenge (Phase 1 deadline: **10 Nov 2026**).

This repo is a standalone engine. It knows nothing about pedagogy. Product features
(knowledge tracing, explanation-style bandit, group sessions, UI) are OUT OF SCOPE
and will later call this engine as clients. Do not build them here.

The production target is **FelCloud**: an OpenAI-compatible API running open models on
**CPU only**. It is slow, quota-limited, and sometimes saturated. Handling that well
is most of what we are graded on (model orchestration 25%, handling constraints 25%,
resilience under load 15%). Every design decision should serve those three criteria.

## Today's setup: local Ollama

FelCloud is only reachable during our assigned slots. Outside them we develop against
**local Ollama** (OpenAI-compatible at `http://localhost:11434/v1`).

The engine must be provider-agnostic. Switching provider = changing `.env` only.

```env
# .env (never committed)
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama                 # Ollama ignores it; FelCloud needs the real key
MODEL_COMPACT=qwen2.5:1.5b         # stand-in for FelCloud compact class (<3B)
MODEL_MIDSIZE=qwen2.5:7b           # stand-in for FelCloud mid-size class (<=16B)
MODEL_EMBED=bge-m3                 # multilingual (Arabic/French/English)
RATE_LIMIT_PER_MIN=20
MAX_CONCURRENT=2
SIMULATE_CONSTRAINTS=true          # enable the fault/latency simulator (see below)
```

Setup:
```bash
ollama pull qwen2.5:1.5b && ollama pull qwen2.5:7b && ollama pull bge-m3
```

Ollama is too forgiving: no quota, no 503s, no Retry-After. So local dev MUST run
through the **constraint simulator** (`SIMULATE_CONSTRAINTS=true`), otherwise the
resilience code is never exercised.

## Hard rules

- **Never hard-code** base URL, keys, or model names. Everything comes from env via `config.py`.
- `.env` is in `.gitignore`. Ship `.env.example` with placeholders only. Never log or print keys.
- **No streaming.** FelCloud returns whole responses. Do not rely on `stream=True` anywhere.
- **Every LLM and embedding call goes through the gatekeeper.** No direct client calls from
  anywhere else. This is what makes the metrics trustworthy.
- **Only synthetic/public data** in tests, fixtures and prompts. No real personal data.
- Model **classes**, not model names, in all logic: code says `ModelClass.COMPACT`, config maps it to a name.
- Never send every request to mid-size "to be safe". Compact first; mid-size only by explicit rule.
- Prefer fewer, well-built requests over chains of small calls. One compact JSON call
  should do classification + language + confusion + complexity together.

## Platform contract (from the organizers' technical annex)

Implement against this contract; it is stable for the whole challenge.

| Situation | Service response | Required handling |
|---|---|---|
| Team quota exhausted | quota-exhausted error with a delay | wait the stated delay, then retry |
| Model class saturated | 503 + Retry-After | retry at intervals, or fall back to compact |
| Request beyond a ceiling (max tokens, etc.) | 400, message names the ceiling | fix the request (trim context / max_tokens), retry once |
| Normal | 200 + quota headers | read remaining quota, pace accordingly |

- Limits: 20 req/min (v1.1: sliding window, at most 20 sends in any rolling 60 s; FelCloud keeps the key closed for the full 60 s after the 21st), max 2 concurrent.
- Embeddings have a **separate** quota from generation.
- Quota-exhausted and concurrency refusals are CONFIRMED (2026-10-26): both 429 with `retry-after: 60`, told apart by the `rate_limit_type` header (`requests` / `max_parallel_requests`). Parsing stays in one function (`gatekeeper/errors.py`). Mark assumptions with `# ASSUMPTION(felcloud):`, observed facts with `# CONFIRMED(felcloud):`.
- Judges track: calls per model, compact/mid split, input/output tokens, repeated
  identical requests (cache indicator), quota refusals and how they were handled.

## Architecture

```
caller (future feature layer)
   │  engine.run(task, inputs, context, priority)
   ▼
engine/           task registry: task -> prompt template, model class, output schema
router/           compact vs mid-size decision, escalation, fallback policy
gatekeeper/
   cache.py       exact hash cache + semantic cache
   queue.py       priority queue: INTERACTIVE before BACKGROUND
   limiter.py     sliding window (rate) + asyncio.Semaphore (concurrency) — two separate mechanisms
   retry.py       stated-delay retry, backoff+jitter only when no delay given
   errors.py      maps provider responses to typed errors
   client.py      thin OpenAI-compatible async client (httpx or openai SDK)
rag/              embed, index, query with metadata filters, compress context
metrics/          one structured record per call (JSONL/SQLite)
simulator/        constraint simulator (latency, quota, 503, 400) — wraps the client
prompts/          versioned templates, one file per task
tests/
```

### Public interface

```python
result = await engine.run(
    task="hint",                       # classify | hint | explain | grade | compress
    inputs={...},                      # task-specific
    context={"language": "darija", "level": "beginner", "style": "analogy"},
    priority=Priority.INTERACTIVE,     # or BACKGROUND
)
# Result: text | parsed JSON, model_class_used, from_cache, degraded (bool),
#         degrade_reason, events (list of progress events), latency_ms, tokens_in, tokens_out
```

`events` are emitted as the call progresses (`"checking_cache"`, `"queued"`, `"retrieving"`,
`"generating"`, `"falling_back_to_compact"`...). The UI will use them for staged progress
since there is no token streaming.

### Task routing table (the documented, deliberate choice)

| Task | Default class | Escalation / fallback |
|---|---|---|
| classify (intent, language, confusion, complexity) — JSON | compact | none |
| hint / guiding question | compact | none |
| grade an answer attempt vs stored solution — JSON | compact | none |
| compress retrieved context | compact | none |
| explain (deep concept explanation) | mid-size | fallback to compact if mid-size saturated / quota low |
| summary of targeted text | compact | none |
| ocr_transcribe (photo of work → text, LaTeX) | vision | none (compact cannot read an image); shares the chat window |
| check_claim (is this true in the course) | compact, after sympy and the retrieval gate | none |
| check_answer (a worked solution, line by line) | none; compact only for a guiding question | none |
| prep_plan | none (rules) | none |

Escalation input comes from the caller (e.g. `context["escalate"]=True`) or from
classify's `complexity >= 4`. Keep the rule in one place: `router/policy.py`.

Fallback is always visible: `degraded=True` + a reason. Never fail silently.

### Classify output schema

```json
{
  "intent": "EXPLAIN | HINT | QUIZ | ANSWER_ATTEMPT | DIAGNOSTIC | OFF_TOPIC | CHITCHAT",
  "language": "DARIJA | ARABIZI | FRENCH | ARABIC | ENGLISH",
  "confusion": "LOW | MEDIUM | HIGH",
  "complexity": 1,
  "confidence": 0.0
}
```

Validate with pydantic. On invalid JSON: one repair retry on compact, then a safe default.
Small models break JSON often — use `response_format={"type":"json_object"}` where the
provider supports it, keep prompts short, give one example.

### Cache rules

- Exact cache key = hash(task, model_class, normalized prompt, language, level, style).
- Semantic cache ONLY for `explain` of a concept, never for specific problems
  ("derivative of x²" and "of x³" embed almost identically). Threshold configurable, default 0.94.
  Key must still match language + level band + style.
- Cache hits are logged as metrics (they are the cache indicator the judges watch).

### Constraint simulator

Wraps the client when `SIMULATE_CONSTRAINTS=true`. Configurable via env:
- extra latency per class (compact ~2–6 s, mid-size ~8–30 s)
- token bucket quota (the provider's own; the engine's limiter is a sliding window) → quota-exhausted error with a delay
- random 503 + Retry-After on mid-size (e.g. 15%)
- 400 when `max_tokens` or prompt length exceeds a configured ceiling
- quota headers on success

It must reproduce the platform contract table exactly, so code tested against it works on FelCloud.

## Stack

- Python 3.11+, `asyncio`, `httpx` (or `openai` SDK with custom base_url), `pydantic` v2,
  `tenacity` optional, `pytest` + `pytest-asyncio`.
- Storage: SQLite for cache + metrics (JSONL acceptable early).
- No LangChain inside the engine core. LangGraph may be used later by the feature layer, calling `engine.run`.
- Vector store: abstract behind `rag/store.py`. Local dev may use a simple in-process store
  (numpy cosine); production uses FelCloud's team collection. No third-party hosted AI services.

## Working conventions

- Small, testable modules. Each gatekeeper component has unit tests that run without any LLM.
- Integration tests run against Ollama + simulator; mark them `@pytest.mark.integration`.
- Log every routing decision with its reason (`DECISIONS.md` collects design choices for the report;
  append a line whenever a non-obvious choice is made).
- Prompts live in `prompts/*.md` with a version header; never inline long prompts in code.
- Commands:
  - `pytest -q` — unit tests
  - `pytest -q -m integration` — needs Ollama running
  - `python -m scripts.smoke` — one call per task, prints model used, latency, cache, degraded
  - `python -m scripts.load_test --n 50` — fires concurrent requests, prints metrics summary

## Existing work — read this first

This folder already contains work. Before writing any code:

1. Survey the repo (structure, dependencies, existing modules, tests, `.env*`, notebooks).
2. Write `AUDIT.md`: what exists, what maps to which part of the architecture above,
   what conflicts with the hard rules (hard-coded URLs/keys/model names, streaming,
   direct LLM calls bypassing a central layer, real personal data), and what is missing.
3. Propose a migration plan and **wait for approval** before restructuring or deleting anything.

Rules for existing code:
- Prefer adapting and refactoring over rewriting. Keep working code working.
- Keep the existing layout if it is reasonable; the module tree above is a target, not a mandate.
- Never delete files without approval; move obsolete ones to `_legacy/` instead.
- If a key or secret is found committed anywhere, stop and report it immediately
  (it must be rotated and purged from git history, not just deleted).

## Build order

Skip or adapt steps already covered by existing work (per `AUDIT.md`).

1. `config.py`, `.env.example`, `.gitignore`, metrics record format
2. `gatekeeper/client.py` against Ollama (chat + embeddings)
3. `simulator/` reproducing the contract table
4. `limiter.py`, `retry.py`, `errors.py` + unit tests
5. `queue.py`, `cache.py` + unit tests
6. `router/policy.py` + task registry + prompts for classify and hint
7. `engine.run` end-to-end with events; `scripts/smoke.py`
8. `rag/` (embed, index, query, compress) + explain task
9. `scripts/load_test.py` and a metrics summary (compact/mid split, cache hit rate, fallbacks)

## Out of scope (do not build)

Knowledge tracing, bandits, learner profiles, group sessions, frontend, auth, business logic.
If a task seems to need them, expose a parameter on `engine.run` instead.
