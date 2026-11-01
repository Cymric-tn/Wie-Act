# EdNova engine: API contract (v1.3)

This is the contract the feature layer (B: tutor, rooms, prep pages; C: professor tools, ingestion) codes against.
It is generated: edit `docs/ENGINE_API.src.md`, then run `python -m scripts.gen_api_examples --doc`. Every JSON
example below is a file in `docs/examples/`; those marked **real** are what `Engine.run` returned with a scripted
provider (guards, cache, queue, exercise bank and degrade logic are the real code, only the model's words are canned),
those marked **canned** come from `FakeEngine` (see "Building a UI without any LLM").

Status of each task in the engine today is in the table below; the contract is the same either way and `FakeEngine`
implements all of it.

| Task | Class | Status |
|---|---|---|
| `classify`, `hint`, `explain`, `quiz`, `grade`, `compress` | compact / mid-size | implemented since v1 |
| `ocr_transcribe` | vision | v1.3 (phase 3) |
| `check_claim` | compact, rules first | v1.3 (phase 3) |
| `check_answer` | rules first, compact only for a guiding question | v1.3 (phase 3) |
| `prep_plan` | no model | v1.3 (phase 3) |
| `generate_page` (helper, not a task) | mid-size via `explain` | v1.3 (phase 3) |

## 1. The call

```python
result = await engine.run(task, inputs, context, priority, on_event=None)
```

* `task`: one of the names above. An unknown task or a missing required input raises `ValueError` before anything is
  sent (a caller bug). **`run` never raises anything else**: a busy, out-of-quota, too-large or broken provider comes
  back as a well-formed `Result` with `degraded=True` and a reason.
* `inputs`: task-specific, below.
* `context` (all optional): `language` (`FRENCH | ENGLISH | ARABIC | DARIJA | ARABIZI`, detected when absent),
  `level`, `style`, `topic`, `escalate` (bool, force mid-size where the table allows it), `difficulty`, `confusion`,
  `turn_id`, and the identity ids `learner_id`, `room_id`, `course_id`, `class_id` (section 2).
* `priority`: `Priority.INTERACTIVE` (a person is waiting) or `Priority.BACKGROUND` (prep pages, ingestion).
* `on_event`: called with each `Event` as it happens, for staged progress (no token streaming exists).

### Result

| Field | Meaning |
|---|---|
| `task`, `request_id` | the task; one id per `run`, what a professor's `cache.invalidate` takes |
| `text` | the text answer (hint, explain, quiz) or `null` |
| `data` | the validated JSON answer (classify, grade, check_*, ocr, prep_plan) or extra structure (explain: `body`, `opener`, `exercise`) |
| `model_class_used` | `compact`, `midsize`, `vision`, or `null` when rules answered |
| `model` | provider model name or `null` |
| `from_cache` | served from the exact cache (0 provider requests) |
| `degraded`, `degrade_reason` | see section 5. `degrade_reason` is the main reason; `notes.degrade_reasons` lists all |
| `eta_seconds` | when trying again makes sense (`quota_exhausted`, `room_busy`, `service_busy`, `midsize_unavailable`) |
| `mode` | `fast_extractive` (course sentences verbatim, no generated explanation), `bank` (exercise bank), `rules` |
| `followup_available` | a full answer can be asked again later (fast mode) |
| `safe_default`, `repaired` | JSON could not be obtained, `data` is the safe default / needed one repair call |
| `events` | the progress events (section 6), each `{name, t_ms, data}` |
| `latency_ms`, `tokens_in`, `tokens_out` | cost of this run (0 tokens on a cache hit) |
| `route_reason`, `notes` | why this class; task-specific details (sources, guards, ...) |

A UI needs only three branches: `degraded` false (show it), `degraded` true with `text`/`data` (show it with the
reason's wording, section 5), `degraded` true with an `eta_seconds` and nothing to show (show a wait and retry).

## 2. Identity, fairness, reserve

* **Ids.** `learner_id`, `room_id`, `course_id`, `class_id` in `context` are copied into every metrics record of the
  run (ids only: never names). They are **not** part of any cache key, so two learners asking the same thing share
  one answer. `course_id` and `class_id` and `learner_id` also scope retrieval (phase 2).
* **One waiting request per learner.** A learner has at most one INTERACTIVE request waiting in the queue. If they
  send a newer one, the older one returns at once, degraded `superseded`, nothing sent. A request already running is
  not touched. BACKGROUND work is not limited. Debounce in the UI anyway: the older answer is lost.
* **One request per room.** At most one `run` per `room_id` is in flight. A second one returns at once with
  `degrade_reason="room_busy"` and `eta_seconds` (the expected remaining time of the first), `data` and `text` null,
  nothing sent. Disable the room's AI button while a request is running and use `eta_seconds` for the message.
* **Background reserve.** A BACKGROUND chat request waits while fewer than `BACKGROUND_RESERVE` (default 6) sends
  remain in the 20-per-minute window, so a prep plan or an ingest never uses the window a learner is about to need.
  The wait is announced once with the event `waiting_reserve` (`remaining`, `reserve`, `eta_s`). A BACKGROUND caller
  must be written for a result that takes minutes; it never loses its place.

### Examples: the three refusals

`superseded` (the learner sent a newer question while this one waited):

{{example classify.superseded}}

`room_busy`:

{{example classify.room_busy}}

`quota_exhausted` (the team's 20 requests per minute are used; the provider states a 60 s delay and an INTERACTIVE
request does not wait more than 30 s, so it is answered degraded with the remaining delay as `eta_seconds`; a BACKGROUND
request waits up to 65 s and retries by itself):

{{example classify.quota_exhausted}}

## 3. Tasks

Request cost = provider requests spent. The window is **20 per minute** for the team key (sliding), at most
**2 in flight**; mid-size holds at most 1 slot. A cache hit, a rules answer and a `NOT_IN_COURSE` cost 0.

### 3.1 `classify`

Intent, language, confusion and complexity of a learner message, in one compact JSON call.

* inputs: `message` (required).
* `data`: `{intent: EXPLAIN|HINT|QUIZ|ANSWER_ATTEMPT|DIAGNOSTIC|OFF_TOPIC|CHITCHAT, language: DARIJA|ARABIZI|FRENCH|ARABIC|ENGLISH, confusion: LOW|MEDIUM|HIGH, complexity: 1..5, confidence: 0..1}`.
* cost: 0 (greeting or thanks by rules; cache hit), else 1 (+1 if the JSON needed the repair call). Compact, ~2.6 s.
* degrade: when JSON cannot be obtained after the repair call, `data` is a safe default (`safe_default=true`, `repaired=true`,
  not `degraded`, confidence 0.3, never cached); `quota_exhausted`,
  `service_busy`, `superseded`, `room_busy`.
* events: `checking_cache`, [`cache_hit`] or [`queued`, `generating`, [`repairing_json`, ..., `using_safe_default`]];
  `answered_by_rules` for greetings.

{{example classify.ok}}

Served from the cache (identical message, any learner):

{{example classify.cache_hit}}

Answered by rules (a greeting; no model):

{{example classify.rules}}

JSON could not be obtained (two garbled replies): safe default, `safe_default=true`:

{{example classify.safe_default}}

### 3.2 `hint`

A guiding question that never states the answer.

* inputs: `problem` (required); `attempt`; `solution` (only used to guard against leaking it); `stored_hints` (list,
  graded, most general first) with `hint_level` (1-based).
* `text`: the hint. With `stored_hints` the hint comes from the list: 0 requests, event `answered_by_rules`,
  `notes.hint_level`, `notes.hints_total`.
* cost: 0 (stored hint, cache hit) or 1 compact (~3.8 s).
* degrade: `hint_guard:answer_leak | too_long | language_drift | degenerate` (the text is then a safe fallback),
  `quota_exhausted`, `service_busy`, `superseded`, `room_busy`.

{{example hint.ok}}

Stored hint, level 2 (0 requests):

{{example hint.stored}}

### 3.3 `explain`

A grounded explanation of a course concept. Mid-size model, retrieval over the course (BM25 + glossary, no model).

* inputs: `question` (required); `history` (list of `{role, content}`); `exclude_exercises` (ids not to propose again);
  `passage` (phase 3: text the learner selected on a prep page); `board_text` (phase 3: text from the whiteboard).
  `passage` and `board_text` are part of the cache key (by hash); the other ids are not.
* `text`: `[opener] + body + "À toi : <exercise>"`. `data`: `{body, opener, exercise}` so a UI can lay them out.
  `notes.sources`: chunk ids the answer is grounded in. The body's formulas are checked (sympy) against the course.
* cost: 1 mid-size (~9.5 s on FelCloud) on a miss; 0 on a hit. Fast mode adds at most 1 compact line.
* degrade: **fast mode** (`mode="fast_extractive"`, `followup_available=true`): the learner gets up to 5 course
  sentences verbatim plus the exercise, with reason `midsize_saturated`, `midsize_unavailable`, `quota_low`,
  `breaker_open` or **`deadline`** (no body after 25 s: the learner is answered, the mid-size generation continues
  into the cache and the next ask is a hit). Also `ungrounded_formula`, `wrong_math`, `explain_guard:*`,
  `input_truncated`, `quota_exhausted`, `superseded`, `room_busy`. A question the course does not cover returns
  `text` saying so, `0` requests, event `no_relevant_context`.
* events: `retrieving`, `expanding_query`, `checking_cache`, then [`cache_hit`] or [`partial_result` (course
  excerpts, show them while waiting), `queued`, `generating`], fast mode adds `falling_back_to_compact`,
  `fast_extractive`, `deadline`.

{{example explain.ok}}

Cache hit (same question and same retrieved chunks):

{{example explain.cache_hit}}

Fast mode, mid-size saturated:

{{example explain.fast_extractive}}

Fast mode, the 25 s deadline (canned: needs 25 real seconds):

{{example explain.deadline}}

Not in the course (0 requests):

{{example explain.not_in_course}}

### 3.4 `quiz`

An exercise from the bank. No model.

* inputs: `message`; `concept`; `difficulty` (1-3); `exclude` (ids already served); `diagnostic` (true: three exercises of
  increasing level); `intro: "llm"` (opt-in one compact line).
* `data`: `{exercises: [{id, concept, level, statement, expression}], requested_concepts, diagnostic, intro}`; `text`
  is intro + statements. `mode="bank"`.
* cost: 0.

{{example quiz.ok}}

{{example quiz.diagnostic}}

### 3.5 `grade`

Grades an answer attempt against a stored solution. Sympy first, compact JSON only when the answer is not an expression.

* inputs: `problem`, `solution`, `attempt` (all required); `common_mistakes` (`[{expr, feedback}]`).
* `data`: `{verdict: CORRECT|INCORRECT|PARTIAL|UNKNOWN, mistake, feedback, graded_by: sympy|llm}`. A wrong answer equal
  to a listed mistake returns that mistake's feedback. The feedback never contains the solution.
* cost: 0 (sympy) or 1 compact.

{{example grade.correct}}

{{example grade.incorrect}}

Text answer, graded by the model:

{{example grade.llm}}

### 3.6 `compress`

Internal (explain calls it when the retrieved chunks exceed the input budget); documented for completeness.
inputs `question`, `chunks`; `data` `{method: llm|lexical, sentences: [{chunk_id, text}]}`. 0-1 compact request.

### 3.7 `ocr_transcribe` (v1.3)

Transcribes a photo of handwritten or printed work. **It never grades**: feed the text to `check_answer` or `grade`.

* inputs: `image` (required): a `data:image/...;base64,...` URL or a base64 string; JPEG/PNG. Resized to at most 1
  megapixel before sending (event `resizing_image`). No URL fetching.
* `data`: `{text, latex, confidence_note}`. `latex` is set when math was detected, else `null`. `confidence_note` is
  empty or one sentence saying what to double-check. `text` is also in `Result.text`.
* priority: INTERACTIVE by default. Cache key = hash of the (resized) image.
* cost: 1 vision request (~14 s). It shares the chat window and the 2 slots with everything else until the organizers
  say otherwise.
* degrade: `low_confidence` (text still returned, ask the learner to check it), `quota_exhausted`, `service_busy`,
  `superseded`, `room_busy`, `request_too_large` (image too big after resizing).
* events: `checking_cache`, `resizing_image`, `queued`, `generating` (`model_class: vision`).

{{example ocr_transcribe.ok}}

{{example ocr_transcribe.low_confidence}}

### 3.8 `check_claim` (v1.3)

Is this statement true according to the course? For "check my notes" and whiteboard claims.

* inputs: `claim` (required).
* `data`: `{verdict: SUPPORTED|CONTRADICTED|PARTLY|NOT_IN_COURSE, correction (at most 25 words, empty when SUPPORTED),
  source_chunk_id, checked_by: sympy|rules|llm}`.
* How it decides: a claim with a formula goes through the formula checker first (`sympy`: verified = `SUPPORTED`,
  refuted = `CONTRADICTED` with the correct form); otherwise retrieval with the coverage gate: nothing relevant =
  `NOT_IN_COURSE` (`checked_by: rules`, 0 requests); otherwise one compact JSON call with the claim and the best
  chunk. If the JSON cannot be obtained the answer is `PARTLY` with `checked_by: llm`, `safe_default=true`,
  `degraded=true`. Cache key = normalized claim + chunk id.
* cost: 0 (sympy, `NOT_IN_COURSE`, cache hit) or 1 compact (~3 s).
* events: `checking_formula`, `retrieving`, `checking_cache`, `queued`, `generating`, `no_relevant_context`.

{{example check_claim.SUPPORTED}}

{{example check_claim.CONTRADICTED}}

{{example check_claim.PARTLY}}

{{example check_claim.NOT_IN_COURSE}}

The model failed twice (safe default):

{{example check_claim.llm_failed}}

### 3.9 `check_answer` (v1.3)

Checks a learner's worked solution line by line.

* inputs: `problem`, `steps` (a list of lines, required); `solution`; `exercise_id` (uses the bank's common mistakes).
* `data`: `{steps: [{index (0-based), verdict: OK|WRONG|UNCHECKED, note}], first_wrong (index or null),
  final_verdict: CORRECT|INCORRECT|UNKNOWN, feedback, graded_by: sympy|rules|llm}`.
* A line that cannot be parsed is `UNCHECKED`, never `WRONG`. For derivative problems each line must equal the true
  derivative or be an algebraic rewrite of the previous line; the first failing line is `WRONG`. Feedback comes from
  the exercise's common mistakes, then from buggy-rule diagnosis (`PRODUCT_AS_PRODUCT`, `POW_NO_DECREMENT`,
  `CHAIN_NO_INNER`, `CONST_KEPT`), else one compact call writes a guiding question (never the answer). The final
  answer goes through `grade`.
* cost: 0 (rules) or 1 compact. events: `parsing_steps`, `checking_steps`, [`queued`, `generating`].

{{example check_answer.ok}}

{{example check_answer.wrong_step}}

{{example check_answer.unchecked}}

### 3.10 `prep_plan` (v1.3)

A study plan for an assessment. No model.

* inputs: `scope` (`{unit_ids, concept_ids, material_ids}`), `deadline` (`YYYY-MM-DD`), `pace_min_per_day`,
  `mastery` (`{concept_id: p}`), `language`, `level`.
* `data`: `{focus_points: [{concept_id, day_index, reason, page_kind: learn|review}], assessment_exercise_ids}`.
  Prerequisites first, then lowest mastery; spread over the days until the deadline at the given pace; a concept
  with `p >= 0.85` gets a `review` page only.
* cost: 0. `mode="rules"`.

{{example prep_plan.ok}}

### 3.11 `engine.generate_page(plan, focus_point, context)` (v1.3)

Builds one prep page for a focus point: `explain` at BACKGROUND (shares the tutor's cache), a worked example from
the bank (or a generated variant) and 3 mini-quiz items from the bank. Returns
`{concept_id, page_kind, explanation, from_cache, degraded, degrade_reason, worked_example, quiz}`. Cost: 1 mid-size
request on a miss. Because it is BACKGROUND it waits for the reserve and for free slots; generate pages one at a
time, ahead of when they are read.

## 4. Cache administration

`engine.cache.invalidate(request_id)` removes the cache entries behind that request's answer, whether the request
generated the entry or was served it, and returns how many entries it removed (0: unknown id, expired, or nothing was
cached because the answer was degraded). A request id names one generation of an entry: after an entry is
regenerated, older ids no longer reach it. Use it when a professor rejects a flagged answer; the next ask
regenerates.

## 5. Degrade reasons

`degraded=true` always comes with a reason. What each means and what a UI should do:

| `degrade_reason` | Meaning | UI |
|---|---|---|
| `midsize_saturated` | mid-size busy after retries | fast mode text is in the result; offer "ask again later" |
| `midsize_unavailable` | mid-size timed out or is down (breaker); `eta_seconds` = when it is probed again | same |
| `quota_low` / `breaker_open` | we chose not to use mid-size (quota almost used / class down) | same |
| `deadline` | explain had no body after 25 s; generation continues into the cache | fast mode text now; asking again soon is a cache hit |
| `quota_exhausted` | the team's per-minute quota is used; `eta_seconds` = remaining delay | show a wait, retry after `eta_seconds` |
| `service_busy` / `service_unavailable` | provider refused or is unreachable; `eta_seconds` | show a wait and retry |
| `superseded` | the same learner sent a newer request | drop this result silently |
| `room_busy` | another AI request of the room is running; `eta_seconds` | disable the button, retry after the ETA |
| `request_too_large` / `provider_error` | not retryable as is | show a generic error |
| `input_truncated` | the provider cut the prompt: the answer is unreliable (never cached) | show with a caution |
| `ungrounded_formula` / `wrong_math` | a formula in the answer is not in the course / is wrong | course extracts are served instead |
| `explain_guard:*`, `hint_guard:*` | answer failed a text guard (degenerate, loop, language drift, answer leak, too long) | safe fallback text is in the result |
| `json_invalid` | `check_claim` only: no valid JSON after the repair call (`safe_default=true`, verdict `PARTLY`) | treat the verdict as unknown. (`classify` and `grade` set `safe_default=true` without degrading.) |
| `low_confidence` | `ocr_transcribe`: text returned but unreliable | ask the learner to check it |

## 6. Events

Each `Event` is `{name, t_ms, data}`; `t_ms` is since `run` started. Names and when they occur:

| Event | Data | When |
|---|---|---|
| `checking_cache` | | before the cache lookup |
| `cache_hit` | | served from the cache |
| `negative_cache_hit` | `reasons` | the same request failed a check < 10 min ago: fast mode, 0 requests |
| `retrieving`, `expanding_query` | `glossary` | grounded tasks: course search (no model) |
| `partial_result` | `excerpts` | explain: course sentences to show while the answer is generated |
| `compressing` | | retrieved text over the input budget |
| `queued` | `position`, `eta_s` | waiting for a window/slot. Also the final event of a retryable degrade (`reason`, `eta_s`) |
| `waiting_reserve` | `remaining`, `reserve`, `eta_s` | BACKGROUND held back to keep sends free for learners |
| `waiting_identical_request` | | the same request is already being generated: waiting for it |
| `generating` | `attempt`, `model_class` | a provider request is sent |
| `retry_after_interval`, `backoff`, `wait_stated_delay`, `wait_slot`, `fix_request` | `error`, `seconds`, `attempt` | what the retry layer did with a provider refusal |
| `gave_up` | `error`, `retry_after_s` | the request failed for good (the degrade follows) |
| `falling_back_to_compact`, `breaker_open` | `reason`, `retry_in_s` | explain lost mid-size |
| `fast_extractive` | `reason` | fast mode is being built |
| `deadline` | `deadline_s`, `generation_continues` | explain passed 25 s |
| `repairing_json`, `using_safe_default` | `error` | JSON task problems |
| `guarded`, `output_trimmed`, `output_truncated`, `input_truncated`, `trimming_input` | | a guard acted on the text/prompt |
| `answered_by_rules` | | no model needed |
| `picking_exercise` | | quiz |
| `no_relevant_context` | | the course does not cover it |
| `unavailable` | `reason`, `eta_s`, `error` | final event of a non-retryable degrade (`superseded`, `request_too_large`) |
| `room_busy` | `room_id`, `eta_s` | refused: the room already has a request running |
| `resizing_image`, `checking_formula`, `parsing_steps`, `checking_steps`, `planning` | | v1.3 tasks |

New event names may be added; ignore names you do not know.

## 7. Building a UI without any LLM

```python
from engine.fake import FakeEngine
fake = FakeEngine(mode="normal", speed=0.2)          # normal | slow | degraded ; speed 1.0 = real time, 0 = instant
r = await fake.run("explain", {"question": "..."}, {"language": "FRENCH"}, on_event=show)
r = await fake.run("check_claim", {"claim": "(x²)' = x"}, {"fake_outcome": "CONTRADICTED"})
```

`FakeEngine.run` has `Engine.run`'s signature and returns `Result`s with the events and timings of the real engine
(`t_ms` is engine time whatever `speed` is). Modes: `normal` = each task's happy outcome; `slow` = 3x slower, explain
misses the 25 s deadline; `degraded` = each task's typical degraded outcome. `context["fake_outcome"]` forces one
state for one call; `FakeEngine.OUTCOMES` lists them per task (`quota_exhausted`, `superseded` and `room_busy` work
for every task). `room_busy` is real: a second concurrent run with the same `room_id` is refused. `fake.cache.invalidate`
and `fake.generate_page` exist.

## 8. Platform limits that shape behaviour

20 requests per minute and 2 in flight for the whole team key (not per learner); embeddings are a separate quota, and
FelCloud has no embedding model, so retrieval is lexical. Expect 2-5 s for compact tasks and 8-15 s for mid-size;
the engine never makes a learner wait more than 25 s for an explanation. With a class of 30 learners the quota is
the bottleneck: caching, rules-first tasks and `superseded` exist to protect it.
