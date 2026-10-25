# AUDIT.md: existing work vs. the CLAUDE.md engine architecture

Audited 2026-10-24. Scope: every file in `/home/ibrahim/wie` except the `myenv/` virtualenv.

## 0. Security findings (read first)

| Finding | Severity | Status |
|---|---|---|
| `.env.real` contains a **real FelCloud API key** (`sk-h…`, value not reproduced here). | High *if shared* | The folder is **not a git repo** and there is **no `.gitignore`**, so the key has not been committed anywhere. The danger is a future `git init && git add .`, which would commit it. Fix: add `.gitignore` before anything else. Rotate the key only if this folder was ever zipped, synced, or shared. |
| Key leakage into artefacts | none | I grepped `recon_log.jsonl`, `recon_summary.json`, `metrics*.jsonl`, all `.py` and `mock_config.json` for the key value and found 0 hits. `recon.py` logs response headers only and never request headers. |
| `sk-` pattern hits in `CLAUDE.md` and `mock_config.json` | false positive | They come from "ta**sk-sp**ecific" and "a**sk-org**anizers". |
| Personal data | none | Prompts, few-shot examples and test strings are synthetic. Session ids are random. |

## 1. Inventory

| File | What it is | Lines |
|---|---|---|
| `gateway.py` | Central async LLM gateway built on httpx. It combines a sliding-window rate limiter (18/min), `Semaphore(2)`, an exact-hash SQLite cache, a per-model circuit breaker (cooldown), tier fallback (mid→compact, code→mid), quota-vs-saturation handling via Retry-After, an input-size guard, and JSONL metrics. | ~290 |
| `mock_server.py` + `mock_config.json` | FastAPI mock of the platform. It has a token bucket (separate gen and embed buckets), the concurrency limit, 400 on the max_tokens ceiling, 503 on mid saturation, the LiteLLM "cooldown" 429, quota headers, live fault injection (`/admin/fault`), `/admin/stats` (calls per model, compact share, repeated identical, refusals), and optional proxying to Ollama for real text. | ~200 |
| `test_gateway.py` | Script that runs 8 scenarios against the running mock and prints PASS/FAIL. It is **not pytest** and needs the mock server up. | ~80 |
| `recon.py` | Paced probe of the real FelCloud (nonce per prompt, honours Retry-After, never logs the key). Produced `recon_log.jsonl` and `recon_summary.json`. | ~250 |
| `recon_log.jsonl`, `recon_summary.json` | **Real platform observations from 22 Oct**, which are very valuable (see §3). | data |
| `triage.py` | One compact JSON call that classifies intent, subject, concept, difficulty and needs_reasoning, plus a rule shortcut for greetings and `choose_tier()`, which is the routing rule. | ~100 |
| `lang.py` | Rule-based language detection (darija, arabic, french, english) with no LLM call, plus `reply_plan()`. It has a built-in self-test. | ~90 |
| `prompts.py` | Hint and answer prompts in FR/EN/AR (inline Python dicts) and a language-drift guard for hints. | ~140 |
| `app.py` | FastAPI product app: `/ask` streams NDJSON events to a browser, serves an inline HTML chat page, and holds Darija UI templates. | ~170 |
| `cache.sqlite`, `metrics.jsonl`, `metrics_test.jsonl`, `__pycache__/` | Runtime artefacts. | — |
| `myenv/` | Python **3.10** venv containing httpx, pydantic 2, fastapi, uvicorn and python-dotenv. **No pytest.** | — |
| `.env`, `.env.mock`, `.env.real` | `.env` points to the mock (`LLM_API_KEY=mock`). `.env.real` points to FelCloud with the real key. Only `LLM_BASE_URL` and `LLM_API_KEY` are set; there are no model or limit vars. | — |

Missing entirely: `.gitignore`, `.env.example`, `config.py`, requirements/pyproject, pytest setup, `DECISIONS.md`, package structure, priority queue, semantic cache, embeddings client, RAG, task registry, `engine.run`, smoke/load scripts.

## 2. Mapping to the target architecture

| Target | Existing source | Reuse verdict |
|---|---|---|
| `config.py` | constants at top of `gateway.py` plus `os.environ` | **New.** Move the constants into env-backed config. |
| `metrics/` record | `Gateway._log` (ts, tier, model, status, lat, in, out, cached, etype) | **Extend.** It is missing task, request id, priority, attempt number, retry/fallback reason, degraded, wait time, and the quota-remaining header. |
| `gatekeeper/client.py` | `Gateway._try_model` HTTP part, `recon.call` | **Extract.** Make it a thin chat+embed client. The current code has no embeddings call. |
| `simulator/` | `mock_server.py` | **Port the logic.** Keep its bucket, faults, ceilings, headers and stats, but turn it into an in-process httpx transport that wraps the real client (see plan). |
| `gatekeeper/limiter.py` | `SlidingWindow` + `Semaphore` | **Replace the window with a token bucket** (the contract says continuous refill + burst). Keep the semaphore and the "remaining=0 ⇒ block" header feedback. |
| `gatekeeper/errors.py` | scattered `if resp.status_code ==` in `_try_model`, `_retry_after`, `_read_headers` | **Extract** into one function. The quota-vs-cooldown distinction is already right. |
| `gatekeeper/retry.py` | loop in `_try_model` | **Rewrite** from the existing behaviour (see gaps below). |
| `gatekeeper/cache.py` | `Cache` (SQLite, sha256, whitespace + lowercase normalisation) | **Adapt** (step 5). The key must add task, language, level and style. Lowercasing is risky for code and math (`X` vs `x`). |
| `gatekeeper/queue.py` | none | New (step 5). |
| `router/policy.py` | `triage.choose_tier`, `FALLBACK`, circuit breaker in `Gateway.call` | **Port** (step 6). The circuit breaker is a good idea and worth keeping. |
| `engine/` task registry, classify | `triage.py` (+ `lang.py`) | **Adapt** (step 6). The schema differs from CLAUDE.md's classify schema: it has no language/confusion/complexity/confidence fields and uses different intent labels. |
| `prompts/*.md` | `prompts.py`, `triage.SYSTEM` | **Move** to versioned `.md` files (step 6). Keep the drift guard as post-processing. |
| `scripts/` | `recon.py` | Keep as `scripts/recon.py` (see conflicts). |
| Feature layer / UI | `app.py`, Darija templates, `lang.reply_plan` | **Out of scope** for this repo. Move to `_legacy/` once `engine.run` replaces it (step 7). |

## 3. What recon learned about the real FelCloud (it changes some contract assumptions)

These are facts from `recon_log.jsonl` (104×200, 2×404, 1×429) and must feed `errors.py` / `config.py` as `# ASSUMPTION(felcloud):` with evidence:

1. **It is a LiteLLM proxy** (`x-litellm-version: 1.103.0`, behind Caddy). Server timeout is `x-litellm-timeout: 90` s.
2. **Saturation was observed as `429`, not `503`**: `{"error":{"type":"all_deployments_in_cooldown","code":"429"}}` + `Retry-After: 5`. The annex says 503. `errors.py` must map **both** to `ModelSaturated`, and tell it apart from a quota 429 by `error.type` and message.
3. **Quota headers on 200** are `x-ratelimit-api_key-remaining-requests`, `x-ratelimit-api_key-limit-requests`, `x-ratelimit-api_key-remaining-max_parallel_requests`, `x-ratelimit-api_key-limit-max_parallel_requests`, and `x-litellm-key-rpm-limit: 20`. The **quota-exhausted** error itself was never triggered, so its shape is still unconfirmed.
4. **No max_tokens ceiling was found up to 8192** on any model. But **input is silently truncated near 2048 tokens**: a ~6k-token prompt returned **200** with `prompt_tokens: 2050`. A 400 will never warn us, so the client must enforce an input budget itself.
5. **Models**: compact class is `llama3.2-3b` and `qwen2.5-3b`. Mid class is `llama3.1-8b`, `qwen2.5-coder-7b` and `mistral-7b`. `deepseek-ocr` is unusable (65 s median for a short reply).
   JSON mode (`response_format`) parsed on llama3.2-3b, qwen2.5-3b and llama3.1-8b. It **failed** on mistral-7b, qwen2.5-coder-7b and deepseek-ocr.
6. Latency: short replies take 0.3–3.4 s on compact and 6–8 s on mid. Output costs ~0.025 s/token on compact and ~0.046 s/token on mid.
7. **Embeddings: no embedding model is listed.** `/embeddings` with a chat model returned 404. The embedding model name is an open question for the organisers.
8. Recon notes that no model reliably *labels* Tunisian Darija, which is why `lang.py` is rule-based.

## 4. Conflicts with the hard rules

| Rule | Violation | Where |
|---|---|---|
| Never hard-code model names | `TIERS` (6 model names), `BLOCKLIST`; `mock_config.json` (acceptable for a simulator config) | `gateway.py:27-33` |
| Never hard-code limits | `RPM=18`, `MAX_PARALLEL=2`, `MAX_TOKENS`, `INPUT_TOKEN_BUDGET`, `CLIENT_TIMEOUT`, `MAX_WAIT`, header name `x-ratelimit-api_key-remaining-requests` | `gateway.py:34-40, 239` |
| Model classes, not names | there are 3 "tiers" incl. a `code` class; CLAUDE.md defines 2 classes | `gateway.py`, `triage.choose_tier` |
| Every call through the gatekeeper | `recon.py` calls the API directly (deliberately: it is a probe). `mock_server.generate` calls Ollama directly (it *is* the provider stand-in, so this is fine). | `recon.py:43`, `mock_server.py:151` |
| Prompts in `prompts/*.md`, versioned | prompts inline in code | `prompts.py`, `triage.py:7` |
| Simulator wraps the client, `SIMULATE_CONSTRAINTS` env | the mock is a separate HTTP server, switched by pointing `LLM_BASE_URL` at it; there is no flag | `mock_server.py` |
| 400 ⇒ fix request and retry once | it raises immediately | `gateway.py:225` |
| Backoff + jitter when no delay given | uses a fixed `sleep(2)` | `gateway.py:209, 228` |
| Out of scope: UI / product | HTML page, Darija templates, NDJSON endpoint | `app.py` |
| No streaming | **No violation.** No `stream=True` anywhere. `app.py` streams *its own* NDJSON to the browser, not LLM tokens. | — |
| Python 3.11+ | the only interpreter on the machine is **3.10.12** | `myenv/` |

Behavioural bugs and gaps in `gateway.py` worth knowing (they will not be carried over):
- A quota 429 with `Retry-After > MAX_WAIT` returns "not ok", so the caller **falls through to the next model and tier**. Quota is per key, so every fallback burns another refused request.
- `Cache.get` truthiness check plus cache-before-validation: a cached empty or invalid JSON answer is served forever.
- There is no separate embeddings quota/limiter and no priority. Metrics have no task or request id, so "repeated identical requests" cannot be attributed.

## 5. What is good and should be kept

- The **quota 429 vs cooldown 429** distinction and the **per-model circuit breaker**.
- The **input-budget guard**, which is justified by the silent 2048 truncation.
- `recon.py`'s nonce discipline, so probes do not inflate the repeated-request indicator.
- The mock's fault modes and `/admin/stats` view (good for the demo video).
- The rule-based greeting shortcut and language detection (zero-cost requests).
- Using the drift guard as validation.

---

## 6. Migration plan (proposed; nothing has been moved or deleted yet)

Principle: **additive first**. Steps 1–4 only *create* new modules. `gateway.py`, `app.py` and the rest keep working untouched until `engine.run` (step 7) can replace them; then they move to `_legacy/`.

### Steps 1–4 (what I'll do on approval)

**Step 1: foundation**
- `.gitignore`: `.env`, `.env.*` except `.env.example`, `myenv/`, `__pycache__/`, `*.sqlite`, `*.jsonl` runtime logs, `recon_log.jsonl`. This is done first, because of the key.
- `.env.example`: placeholders only. Proposed vars: `LLM_BASE_URL`, `LLM_API_KEY`, `MODEL_COMPACT`, `MODEL_MIDSIZE`, `MODEL_EMBED`, `RATE_LIMIT_PER_MIN`, `RATE_BURST`, `EMBED_RATE_LIMIT_PER_MIN`, `MAX_CONCURRENT`, `MAX_TOKENS_COMPACT`, `MAX_TOKENS_MIDSIZE`, `INPUT_TOKEN_BUDGET`, `REQUEST_TIMEOUT_S`, `MAX_RETRY_WAIT_S`, `SIMULATE_CONSTRAINTS`, `SIM_*`, `METRICS_PATH`.
- `config.py`: a frozen dataclass loaded from env (python-dotenv is already installed, so there is no new dependency), plus `ModelClass` enum (`COMPACT`, `MIDSIZE`). `MODEL_COMPACT` may be a comma list, read as an in-class preference order. This keeps the existing llama3.2-3b → qwen2.5-3b failover without naming models in code. `__repr__` masks the key.
- `metrics/record.py` + `metrics/sink.py`: a pydantic `CallRecord` (ts, request_id, task, priority, model_class, model, status, error_kind, attempt, latency_ms, wait_ms, tokens_in, tokens_out, from_cache, degraded, degrade_reason, quota_remaining) and an append-only JSONL sink.
- `requirements.txt` / `pyproject.toml`, `pytest.ini` (with an `integration` marker), and `pip install pytest pytest-asyncio` into `myenv`.
- `DECISIONS.md`, seeded with the choices in this plan.

**Step 2: `gatekeeper/client.py`**: an async httpx client with `chat(model_class, messages, max_tokens, json_mode, …)` and `embed(texts)`. It returns a raw `httpx.Response` plus timing and contains **no retry logic**. It accepts an injectable `transport`, which is how the simulator plugs in. Integration test against Ollama.

**Step 3: `simulator/`**: an `httpx.AsyncBaseTransport` that wraps the real transport when `SIMULATE_CONSTRAINTS=true`. The gatekeeper therefore parses genuine `httpx.Response` objects, the same code path as FelCloud. Logic is ported from `mock_server.py`: gen and embed token buckets, per-class latency, 400 ceilings (max_tokens and prompt length), saturation, and quota headers.
- Two **saturation dialects**: `annex` (503 + Retry-After) and `litellm` (429 `all_deployments_in_cooldown`, as observed). Both must pass the same tests.
- Quota-exhausted shape is a configurable `# ASSUMPTION(felcloud)`.
- Seeded RNG for deterministic tests.
- `mock_server.py` stays as-is for now. Later it can become a 20-line FastAPI wrapper around the same simulator for the demo.

**Step 4: `gatekeeper/errors.py`, `limiter.py`, `retry.py` + unit tests (no LLM)**
- `errors.py`: one function `classify_response(resp) -> Ok | QuotaExhausted(delay) | ModelSaturated(retry_after) | CeilingExceeded(kind, limit) | ConcurrencyLimited | Transient | Fatal` and `parse_quota_headers(resp)`. All header and type names sit here with `ASSUMPTION(felcloud)` comments.
- `limiter.py`: a continuous-refill `TokenBucket` (rate + burst; separate gen and embed instances) and a separate `Semaphore`. Header feedback: `remaining == 0` drains the bucket; a stated quota delay blocks it until then.
- `retry.py`: quota waits the **stated delay**, then retries the same model, but if the delay exceeds `MAX_RETRY_WAIT_S` it raises with the delay instead of falling through to other models. Saturated retries at intervals up to N, then raises `ModelSaturated` (the router decides the fallback in step 6). Ceiling calls a `fix_request` hook once, which trims `max_tokens` and context. Transient and network errors use exponential backoff + full jitter. Fatal never retries. Every attempt emits a metrics record and an event. The clock and sleep are injectable so tests run instantly.
- Tests: bucket refill/burst, the semaphore cap of 2 under 10 concurrent tasks, each contract-table row × both dialects, the quota delay being honoured, and no model-hopping on quota.

### Later steps (for context, not done now)
- Step 5: queue and cache, adapting `Cache`.
- Step 6: router, task registry and prompts. This ports `choose_tier`, the circuit breaker, `triage` and `prompts.py`. `lang.py` becomes a rule-based pre-pass whose result can override the model's language guess.
- Step 7: `engine.run`. Then move `gateway.py`, `app.py`, `triage.py`, `prompts.py`, `test_gateway.py` → `_legacy/`, and `recon.py` → `scripts/recon.py`.

### Decisions I need from you
1. **Local stand-in models.** Ollama already has `llama3.2:3b`, `qwen2.5:3b` and `llama3.1:8b`. These are the *same families FelCloud actually serves*. I propose using them instead of CLAUDE.md's `qwen2.5:1.5b`/`qwen2.5:7b`. `bge-m3` is not pulled yet; it is needed at step 8, not for steps 1–4.
2. **Python 3.10 vs 3.11+.** Only 3.10 is installed. I propose writing 3.10-compatible code now (no `TaskGroup`/`asyncio.timeout`), which costs nothing. The alternative is installing 3.11 (deadsnakes/uv) and rebuilding the venv.
3. **Drop the `code` class?** CLAUDE.md has two classes. I propose dropping `code` from the engine; `qwen2.5-coder-7b` is also bad at JSON. If the feature layer wants it later, it can be a third `ModelClass`.
4. **`.env` edit.** Your current `.env` only has the base URL and key. Can I add the new variables to it, with the mock values unchanged? I will not touch `.env.real`, except to tell you which variables to add.
5. **`recon.py` exemption.** I propose keeping it as a documented exemption from "every call through the gatekeeper", because it is a platform probe that must see raw behaviour.
