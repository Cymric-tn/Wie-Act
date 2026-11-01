# EdNova engine: API contract (v1.3)

This is the contract the feature layer (B: tutor, rooms, prep pages; C: professor tools, ingestion) codes against.
It is generated: edit `docs/ENGINE_API.src.md`, then run `python -m scripts.gen_api_examples --doc`. Every JSON
example below is a file in `docs/examples/`; those marked **real** are what `Engine.run` returned with a scripted
provider (guards, cache, queue, exercise bank and degrade logic are the real code, only the model's words are canned),
those marked **canned** come from `FakeEngine` (see "Building a UI without any LLM").

Every task below is implemented in the real engine (`Engine.run`) and in `FakeEngine`; the examples marked **canned** are
the few outcomes that cannot be produced offline (for instance the 25 s deadline needs 25 real seconds, and a reading of a
photo that needs the real model).

| Task | Class | Cost |
|---|---|---|
| `classify`, `hint`, `explain`, `quiz`, `grade`, `compress` | compact / mid-size | since v1 |
| `ocr_transcribe` | vision | v1.3 |
| `check_claim` | rules, then compact | v1.3 |
| `check_answer` | rules, then compact only for a guiding question | v1.3 |
| `prep_plan` | no model | v1.3 |
| `generate_page` (a helper, not a task) | mid-size via `explain` | v1.3 |
| `add_material`, `remove_material`, `ingest_file` | none (OCR of scans: vision) | v1.3, section 4 |

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
| `degraded`, `degrade_reason` | see section 6. `degrade_reason` is the main reason; `notes.degrade_reasons` lists all |
| `eta_seconds` | when trying again makes sense (`quota_exhausted`, `room_busy`, `service_busy`, `midsize_unavailable`) |
| `mode` | `fast_extractive` (course sentences verbatim, no generated explanation), `bank` (exercise bank), `rules` |
| `followup_available` | a full answer can be asked again later (fast mode) |
| `safe_default`, `repaired` | JSON could not be obtained, `data` is the safe default / needed one repair call |
| `events` | the progress events (section 7), each `{name, t_ms, data}` |
| `latency_ms`, `tokens_in`, `tokens_out` | cost of this run (0 tokens on a cache hit) |
| `route_reason`, `notes` | why this class; task-specific details (sources, guards, ...) |

A UI needs only three branches: `degraded` false (show it), `degraded` true with `text`/`data` (show it with the
reason's wording, section 6), `degraded` true with an `eta_seconds` and nothing to show (show a wait and retry).

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
  `passage` and `board_text` (below).
  * `passage`: the text a learner selected on a prep page ("this confuses me"). It is put in the prompt and **retrieval
    runs on it first** (it is the topic), then on the question; `notes.retrieval_on` says which found the course text.
    The question may be as vague as "explain this".
  * `board_text`: the whiteboard text for "Ask AI". It is put in the prompt as the learner's own notes (it may contain
    mistakes). Retrieval runs on the question; if that names no topic, on the whiteboard text; the coverage gate still
    applies, so an off-course board gets "not in the course".
  * Both are **trimmed to a share of the prompt budget** (`PASSAGE_BUDGET_SHARE`, `BOARD_BUDGET_SHARE`, 25% each; the head
    is kept, cut on a sentence or line, marked `[…]`; event `trimming_input` with `input`, `from_chars`, `to_chars`) so
    the request always fits. Each is part of the **cache key by a hash of the full original text**, so two passages that
    differ only in the trimmed part are still two entries, and the same passage asked by two learners is one.
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
* `data`: `{text, latex, confidence_note}`. `latex` is set when math was detected (the maths lines as one LaTeX string,
  lines joined by `\\`), else `null`. `confidence_note` is empty or says what to double-check, in the learner's
  language: the model gives no confidence, so the note reports only what can be seen (a symbol it could not read, brackets
  that do not close, no text, almost no text). `text` is also in `Result.text`. `notes.image` has the hash, the size sent
  and `resized`. The text is transcribed, never corrected: a wrong line stays wrong.
* priority: INTERACTIVE by default. Cache key = hash of the (resized) image.
* cost: 1 vision request (~14 s). It shares the chat window and the 2 slots with everything else until the organizers
  say otherwise.
* degrade: `low_confidence` (text still returned and shown with the note; not cached, so a retry reads again),
  `invalid_image` (not a readable image, a URL, too big a file: `data` and `text` null, nothing sent),
  `quota_exhausted`, `service_busy`, `superseded`, `room_busy`, `request_too_large` (the image alone exceeds the input
  budget).
* events: [`resizing_image`: only when the image was larger than 1 MP; it comes first because the cache key needs the
  prepared image], `checking_cache`, [`cache_hit`] or [`queued`, `generating` (`model_class: vision`)].

{{example ocr_transcribe.ok}}

{{example ocr_transcribe.low_confidence}}

{{example ocr_transcribe.invalid_image}}

### 3.8 `check_claim` (v1.3)

Is this statement true according to the course? For "check my notes" and whiteboard claims.

* inputs: `claim` (required). `context.language` sets the language of the correction (detected when absent: a bare
  formula has no language, pass it).
* `data`: `{verdict: SUPPORTED|CONTRADICTED|PARTLY|NOT_IN_COURSE, correction (at most 25 words, empty when SUPPORTED or
  NOT_IN_COURSE), source_chunk_id (the course chunk it was checked against, null when computed or not in the course),
  checked_by: sympy|rules|llm}`.
* How it decides, in this order:
  1. **sympy.** A claim made of formulas is checked by computation (derivatives in `(x²)' = 2x`, `d/dx`, `f(x) = ...,
     f'(x) = ...`, numeric identities). Verified and nothing else claimed: `SUPPORTED`. Refuted: `CONTRADICTED` with
     the correct form as the correction (`Plutôt : (x³)' = 3x²`). Part verified and part refuted: `PARTLY`. Only
     connectives (`donc`, `so`, `نعلم`) may surround the formulas: a claim with any other word
     (`(x²)' = 2x et la dérivée d'une constante est 1`) is not settled by sympy and goes on, with the verified part
     marked as verified for the model. `checked_by: sympy`, 0 requests, event `answered_by_rules`.
  2. **The course.** Retrieval with the coverage gate (visibility rules of section 4). Nothing relevant:
     `NOT_IN_COURSE`, `checked_by: rules`, 0 requests, event `no_relevant_context`.
  3. **One compact JSON call** with the claim and the best chunk; the model may answer any of the four verdicts. The
     correction is cut to 25 words; a correction containing a formula sympy refutes is dropped (`notes.correction_dropped`).
     One repair call if the JSON is invalid; if it still is, `PARTLY` with `checked_by: llm`, `safe_default=true`,
     `degraded=true`, `degrade_reason: json_invalid` (never cached).
* cache key: the normalized claim, the chunk id and the language; learners share entries.
* cost: 0 (sympy, `NOT_IN_COURSE`, cache hit) or 1 compact (~3 s), +1 on a repair.
* events: `answered_by_rules`; or `retrieving`, [`expanding_query`], [`no_relevant_context`] or `checking_cache`, then
  [`cache_hit`] or [`queued`, `generating`, [`repairing_json`, `using_safe_default`]].
* degrade: `json_invalid`, `quota_exhausted`, `service_busy`, `superseded`, `room_busy` (with the safe default as `data`).

Settled by computation, refuted (real):

{{example check_claim.CONTRADICTED}}

Verified by computation (real):

{{example check_claim.SUPPORTED}}

Half right, half refuted by computation (real):

{{example check_claim.PARTLY}}

Judged by the model against the course (real):

{{example check_claim.CONTRADICTED_llm}}

Not in the course (real, 0 requests):

{{example check_claim.NOT_IN_COURSE}}

The model failed twice (real; safe default):

{{example check_claim.llm_failed}}

### 3.9 `check_answer` (v1.3)

Checks a learner's worked solution line by line.

* inputs: `problem`, `steps` (a list of lines, or one multi-line string; required); `solution` (optional); `exercise_id`
  (optional: a bank exercise, whose stored common mistakes give the feedback and whose answers are compared as text, so
  variations and extrema work). An unknown `exercise_id` or no non-empty line is a caller bug (`ValueError`).
* `data`: `{steps: [{index (0-based), verdict: OK|WRONG|UNCHECKED, note}], first_wrong (index or null),
  final_verdict: CORRECT|INCORRECT|PARTIAL|UNKNOWN, feedback, graded_by: sympy|rules|llm}`. `note` is in the learner's
  language and empty for `OK`.
* **The lines.** A line goes through the safe sympy parser (digits, `x`, `+ - * / ( ) .` and `sqrt`; nothing else ever
  reaches sympy). A line it cannot parse (a sentence, `u = x, v = x + 3`, another variable) is `UNCHECKED`, **never
  `WRONG`**. For a derivative problem (read from the bank exercise, or from the text: `Dérive f(x) = ...`) a line is `OK`
  when it equals the true derivative, the function itself, or the value asked for (`f'(a)`, `f(a)`, the tangent), or is an
  algebraic rewrite of an `OK` line before it. A line's label says what it claims to be: `f'(x) = ...` must be the
  derivative, `f(3) = ...` the value of f at 3, `y = ...` the tangent. A chain of equalities must agree with itself. The
  first failing line is `WRONG` (`first_wrong`). A problem that is not a derivative problem has every line `UNCHECKED`.
* **Feedback**, for the first wrong line: (1) the exercise's stored common mistake that equals the line (`graded_by:
  rules`); (2) buggy-rule diagnosis, precomputed per function: `PRODUCT_AS_PRODUCT`, `POW_NO_DECREMENT`, `CHAIN_NO_INNER`,
  `CONST_KEPT` (`graded_by: rules`, `notes.diagnosis: rule:NAME`); (3) otherwise one compact call writes a guiding
  question (`graded_by: llm`). The question is guarded so it never states the answer (the hint guard plus the true
  derivative written out): a leaking question is replaced by a fixed one (`notes.diagnosis: template`, event `guarded`).
  Darija and Arabizi always get the fixed question (compact does not write Tunisian reliably).
* **The final answer** (the last line) goes through the `grade` task, after a free comparison with the stored or stated
  solution and the exercise's stored answers: `final_verdict`. Its feedback is used when no wrong line explained
  anything (a right derivative that is not the answer asked for). If the grading is refused, the answer is `degraded`
  with `eta_seconds` and `final_verdict: UNKNOWN`.
* cost: 0 (rules, stored answers, sympy grade) or 1 compact (guiding question) + 1 (a text answer graded by the model).
  `mode: rules` when no model was used. events: `parsing_steps`, `checking_steps`, [`queued`, `generating`], [`guarded`].
* degrade: `quota_exhausted`, `service_busy` (from the model calls), `superseded`, `room_busy`. Helper runs (grade) keep the
  learner's ids but not the `room_id`: the room is held by this call.

All lines fine (real, 0 requests):

{{example check_answer.ok}}

A stored common mistake, caught on the right line (real, 0 requests):

{{example check_answer.wrong_step}}

A buggy rule recognised (real, 0 requests):

{{example check_answer.rule}}

A wrong line nothing explains: one compact guiding question (real):

{{example check_answer.llm_feedback}}

Nothing readable (real, 0 requests):

{{example check_answer.unchecked}}

### 3.10 `prep_plan` (v1.3)

A study plan for an assessment. No model, no request.

* inputs: `scope` (`{unit_ids, concept_ids, material_ids}`, at least one), `deadline` (`YYYY-MM-DD`),
  `pace_min_per_day`, and optionally `mastery` (`{concept_id: p}`, missing = 0), `language`, `level` (also read from the
  context), `start` (`YYYY-MM-DD`, default today). The course is `context.course_id` (the only course when there is one).
  A bad date, a pace under 1, an empty scope or an unknown course is a caller bug (`ValueError`).
* `data`: `{focus_points: [{concept_id, day_index, reason, reason_code, page_kind: learn|review, minutes}],
  assessment_exercise_ids}`. `reason` is a sentence in the learner's language, `reason_code` is `prerequisite`,
  `low_mastery`, `learn` or `review`, `minutes` the planned study time. `notes` has `days`, `minutes_needed`,
  `minutes_available` and what could not be resolved (`unknown_concepts`, `unknown_units`, `unknown_materials`,
  `skipped_draft`).
* Rules. **Prerequisites first**, then **lowest mastery**, then the course's order: a prerequisite that is in the scope and
  still to be learned always comes before what needs it; a mastered one does not block. A concept with `p >= 0.85` gets a
  **review page only**, after the pages to learn. Pages are **spread evenly** over the study days (`start` to the day
  before `deadline`), never over `pace_min_per_day` in a day (a learn page is planned at 20 minutes, a review page at 8:
  `PREP_LEARN_MINUTES`, `PREP_REVIEW_MINUTES`). If the days cannot hold the work, the last day takes the rest and
  `notes.over_capacity` / `notes.overflow_minutes` say so; a deadline that is today or past puts everything on day 0 with
  `notes.deadline_not_in_future`. Draft concepts (no course text or no exercise yet) are left out.
  `assessment_exercise_ids`: one bank exercise per concept in plan order, harder when mastery is higher (at most 10).
* `material_ids` are uploaded files: a chunk id up to the `#` (`professor/prof-7/polycopie`); their concepts are the
  tags of their chunks.
* cost: 0. `mode: rules`, event `answered_by_rules`. Priority does not matter (nothing is queued).

{{example prep_plan.ok}}

### 3.11 `engine.generate_page(plan, focus_point, context)` (v1.3)

Builds one prep page for a focus point. `plan` is the `prep_plan` Result (or its data, or `{}`): only `focus_point` is read.
Put the learner's `mastery` for that concept on the focus point to pick the exercise level (default: 0.9 for a review
page, 0.2 for `low_mastery`, else 0.5).

* The explanation is the `explain` task at **BACKGROUND** for the question `C'est quoi : <concept title> ?` (in the
  context's language). It goes through the **same cache as the tutor**: if the tutor already explained that question, the
  page costs no request, and a professor can reject it with `cache.invalidate(page["explain_request_id"])`.
* A **worked example** from the bank: the concept's easiest exercise, its graded hints as the steps, and the solution.
* **Three mini-quiz items**: other bank exercises of the concept, then of its prerequisites, then deterministic variants
  (the function times 2, 3, ...; the solution computed with sympy; `source: "variant"` with a `solution`). A text-only
  concept (variations) has no variants, so it may have fewer than three items rather than made-up ones. The
  explanation's own practice exercise is never one of the page's.
* Returns a dict (not a `Result`): `{concept_id, page_kind, title, language, explanation, explanation_sections
  ({body, opener, exercise}), explain_request_id, from_cache, degraded, degrade_reason, eta_seconds, worked_example, quiz}`.
  A refused or degraded explanation comes back marked (`degraded`, `degrade_reason`, `eta_seconds`) and the exercises are
  still there; it never raises a provider error.
* cost: 1 mid-size request on a cache miss, 0 on a hit. It is BACKGROUND: it waits for the reserve and for free slots, and
  mid-size holds one slot, so generate pages **one at a time**, ahead of when they are read.

{{example generate_page.ok}}

## 4. Course material (professor and learner uploads)

```python
res = await engine.ingest_file("polycopie.pdf", course_id="analyse", unit_id="derivees", owner="prof-7",
                               visibility="class", source="professor", class_id="C1", add=True)
await engine.add_material(chunks)                       # put prepared chunks in the live index
await engine.remove_material(source="polycopie.pdf", course_id="analyse", owner="prof-7")   # or chunk ids
```

* `visibility`: `public` (everyone in the course), `class` (that `class_id` only), `private` (the owner only, the
  `learner_id`). `source`: `professor` (its chunks are boosted in retrieval, `SOURCE_BOOST_PROFESSOR`), `learner` or
  `course`. A viewer without a `learner_id` or `class_id` never sees private or class material. Retrieval filters by the
  `course_id`, `class_id` and `learner_id` of the `context` of the asking `run`.
* `ingest_file` reads a PDF (PyMuPDF), a DOCX or a Markdown file, splits it by headings into chunks of about
  `CHUNK_MAX_TOKENS`, and tags each chunk with a concept and a **confidence** (a low one is for a professor to confirm:
  `res.low_confidence`). Pages with no extractable text are `needs_ocr`; with `ocr=True` (default) up to 20 per file are
  read through `ocr_transcribe` at BACKGROUND, one at a time, and the rest stays in `res.needs_ocr` and `res.ocr["pending"]`
  (call again later: transcriptions are cached by image, so only the unread pages cost). A page read with visible problems
  keeps its text but its chunks have confidence under 0.5. No model is used otherwise.
* `res.chunks`, `res.warnings`, `res.pages`, `res.needs_ocr`, `res.ocr` (`transcribed`, `failed`, `pending`,
  `low_confidence`, `flagged`, `cap`). `add=True` also puts the chunks in the live index; either way chunks added through the
  engine are saved per course (`RAG_COURSE_INDEX_DIR`) and survive a restart. Chunk ids are
  `<source>/<owner>/<file>#<heading>`: ingesting the same file again replaces its chunks.

## 5. Cache administration

`engine.cache.invalidate(request_id)` removes the cache entries behind that request's answer, whether the request
generated the entry or was served it, and returns how many entries it removed (0: unknown id, expired, or nothing was
cached because the answer was degraded). A request id names one generation of an entry: after an entry is
regenerated, older ids no longer reach it. Use it when a professor rejects a flagged answer; the next ask
regenerates.

## 6. Degrade reasons

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
| `low_confidence` | `ocr_transcribe`: text returned but with visible reading problems | show it with `confidence_note`, ask the learner to check |
| `invalid_image` | `ocr_transcribe`: the image could not be read | ask for another photo |

## 7. Events

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
| `guarded`, `output_trimmed`, `output_truncated`, `input_truncated` | | a guard acted on the text/prompt |
| `trimming_input` | `input`, `from_chars`, `to_chars` (explain) or `estimated_tokens`, `budget` (retry layer) | an input was cut to fit the budget |
| `answered_by_rules` | | no model needed |
| `picking_exercise` | | quiz |
| `no_relevant_context` | | the course does not cover it |
| `unavailable` | `reason`, `eta_s`, `error` | final event of a non-retryable degrade (`superseded`, `request_too_large`) |
| `room_busy` | `room_id`, `eta_s` | refused: the room already has a request running |
| `resizing_image`, `parsing_steps`, `checking_steps` | | v1.3 tasks |

New event names may be added; ignore names you do not know.

## 8. Building a UI without any LLM

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

## 9. Platform limits that shape behaviour

20 requests per minute and 2 in flight for the whole team key (not per learner); embeddings are a separate quota, and
FelCloud has no embedding model, so retrieval is lexical. Expect 2-5 s for compact tasks and 8-15 s for mid-size;
the engine never makes a learner wait more than 25 s for an explanation. With a class of 30 learners the quota is
the bottleneck: caching, rules-first tasks and `superseded` exist to protect it.
