# DECISIONS.md

Design choices for the report. One line per non-obvious choice; evidence in brackets.

## Platform facts (from recon of the real FelCloud, 2026-10-22, `recon_log.jsonl`)

- FelCloud is a LiteLLM 1.103 proxy behind Caddy, server timeout 90 s → `REQUEST_TIMEOUT_S=85`.
- Saturation was observed as **429 `all_deployments_in_cooldown`** + `Retry-After: 5`, not the annex's 503. `errors.py` maps both to `ModelSaturated` and tells it apart from a quota 429 by error type/message.
- Quota headers on 200 are `x-ratelimit-api_key-{remaining,limit}-{requests,max_parallel_requests}`. All header names live in `gatekeeper/errors.py`.
- The quota-exhausted error was never triggered, so its shape is an `ASSUMPTION(felcloud)`: any 429 that is not cooldown/concurrency is quota; delay from `Retry-After`, else a reset header, else "retry in N s" in the message. (superseded in v1.1: the quota-exhausted shape is CONFIRMED, a 429 with `retry-after: 60` and `rate_limit_type: requests`, see "Engine v1.1")
- No max_tokens ceiling found up to 8192, but **input is silently truncated near 2048 tokens** (6k-token prompt → 200 with `prompt_tokens: 2050`). The client therefore enforces `INPUT_TOKEN_BUDGET=1800` itself before sending; a 400 would never warn us. (superseded in v1.1: the start of the prompt is dropped above about 4,096 tokens, not 2048, and `INPUT_TOKEN_BUDGET` is 2800, see "Engine v1.1")
- JSON mode works on llama3.2-3b, qwen2.5-3b and llama3.1-8b; it fails on mistral-7b and qwen2.5-coder-7b. Those two are not used.
- No embedding model is listed on FelCloud (`/embeddings` with a chat model → 404). `MODEL_EMBED` on FelCloud is an open question for the organizers.

## Configuration

- Two model classes only (`COMPACT`, `MIDSIZE`). The old `code` tier is dropped from the engine; it can return as a third `ModelClass` if the feature layer needs it. (superseded in v1.3: three classes, `COMPACT`, `MIDSIZE` and `VISION`, see "Engine v1.3")
- `MODEL_COMPACT` / `MODEL_MIDSIZE` accept a comma list = in-class preference order, so failover inside a class needs no model names in code.
- Local stand-ins are the same families FelCloud serves (`llama3.2:3b`, `qwen2.5:3b`, `llama3.1:8b`) rather than `qwen2.5:1.5b`/`7b`, so prompt behaviour transfers.
- Code targets Python 3.10 (the only interpreter installed): no `TaskGroup`, no `asyncio.timeout`.
- `.env` profiles: `.env.ollama` (Ollama + in-process simulator), `.env` (standalone `mock_server.py`, simulator off), `.env.real` (FelCloud). Pick with `ENV_FILE`.

## Gatekeeper

- The simulator is an `httpx` transport wrapping the real one, not a separate server: the gatekeeper parses genuine `httpx.Response` objects on the same code path as FelCloud.
- The simulator supports both saturation dialects (`annex` 503, `litellm` 429), and the tests run both.
- Rate (token bucket, continuous refill + burst) and concurrency (semaphore) are separate mechanisms. Chat and embeddings have separate buckets; the concurrency cap is assumed shared per key. (superseded in v1.1: the limiter is a sliding window, at most 20 sends in any rolling 60 s; the token bucket stays only inside the simulator, see "Engine v1.1")
- The limiter waits for a rate token *before* taking a concurrency slot and spends the token only once it has the slot. A quota wait never holds a slot, and requests queued on the semaphore cannot burst past the bucket.
- Server quota headers can only *lower* the local token count, never raise it.
- A token deficit below 1e-9 counts as zero: float refill can stop at 0.999999999999, and the matching sub-ulp sleep never advances the clock (found as an infinite loop by the contract test).
- Quota exhausted → wait the stated delay on the **same** model; never hop models (the quota is per key, so another model would be refused too and burn a request). If the delay > `MAX_RETRY_WAIT_S`, raise it to the caller.
- Saturation → retry `SATURATION_RETRIES` times at the stated interval, then raise so the router (step 6) decides the compact fallback; the retry layer never changes model class itself.
- Ceiling 400 → fix the request once (trim `max_tokens` to the named limit, or trim the longest non-system message), retry once.
- Exponential backoff with full jitter only when no delay is stated (transient 5xx, network errors).
- One `CallRecord` per provider attempt, with `request_hash` of the full body, so repeated identical requests are countable.
- `recon.py` is a documented exemption from "every call through the gatekeeper": it is a probe that must see raw platform behaviour.

## Never raise to the caller (fix before step 5)

- `engine.run` never raises a provider error. Every give-up maps to a degraded Result: saturation and concurrency limits → `service_busy`, long quota waits → `quota_exhausted`, exhausted transient errors → `service_unavailable` (these three are retryable: they get `eta_seconds` and a `queued` event); an unfixable ceiling → `request_too_large`, and 4xx → `provider_error`. Only caller bugs (unknown task, missing input, bad priority) raise, as `ValueError`, before any request is sent.
- Compact saturation retries up to `COMPACT_MAX_RETRIES` (default 3), honouring Retry-After each time, because compact has nothing to fall back to. Mid-size keeps `SATURATION_RETRIES=1` so the fallback to compact happens quickly.
- `eta_seconds` = the last stated Retry-After + (queue depth + 1) × the moving average of how long requests hold a slot / `MAX_CONCURRENT`. It is an estimate for the UI, not a promise.
- When several degrade reasons stack up, the terminal one wins (`service_busy` over `midsize_saturated`); the full chain goes into `notes.degrade_reasons`.
- A JSON task that ends degraded still returns its safe-default `data` (classify → `EXPLAIN`, confidence 0, language from rules); a text task returns its language's template. Callers never need a None check on the shape.
- The recorded FelCloud 429 is now `CONFIRMED(felcloud)` in `errors.py`. Caveat: it was triggered by deployment failures (two 404s on `/embeddings`), not by load; that load produces the same shape is inferred. The quota header names were also observed (on every 200), so they are marked confirmed too.

## Queue and cache (step 5)

- The priority queue picks the waiter at *grant* time: one dispatcher per kind (chat / embed) waits for limiter capacity, then pops the best waiter. Choosing when a request is enqueued would let a background request that is already waiting beat a newer interactive one.
- A retry keeps its original queue sequence number (stored on the call context), so a request that waited out a quota delay is not pushed to the back of its priority class.
- Unknown priority labels rank as BACKGROUND, so they can never jump the queue.
- If a waiter is cancelled after being granted a slot, it releases the slot and refunds the rate token (the request was never sent).
- The cache key also includes the prompt-file version, so editing a prompt invalidates its old answers without a manual flush.
- The cache never lowercases (`X` ≠ `x` in math and code). It normalizes whitespace and Unicode NFC only.
- The cache looks up under the router's *preferred* class. A request downgraded proactively for low quota can still be served a cached mid-size answer, which is better than a fresh compact one.
- `put()` refuses degraded, fallback and safe-default entries itself (`NotCacheable`), so the rule cannot be bypassed by a caller mistake.
- A cache hit writes a `kind="chat", outcome="cache_hit", from_cache=true` record (the judges' cache indicator) plus the usual `kind="run"` record.

## Router, tasks, prompts (step 6)

- The routing table lives only in `router/policy.py`. The task registry reads a task's model class from it instead of storing its own copy.
- Escalation (`escalate=True` or `complexity >= 4`) keeps explain on mid-size when quota is low; without it, low quota (`< QUOTA_LOW_THRESHOLD` local tokens) sends explain to compact proactively, `degraded=True, reason=quota_low`. Saturation fallback happens either way.
- Every routing decision, initial or runtime fallback, is written as a `kind="route"` metrics record whose `detail` field holds the reason, and is logged via `logging`.
- Explain is in the routing table but is not registered until step 8. The fallback tests register a test-only `explain` spec.
- Classify accepts sloppy but unambiguous JSON without a repair call: ```json fences, lowercase enums, `"answer attempt"`, a numeric string for complexity, confidence clamped to [0, 1]. This is normalization, not repair, and costs no request.
- The repair call is always compact at temperature 0. It sends the original messages plus the bad output plus the validation error, because small models fix their own output better when they see it.
- The classify language is reconciled with rules: a script mismatch (Arabic letters but the model says FRENCH, or the reverse) or a strong dialect marker overrides the model. Eval: raw qwen2.5:1.5b 47% → 93% final; raw llama3.2:3b 80% → 93% final.
- Bare greetings and thanks (exact match only) are classified by rules: 0 requests. The legacy `len <= 3` shortcut was dropped because it would label "2x?" as chitchat.
- Hint prompts use English instructions plus one example shot *in the reply language* (from `prompts/hint.md`), so there is one file per task, not one per language.
- Hint guards, applied in order: a repetition loop (some 3-word sequence occurs 3+ times) → template; more than 40 words → keep the leading complete question if there is one, else template; the stored solution leaked (verbatim, or as `= value`) → template; wrong language → template. Every guarded hint is degraded and not cached. The repetition guard was added after the first smoke run cached a llama3.2:3b Arabizi loop.

## Engine (step 7)

- Events carry `t_ms` since the start of the call and are also pushed live through an optional `on_event` callback (for the UI's staged progress).
- Legacy prototype files moved to `_legacy/` (nothing deleted); `recon.py` moved to `scripts/recon.py`. `mock_server.py` stays at the root as the standalone demo mock.
- Compact model choice: llama3.2:3b stays first in `MODEL_COMPACT`. On the 15-message eval it gets 87% intent vs 67% for qwen2.5:1.5b, at about twice the latency (2.6 s vs 1.3 s p50 on local CPU).

## Retrieval and context budget (step 8)

- **The token estimate was off by 1.8×; fixed.** Measured on `data/docs` with llama3.2:3b and qwen2.5:3b: 3.5 chars/token for English prose, 3.0 for French, 2.2–2.3 for math-heavy French and for Arabic. The step-4 guard assumed 4 chars/token, so a prompt it counted as 1800 tokens could really be ~3200, far past FelCloud's silent ~2048 truncation. `estimate_tokens` now uses 2.0 chars/token (below the worst case, so it is an upper bound), and budget assembly and the retry guard share it. The simulator counts at the measured average (2.5) to stay closer to the real provider. In smoke, the estimate was 555 for a prompt with 333 real tokens: safe, at the cost of some unused room. (superseded in v1.1: the estimator is 2.3 chars/token, the worst measured case, and FelCloud truncates near 4,096 tokens, not 2048, see "Engine v1.1")
- Prompts are assembled within `INPUT_TOKEN_BUDGET × (1 − BUDGET_SAFETY_MARGIN)` = 1800 × 0.9 = 1620 estimated tokens. Priority: system + question (an error if they alone do not fit) → chunks in rank order (an oversized chunk is skipped and a smaller lower-ranked one may still fit) → history newest-first; once a turn does not fit, it and every older turn are dropped, so there are no gaps. Everything dropped goes into `notes.dropped` and is logged. (superseded in v1.1: `INPUT_TOKEN_BUDGET` is 2800, so 2800 × 0.9 = 2520 estimated tokens, see "Engine v1.1")
- **BM25, pure Python, as the default** (no new dependency, no model, no quota). Recon: FelCloud lists no embedding model. `EmbeddingRetriever` is a small working stub (cosine over gatekeeper embeddings, so it uses the separate embeddings quota), selected with `RETRIEVER=embedding`.
- Tokenization: NFKC first (`x²` → `x2`); Arabic diacritics and tatweel stripped and alef/yaa/taa marbuta normalized *before* splitting into words (a diacritic is not a word character, so stripping later would cut words in half); lowercasing and accent folding only on Latin-script tokens (learners often type `derivee`); light plural strip on Latin words (no digits, so Arabizi is untouched); light Arabic prefix strip (`ال وال بال كال فال لل`, plus a bare `و` only if 4+ letters remain); FR/EN/Arabizi/Arabic+Darija stopwords.
- **Relevance gate = query coverage, not BM25 score.** On a one-topic corpus the topic word has low IDF: "C'est quoi une dérivée ?" scores 1.29 while off-topic "the function in my code is slow" scores 2.43, so no score threshold works. Coverage is the IDF-weighted share of the question's terms found in the chunk; words absent from the corpus count at the *median* IDF (at the max IDF, plain words like "know"/"using" sank a valid question). Measured: on-topic ≥ 0.49, off-topic ≤ 0.28 → `RAG_MIN_COVERAGE=0.4`, applied to every hit, not just the top one.
- If nothing passes the gate, explain returns a localized "not in the course material" template: no LLM request, not degraded, not cached (the index may grow).
- Docs are FR / EN / AR. Arabizi learners retrieve French chunks through shared French math words (this is how Tunisian students actually code-switch); explain still answers in the learner's language.
- Chunks follow `##` sections (authored at about 250–300 tokens); a longer section is packed by paragraph, then by sentence. `chunk_id = <file stem>#<section slug>` stays stable across re-indexing, so eval sets and cache keys stay valid. The store is JSON with an atomic write; BM25 statistics are rebuilt in memory at load time.
- **Compress is extractive:** the model sees numbered sentences and returns `{"keep": [numbers]}`, so compression cannot invent facts, and its output costs ~10 tokens. If the JSON is invalid after one repair, or the model keeps nothing, a lexical pick (overlap with question terms, no LLM) is used instead.
- **Compress never drops a relevant chunk entirely.** Any retrieved chunk left with no kept sentence gets its single best sentence back (by overlap with the question), and the budget is enforced again afterwards. Found in the real-model smoke run: compact kept 5 sentences from 2 of 3 chunks, and mid-size then invented a wrong explanation of the missing "constant → 0" rule.
- Compress only runs when the top chunks do not fit (`assemble(...).all_chunks_fit` is false); it is a nested `engine.run("compress")` (own request id, own metrics record, own cache entry), and its tokens are added to the explain Result.
- The explain cache key = system prompt + question + history + the *IDs* of the retrieved chunks, not the compressed text. The lookup therefore happens *before* compress, and a cache hit spends no request at all, compress included.
- Explain drift guard: only a script mismatch counts (Arabic vs Latin letters), because French math words inside Darija are normal. On mismatch the text is kept but marked degraded and not cached.
- Retrieval eval (BM25): hit@1 13/15, hit@3 14/15, off-topic 4/4 rejected. Known miss: "Pourquoi la dérivée de x(x+1) n'est pas 1 × 1 ?" keeps only "dérivée" after stopwords and 1-character math tokens are dropped; the question never says "produit". Left in the set as an honest limit of lexical retrieval.

## Fast mode, circuit breaker, keywords (before step 9)

- **Grounded fast mode.** When explain loses mid-size (runtime saturation, `quota_low` or `breaker_open`), compact does not write the explanation: the fallback smoke run showed compact misquoting the material ("(2ax + h)/h"). Instead the learner gets up to 5 course sentences *verbatim* (formulas intact), chosen lexically (overlap with question terms, in course order; no LLM), introduced by one compact-written line. `degraded=True`, `mode="fast_extractive"`, `followup_available=True`, never cached. Selection is lexical rather than an LLM compress call because a fallback is exactly the moment to spend fewer requests.
- The framing line is a nicety: max 40 tokens, `saturation_retries=0` (no Retry-After waits), one line ≤ 30 words in the right script, else the localized template. The fast-mode *result* is never cached, but the framing line (a function of question + language only) is, so repeated questions under pressure cost no extra request.
- With both classes saturated, explain now still returns course excerpts (template framing) instead of `service_busy`; hint and classify keep the `service_busy` path.
- **Circuit breaker per model class** (only classes that are some task's default *and* have a fallback: today mid-size). It counts saturation responses only (503 / LiteLLM cooldown 429), per *attempt*, reported by the retry layer through an observer hook; quota refusals are per key and say nothing about class load. Sliding window, `BREAKER_THRESHOLD=3` in `BREAKER_WINDOW_S=60` → open for `BREAKER_COOLDOWN_S=30` → half-open lets exactly one probe through → close on success, reopen on saturation (a reopen is not counted as a new trip). Results arriving while open (requests sent before the trip) are ignored.
- Escalation does not bypass an open breaker: escalating cannot help a class that is down.
- A half-open probe that never reaches mid-size (cache hit, nothing retrieved, fast path) releases its slot in `engine.run`'s `finally`; a probe that never reports at all expires after one cooldown, so the breaker can never get stuck half-open.
- **Keywords.** Classify returns 0–5 `keywords` in `COURSE_LANGUAGE` (default FRENCH). Lenient coercion (a comma string, duplicates, more than 5, non-strings), so keywords never trigger a repair call. BM25 query weights: question terms 1.0, keyword-only terms `KEYWORD_WEIGHT=0.5`. Callers pass `inputs.keywords` to explain; the engine does not run classify itself (one call per turn, not two).
- **The coverage gate with keywords is script-aware.** When keywords are given, question terms written in a script the chunk does not contain at all (Darija in Arabic letters vs a French chunk) leave the coverage denominator, since they cannot match by construction and the keywords are their translation. Same-script misses still count, so "comment installer python sur windows" + keyword "fonction" stays rejected while "كيفاش نلقى معادلة المماس؟" + ["équation", "tangente"] finds the French tangent chunk. Without that rule, the untranslatable question terms drowned out the keywords.
- Retrieval eval, both modes, same set: plain hit@1 13/15, hit@3 14/15, off-topic 4/4; with llama3.2:3b keywords: identical. Every question in this set has a same-language doc, so keywords have nothing to rescue here; their value is cross-language (covered by unit tests). Observed: llama3.2:3b wrote keywords for Arabic questions in Arabic despite the instruction, and copied "puissance" from the prompt's single example into most French questions.
- `EXPLAIN_MAX_TOKENS=350` (`Settings.task_max_tokens`), still capped by the class limit.
- **Identical concurrent requests are coalesced.** After a cache miss, a request whose cache key is already being generated waits for the leader and is then served from the cache (`waiting_identical_request` → `cache_hit`). If the leader's result was degraded (and so not cached), the follower makes its own call. Repeated questions arriving together are exactly what the "repeated identical requests" indicator penalizes.
- **Keywords cost classify some accuracy.** With prompt v2 (keywords added), llama3.2:3b intent accuracy went 87% → 80% on the 15-message eval set (azi-1 "chnowa ya3ni limite? ma fhemt chay" flipped EXPLAIN → DIAGNOSTIC; deterministic at temperature 0), and latency went 2.6 s → 3.6 s p50 from the extra output tokens. Kept as a measured trade-off, not tuned against the eval set; the alternative would be a separate keyword call, which CLAUDE.md's "one compact call" rule argues against.

## Load test and metrics (step 9)

- `scripts/load_test.py` runs real Ollama behind the simulator; each profile gets a fresh in-memory cache, a fresh breaker and `metrics/load_<profile>.jsonl`. Poisson arrivals (30/min), task mix classify 0.4 / hint 0.3 / explain 0.3, priority mix 0.8 / 0.2, questions drawn with replacement from small synthetic pools (repeats are realistic and exercise the cache). Explain inputs carry keywords as classify would produce them.
- Profiles change only the *simulated provider*, never the client's config: `saturated` = mid-size 90% and compact 20% saturation; `quota_low` = the provider really allows 10/min with burst 4 while the client was told 20/min, so the client must learn from refusals and quota headers.
- The report counts a cache hit as no provider request; "repeated identical provider requests" = successful provider calls whose request hash had already succeeded earlier in the log. Latency percentiles exclude cache hits (they are ~0 ms and would flatter p50); nested compress runs are reported separately from caller-visible runs.
- Results (n=50 each): 0 exceptions reached the caller in any profile; 0 repeated identical provider requests (cache + coalescing); compact share of ok calls 87% / 91% / 95%; cache hit rate 30% / 36% / 28%. `quota_low`: all 18 quota refusals were handled by waiting the stated delay, and 16 explain decisions switched to fast mode on low quota headers.
- **Caveat on "normal".** Locally, mid-size (llama3.1:8b on CPU) takes ~20–60 s per explain against 6–8 s on FelCloud (recon), and the simulator allows one mid-size request in flight. So even the normal profile saturated mid-size often enough to trip the breaker twice (mid-size ok 3/18 attempts). Read normal-profile mid-size numbers as a stress case, not as a FelCloud prediction; rerun on FelCloud during a slot.
- Priority works under contention: interactive p95 vs background p95 is 60.6 vs 67.2 s (normal), 44.7 vs 24.2 s (saturated, small n), 34.2 vs 81.6 s (quota_low). The saturated-profile inversion is not explained yet: only 7 uncached background runs (4 explain), against 25 interactive (13 explain), so p95 there rests on a handful of samples; it needs a larger n before drawing a conclusion.

## Glossary instead of LLM keywords

- **LLM keywords removed.** Classify is back to the exact v1 prompt and schema (`version: 1`, same text, so cache entries stay valid); the eval is back to 87% intent / 93% language at 2.6 s p50. Keywords cost 7 points of intent accuracy and 1 s, and added nothing on the same-language set.
- **Deterministic glossary** (`data/glossary.yaml`, PyYAML added to requirements): 60 French course concepts for the derivatives topic, 327 learner variants across Darija (Arabic letters), Arabizi (incl. construct forms such as `mo3adlet`, `mochtaqet`), MSA and English. No LLM call, no quota, reproducible. Loading rejects a variant that maps to two different concepts, and variants that tokenize to nothing.
- Matching runs on the BM25 tokens themselves (same normalization: folded accents, stripped Arabic article and diacritics, ة→ه), longest variant first, so `العدد المشتق` → "nombre dérivé" wins over `مشتق` → "dérivée".
- English "car" is not a variant of "voiture": in French "car" means "because", and the glossary is applied to French questions too.
- Course terms are scored at `GLOSSARY_WEIGHT=0.5`, so a question's own words still dominate (English questions stay on the English doc).
- **The coverage gate counts concepts, not tokens.** Each glossary match becomes one unit (the learner's word plus its course terms), satisfied if either is in the chunk. Remaining question terms are units of their own, and the script-aware rule still drops untranslated words in a script the chunk lacks once the glossary matched something. This is stricter than the keyword version: an expansion can only stand in for words the learner actually wrote, so it cannot turn an off-topic question on-topic ("the derivative of my business revenue strategy" stays rejected).
- Cross-language eval (8 Darija/Arabizi questions answerable only from French docs, `tests/eval/retrieval_crosslang.jsonl`): plain hit@1 0/8, hit@3 0/8 → glossary hit@1 6/8, hit@3 7/8. The original 19-question set is unchanged under the glossary (13/15, 14/15, 4/4). Caveat: I wrote both the glossary and these questions, and added the construct forms after one of my probe questions used one, so 6/8 is an optimistic upper bound until real learner questions are collected.
- Misses: xdar-3 ("جداء", product) ranks the "product errors" chunk above "product and quotient", a near miss; xdar-4 ("خارج", quotient) fails the gate because the quotient chunk contains "quotient" but neither "dérivée" nor "calculer".

## Capacity and console

- `turn_id` (from `context["turn_id"]`) is copied to every metrics record of a call, including nested compress, repair and framing calls and route decisions, so requests can be attributed to learner turns.
- Capacity profile = turn-based load (classify, then hint or explain by intent, as the feature layer would) at an offered ~15 engine calls/min (7.5 turns/min). Requests per turn count *every* attempt, refused or not, because that is what the per-key quota is charged.
- The learner estimate depends on an assumption (how often an active learner sends a message), so the report shows 1 turn per 30 s / 60 s / 120 s instead of one number. It also shows the binding limit: the 20 req/min quota, or `MAX_CONCURRENT × 60 / mean provider latency` when calls are slow (local CPU), whichever is lower.
- `scripts/chat.py`: I found no earlier spec in the repo, CLAUDE.md or memory, so this design is an assumption: a plain line is one learner turn (classify → hint or explain by intent; chitchat/off-topic stop), with live progress events printed as the UI would show staged progress; slash commands to call a task directly, set language/level/priority, and show session stats. Everything goes through `engine.run`.
- **Capacity result** (40 turns, 360 s, 0 exceptions): 1.88 engine calls per turn (chitchat stops after classify). **2.08 provider requests per cold turn** (no cache hit, the realistic case for distinct questions); 0.72 observed overall, because the run reused 14 messages and hit the cache 55% of the time, so that figure is optimistic and not used for planning.
- **Estimate, one key at 20 req/min, cold turns:** about 19 active learners at one message per 2 minutes, 10 at one per minute, 5 at one per 30 s. This holds only when the quota is the binding limit. Locally it is not: mean provider latency was 10.6 s (8B on CPU), so the 2-concurrent cap allows only 2 × 60 / 10.6 = 11.3 req/min, giving 11 / 5 / 3 learners. On FelCloud (recon: compact 0.3–3.4 s, mid-size 6–8 s) the concurrency ceiling should sit above 20 req/min, so the quota-bound figures are the planning numbers. Confirm with a capacity run during a FelCloud slot.
- The console run showed the glossary path working live (Darija question → French tangent chunk → mid-size answer with the right formula), but mid-size wrote Moroccan-flavoured Darija ("ديال" where Tunisian says "متاع") and a typo. Generating Darija remains the weakest output; a reply policy (e.g. French explanation + Darija framing, as the legacy app did) is a product decision for the feature layer.

## Manual-testing fixes (chat.py)

- **Language policy (decision).** FRENCH / ENGLISH → answer in that language. ARABIC (MSA) → MSA with correct math terms; the relevant `data/terms_ar.yaml` entries (those whose French term occurs in the question or retrieved chunks, at most 12) are injected into the prompt. DARIJA / ARABIZI → the explanation body in French (the course language) with formulas verbatim, one short Tunisian Darija line at the start and the practice item in Darija, in Arabic letters for DARIJA and Latin for ARABIZI. The prompt gives Tunisian example lines (متاع, شنوة, كيفاش, باهي; never Moroccan ديال / واش) and says to use the French math term when unsure, never an invented dialect word. Hints keep replying in the learner's language (one short question) under the same Tunisian-forms rule.
- Explain's drift guard follows the policy: only MSA answers are expected in Arabic script; Darija/Arabizi answers are expected to have a French (Latin-script) body.
- **One-liners in Darija/Arabizi always use the fixed Tunisian templates.** In the live replay compact wrote "كيفاش هتستعلم كل شئ عن ..." (Egyptian, unfinished), which passed the script check; an earlier quiz intro invented a task ("polynôme premier"). Accordingly, the quiz intro defaults to the template in every language; a model-written intro is opt-in (`intro: "llm"`), cached, ≤ 12 words, tutoiement.
- **Contamination guard.** In every Arabic-script line of an explain or hint answer, a Latin word is flagged unless it is notation (1–2 letters, sin/cos/ln/lim…), a French course term from the glossary, or a word of the sources the answer is based on (retrieved chunks, problem, attempt). Flagged results are degraded (`*_guard:contamination`), not cached, logged as warnings, and list the words in `notes.contamination`. The text is kept: it is usually still usable.
- **Partial result.** After a cache miss on explain (and before any mid-size call), a `partial_result` event carries up to 3 course sentences verbatim (the same lexical pick as fast mode, 0 requests). The console prints them as "course excerpt", then the full answer.
- **Repetition loops in explain are unusable output.** A `degenerate` (or empty) explain answer is replaced by fast mode (course excerpts verbatim), degraded and not cached. Found live: llama3.1:8b produced 350 tokens of "La mélange des concepts de…", which had been served *and cached*; the poisoned entry was deleted.
- Explain v3: the closing item must be a concrete practice calculation on a function written in the answer, never a definition question, never unseen symbols. No praise before the learner has done anything; MEDIUM/HIGH confusion → brief acknowledgement and never "you understood" (taken from classify's `confusion`, passed by the caller). `RAG_TOP_K=2`: the two most relevant chunks after the gate.
- **Classify v3:** few-shot examples for DIAGNOSTIC ("teste mon niveau", "chouf niveau mte3i"), QUIZ ("donne moi un exercice", "a3tini exercice"), HINT and CHITCHAT; complexity 1–2 for single-concept "what is / I don't understand", 4–5 only for multi-step. DIAGNOSTIC keeps covering "find my mistake" (the original eval labels it that way) and now explicitly excludes "I don't understand X". The HINT example is Arabizi with "indice": with only the "a3tini exercice" QUIZ example, "…a3tini indice" flipped to QUIZ. EXPLAIN examples come last (small models weight the most recent examples).
- Model confidence is capped at 0.9 in the rules layer (`notes.model_confidence` keeps the raw value); 1.0 is reserved for rule matches.
- Classify eval, llama3.2:3b. Original 15: intent 87% → **93%**, both-correct 87% → 87%. Manual inputs (4): 75% → **100%**, complexity in band 1/2 → 2/2, max confidence 1.00 → 0.90. Caveat: "teste mon niveau" and "donne moi un exercice" are also few-shot examples, as specified, so their manual-set score measures recall of the examples, not generalization; the original set is the honest number.
- **Exercise bank** (`data/exercises/derivees.yaml`, 15 exercises, 11 concepts, levels 1–3, 3 graded hints and common mistakes each). `quiz` picks deterministically (concept from the message, via glossary terms; the requested difficulty or the nearest; first id not yet served). Diagnostic = levels 1/2/3 on three different concepts, with a template intro, so 0 requests. `grade` matches the stored solution and the common mistakes first (normalized: x³ = x^3 = x3, "f'(x) =" / "y =" prefixes), so frequent answers are judged without a request; otherwise compact JSON with a solution-leak guard on the feedback. Bank exercises use their stored hints in order, with 0 requests.
- Console: while an exercise is open, a line with math content is graded directly; any other line is classified first (ANSWER_ATTEMPT → graded, HINT → stored hint, QUIZ → new exercise). Grading "donne moi un exercice" as an answer was the literal reading of "the next message is graded", and the replay showed why it fails.
- `simulator/profiles.py` holds the named profiles (`normal`, `calm`, `saturated`, `quota_low`, `capacity`), shared by load_test (unchanged values) and the console's `/sim`. `calm` = no saturation, no simulated latency (only the model's real latency); it is the console default. `off` = pass-through.
- Retrieval: "معناها / معنى / يعني / تعني" are stopwords, consistent with Arabizi `ya3ni`. Manual set: "ma fhemtch el tangente" hit@1; "شنوة معناها المشتقة؟" only hit@3, because the definition chunk says "العدد المشتق" while the learner wrote "المشتقة", which matches the "usual derivatives" chunk. With top-2, the definition chunk does not reach the prompt.
- **Open issue:** for "شنوة معناها المشتقة؟" llama3.1:8b looped in 3 of 3 runs (now caught and served extractively). Hypothesis, not yet tested: the Darija policy asks for a French body while both retrieved chunks are Arabic. Candidates: prefer French chunks for Darija learners via the glossary, or let the body follow the material's language.
- **Open issue:** the 8B copies the prompt's Tunisian example lines verbatim (opening line = the example practice question; closing item = "Calcule f'(2) pour f(x) = x³." in French). A deterministic alternative for the practice item: end explanations with a bank exercise on the same concept.

## Deterministic scaffolding (principle: narrow what the LLM writes)

- **Explain = [opener template] + [LLM body] + [bank exercise].** The opener comes from `data/templates.yaml` (learner language × confusion LOW/MEDIUM/HIGH; Tunisian forms for Darija/Arabizi, which say the explanation follows in French). The model writes only the body, in ONE language: French for FRENCH/DARIJA/ARABIZI, MSA for ARABIC (with `terms_ar.yaml`), English for ENGLISH. The explain prompt v4 has no opener, tone rules, Darija lines or practice question, and asks for ≤ 120 words. The practice item is the first unserved bank exercise on the top chunk's concept at the learner's difficulty (`context.difficulty`, default 1, nearest level), rendered with a template line ("À toi :", "Jarreb enti :"). Concept exhausted → no practice line rather than another concept's exercise (`pick(strict=True)`).
- The cache stores the model's body only; opener and exercise are rebuilt on every hit. One cached body serves every confusion level, and a learner never gets an exercise they already did from a cached answer. Fast mode gets the same practice line (its framing line is the opener).
- Every doc section carries `<!-- concept: … -->` with the exercise bank's concept ids. The chunker stores it on the chunk (index format v2: a stale index is refused, not misread), and a test checks every chunk's concept has at least one exercise.
- Bank statements are French (the course language), so English and Arabic learners get a French exercise after a localized "Your turn:" / "حاول بنفسك:". Translating the bank is open.
- **Retrieval by learner language** (`rag.retriever.language_plan`): DARIJA/ARABIZI → French chunks only (the glossary carries the terms over); ARABIC → Arabic chunks, else French; FRENCH/ENGLISH → all. `eval_retrieval` gained a `policy` mode that applies it (dar-1 got an `expected_french`; its original label is kept for the other modes).
- **One glossary concept for the derivative.** `العدد المشتق`, `المشتقة`, `مشتق`, mochtaqa… all map to "dérivée". A first version expanded them to "dérivée" + "nombre dérivé", which pulled the definition section to the top of every "mochtaqa" question (cross-language hit@1 6/8 → 4/8). Instead, French light stemming now strips final e's (dérivée, dérivé, dérivées → `deriv`), so "dérivée" also reaches the "nombre dérivé" sections without adding "nombre" to every query. Result: original 13/15 in all modes, cross-language 6/8, manual set 2/2 hit@1 under the policy.
- Side effect, kept: "tangente" and English "tangent" now share a stem, so English tangent questions match French tangent text directly.
- **Loop prevention:** explain requests carry `frequency_penalty` (`EXPLAIN_FREQUENCY_PENALTY=0.3`). A provider that rejects it with a 400 naming the parameter gets the same request again without it, and never again (logged once). Ollama accepted it. The degenerate-output guard stays. In the replay, "شنوة معناها المشتقة؟", which looped in 3 of 3 runs before, answered normally in 19 s; the change of policy and chunks (French body from French chunks) and the penalty arrived together, so which one fixed it is not isolated.
- **Deterministic grading (sympy).** Answer and solution are parsed (x^2, x², 2x, x(x+3), √x, ×, ·, unicode minus, French decimal commas; a left-hand side is dropped only if it is f'(…), f(…) or y) and compared by `simplify(a − b) == 0`. A wrong answer equivalent to a stored mistake gets that mistake's feedback; otherwise the generic template. 0 requests. Unparseable answers or solutions (sentences; d14 and d15 are text answers) → the compact LLM grader with the leak guard. `graded_by: sympy|llm` is on the result and on the run metrics record.
- **Parser security:** sympy's parser evaluates Python, so learner text is reduced to a whitelist (digits, x, + − * / ( ) . and `sqrt`, ≤ 80 characters) before parsing; anything else is refused and goes to the LLM grader. Tests cover code-injection strings.
- Bug found while building it: "maximum 5 atteint en x = 2" parsed as "2" (everything after the "=" sign), so "2" would have been graded correct. Only recognized left-hand sides are dropped now.
- **Intents:** DIAGNOSTIC = level testing only; "where is my mistake?" = HINT (eval items ar-2 and en-1 relabeled, marked `relabeled` in the file). Classify prompt v4: the DIAGNOSTIC definition excludes "I don't understand X" and "where is my mistake", plus one French HINT few-shot. With the new labels, the original set went 80% (v3) → 87% (v4); manual set 4/4. Iterating showed small models are order-sensitive: the new example placed after the Arabizi HINT example broke azi-1/azi-2; placed first, it did not. azi-1 ("chnowa ya3ni limite? ma fhemt chay") stays DIAGNOSTIC in every variant tried.

## Routing override, multilingual exercises, grounding

- **Intent → task now lives in the routing layer** (`router.policy.Router.follow_up`), not in the console. Override: confusion HIGH + DIAGNOSTIC or QUIZ → explain, because a learner who says they are lost is taught first, not tested. Each follow-up is written as a `kind="route"` record (`task="follow_up"`, outcome `routed` / `overridden`, the reason in `detail`). The console and the load test's turn flow both call it. This is the fix for azi-1 ("chnowa ya3ni limite? ma fhemt chay"), which the classifier labels DIAGNOSTIC with HIGH confusion in every prompt version: the label stays wrong, the learner now gets an explanation.
- **Exercises are data + phrase templates.** Each bank exercise is `template_key` + `expression` (+ `a`, `condition`); `data/templates.yaml` holds each phrase in fr / en / ar (MSA, with the `terms_ar.yaml` vocabulary: مشتقة، مماس، فاصلة، قيمة عظمى / دنيا). An exercise that fits no template sets `statement: {fr, en, ar}`. Language mapping: ENGLISH → en, ARABIC → ar, FRENCH / DARIJA / ARABIZI → fr (the course language). The French renders are identical to the old statements; hints, solutions and mistake feedback stay French.
- **Retrieval for ENGLISH:** English chunks first, French when no English chunk passes the gate (the glossary maps English terms to French ones; "tangent" and "tangente" share a stem). Unchanged eval numbers; "How do I write the equation of the tangent line?" now finds the French tangent section instead of nothing.
- **Formula grounding check on explain.** Every "A = B" in the model's text (the math run on each side of each "="; works inside French, English and Arabic sentences) must match a source equation: the retrieved chunks, the appended exercise, or the learner's question. Matching needs the same left side (normalized; one may end with the other) and an equivalent right side (sympy over x a h t n k u v y, or exact text when it does not parse). Comparing left sides is what makes "f'(x) = 1/x" ungrounded when the course only says "f(x) = 1/x". A miss → `degraded`, reason `ungrounded_formula`, spans in `notes.ungrounded`, a warning in the log, not cached. The text is still shown.
- The grounding check flags correct NEW computations too (f'(3) = 6 for a value the course does not use). That is the rule as specified, and it means the check also measures how often the model goes beyond the course. Expressions with f(…) on the right side can only match as exact text.
- The general parser (`mathcheck.parse_expression`) uses the same whitelist protection as grading (nothing else reaches sympy's parser) and `split_symbols`, so `2ah` = 2·a·h.
- **How often the grounding check fires.** Explain eval (`scripts/eval_explain.py`, 25 on-topic questions across the three retrieval sets, real models): 9/25 = 36% of answers. By kind: 3 invented examples or formulas (en-3 `f(x) = x² - 4x + 4`, xdar-3's own product example, xazi-4 `f(t) = t^2`), 1 course formula from a section that retrieval missed (fr-6), and 5 with only intermediate steps or variable assignments (`u = 1`, `n = 2`, `2x − 4 = 0`). 2/25 more hit the repetition guard despite frequency_penalty. The first run reported 9/25 too, but 4 of those were extractor errors ("C'est" and "(racine" read as math, "²".isdigit() being True, an inequality kept in a side); those are fixed and tested, and answers are now saved in `reports/eval_explain.json` so the check can be recomputed offline. Replay: it fired on "What is a derivative?" both from the cache (an entry stored before the rule; dropped) and fresh (the model's own limit definition, which the English sections do not contain).
- **Formulas without "=" are checked too:** a math run that applies a function or a bracket and uses an operator ("the limit of [f(x + h) - f(x)]/h") must appear in the sources as text or as an equivalent expression. Found because the first replay's answer slipped through: its limit formula had no "=".
- **Cache hits run the deterministic checks of today, not of the day the entry was written.** Explain hits run the grounding check (a failing entry is degraded and deleted); classify hits re-run its rules layer (`rules_on_hit`: language check, confidence cap, confusion floor; all idempotent).
- **Confusion floor (rules layer, beyond the spec).** Explicit non-understanding markers ("ma fhemt", "ما فهمتش", "لم أفهم", "je comprends pas / rien", "I don't understand") raise confusion to HIGH; the model's value is kept in `notes.model_confusion`. Reason: live, llama3.2:3b labelled azi-1 DIAGNOSTIC with MEDIUM confusion, so the HIGH-only override never fired and the learner got 3 exercises. With the floor, azi-1 reaches explain (and is told limits are not in the course).
- **Console answer detection:** digits inside words are Arabizi letters ("ya3ni", "3lech"), not math. Before this, "chnowa ya3ni limite?" was graded as an answer to the open exercise.

## Correctness over grounding, loop salvage, content drafts (last engine round)

- **The formula check now asks "is it right?" before "is it in the course?"** (`engine.grounding.check_formulas`):
  - verified: derivative claims checked with sympy against the answer's own most recent `f(x) = E` (`f'(x) = D` → D ≡ diff(E); `f'(a) = v` → value at a), `(E)'` notation evaluated, and identities whose two sides parse (`3 × 2x = 6x`);
  - wrong (`wrong_math`): such a claim that is false, or a numeric identity that is false;
  - skipped: single-variable assignments (`u = 1`, `n = 2`; applied when checking later claims, so "n = 2, donc f'(x) = 2x" verifies) and equations to solve (`2x − 4 = 0`, "setting f'(x) = 0": a constant right side for a non-constant derivative is a condition, not a claim);
  - ungrounded (`ungrounded_formula`): neither verifiable nor in the sources;
  - new examples: functions the answer defines that the sources do not. Correct ones pass.
  Only wrong and ungrounded degrade (and so are not cached). `ln`, `exp`, `sin`, `cos`, `tan` were added to the parser's whitelist (mapped to sympy functions) so correct examples using them can be verified.
- Offline re-check of the 23 saved answers from the previous eval (`reports/eval_explain.before-v1.json`): 3 answers with new examples (all verified correct), **wrong_math 0, ungrounded_formula 1** (azi-1 writes `f(x) = (3x²)' + (5x)' - 0'`: f set equal to its own derivatives, a real error). Before: 9 ungrounded. Two first-pass "wrong" flags were false positives ("setting f'(x) = 0", and "n = 2, donc f'(x) = 2x"); they led to the condition and assignment rules above, both general.
- **Loop salvage:** on a repetition loop, if the text before the loop has ≥ 40 words and ends on a complete sentence, it is kept (degraded `explain_guard:loop_truncated`, not cached); otherwise fast mode. The loop starts at the first 3-word sequence that repeats 3+ times and comes back within 20 words; the cut goes back to the last `. ! ? ؟`.
- **Content drafts** for review: `data/docs/derivee-definition.en.md` (definition with the limit formula, tangent line, worked example x², 239 words) and `data/docs/limite.fr.md` (notion of limit, limit and derivative number, a non-differentiable case √x at 0, 213 words), concept-tagged and `status: draft`. The chunker skips drafts (`index_docs` lists them) until the header changes. `limite.fr.md` introduces the concept `limite`, which has no bank exercise yet: approving it will (on purpose) fail the "every chunk's concept has an exercise" test until exercises are added.
- **The first loop detector was wrong, and the "loop rate" with it.** It called any 3-word phrase used 3 times a loop, and a 2-run explain eval reported 8/50 loops (16%) with 0% salvage. Looking at the raw text (now kept in `notes.model_text` when a loop guard fires), the regenerated "loops" were complete, correct answers that said "le taux d'accroissement" or "la pente de la tangente" three times. A loop is now a 3-word phrase recurring 3+ times **in a row**, each within 12 words of the previous one (`engine.tasks.loop_start`); the same detector serves hints and the salvage cut. Fresh 2-run eval after the fix: **loop rate 2/50 (4%), salvage 1/2** (fr-6 kept, cut after its last complete sentence; azi-1 to fast mode). Earlier rates in this file (3/25, 2/25) used the eager detector and overstate loops.
- More formula-check cases, all general: a derivative claim may come before its definition ("f'(x) = 2x + 1, où f(x) = x² + x"); bare `x'` (= 1) and `d/dx (E)` are evaluated; a stray closing bracket is dropped; a claim right after a negation ("et non f'(x) = 2x + 7", not, pas, ليس, moch…) is a refuted claim and skipped; arrows outside brackets split a line into steps ("2x − 4 = 0 → 2x = 4 → x = 2"), while "lim(h → 0)" stays whole; a comma ends a formula.
- **Final formula numbers**, recomputed offline on all 91 saved answers (`reports/eval_explain.run-a.json` + `reports/eval_explain.json`): answers with new examples 10 (all verified or unflagged); **wrong_math 1** (azi-2: "f(x) = 2x + 7 … f'(x) = 2x + 0", a real error: the model took the learner's wrong derivative for the function); **ungrounded_formula 2**, both correct but not verifiable by the checker (`y = f'(1)(x − 1) + f(1)` with no f defined; the product rule written as `u'(x)v(x) + u(x)v'(x)`). Before this round: 9/25 answers flagged.

## Engine v1 summary (frozen 2026-10-24)

- **Load, `reports/load-engine-v1.md`** (real Ollama behind the simulator; n=50 calls per profile, capacity = 40 turns / 69 calls). Columns: normal / saturated / quota_low / capacity.
  - Volume: provider requests (all attempts) 28 / 35 / 33 / 27, of which ok 21 / 21 / 21 / 24; 15.1 / 22.0 / 14.4 / 4.5 requests per minute.
  - Class split (ok calls): compact 90% / 100% / 86% / 75%, mid-size 10% / 0% / 14% / 25%. Mid-size ok/attempts: 2/9, 0/10, 3/9, 6/9.
  - Tokens per ok call (input): 455 / 427 / 470 / 619. Output tokens in total: 835 / 624 / 935 / 1349.
  - Cache hit rate: 44% / 28% / 40% / 61%. Repeated identical provider requests: 0 / 0 / 0 / **1** (see the negative cache below).
  - Refusals: 7 / 14 / 12 / 3. Saturation: 4 / 7 / 3 / 2 retried at the stated interval, 3 / 7 / 2 / 1 raised to the router. Quota (quota_low only): 7 of 7 handled by waiting the stated delay.
  - Degraded runs: 11 / 21 / 12 / 2. Explain served in fast mode 11 / 21 / 12 / 1, generated in full 10 / 0 / 9 / 10. quota_low: 15 routing decisions went to `quota_low`, and 10 runs were served degraded for it. One breaker trip per profile; saturated re-opened twice after failed probes.
  - 0 exceptions reached the caller in any profile.
  - Capacity: 1.73 engine calls per turn, 0.68 provider requests per turn observed and **2.00 per cold turn** (13 of 40 turns). Mean provider latency 11.5 s, so the local binding limit is concurrency (10.4 req/min), not the quota. Learners per key at 1 turn / 2 min: 20 quota-bound, 10 at local latency (cold turns).
  - Priority: interactive p95 49.6 s vs background 65.1 s (normal), 53.1 vs 88.2 s (quota_low); saturated 15.5 vs 13.7 s, still inverted (n = 28 vs 8, small).
- **Explain eval** (`scripts/eval_explain.py`; formula numbers recomputed offline today from the saved answers and identical to the last round): over 91 saved answers, **10 contain new examples** (all verified or unflagged), **wrong_math 1** (azi-2), **ungrounded_formula 2** (man-2 tangent formula with no f defined; fr-6 product rule in u/v notation). Fresh 2-run eval with the current loop detector: **loop rate 2/50 (4%), salvage 1/2** (fr-6 truncated and kept, azi-1 to fast mode). The 8/50 in `eval_explain.run-a.json` came from the old, eager detector.
- **Negative cache.** The capacity run's single repeated request was turn t016 re-sending t011's explain: t011's answer failed the formula check (`ungrounded_formula`), so it was correctly not cached, and the identical question 38 s later generated again. An explain answer degraded by `ungrounded_formula`, `wrong_math`, `explain_guard:loop_truncated` or `explain_guard:degenerate` now stores a marker for its cache key (`NEGATIVE_CACHE_TTL_S`, default 600, 0 = off; table `negative` in the cache DB). A repeat within the TTL is served in fast mode (course excerpts verbatim + the exercise) with **0 provider requests**: the framing line comes from the cache or the template, never from a request. The original degrade reasons are kept, and metrics get a `kind="chat", outcome="negative_cache_hit"` record plus `from_negative_cache=true` on the run record; `metrics_report` does not count either as a provider request. A concurrent identical follower whose leader failed a check also uses the marker. Transient degradations (saturation, quota, breaker) are never marked: they say nothing about the request itself. Why fast mode and not a regeneration: at temperature 0.3, the same prompt usually fails the same deterministic check again, and the markers expire within minutes, so a fixed answer is not locked out for long.
- `explain_guard:degenerate` is marked too (added after the freeze, on request). The first degenerate answer already goes to fast mode, so before this its repeat sent the identical mid-size request again only to fail the same way. Its marker is written on the fast-mode branch, which returns before the other three reasons are marked.
- **Confirmation.** A deterministic replay of t011/t016 (`test_capacity_t011_t016_repeat_is_no_longer_a_repeated_identical_request`: classify then explain, twice, with the first answer ungrounded) gives 0 repeated identical provider requests and 1 negative-cache hit, with 2 provider requests in total instead of 3. A live capacity rerun (same seed, real Ollama; `reports/load-capacity-negcache.md`, v1 logs kept as `metrics/load_capacity.v1.jsonl`) also gives **0 repeated identical requests**, but it did *not* recreate the case: this time t011 lost mid-size to the open breaker (transient, so no marker was stored) and no answer failed a formula check, so there were 0 negative hits. Breaker timing depends on real model latency, so the same seed does not replay the same turns. Rerun numbers: 28 provider requests (24 ok), cache hit rate 59%, 3 degraded runs (2 midsize_saturated, 1 breaker_open), 2.00 requests per cold turn, 0 exceptions.

## Repo organisation (2026-10-25)
- Initialised git (`main`). `.gitignore` previously ignored all of `data/` except `data/docs/`, which would have dropped the exercise bank, glossary, templates, Arabic terms and index from a clone; now only `data/*.sqlite*` and runtime `metrics/*.summary.json` are ignored.
- Unused root files (`cache.sqlite`, `metrics.jsonl`, `metrics_test.jsonl`) moved to `_legacy/`; research PDF moved to `docs/research/`. `mock_server.py` stays at the root (see above). Added a root `README.md`.
- Not published: `.env`, `.env.real`, `.env.mock`, `.env.ollama` (git-ignored), `recon_log.jsonl`/`recon_summary.json` (real platform responses).

## Engine v1.1 (2026-10-26, branch `engine-v1.1`, from the FelCloud probes of the same day)

Evidence: `reports/felcloud-2026-10-26.md` (smoke, latency, refusal shapes) and `-b.md` (probes A rate window, B output length, C context ceiling, D chars per token); raw exchanges in `reports/felcloud-raw.jsonl`. Public interface (`engine.run`, `Result`, events) unchanged; version string `engine.__version__ = "1.1"`.

- **Sliding-window limiter replaces the token bucket** (`gatekeeper/limiter.py::SlidingWindow`; the bucket stays only inside the simulator). Probe A: 20 requests in a burst, then the 21st was refused and the key stayed closed for the full 60 s, so a bucket with a burst reserve overshoots. A sliding log of send times cannot send more than N in any W seconds, which is also safe if the server uses a fixed window (the probe could not tell the two apart). `RATE_LIMIT_PER_MIN` + `RATE_WINDOW_S` (60), embeddings `EMBED_RATE_LIMIT_PER_MIN` + `EMBED_RATE_WINDOW_S`; `RATE_BURST`/`EMBED_BURST` are deprecated (simulator only). `RATE_WINDOW_SLACK_S` (0.5): a send frees its slot 0.5 s after the window, for clock and latency skew (the first refusal of probe A came 39 ms before expiry). A window slot is taken only together with a concurrency slot, so a quota wait never holds a slot (unchanged property). A 200 whose `x-ratelimit-api_key-remaining-requests` is below the local free count adds phantom entries stamped at the send time, so local never exceeds the server (other traffic on the key). Tests: `tests/test_limiter_window.py`, six arrival patterns (20 at t=0 then steady 20/min and 30/min, 10 every 15 s, Poisson 90/min plus a burst, 100 at once, slow trickle) with and without slack: the densest 60 s window never exceeds 20.
- **Retry rules from the two observed 429s** (`gatekeeper/errors.py`, `retry.py`). Both are 429 with `retry-after: 60`; `rate_limit_type` says `requests` (quota) or `max_parallel_requests` (concurrency). Quota: the delay is the header only; `Limit resets at` in the body is always the refusal time plus 60 s (probe A) and is ignored. The window is blocked until the delay expires whatever the priority. BACKGROUND waits up to `MAX_RETRY_WAIT_S_BACKGROUND` (65); INTERACTIVE only if the delay is at most `MAX_RETRY_WAIT_S` (30), otherwise degraded `quota_exhausted` with `eta_seconds` = the remaining block. Concurrency: waits `min(retry-after, CONCURRENCY_RETRY_WAIT_S)` (2), because the 429 states 60 s but the slot frees when a request ends. Both shapes are now `CONFIRMED(felcloud)`; the real redacted status/headers/body of each are fixtures in `tests/test_errors.py` (`REAL_429`); only the API-key hash is redacted.
- **Output cap the platform honours.** Probe B: `max_tokens`, `max_completion_tokens`, `stop` and a bare `num_predict` are ignored (790 and 729 tokens for a cap of 30); a top-level `options: {"num_predict": N}` gives exactly N on both classes. The client sends it with `max_tokens` (`OUTPUT_CAP_MODE=both`; `options` and `max_tokens` also available, since it is provider-specific). Per-task caps (`config.task_max_tokens`, one env var each): classify 50, hint 80, grade 120, compress 30, explain 350, quiz intro 60, framing 40. **Deviation from the brief: classify is 50, not 40**, because a cut JSON object is invalid and costs a repair call; it is verified against the real reply sizes in `reports/engine-v1.1-felcloud.md`. Truncation: `finish_reason` stays `stop` when `num_predict` cuts, so `completion_tokens >= cap` means cut (`ChatResult.truncated`, `CallRecord.truncated_output`, event `output_truncated`). Text tasks are trimmed to the last complete sentence (`trim_to_last_sentence`; a reply with no early sentence end is kept whole, a cut answer beats an empty one) and carry `notes.truncated_output`; JSON tasks go through the existing repair ladder.
- **Context window and estimator.** Probe C: the start of the prompt (system prompt included) is dropped above about 4,096 tokens (whole at 3,899, cut at about 4,200), and `usage.prompt_tokens` then reports the kept length (2,050). Probe D, chars per token with the 26-token baseline removed: French 3.9, Arabic 2.84, Darija in Arabic script 2.54, Arabizi 2.31. `ESTIMATE_CHARS_PER_TOKEN` default 2.3 (worst measured case; was 2.0), `CONTEXT_WINDOW` 4096, `CONTEXT_FILL` 0.85: estimated prompt + the output cap must fit 4096 × 0.85, in addition to `INPUT_TOKEN_BUDGET`, now 2800 (was 1800; about 1,650 real French tokens at 3.9 chars per token, leaving room for retrieval). `RAG_TOP_K` stays 2. Chunk sizing keeps its own 2.0 constant so the index does not change with the estimator. Both limits apply in the engine's prompt assembly and in the retry layer's pre-send check.
- **Silent-truncation detector.** After each 200, `prompt_tokens < prompt chars / 5` (`INPUT_TRUNCATION_CHARS_PER_TOKEN`; real text packs at most about 4 chars per token; 0 = off) marks the call input-truncated: the result is degraded `input_truncated` (so it is not cached) and logged; the quiz/framing one-liners fall back to the template. The test fixtures turn it off because fake providers report arbitrary token counts; the detector has its own tests.
- **Quota ETA.** The degraded `quota_exhausted` result reports the limiter's remaining block as `eta_seconds`, not the provider's stated delay, since the block started at the refusal.
- **Metrics report** gained: output tokens per ok call, outputs cut at the cap, attempts that waited on the limiter (> 100 ms) and the longest wait.
- **Test hygiene.** Every virtual-clock wait loop in the new tests is bounded (1000 spins, and the clock must advance on each wait) and the concurrent limiter test has a 5 s timeout, so a limiter regression fails instead of hanging.
- **Not decided here:** whether the real server uses a fixed or a sliding window (the local sliding log is safe under both); the embeddings limits on FelCloud (no embedding model there).

## Engine v1.2 (2026-10-27 to 2026-11-01, branch `engine-v1.2`, merged into `main` on 2026-11-01)

Evidence: `reports/midsize-outage-2026-10-26.md` (the live mid-size outage) and `tests/test_outage_replay.py` (a virtual-time replay that was xfail on v1.1 and passes now).

- **A hung mid-size model counts toward the breaker** (`BREAKER_TIMEOUT_THRESHOLD=1`: one timeout costs a whole timeout period) and is **never retried**: it will not answer a second try. Explain falls back to fast mode with `midsize_unavailable` and `eta_seconds` = the breaker's cooldown. The half-open probe is a tiny BACKGROUND request, never a learner's.
- **Timeouts per class and priority**: mid-size 40 s INTERACTIVE / 85 s BACKGROUND, compact 30 s, all validated under `SERVER_TIMEOUT_S` (90).
- **Ghost slots**: a request abandoned by timeout or cancel keeps a local concurrency slot until `SERVER_TIMEOUT_S`, because the proxy still counts it; a 429 right after a timeout is a ghost, not a retry case.
- **Reserved compact slot**: `MIDSIZE_MAX_SLOTS=1` (ghosts included); the queue grants the best *eligible* waiter, so compact work behind a hung mid-size request is never blocked.
- **Interactive deadline**: an INTERACTIVE explain with no body after `INTERACTIVE_DEADLINE_S` (25) is answered in fast mode (`deadline`) while the generation continues into the cache; the learner's next ask is a hit. The deadline path carries the retrieval's glossary terms to the generation run.
- **Key-hash scrubbing** (added 2026-11-01 when v1.2 was finished): LiteLLM echoes a hash of the team key in its 429 bodies. `gatekeeper/errors.py::scrub` replaces `api_key: <hash>` and key-looking tokens in every `LLMError.message`, the one place provider text enters metrics, logs and notes.

## Engine v1.3 (2026-11-01, branch `engine-v1.3`, from the team's contracts document)

Goal: ready for every Phase 1 feature. Public interface unchanged. The integration contract is `docs/ENGINE_API.md`, **generated** from the real engine's output (`scripts/gen_api_examples.py`), with tests that fail when it drifts (examples, event names, degrade reasons).

### Identity, fairness, reserve, cache administration
- **Ids by ContextVar.** `learner_id`, `room_id`, `course_id`, `class_id` are set once in `engine.run` and read as defaults by `CallRecord` (and by `CallContext.owner`), so none of the eight record-writing sites passes them and background tasks inherit them (asyncio copies the context). A breaker probe clears them. They are never in a cache key. The generation that outlives the interactive deadline has no owner, so a learner's next question cannot supersede it.
- **`superseded`** only replaces a request still *waiting* in the chat queue (INTERACTIVE, one per learner); nothing sent, degraded, no ETA. A granted request is never touched, BACKGROUND is not limited. Not done: a request still in retrieval or compression has not reached the queue and is not replaced.
- **`room_busy`**: one `run` per room, refused at once with `eta_seconds` = an EWMA of the room's recent run times minus the time already spent (floor 1 s). Helper runs (grade inside check_answer, compress) keep the learner's ids but never the room (`_sub_context`), or the room would refuse its own helper. This also fixed compress runs having lost the ids.
- **Background reserve** (`BACKGROUND_RESERVE=6`): a BACKGROUND chat request is not granted while fewer than 6 sends remain in the window, announced once as `waiting_reserve`. The rule is the literal one (wait while fewer than 6 remain), so a background send may leave 5. The idle dispatcher sets its own timer for the reserve, because nothing else would wake it when window entries expire. Embeddings are not affected (their own window).
- **Cache administration**: an `origin` table links `request_id` to the key behind its answer, written on a write *and* on a hit (a professor flags what they were shown). A request id names one generation: a rewrite or an invalidation drops every link of that key, so an old id can never remove a freshly regenerated entry (found by a test).

### Vision class and OCR
- `ModelClass.VISION` (`MODEL_VISION`, default `deepseek-ocr`), `TIMEOUT_VISION_S=40`, shares the chat window and slots. Mid-size and vision share a **heavy budget of `MAX_CONCURRENT - 1` slots** (switched off by `MIDSIZE_MAX_SLOTS=0`) so compact always keeps one; a vision hang is not retried and leaves a ghost slot.
- Images: content parts carry `x_est_tokens` and `x_sha`, stripped before sending. Resized to at most 1 MP with Pillow (EXIF orientation, transparency to white, JPEG re-encode). **Identity = hash of the normalized pixels**, so the same photo as a data URL, bare base64 or re-encoded is one cache entry. Token estimate = `IMAGE_TOKENS_PER_MEGAPIXEL` (1000) per MP, floor 256: **an assumption, to be measured on FelCloud** (`# ASSUMPTION` in config). An image that alone exceeds the input budget is refused locally (`request_too_large`).
- `ocr_transcribe`: the model's cleaned text is what is cached; `decorate` rebuilds `{text, latex, confidence_note}` deterministically (also on hits). The model gives no confidence, so the note only reports what can be seen (`[?]`, brackets that do not close, no text, almost none) and such a reading is `low_confidence`, returned and **never cached**. The text is transcribed, never corrected. An unreadable image or an image URL is a degraded `invalid_image`, not a `ValueError` (it is the learner's photo, not a caller bug). The `resizing_image` event comes *before* `checking_cache` (the key needs the prepared image).
- The OCR output cap is excluded from the text-prompt room computation (it had shrunk every text prompt by 350 tokens).

### check_claim
- Order: sympy, retrieval gate, one compact JSON call. **`check_formulas` was written for explanations and files `(2x) = x` under "equations to solve"**, so the classic false claim `(x²)' = x` was not refuted; `engine/claims.py` decides the derivative-on-the-left case itself (explain's behaviour and evals are untouched). `parse_expression` could not read `2√x` (no word boundary between the digit and `sqrt`); fixed with lookarounds.
- **Only connectives may surround a verified formula** (`donc`, `so`, `نعلم`): a first version allowed four words and would have declared `(x²)' = 2x et la dérivée d'une constante est 1` SUPPORTED. Anything else goes on, with the verified part marked for the model.
- **A claim half verified and half refuted is `PARTLY`**, not `CONTRADICTED` (not in the contract: half right is not "wrong"). The correct form is stated in the learner's language; a bare formula has no language, the caller passes it.
- `source_chunk_id` is null for computed verdicts and `NOT_IN_COURSE`. A correction whose own formula sympy refutes is dropped. Safe default after the repair ladder: `PARTLY`, `checked_by: llm`, `degraded: json_invalid`, never cached. Cache key: normalized claim + chunk id + language.
- **Eval**: `tests/eval/claims_set.jsonl` (30 claims: 10 SUPPORTED, 10 CONTRADICTED, 4 PARTLY, 6 NOT_IN_COURSE; 10 each in French, English, Arabic) and `scripts/eval_check_claim.py`. Offline, the 11 claims the rules decide are all correct (tested); the other 19 need a model and **have not run** (no FelCloud slot on a Sunday; Ollama is off limits). No accuracy number is claimed.

### check_answer
- Lines go through the safe parser; unparseable = UNCHECKED, never WRONG. **A labelled line must be what its label says** (`f'(x) =` the derivative, `f(3) =` the value at 3, `y =` the tangent): a first value-only version passed `y = 2x` for a tangent problem because `2x` is f'. A line is OK if it equals the allowed values or rewrites an OK line before it; chained equalities must agree with themselves.
- Feedback order: the exercise's stored mistake that equals the line; the four buggy rules (`bdiff` differentiates structurally with one rule wrong; precomputed and cached per function, listed only when different from the true derivative); one compact guiding question through the existing one-liner machinery (cached, template on any trouble, never a request in Darija/Arabizi). The question is checked with the hint guard **plus the true derivative written out** and replaced by a fixed question if it leaks.
- The final line: a free comparison with the stated / stored solution and the exercise's stored answers (text included), then the `grade` task (sympy first). Over all 15 bank exercises the correct derivation passes with no request and every one of the 30 stored mistakes is caught with its stored feedback: 24 as a wrong line, 6 (d10 `2x`, d11 `3x² − 2`, and the four text answers of d14 and d15) as a valid or unparseable line whose **final verdict** is INCORRECT, which is where they are wrong.
- `graded_by`: `llm` if a model request was made (guiding question or text grade), `rules` if the feedback came from stored mistakes or rules, else `sympy`. A refused grade degrades the result with the ETA and `final_verdict: UNKNOWN`.

### Content model, retrieval, ingestion
- **Course graph** `data/courses/analyse.yaml`: 11 published concepts as one unit plus `limite` as a `status: draft` concept (its text is a draft and the bank has no exercise). The validation test checks graph, `data/docs` tags and bank both ways; **draft concepts are exempt from needing text and exercises** and prep plans skip them. To make the bank's `quotient`, `inverse` and `racine carrée` appear in the course text, `derivee-quotient-inverse-racine.fr.md` was written as a **draft pending review** (not indexed, so retrieval is unchanged). The course docs gained `course:` and `unit:` front matter (chunk text and ids unchanged) and the index was rebuilt (format 3; format 2 still loads).
- **Visibility**: public (everyone in the course; a chunk with no course is in every course), class (that `class_id` only), private (the owner only). A viewer without an id never sees class or private material: leaking is worse than missing. BM25 statistics are global (a private upload shifts idf a little; accepted). The professor boost multiplies the score before the coverage gate and does not touch coverage: it reorders relevant chunks and wins exact ties, it cannot let an off-topic chunk in. Retrieval evals unchanged (13/15, 14/15, 4/4; cross-language 6/8; manual 2/2), asserted by a test.
- **Ingest**: PDF through PyMuPDF imported lazily, **not installable offline here, so the PDF reader is tested through a stand-in module only**; DOCX through the standard library (a zip of XML), no new dependency. PDF headings are a heuristic (numbered, ALL CAPS, short capitalised lines without maths). Concept tags: BM25 of each chunk against each concept's titles, the **titles scored as alternatives** (a first version scored them as one long query and a clear tangent chunk got 0.33 because "Tangent line" is not French); confidence = coverage halved by how close the runner-up is. Chunk ids `<source>/<owner>/<stem>#<heading>`: re-ingesting replaces, two owners never collide.
- **OCR of scanned pages**: at most 20 per file (`INGEST_MAX_OCR_PAGES`), one at a time at BACKGROUND, stop after two failures in a row (the refusal will repeat), the rest stays in `needs_ocr`; transcriptions are cached by image, so a second call costs only the unread pages. An unreliable page keeps its text with chunk confidence < 0.5. A page is rendered at 150 dpi (about 2 MP) and resized to 1 MP: small print may lose quality.

### prep_plan, generate_page, explain extensions
- **Plan**: a mastered prerequisite does not block (only unlearned in-scope ones gate); mastered concepts get a review page after the learn pages; missing mastery counts as 0; pages are spread *evenly* over the study days (start to the day before the deadline) without exceeding the pace; too little time puts the overflow on the last day with `notes.over_capacity`, a deadline today or past is day 0 with a flag. Page cost 20 / 8 minutes is a default, not a measurement. Extra output fields beyond the contract: `reason_code`, `minutes`.
- **generate_page** is `explain` at BACKGROUND on a canonical question per language, so the page shares the tutor's cache; the bank's worked example, three quiz items (own concept, then prerequisites, then deterministic variants whose solutions sympy computes) and the explanation's own practice item never overlap. It returns a dict, not a `Result`.
- **passage / board_text**: retrieval order passage, question, board text (a vague question has no topic; the coverage gate still stops an off-course board). Each is trimmed to 25% of the prompt budget and keyed by a hash of the **full** original, so passages that differ only in the trimmed part are two entries.

### Found by the mixed load, fixed
- **The interactive deadline did not cover an identical request waiting for another.** An INTERACTIVE explain whose key was already being generated waited for the leader with no limit (a BACKGROUND prep page can take 60 s), and when the leader's answer was not cached (degraded, or the cache off) it then ran its own generation through a path with no deadline: one explain took 41 s in the simulated run. Now the wait is bounded by the learner's own remaining deadline (then fast mode, `deadline`, while the leader keeps filling the cache), and a follower that finds nothing becomes a leader through the normal deadline path, counting the time it already waited (`waiting_identical_request` is emitted once). Tests: `tests/test_interactive_deadline.py` (three new). The simulated mixed load now has every non-OCR interactive request at or under 25 s.
- **Observed trade-off, not changed:** mid-size and vision share one heavy slot (`MAX_CONCURRENT - 1`) so compact work always has a slot. An INTERACTIVE photo can therefore queue behind a mid-size generation that is still running into the cache after its learner was answered (24.6 s for the OCR in the cache-off run, 38.8 s in another seed before the deadline fix; that run's file is not kept). The queue shows `queued` with an ETA; allowing two heavy requests at once would starve classify, which is worse for every learner.

### Tests and tooling
- **Course-index isolation**: tests persisted runtime material into the real `data/index/courses/`, where the next engine loaded it and flipped an unrelated retrieval test (diagnosed by bisecting). `make_settings` now gives every settings object its own directory under one temp root, with a test.
- `FakeEngine` (`engine/fake.py`): same `run` signature (asserted), every outcome of every task, three modes, real `room_busy`, plans from the real planner; the generic refusals are checked against the real engine's examples.
- `scripts/load_mixed.py` runs the mixed scenario in virtual time (`--virtual`, through the constraint simulator, canned replies) or on FelCloud (`--real`, `--probe-vision`).

### Not verified (no FelCloud slot: 2026-11-01 is a Sunday)
The live smoke of every task including one OCR call, the check_claim accuracy, the mixed load on the real API, and the measurement of whether a vision request counts in the 20 / min window. The simulated mixed load (seed 1; `reports/load-mixed-virtual-v13.{md,json}` cache on, `reports/load-mixed-virtual-nocache-v13.{md,json}` cache off) passes all four checks in both runs: interactive p95 17.8 s with the cache on and 14.7 s with it off, 0 server 429s, densest 60 s window 15 (cache on) and 20 (cache off, at the limit), 0 exceptions. It tests scheduling, not FelCloud. (An earlier version of this line said p95 17 s with the cache off and window 18; those came from an earlier run whose file is not kept and are superseded by the files above.)

## Engine service (2026-11-01, branch `engine-service`, review follow-up 3)
- **`services/engine_server.py`** puts `Engine.run` behind FastAPI for the Node app: `POST /run`, `WS /run/stream`, `POST /materials`, `DELETE /materials/{id}`, `POST /cache/invalidate`, `GET /health`. It is a thin front: the body is `run`'s arguments, the answer is the `Result`; it adds no logic about tasks. The contract is in `docs/ENGINE_API.md` section 10, generated like the rest (the responses come from the fake engine served through the real app, so a change in the service fails `tests/test_api_doc.py`).
- **Local and authenticated by construction**: bound to `127.0.0.1` with no option to change it, a `Host` allow-list (DNS rebinding), no OpenAPI page, header `X-Engine-Token` compared with `hmac.compare_digest` on bytes on every route including the WebSocket handshake. The token is never accepted in a URL (URLs end up in logs). The service logs the type of a failed run, never an input, a body or a token.
- **A run outlives its connection**: every run is a task the service holds (`app.state.runs`), awaited through `asyncio.shield`/`asyncio.wait`, so a dropped HTTP request or a closed WebSocket cancels nothing and the answer reaches the cache (the learner who reloads gets a hit). At shutdown the service waits up to `SHUTDOWN_GRACE_S` for them before closing the engine.
- **One process per key** (the quota is per key: two processes would each think they own 20 a minute): a pid file named by a short domain-separated digest of the key (never the key), created with `O_EXCL`; a live pid (checked with `kill(pid, 0)` and `/proc/<pid>/cmdline` against pid reuse) means exit status 3; a dead one is taken over. `--fake` uses no key and no lock.
- **The engine is built inside the server's event loop** (`engine_factory`, called by the lifespan), because it owns asyncio objects (queue dispatcher, locks) that must live in the loop that uses them.
- **Additions to the engine for it**: `Engine.status()` (breaker per class, chat window remaining, in-flight, queue depth; no request) and `FakeEngine.status()/ingest_file()/add_material()/remove_material()`, so `--fake` is the identical API. The fake's ingestion is the real reader without OCR.
- **Choices**: a material id is `base64url([course_id, owner, file])`, stateless, so DELETE needs no registry and survives a restart; an uploaded image is accepted only for `ocr_transcribe`; the upload cap is 25 MB (413); the multipart route accepts the same fields as JSON, with `inputs` and `context` as JSON strings.
- **Verified**: the fake service over a real socket with curl, a Python `websockets` client and the doc's Node example (Node 20, `ws`, run unchanged against `--fake`: staged events, then the text); a Host header other than 127.0.0.1 gets 400; a WebSocket without the token gets 403. **Not verified**: the real engine through the service (only through ASGI with a scripted provider), anything against FelCloud, and `wscat` (it printed nothing in a non-interactive shell here, so the doc uses a Node one-liner instead).

## Slot run (2026-11-01, review follow-up 4: `scripts/slot_run.sh`)
- **One command for the Monday slot**, in the order smoke (every task, one OCR) → vision-window probe → `eval_check_claim` → `load_mixed --real --no-cache` → share of explain results degraded by `deadline` → secret scan of what was written. Everything goes to `reports/felcloud-<date>-*` (date `2026-11-02` unless `SLOT_DATE`).
- **The stop rule is evaluated from files, not exit codes**: each provider step writes counts of server 429s and exceptions (`--guard-out`, a spy on the transport; or the mixed-load / probe report), and `scripts/slot_guard.py check` stops the sequence on any, or on a missing report. The step's outputs are deleted before it runs, so a stale file from an earlier run cannot pass for this one. `load_mixed` exits 1 for any failed verdict (a p95 over 25 s is a finding, not a reason to stop), so its exit code is deliberately ignored.
- **A 65 s gap between steps** (`SLOT_GAP_S`): the steps are separate processes with separate limiters, but the platform's 20-per-minute window is shared, so a step must not start while the previous step's sends are still inside it.
- `--probe-only` was added to `load_mixed` so the vision-window probe is its own step (3 requests) instead of a prefix of the load; the load report gained `interactive.explain` (`n`, `degraded_by_deadline`, share) which `scripts/explain_deadline.py` turns into the step-e report.
- The preflight refuses a missing env file or a profile with `SIMULATE_CONSTRAINTS` on (a simulated run must not be filed as FelCloud numbers) and warns on a weekend. Never start Ollama for this.
- **Tested** with a stub interpreter for the provider steps (order, file names, every stop condition, stale-file case, leak case) and the real guard, share and scan code; smoke and `eval_check_claim` `--guard-out` against a scripted provider. **Not run against FelCloud**: the whole script has never made a real request (2026-11-01 is a Sunday).

## v1.3.1 (2026-11-01: PDF reader and the service, proven)
- **PyMuPDF is installed** (1.28.2) and the PDF reader ran on a real generated PDF (3 pages: two text pages with a heading each, one image-only page): 2 chunks with their headings (`1-la-tangente-et-le-nombre-derive`, `2-derivee-d-un-produit`), concept tags `nombre dérivé` (confidence 0.54: the page also speaks of the tangent) and `produit` (0.95), page 3 flagged `needs_ocr`. Through `Engine.ingest_file` with a scripted vision reply the image page was rendered, read and became a third chunk (`quotient`, 0.75). The real file broke nothing; the one change is that the reader falls back to `import pymupdf` when `fitz` is missing (the `fitz` name is deprecated and prints a warning). Not tested: a real scanned course PDF (none in `data/`), nor real OCR quality.
- **The service with the real `Engine`** (not `FakeEngine`): `tests/test_engine_service_real.py` sends one `/run` per task (classify, hint, explain, quiz, grade, compress, ocr_transcribe, check_claim, check_answer, prep_plan) and one `/run/stream` through ASGI with a scripted provider; the same was done over a real uvicorn socket. An explain through the service is grounded and generated by mid-size; check_claim and prep_plan cost no request. The first version of the explain assertion was too weak (the test module had no course index, so explain answered `no_relevant_context` and still passed); it now asserts mid-size and sources.

## Professor dashboard data (branch `engine-dashboard`, 2026-11-01)

- **Concept-tag cutoff.** It was `LOW_CONFIDENCE = 0.5` in `rag/ingest.py` (the `min_confidence` default of `ingest()`), not
  0.6: the 0.54 of the hand-checked PDF's page 1 (0.536) was *not* flagged. Now the setting `INGEST_MIN_TAG_CONFIDENCE`
  (config.py, default 0.6; `Engine.ingest_file` passes it unless the caller gives `min_confidence`). Test: that PDF's page 1
  is flagged at 0.6 and not at 0.5. Effect on existing material: chunks tagged between 0.5 and 0.6 now ask for a
  professor's confirmation; nothing already ingested changes until it is re-ingested.
- **Where the review data lives.** The metrics records hold ids and numbers only, so the question, the answer and the
  flagged spans cannot be read from them. `metrics/review.py::ReviewStore` keeps two kinds of item (flagged answers; "not in
  the course" questions) and the decisions in tables of the cache's SQLite file (`review_items`, `review_decisions`); the
  engine writes them in `Engine.run` (not for a run that finished after its caller left, not for a cache hit). Learner text
  is stored only for those two kinds. `metrics/summary.py` reads the JSONL (or the sink's records) on every call: 10,000
  records take 52 ms, so no mtime index (the 200 ms trigger is not reached).
- **Nearest concept.** `BM25Retriever.nearest` = the best chunk with the relevance gate off, same visibility filter as the
  failed search; stored as `notes.nearest_concept` (explain: when nothing passed the gate; check_claim: the concept of the
  retrieved chunk when the model says NOT_IN_COURSE). A question sharing no term with any chunk groups under `null`.
  This adds a key to `Result.notes` of those results (tests that compared `notes` exactly were loosened).
- **Decisions.** approve and reject both remove the item from the queue; reject calls `cache.invalidate(request_id)` once
  (a repeat is a 200 `already: true`, the opposite decision a 409). A guarded answer is never cached, so `removed` is usually
  0: the call is there for the case where the same request id was cached too. Not verified: a real professor's use of it.
- Fake engine: outcome `wrong_math` for explain, review store and run records in memory, so the app builds the screen
  against `--fake`.

## Engine v1.4 (2026-11-02, branch `engine-v1.4`; the last engine round before the freeze)

From the first live slot (`reports/engine-v1.3-felcloud.md`): the 3B called half-true compound claims SUPPORTED or CONTRADICTED
(never PARTLY), the formula reader flagged `x² +` for a parameter it did not know, and 10 of 17 interactive explains were
degraded `quota_low`. Five changes and one script; evidence below, none of it measured on FelCloud yet.

- **Compound claims** (`engine/claims.py::split_claim`, `aggregate`; `Engine._check_compound`). A claim is split at `et`,
  `and`, `و`, `ainsi que`, `;` and a comma before a new subject / equation; each part runs the unchanged single-claim
  pipeline (sympy, gate, model, cache entry per part) one after the other (a room has one run in flight) and the verdict
  is aggregated in code (all equal → that verdict, otherwise PARTLY; NOT_IN_COURSE beside a judged part → PARTLY, marked
  in `data.parts`). One request id and one `run` record for the whole claim, so one `cache.invalidate` removes every part.
  The splitter is conservative on purpose: both sides need ≥ 3 tokens and a verb or "=", no split inside brackets, in a
  conditional ("Si P, Q", "Where P, Q"), or between a function definition and its claim; a claim it is unsure of stays one
  part, which is v1.3's behaviour. At most 4 parts. Cost: a compound claim costs the sum of its parts; the cost of
  correctness on the half-true claims. Known limit: the right part of "A, et celle de B est C" loses its subject
  ("celle de B est C" is retrieved and judged without "la dérivée d'une somme").
- **Prompt `check_claim` v2**: one fact, three verdicts (no PARTLY); a PARTLY the model still returns is accepted.
  `CLAIM_VERDICT_CLASS=compact|midsize` (default compact) is the class of that one call; midsize falls back to compact
  (and a claim is never served as fast-mode extracts when the class changes: that path is explain's).
- **Eval: 60 claims.** The original 30 are frozen (a test pins their hash); `tests/eval/claims_set_v14.jsonl` adds 30
  (FR/EN/AR × 10): 20 compound (12 mixed true/false/outside-the-course) with the expected verdict of every part. The
  report gives original / added / compound / single / model-decided / per-part accuracy and p50 latency separately;
  the request count of a row now counts `generating` events (the old window delta undercounted: the window refills).
- **Formula reader**: parser symbols `x a b c h k m n t u v y` (b, c, m added; the whitelist stays a closed character
  set, tested with hostile strings); the token `bx`/`ax`/`kx`/`mx`/`cx` counts as math. A span that ends on an operator,
  holds a lone letter the parser does not know, or is followed by one, is **unchecked** (`notes.unchecked_spans`), not
  ungrounded. `scripts/recheck_formulas.py` on the 146 saved explain answers, before (main) / after: answers with an
  ungrounded formula **5 → 5**, with wrong_math **3 → 3**, unchecked 0 → 0; the only span change is
  `f(x) = a +` (x-fr-4), which now parses as a new example (that answer is still ungrounded for its derivative line).
  The saved answers do not contain the `x² +` case of the load run (its text was not kept), so the gain on real traffic
  is not measured here.
- **Quota-aware wait** (`Engine._quota_wait`). An INTERACTIVE, non-escalated explain that finds fewer than
  `QUOTA_LOW_THRESHOLD` free sends computes from the sliding log when that many free up; if that is ≤
  `INTERACTIVE_DEADLINE_S − EXPLAIN_EXPECTED_S` (new, default 10 → 15 s) it waits (event `queued`, `reason: quota_window`,
  `eta_s`) right before the generation (a cache hit never waits; the wait counts toward the 25 s deadline); otherwise
  `quota_low` as before. **Correction to the v1.3 report:** a `quota_low` explain is answered by *fast mode* (course
  extracts, at most one compact framing line), not by the 3B; the "10 of 17 went to the 3B" reading was wrong.
- **Virtual load, seed 1, v1.3 → v1.4** (`reports/load-mixed-virtual*-v13/-v14`): cache on: identical (no pressure on the
  window; 3 degraded, p95 17.75 s). Cache off: `quota_low` **5 → 0**, `deadline` **0 → 4**, explain degraded 5/14 → 4/16,
  full explains 9 → 12, interactive p95 **14.65 s → 25.0 s** (the 25 s deadline). So the wait turns quick extracts into
  more full answers and a slower tail: four learners wait 25 s for extracts instead of five getting them at once.
  `EXPLAIN_EXPECTED_S` 5, 15 and 20 give the same result (the waits needed are short); 25 (budget 0) restores v1.3
  exactly. Whether this is better for learners is a product decision; the live load (with records) will show the real
  wait lengths. `--setting KEY=VALUE` on `load_mixed --virtual` runs such comparisons.
- **load_mixed records**: `<prefix>.records.jsonl`, one line per engine run (events, window free sends at start),
  provider request (start, end, model, status, remaining header) and metrics record; written also when a live run
  crashes (partial). `scripts/slot_run.sh` for 2026-11-03: smoke, the 60-claim eval twice (compact, then midsize), the
  mixed load with records, the deadline share; stop rules as before plus "no records written".
- Fake engine: `check_claim` outcomes `PARTLY` (two sympy parts) and `COMPOUND` (sympy + model + outside the course),
  explain outcome `quota_wait`. API doc: `data.parts`, `claim_part`, `queued/quota_window`, `notes.unchecked_spans`.
- Not verified: everything on FelCloud (the compound accuracy, the 8B as judge per part, the real wait lengths).

## Engine v1.4 setting choices and fixes after the first live slot (2026-11-03, branch `integration/real-engine`)

- **`CLAIM_VERDICT_CLASS` default = midsize** (`config.py`, `.env.example`). 60 claims on FelCloud, same code, class only:
  3B 40/60 = 66.7% (original 30: 23, added 30: 17, compound 12/24, per part 26/40), 8B 52/60 = **86.7%** (28, 24,
  compound 21/24, per part 36/40); p50 per model request 3.4 s vs 4.5 s. The cost: 1 mid-size request per model-judged
  claim part instead of 1 compact, inside the 20/min window the 8B also serves `explain`. `reports/felcloud-2026-11-03-check-claim-{compact,midsize}.json`.
  The test profile (`tests/conftest.py`) pins compact so the older tests keep their meaning; a test asserts the production default.
- **`EXPLAIN_EXPECTED_S` default = 25** (budget 0: an interactive explain never waits for the window, v1.3 behaviour).
  Rule: compare the load runs at 25 / 15 / 10 and keep the one with the most full explains among those with a deadline rate
  <= 18% and interactive p95 < 25 s; none qualifies or one run only -> 25. Only **one** live run happened (setting 10, 2026-11-03,
  `felcloud-2026-11-03-load-mixed.*`): deadline rate 4/15 = 26.7%, p95 25.0 s, `quota_low` 0. It does not qualify under the
  rule, and a single run cannot be compared, so 25. The wait stays available (`EXPLAIN_EXPECTED_S=10`) and stays unmeasured at 15.
- **Arabizi retrieval.** Diagnosis: "derivee mta3 x²" already retrieves (`mta3` was a stopword; coverage 1.0, four chunks). The
  e2e question that missed was "chnowa el derivee mta3 x² w kifech nal9aha ?": `nal9aha` ("I find it") is a content token with no
  match, coverage 1/3 < the 0.4 gate, so nothing passed. Fix: Tunisian function words and common verbs (ta3, mtaa, wala, 3la,
  3and, nel9a/nal9aha, n7esbha, na3mel, nekteb, nfhem, 9olli..., and Arabic-script تاع، هاذا، نلقاها، نعمل...) are stopwords
  (`rag/normalize.py`); glossary variants stay out of it (`n7seb`, `نحسب` map to "calculer"). New set
  `tests/eval/retrieval_arabizi.jsonl` (6 questions of this pattern): **4/6 -> 6/6**; retrieval_set 13/15 and 14/15, off-topic 4/4,
  crosslang 6/8 and 7/8, manual 2/2 unchanged.
- **`generate_page` quiz length.** Only the "nombre dérivé" page had 1 quiz item: the concept has two bank exercises of the
  "value of f' at a" kind, its only prerequisite (`limite`) is a draft without exercises, and `variant_of` handled only
  "derive f" exercises. Now also "f'(a)" and "tangent at a" exercises get variants (f times n+1 at the same point, solution
  by sympy). Test: every published concept's page has 3 distinct quiz items and a worked example. Side effect found in the app:
  generated items carry their solution; the Node layer keeps it server-side (variant ids contain `~`) and grades them, and sends
  the browser the item without it.
- **e2e report:** the engine numbers are filtered by the session's request ids (`/metrics/summary?request_ids=`), and the session
  runs on its own cache and course index (it wrote an upload into `data/index/courses` before; removed).
- **App:** no links to the static Constellation / Universe pages (they still list MATH102 / CS201 / PHYS101): the Atlas / Universe
  rail links of the board and "Enter 3D universe" on the map are removed; the files stay in `client/public/ednova/` unlinked.

## Last engine changes (2026-11-03, `integration/real-engine`; the engine is frozen after this except dry-run bugs)

- **`follow_up(classification, ctx, has_open_exercise=False)`.** Intent HINT with no open exercise routes to `explain`, reason
  "hint without an open exercise", logged as an override (`outcome=overridden`) like the HIGH-confusion one, which keeps its own
  reason and wins. ANSWER_ATTEMPT is unchanged. Passed by the bridge (`POST /follow_up {has_open_exercise}`), the Node tutor loop
  (from its exercise state: it now looks the exercise up before routing) and the chat console. Why: live, the 3B called "chnowa el
  derivee mta3 x² w kifech nal9aha ?" a HINT and the learner got a nonsensical hint with no sources. Replay offline with a scripted
  HINT classification (`tests/test_bridge.py`): follow-up explain, grounded in two course pages, not degraded. Side effect:
  `scripts/load_test.py` (no open exercise) now routes its HINT turns to explain.
- **Verbal equality in the formula check** (`engine/grounding.py::verbalize`). `est égal(e) à`, `égale`, `vaut`, `equals`,
  `is equal to`, `is`, `يساوي`, `تساوي` between two math tokens are read as "=" (`is` only directly between two math tokens). A
  wrong one is `wrong_math` with `notes.wrong_math_forms` (span -> correct form): the upload answer's "la limite de (x^2 + 2x)/x
  est égale à 2x + 2" -> wrong, correct form `(x^2 + 2x)/x = x + 2`. Correct ones are verified. Guards found while testing: not read
  when the left run is the argument of a derivative / integral / value ("la dérivée de x² est égale à 2x", "the derivative of x²
  is 2x", "مشتقة x² تساوي 2x"), when the spoken equality sits inside a written one ("of f(x) = x² − 4x + 1 is f'(x) = 2x − 4"),
  after a sentence boundary, or when a neighbour is a word; the French verb "a" in "on a (x+1)² est égal à ..." no longer joins the
  left run. **Known false-positive risk, by design of the request:** "limite" is not an argument noun, so a true limit written as an
  equality ("la limite de (x² − 1)/(x − 1) est égale à 2") is judged as an identity and flagged.
  Recheck on 151 saved explain answers (the 146 older ones + 5 e2e answers), before -> after: `wrong_math` 3 -> 4, `ungrounded`
  7 -> 7, unchecked 0 -> 0; the only newly flagged span is `(x^2 + 2x)/x = 2x + 2` (the upload answer); nothing newly flagged
  as ungrounded and nothing un-flagged (`reports/recheck-formulas-verbal.json`). One false positive of the first version
  (a copula inside a written equation) was found by this recheck and fixed. Spoken equalities are rare in the corpus, so this is thin
  evidence about false positives on new answers.

- **Spoken limits (dry-run bug of the verbal-equality check).** Reading "la limite de (x² − 1)/(x − 1) est égale à 2" as the identity
  `(x²−1)/(x−1) = 2` flagged true limits. Now, after "limite / limit / lim(h→0) / la limite de / نهاية" the stated point is looked for
  ("quand / lorsque / when / as / عندما / حين VAR tend vers / tends to / approaches / → / تؤول إلى POINT", "x → a", or inside
  `lim(h→0)`; ∞ / +∞ / l'infini / infinity; in the same sentence, before or after the claim) and the claim is judged with
  `sympy.limit` against the right-hand side (a constant, ∞, or an expression in other variables such as 2x when h → 0): equal ->
  verified; different -> `wrong_math` with the correct limit in `wrong_math_forms` (`lim(x→∞) (x²+2x)/x = 2x + 2` -> `= ∞`); **no
  point, or a limit sympy cannot evaluate -> `notes.unchecked_spans`, never flagged**. A spoken limit is no longer turned into "=".
  Consequence: the upload answer's "la limite de (x^2 + 2x)/x est égale à 2x + 2" (no point stated) is **unchecked, no longer
  wrong_math**; the limit of that expression at ∞ would be flagged if the answer said so. Recheck on the 151 saved answers:
  before this fix wrong_math 4 / ungrounded 7 / unchecked 0, after wrong_math 3 / ungrounded 7 / unchecked 1 (the upload
  sentence moved from wrong_math to unchecked; nothing else changed); against the state before verbal equality at all: 3 / 7 / 0 ->
  3 / 7 / 1. `reports/recheck-formulas-limits.json`.

## Diagram generator (2026-11-09, from the team's `ednova-diagrams` package; feature layer, `engine/` not touched)

- **Where it lives.** Python in `app/diagrams/` (feature code, not engine: it makes no LLM call and `engine/` is frozen), renderer in
  `app/client/src/lib/diagrams/`, contract in `docs/diagram.schema.json` (`python -m scripts.gen_diagram_schema`; a test fails on drift). Route:
  browser -> `POST /api/diagrams`, `/api/diagrams/check` (Node, own 60/min limit, 200 kB) -> bridge `/diagrams`, `/diagrams/check` -> worker. It works
  on every engine mode because no model is involved, and it does not appear in the engine's metrics or the AI run log.
- **The maths runs in a child process (`diagrams.worker`), killed at `DIAGRAM_TIMEOUT_S` (10 s), not in a bridge thread.** Measured on the package as
  delivered: the slowest legitimate build was 5.8 s (`sqrt(x**5+x**3+1)/(x**7-x+1)`), but `x+((((9**99)+1)**99+1)**99+1)**99` passes the parser's whitelist and
  was still running at 25 s, in big-integer arithmetic that holds the GIL. In the bridge that freezes the engine's event loop, and a thread cannot be stopped. The
  worker is restarted on the next request; it has a 2 GB address-space limit. A timeout is a 503 `diagram_timeout`, a refusal a 400 whose text is for the learner.
- **The concept map reads the real graph `data/courses/analyse.yaml`, not the package's own course file**, whose concept ids (`derivee`, `somme`...) differ from
  the bank's. Draft concepts are left out, as on the progress map. The package's file is kept only as a test fixture (`tests/fixtures/diagrams_course.yaml`).
- **Mastery colours come from the server's measured attempts** (`store.mastery`, as for prep plans); a learner with none gets a neutral map.
- **Labels follow the UI language,** not the language guessed from the sentence, so a French UI does not get an English table for "plot x²".
- **Parser errors are replaced by a localized message** (`err_parse`, `err_failed`): the package returned English developer text in every language.
- **Board adapter (`toBoard.js`).** The board has no dashed line (a dash = short lines; solid if that would pass 500 items in one operation), its text is left-aligned from
  the top (anchors and baselines are converted), and a curve is a pen path with constant pressure so it keeps one thickness. The package's `toBoardPrimitives` now also
  returns `role` and `width`, without which the derivative and the tangent had the same colour. Every item is checked against the server's `cleanItem` in the tests.
- **Practice tables:** the answer key is in the spec the browser receives (the widget's "reveal" needs it), and results are not recorded as attempts, so they do not change mastery.
- **Not done:** the tutor chat does not draw diagrams on its own, and there is no diagram box inside the board. Detection is by trigger words, which are substring matches, so it
  suits an explicit "draw this" field better than free chat.

## Assessment Prep redesign (2026-11-09, branch `prep-redesign`)

- **Bugs 1 and 2 were only in `engine/fake.py`** (canned `Dérive l'exemple {id}`; the fake's "À toi" was always d01, also its worked example). The real engine was checked first: 36 generated pages, no id in a statement, no duplicate. The fake now uses the real `prep.page_exercises` and honours `exclude_exercises`; nothing else under `engine/` changed.
- **Exams are the learner's, in this browser** (`lib/exams.js`, like the preview accounts), because the upcoming exams were hardcoded examples and adding them to the server would be an API change. Readiness is the course's measured mastery, so it is the same for every exam.
- **Final assessment:** the plan's `assessment_exercise_ids` cannot be shown, no route returns their statements. It uses the existing `quiz` task, one exercise per concept of the plan (at most 6), harder where mastery is higher, which is how the planner defines the assessment.
- **"This confuses me" did not exist before and was built here:** it is the existing `explain` task with `passage` and `confusion: HIGH`.
- **Level test "done"** is a flag in this browser, set when the learner reaches Finish.
- **Before/after screenshots:** `reports/prep-redesign/` ("before" = commit fbd8fe3, with the same two exams seeded).

## Rebrand to Lumi and design system (2026-11-09, branch `rebrand-lumi`)

- **Not renamed, on purpose:** browser storage keys (`ednova-user`, `-exams`, `-lang`, `-board-client`...: renaming signs people out and loses their exams), `EDNOVA_ENGINE_DIR`, the instance-lock tag (it feeds the lock file's hash), `x-ednova-mock-reset`, the `ContextVar` names, the bridge's `EdNovaBridge/2` header, the organiser's "EdNova Technical Challenge", `reports/`, `_legacy/`, past entries here, the two PDFs. Socket events, API routes and the SQLite file never contained the name.
- **Light theme by the role of each colour,** not hex-for-hex: the old UI was dark and full of translucent white overlays. Overlays became `--lumi-fill` (ink-based, so they work on light and inside `.lumi-dark`), and text on a solid Iris is forced white. Functional gradients were restored by hand (mastery ring, timer bar, custom colour swatch).
- **`styles/lumi-app.css` holds what the tokens file does not define,** so `lumi-tokens.css` stays as delivered: status colours (ok, warn, bad; 4.5:1 on white), fills, size variants, the 44px rule. In `.lumi-dark`, `--lumi-iris-soft` and `--lumi-iris-strong` are redefined because the light values are unreadable there.
- **Glow** marks AI activity, and on the map the concept being worked on, as asked. On the canvas the selection turns Glow while the AI checks it. **Spark** marks mastery only. The pen palette and sticky-note colours are the learners' content and were left alone.
- **Default canvas is Paper (Room).** The stored preference moved to `lumi-board-bg`, because the old key was written on every visit, so it could not tell a real choice from the old Starfield default. Starfield stays selectable.
- **The board stays left-to-right in Arabic,** as before: its interface is English only and canvas text is positioned from the document direction. Its CSS is logical, so this can change later. The switch thumb and `left: 50%` centring stay physical on purpose.
- **Map planets stay under 44px wide:** a bigger hit area would change the map's behaviour, which was out of bounds.

## Glass & Aurora restyle (2026-11-09, branch `restyle-glass-aurora`, from `rebrand-lumi`; `app/client` only)

- **The `--lumi-*` names stayed and now point at the new tokens,** so every screen moved with one change. `styles/glass.css` loads last and holds the primitives, controls and per-screen rules.
- **Contrast over a bright blob: a dark layer under the glass, not more white** (a change from the brief's "raise opacity to 0.12-0.16"). White glass over a bright blob gets lighter, which lowers contrast for light text. `--glass-scrim` dims the sky first. Measured with each blob parked at full strength under the panel: `--text-3` lowest 5.08:1 on desktop, 4.94:1 on a phone across the drift. Hero cards (`.glass-strong`) use `--text-2` for secondary text.
- **Gold means mastery only,** so `--lumi-warn` moved from amber to orange.
- **Dark board by default (`grid` on #0E1030), Paper is the light option.** The preference moved to `lumi-board-surface`, because `lumi-board-bg` was written on every visit and so could not tell a choice from the old default. It is now stored only on a real choice.
- **The two default inks follow the surface at draw time (`theme.inkOn`).** Boards drawn on Paper use dark ink, which would vanish on the dark board. Stored items are unchanged; other colours are the learners' and stay.
- **Nothing blurs behind a drawing.** Whatever floats on the canvas is solid, and so is the side panel when it covers the canvas (below 1240px).
- **AI text is revealed in the browser** (the engine returns whole answers). The full text is in the DOM from the start and only transparent, so nothing reflows and a screen reader hears it once.
- **"Continue" uses real data only:** the next exam the learner added, else the weakest concept. There is no "review due" tile, because the app has no data for one.
- **Concept details are new** (a panel on the full map), built from what the course map already holds.
- **Scrolling cost is not established.** The test machine renders without a GPU (about 8-9 fps with or without blur), so it says nothing about a laptop. Blur stays at 24px with 3 blobs; low-power mode turns on by itself at 4 cores or fewer.
- **Room codes: capitals are display only.** On the server a code is any case-sensitive name up to 80 characters, so rewriting the input would open a different room. A room the list knows is matched whatever the case. "Valid length" is therefore 1 to 80 characters.
- **The inline "invalid code" message has nothing to fire on yet.** The server creates any room that does not exist and has no expiry; a private room reports in the lobby. The dashboard can only learn more from a new endpoint, which was out of scope (frontend only).
- **Progress map: planets share orbits (up to 3)** instead of one orbit each: 11 concepts on 11 rings cannot fit. The canvas is laid out in real pixels (`lib/mapLayout.js`, tested over many orbit positions), so sizes and the 32px margin hold at every width.
- **Planets stay in one DOM order** (they no longer move between "behind" and "in front of" the sun lists), so keyboard focus survives their motion. Inner orbits are placed to clear the sun and its label, so the two lists were no longer needed.
- **"Example data" on the map appears only if the learner's mastery could not be loaded:** the course graph and mastery are real otherwise.

## Voice chat and "Ask Lumi" (2026-11-10, branch `restyle-glass-aurora` working tree, from `VOICE_CHAT_IMPLEMENTATION.md`; feature layer, `engine/` not touched)

- **The answer goes through `engine.run`, not a new FelCloud prompt in Python** (the spec's `POST /voice/ask` with its own system prompt). Hard rules: every LLM call goes through the gatekeeper, and the engine is frozen, so the feature layer calls `engine.run`. A spoken question takes the tutor's path: classify, then `follow_up`, then explain (with the board text as `board_text` and the last 4 voice turns as `history`), hint or quiz. It gets the cache, quota pacing, retrieval with the professor boost, metrics, the run log and review flags at no extra cost. The cost: the spec's "2 to 4 spoken sentences, peer tone" prompt does not apply. The answer is the explain answer, trimmed for speech to 600 characters at a sentence end, and the full text (up to 1,500 characters) shows in the strip.
- **The bridge does speech to text only** (`POST /voice/transcribe`, JSON with base64 audio). The spec asked for multipart, but the bridge is standard-library HTTP and every other route is JSON. 2 MB of audio is 2.7 MB of base64, well under the bridge's 30 MB body limit.
- **Whisper picks among fr / en / ar** (`WHISPER_LANGUAGES`). It detects the language over the whole list, and the best of the three is used for decoding. The spec said "do not pin `language=`". This still detects, but a short, accented or Darija question can no longer come out as Spanish or Persian. Darija is heard as Arabic (Arabic script). Classify then labels it DARIJA, and the reply language follows classify.
- **The reader voice follows the answer's script, not the question's language** (`speech.guessLang`). The engine's Darija answers often have a French body (open quality issue), and an `ar-SA` voice reading French is unusable.
- **Chit-chat and off-topic get a fixed line in fr / en / ar** after classify, with no explain call. Degraded results (quota, room busy, bridge down) become a fixed spoken line with the engine's `eta_seconds`, so the room is never left waiting in silence.
- **`lumi:error` goes to the whole voice channel** with `askedBy` (the spec sends it to the asker only). Otherwise the others' "Lumi is thinking…" never clears. Only the asker shows the message. The `lumi:ask` ack also carries the error.
- **`lumi:thinking` is sent twice:** `listening` when the audio arrives, then `answering` with the transcript. The room sees the question while the model works.
- **Overall deadline 60 s** (`LUMI_VOICE_TIMEOUT_MS`), not 30 s. STT is capped at 30 s and explain alone is bounded at 25 s plus classify. On a deadline the room lock is released, and the engine run finishes into the cache.
- **Voice membership = the socket has joined that board room.** There are no accounts, so the board's access code is the room's only gate. Leaving the board, switching rooms or disconnecting leaves the voice channel. The join ack also carries the ICE servers (`ICE_SERVERS`, so TURN credentials stay server-side), `lumi` (the tier check) and the room's last answers for latecomers.
- **Tiers do not exist yet.** `canUseLumiVoice(user)` allows everyone unless `user.tier === "free"`, which nothing sets today. Server enforcement is tested with an injected rule (`createServer({ voice: { canUseLumi } })`).
- **Recording uses a clone of the call's mic track, forced on,** as the spec decided: a member muted to the group can still ask. The recorder is webm/opus at 32 kbps (20 s ≈ 80 KB).
- **TTS is queued sentence by sentence** (≤ 200 characters each), because Chrome cuts long utterances after about 15 s. A safety timer always lifts the mic suppression.
- **Entry points (added 2026-11-10 after "I don't see voice chat"):** the call lives on a room's board, which was its only door. The dashboard's old placeholder Voice Chat page (hidden, `/app/voice` sent you home) is now the hub: a room code field with "Start voice session", the room list, and how it works. Every room row (Group Rooms, Voice Chat, Whiteboard) has a Voice button. Both open the board and join the call on arrival (`openVoice` sets a one-shot `sessionStorage` flag that `VoicePanel` reads). The click is the user gesture the microphone needs, since the app never reloads the page.
- **Verified:** server `npm test` 41/41 (16 new voice tests), `tests/test_bridge.py` 23/23 (3 new), client tests 92/92. End to end with two headless Chromiums whose fake microphones loop a synthesized English question (espeak-ng), against the fake engine with real Whisper `small`: the WebRTC peer connection comes up, Ana's speaking ring lights from Ben's remote audio, and mute shows on the other side and silences the ring. A tap is discarded. Holding the button sent the question, Whisper transcribed "What is the derivative of x squared?", both members got the answer, leaving updates the other side, a latecomer gets the strip, and there is no horizontal scroll at 390px. Real Whisper on the clips: English correct (about 3 s on CPU). The espeak French and Arabic clips are too robotic to judge accuracy.
- **Not verified:** FR / AR / Darija accuracy with real voices; the real engine on FelCloud (latency of classify + explain over voice); audio across two machines or through a NAT; speechSynthesis voices (headless Chromium has none, so the "no voice" note showed); Safari / iOS (MediaRecorder gives `audio/mp4`, which the bridge accepts but no test covered).

## Progress map as a constellation, and a light theme (2026-11-10, working tree on `restyle-glass-aurora`; `app/client` only)

- **The map shows the course's prerequisite graph instead of orbits.** Each concept is a star and each line means "builds on". Concepts sit in steps by their longest prerequisite chain (5 steps for `analyse`), left to right (right to left in Arabic), and the order within a step is chosen to keep lines short (barycentre sweeps). The orbit layout hid this structure. It also cut 4 of the 11 names to "Derivative of a p…" and kept everything moving. Logic and geometry are in `lib/constellation.js`, with no DOM, tested in `test/constellation.test.js` (steps of the real course, next-up rules, no overlapping labels at 600–1500px, RTL mirror).
- **Status by colour and shape** (mastered gold with a check, in progress glow with a ring, needs review rose with "!", not started hollow, "ready" dashed). A ring shows the percentage. Lines turn gold once their prerequisite is mastered. **"Next up"** is the one ready concept to suggest: one already in progress, else one that needs review, else the earliest new one. It is the only thing that moves (a slow pulse, off with reduced motion).
- **Hover, focus or selection highlights the star's chains** (what it builds on and what builds on it) and dims the rest. The full page has a panel: step n of N, status, "builds on" and "unlocks" (clickable), "Study this concept" (Prep's study page, `?study=<concept>`) and "Ask Lumi" (opens the companion with a question). With nothing selected it shows an overview. A star on the dashboard card opens the full map on that concept (`?concept=`).
- **Below 560px the map is a list by step**, not a shrunken drawing.
- **One course, so no "All courses" level and no per-concept "moons" view.** The moons view only ever showed one "Practice" moon or the prerequisites, which the panel now lists. Course tabs appear if a second course is added. `lib/mapLayout.js` and its test are no longer used; I kept them pending approval to delete.
- **Light theme: Dark (default) / Light / System,** per browser (`lumi-theme`), applied to `<html data-theme>` before the first paint (`lib/themeMode.js`). Dark stays the default because it is the look the app was designed in. Toggle in the dashboard top bar, the landing and sign-in headers. The three-way choice is under Settings › Display.
- **Theme roles instead of literal colours** (`lumi-tokens.css`): overlays are `rgb(var(--tint) / a)` and scrims `rgb(var(--base) / a)`. The strongest fill is `--contrast` / `--on-contrast` (white on dark, ink on light). Text on a bright accent is `--on-accent`. An accent read as text uses `--glow-ink` / `--spark-ink` / `--rose-ink` (darker in light, to stay above 4.5:1). 73 hardcoded white or near-black values were converted. The hue of glow, spark and rose stays the same in both themes.
- **The canvas does not follow the theme.** `lumiColors()` reads `--night` for its dark, and a board's surface is the learner's own choice. A learner who never picked one gets Paper in the light theme and the dark grid otherwise.
- **Light aurora:** the same three colours at about a third of the strength, no stars. The landing and sign-in brightened `--lumi-slate` for the full aurora; in light it is `--text-2`.
- **Verified:** client tests 105/105; screenshots of the dashboard, map (desktop, phone, Arabic), settings, prep, landing and board in both themes; a browser run of the toggle (persists, System follows the OS live), the dashboard → map link, Escape, keyboard selection, Study → Prep and Ask Lumi. **Not verified:** contrast measured with a tool (values were picked for ≥ 4.5:1 but not measured on the aurora), and the screens not captured (materials, review, diagrams, the voice panel in light).

## OCR prompt v2 (2026-11-11, FelCloud slot; engine change on an explicit go-ahead)

- **The prompt is one sentence, "Transcribe the math exactly."** (`prompts/ocr_transcribe.md` v2), sent alone after the
  image (`_ocr_build` no longer appends a user section). CONFIRMED(felcloud): deepseek-ocr answered `''` (1 output token)
  to v1, a legible image of f(x) = x³ − 2x came back `low_confidence` "no text read" (scenario HS-08). Same image, direct
  calls: the one sentence reads it with the image first or last; v1's text returns `''` either way, and so does one
  sentence more ("…line by line. Write [?] for a symbol you cannot read."). On a three-line worked solution and a scanned
  exo7 page the two-sentence prompt also returned `''`, the one sentence read both. Any added instruction is dropped,
  including `[?]` for an unreadable symbol, so the `unreadable` note now depends on the model writing `[?]` or `�` on its own.
- **The model wraps each formula line in `\[ … \]`.** `analyze_ocr` removes math delimiters around a whole line and the
  opening and closing marks of a display block split over lines, keeping inline `\( … \)` inside prose, so the board's
  "Add as text" and "Fact-check it" get plain lines. Readings cached before this get cleaned on output too (`_ocr_decorate`).
- **Verified:** through the app on the real engine, all three images read with no note; the scanned 4-page PDF re-ingested
  in 95 s (18 chunks) with its section headings recovered (v1 gave "Page n" sections). `pytest -q`: 1127 passed, after
  `data/index/courses/` (written by the live app's uploads during the slot, which fails
  `test_tests_never_write_runtime_material_into_the_repo`) moved to `_legacy/index-courses-2026-11-11/`. One misread kept: `f'(1) = 11` read as `f(1) = 11`. Still no real phone photo.

## App checkup (2026-11-11, working tree on `restyle-glass-aurora`; feature layer only, `engine/` not touched)

- **Class-only uploads were broken from the UI.** "My class or group" sent no `class_id`: the engine refused the file
  ("visibility 'class' needs a class_id", status failed) and the list hid it even from its uploader. The client now
  sends one demo class id (`ENGINE_CLASS` = `l1-mi-g1`, the id the 2026-11-11 slot used) with uploads, the materials
  list, file links and every AI request's context. One class and no enrolment: every signed-in user is in it.
  The voice path ("Ask Lumi") sends no class id, since its answer is read to the whole room.
- **The server refuses a class upload without a class (400)** before storing anything, and **an upload is always
  visible to its uploader.**
- **`app/server` tests look for the repo's `myenv` python** (as `dev.sh` and the client tests do). Before, on a
  machine whose `python3` lacks the engine's requirements, the whole "AI API through the bridge" suite (18 tests) was
  skipped and the run still read "41 passed".
- **Inputs in a flex row get `width: 0`** (`.d-join-field input`, `.dg-row input`): an input's default 20-character
  width set the row's minimum, wider than a 390px screen in the Arabic font (Home, Rooms, Diagrams scrolled sideways).
- **Two dead `className="input"` props removed** (history slider, concept tag select): a second `className` on the
  same element replaced them, so nothing changes on screen.
- **Verified:** `pytest -q` 1127 passed; server 59/59; client 105/105; build and lint clean of errors; simulated mixed
  load passes its four checks; headless Chromium over every dashboard section for student and professor (desktop,
  phone, dark, light, Arabic on a phone): no console error, no failed request, no sideways scroll; a class-only upload
  through the Materials screen is indexed and listed. Fake engine only, nothing run on FelCloud.
- **Not changed:** the engine findings of `reports/felcloud-2026-11-11-scenarios.md` (HS-07a, HS-12, UNI-01, UNI-10,
  UNI-02 pacing, the relevance gate on filler words, the derivative exercise after every explain), which need a
  go-ahead; AI answers show LaTeX as typed (`\[ … \]`), no math rendering in `AnswerText`; `nodemon` (dev only) pulls
  a `minimatch` with a moderate advisory.

## Engine v1.5: the scenario findings (2026-11-11, FelCloud slot, working tree; engine change on an explicit go-ahead)

Fixes for the failing cases and findings of `reports/felcloud-2026-11-11-scenarios.md`. Replay on the real engine at
the end of that report.

- **Stopwords include the words a question is wrapped in** ("je ne comprends pas … tu peux expliquer ?", "can you help
  me understand …", `rag/normalize.py`). They name no topic and, counted in the coverage gate, turned an on-topic
  question into "not in the course" (0.32 against 0.4; now 0.59 and, after the page change below, 1.0). "calculer" is
  not one of them: the glossary uses it as a course term.
- **An English question takes the French hits when they cover it better** (`rag/retriever.search_plan`, used by the
  engine and by `scripts.eval_retrieval`): higher best coverage, or equal coverage and a higher top score; a full tie
  stays English. Before, any English hit stopped the search. Arabic keeps the plain order (coverage against a French
  chunk is not comparable). The English definition draft stays a draft: UNI-01 is answered from the French definition.
- **The formula page names its rules** (`data/docs/derivee-formules.fr.md`: "Règle du produit : la dérivée d'un
  produit …", "Règle du quotient : la dérivée d'un quotient …", and the `(uv)' = u'v + uv'` spelling). The section never
  said "dérivée" or "règle", so "comment calculer la dérivée d'un quotient ?" retrieved the definition pages instead.
  Maths unchanged; `data/index/chunks.json` rebuilt. Cross-language hit@3 7/8 → 8/8, the other eval numbers unchanged.
- **check_claim reads a derivative stated in words** (`engine/claims.spoken_derivatives`): "la dérivée de 1/x est
  1/x²" becomes "(1/x)' = 1/x²" and sympy decides it (CONTRADICTED, "Plutôt : (1/x)' = -1/x²"), in French, English
  and Arabic. Only when both runs are math; "la dérivée de x² est positive" is left to the model.
- **QUIZ on a message that asks for no exercise goes to explain** (`router/policy.follow_up(…, message)`, the same rule
  in `FakeEngine`; the bridge and the Node server pass the message). The 3B read "Écris un algorithme …" as QUIZ and
  quiz served a derivative exercise. Without a message the rule does not apply (old callers behave as before).
- **explain prompt v5:** the reply language is repeated as the last line of the user message, and the English rule says
  to translate French material. v4 gave a French body under "Reply in: English".
- **Upload tags:** a concept id that is only part of its title ("produit" of "Dérivée d'un produit") is no longer a
  query of its own, and in a file that never names the topic (no "dérivée" in any language the glossary knows, no
  f'(…) notation) every guessed tag is capped at 0.49. **explain appends no exercise on a tag below
  `INGEST_MIN_TAG_CONFIDENCE`.** Sample files: sorting, arrays, binary search, matrices, determinants, elasticity → 0
  confident tags; cinematics 21, the exo7 derivative chapter 30.
- **Formula checker, three false positives:** worked arithmetic on a named quantity (`detA = (2)(5) - (3)(4)`) is
  skipped, not `ungrounded`; "u is 1" is an assignment, not a false identity (`wrong_math`); `f'(a) = lim h→0
  (f(a + h) − f(a)) / h` is the definition and counts as verified however the limit is written. A wrong step in the
  same line (`10 - 12 = 5`), a bare invented number and a wrong limit point are still reported.
- **prep_plan pacing:** each day aims at its share of what is left and may go up to the pace (11 pages over 4 days at
  60 min: 60, 60, 60, 40; was 40, 40, 40, 100). Early days are now the fuller ones. The server sends `start` with
  `deadline`, both from one clock: the engine's "today" is local and the server's date was UTC, so between midnight
  and 01:00 a 5-day plan was built over 4 days.
- **Verified offline:** `pytest -q` 1153 passed; server 59/59. **On FelCloud (app on the real engine, about 12
  requests, no server 429):** HS-07a CONTRADICTED by sympy; HS-12 "not in the course"; the filler-word question
  explains the product rule with its example; UNI-01 gives the limit definition in English, not degraded; UNI-10
  answers the quotient rule in English, not degraded; UNI-02 gives 60, 40, 40, 40, 40; answers from re-uploaded
  physics and determinant notes carry no exercise and no flag.
- **Not fixed:** UNI-04 (no note on the sign error) and the odd guiding question of HS-06; the quotient answer is
  followed by a product exercise (the bank has no quotient exercise on a published page); "explain the rules of
  football" in English passes the gate through the glossary's "rule"; the determinant answer from the 104-chunk text
  file missed the 2×2 section, and "½(vi+vf)" was extracted as "12(vi+vf)" from the speed article.

## Submission round: security, plans, learning-style diagnostic, report (2026-11-11, working tree; feature layer and docs)

- **Security, small and dependency-free** (`app/server/src/security.js`): every response carries `nosniff`, no
  framing, no referrer and a `default-src 'none'` policy, API answers are `no-store`, `x-powered-by` is off. **An
  upload must be what its type says** (PDF header, zip header for .docx, valid UTF-8 without control bytes for text),
  else 400. **An uploaded file is served inert:** Markdown and text as `text/plain` under a CSP sandbox, Word as a
  download; a PDF gets the strict policy without the sandbox, which stops Chrome's viewer. Already in place and kept:
  the bridge on localhost behind a constant-time token check, hashed room codes, input validation, rate limits.
- **Plans from the business model canvas, enforced on the server** (`app/server/src/plans.js`, mirrored for the screens
  in `app/client/src/lib/plans.js`): Free has no AI on the whiteboard (a request with a `room_id` is 403), no Lumi in
  voice (`user.tier` at `board:join`, the check `canUseLumiVoice` already made), 30 AI requests a day per learner (429
  `plan_cap`; rules tasks and bank practice do not count), one exam in prep, no class material and no answer review.
  Educator, Campus and Enterprise are not limited. `GET /api/plans`. **The plan arrives with the request, like the
  role:** there are no accounts, so this enforces the rule, not who may claim a plan. A request without a plan gets
  `LUMI_DEFAULT_PLAN` (default `campus`), so demos and the existing tests are not capped. Counters are in memory.
  Settings shows the four plans. LMS integration (LTI 1.3) from the canvas is not built.
- **Learning-style diagnostic** (the spec's "diagnostic step"; `app/client/src/lib/learningStyle.js`): four questions the
  first time the companion opens, each option votes for one of six styles (visual, analogy, steps, examples,
  repetition, practice); the winner is sent as `context.style` (the engine already put `Style: …` in the explain prompt
  and in the cache key; the client never set it). Stored in the browser, changeable or retaken in Settings, skippable.
  It does not yet adapt from later answers.
- **Root README rewritten for the submission** (what it does, screenshots in `docs/screenshots/`, the GenAI module, run,
  tests, privacy, declared external services). **Technical report:** `docs/report/lumi-technical-report.{tex,pdf}`,
  6 pages (`pdflatex` twice), with the business-model note.
- **Verified:** `pytest -q` 1153; server 61/61; client 108/108; build clean; in headless Chromium on the preview engine:
  the plan picker, the Free lock in the board's AI tab, the diagnostic and the style in the request body.

## No name to the model, and the privacy text corrected (2026-11-11)

- **Voice history is sent to the model as "Student n", not as the display name** (`app/server/src/voice/lumi.js`): the
  earlier turns were `Name: transcript`, the one place a name reached FelCloud. The room still sees who asked.
- **The report and the README said the display name stays in the browser and that only course text, the question and
  the board reach the service.** Both were wrong: the name is shown to room members and stored with uploads
  (`owner_name`), and the service also receives conversation history, uploaded text an answer uses, and images that are
  read. Both texts now say so.
