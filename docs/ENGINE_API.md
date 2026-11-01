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
| HTTP + WebSocket service (`services/engine_server.py`) | none (a front for `Engine.run`) | section 10 |

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

*classify.superseded: real engine output*

```json
{
  "task": "classify",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "intent": "EXPLAIN",
    "language": "FRENCH",
    "confusion": "MEDIUM",
    "complexity": 2,
    "confidence": 0.0
  },
  "model_class_used": "compact",
  "model": null,
  "from_cache": false,
  "degraded": true,
  "degrade_reason": "superseded",
  "eta_seconds": null,
  "events": [
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 8.0
      }
    },
    {
      "name": "gave_up",
      "t_ms": 0.0,
      "data": {
        "error": "superseded",
        "retry_after_s": null
      }
    },
    {
      "name": "unavailable",
      "t_ms": 0.0,
      "data": {
        "reason": "superseded",
        "eta_s": null,
        "error": "superseded"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: classify -> compact",
  "repaired": false,
  "safe_default": true,
  "mode": null,
  "followup_available": false,
  "notes": {
    "error": "superseded[None]: replaced by a newer request of L1"
  }
}
```

`room_busy`:

*classify.room_busy: real engine output*

```json
{
  "task": "classify",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": null,
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": true,
  "degrade_reason": "room_busy",
  "eta_seconds": 8.0,
  "events": [
    {
      "name": "room_busy",
      "t_ms": 0.0,
      "data": {
        "room_id": "R1",
        "eta_s": 8.0
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "room_busy",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "room_id": "R1"
  }
}
```

`quota_exhausted` (the team's 20 requests per minute are used; the provider states a 60 s delay and an INTERACTIVE
request does not wait more than 30 s, so it is answered degraded with the remaining delay as `eta_seconds`; a BACKGROUND
request waits up to 65 s and retries by itself):

*classify.quota_exhausted: real engine output*

```json
{
  "task": "classify",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "intent": "EXPLAIN",
    "language": "FRENCH",
    "confusion": "MEDIUM",
    "complexity": 2,
    "confidence": 0.0
  },
  "model_class_used": "compact",
  "model": null,
  "from_cache": false,
  "degraded": true,
  "degrade_reason": "quota_exhausted",
  "eta_seconds": 60.0,
  "events": [
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    },
    {
      "name": "gave_up",
      "t_ms": 0.0,
      "data": {
        "error": "quota_exhausted",
        "retry_after_s": 60.0
      }
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "reason": "quota_exhausted",
        "eta_s": 60.0,
        "error": "quota_exhausted"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: classify -> compact",
  "repaired": false,
  "safe_default": true,
  "mode": null,
  "followup_available": false,
  "notes": {
    "error": "quota_exhausted[429], retry_after=60s: Rate limit exceeded for api_key: <key-hash>. Limit type: requests. Current limit: 20, Remaining: 0."
  }
}
```

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

*classify.ok: real engine output*

```json
{
  "task": "classify",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "intent": "EXPLAIN",
    "language": "FRENCH",
    "confusion": "MEDIUM",
    "complexity": 2,
    "confidence": 0.9
  },
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 310,
  "tokens_out": 40,
  "route_reason": "table: classify -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "model_language": "FRENCH",
    "language_source": "model"
  }
}
```

Served from the cache (identical message, any learner):

*classify.cache_hit: real engine output*

```json
{
  "task": "classify",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "intent": "EXPLAIN",
    "language": "FRENCH",
    "confusion": "MEDIUM",
    "complexity": 2,
    "confidence": 0.9
  },
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": true,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "cache_hit",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: classify -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "model_language": "FRENCH",
    "language_source": "model"
  }
}
```

Answered by rules (a greeting; no model):

*classify.rules: real engine output*

```json
{
  "task": "classify",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "intent": "CHITCHAT",
    "language": "FRENCH",
    "confusion": "LOW",
    "complexity": 1,
    "confidence": 1.0
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": null,
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "source": "rules"
  }
}
```

JSON could not be obtained (two garbled replies): safe default, `safe_default=true`:

*classify.safe_default: real engine output*

```json
{
  "task": "classify",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "intent": "EXPLAIN",
    "language": "FRENCH",
    "confusion": "MEDIUM",
    "complexity": 2,
    "confidence": 0.0
  },
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    },
    {
      "name": "repairing_json",
      "t_ms": 0.0,
      "data": {
        "error": "invalid JSON: Expecting value"
      }
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 1.6
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    },
    {
      "name": "using_safe_default",
      "t_ms": 0.0,
      "data": {
        "error": "invalid JSON: Expecting value"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 600,
  "tokens_out": 24,
  "route_reason": "table: classify -> compact",
  "repaired": false,
  "safe_default": true,
  "mode": null,
  "followup_available": false,
  "notes": {
    "error": "invalid JSON: Expecting value"
  }
}
```

### 3.2 `hint`

A guiding question that never states the answer.

* inputs: `problem` (required); `attempt`; `solution` (only used to guard against leaking it); `stored_hints` (list,
  graded, most general first) with `hint_level` (1-based).
* `text`: the hint. With `stored_hints` the hint comes from the list: 0 requests, event `answered_by_rules`,
  `notes.hint_level`, `notes.hints_total`.
* cost: 0 (stored hint, cache hit) or 1 compact (~3.8 s).
* degrade: `hint_guard:answer_leak | too_long | language_drift | degenerate` (the text is then a safe fallback),
  `quota_exhausted`, `service_busy`, `superseded`, `room_busy`.

*hint.ok: real engine output*

```json
{
  "task": "hint",
  "request_id": "a1b2c3d4e5f6",
  "text": "Quelle règle donne la dérivée d'une puissance ? Regarde l'exposant.",
  "data": null,
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 1.6
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 260,
  "tokens_out": 22,
  "route_reason": "table: hint -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {}
}
```

Stored hint, level 2 (0 requests):

*hint.stored: real engine output*

```json
{
  "task": "hint",
  "request_id": "a1b2c3d4e5f6",
  "text": "(xⁿ)' = n·xⁿ⁻¹ : ici, que vaut n ?",
  "data": {
    "hint_level": 2,
    "hints_total": 2,
    "source": "bank"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": null,
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "source": "bank"
  }
}
```

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
  into the cache and the next ask is a hit; the 25 s also count while the request waits for an identical one that
  another learner or a prep page is generating). Also `ungrounded_formula`, `wrong_math`, `explain_guard:*`,
  `input_truncated`, `quota_exhausted`, `superseded`, `room_busy`. A question the course does not cover returns
  `text` saying so, `0` requests, event `no_relevant_context`.
* events: `retrieving`, `expanding_query`, `checking_cache`, then [`cache_hit`] or [`partial_result` (course
  excerpts, show them while waiting), `queued`, `generating`], fast mode adds `falling_back_to_compact`,
  `fast_extractive`, `deadline`.

*explain.ok: real engine output*

```json
{
  "task": "explain",
  "request_id": "a1b2c3d4e5f6",
  "text": "Voici l'essentiel du cours sur ce point.\n\nLe taux d'accroissement mesure la pente de la sécante entre deux points ; quand h tend vers 0, il tend vers le nombre dérivé, c'est-à-dire la pente de la tangente.\n\nÀ toi : Soit f(x) = x². Calculer f'(3).",
  "data": {
    "body": "Le taux d'accroissement mesure la pente de la sécante entre deux points ; quand h tend vers 0, il tend vers le nombre dérivé, c'est-à-dire la pente de la tangente.",
    "opener": "Voici l'essentiel du cours sur ce point.",
    "exercise": {
      "id": "d10",
      "concept": "nombre dérivé",
      "level": 1,
      "statement": "Soit f(x) = x². Calculer f'(3).",
      "expression": "x²"
    }
  },
  "model_class_used": "midsize",
  "model": "mid-a",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "retrieving",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "partial_result",
      "t_ms": 0.0,
      "data": {
        "excerpts": [
          {
            "chunk_id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
            "text": "Pour une fonction f et un nombre a, le taux d'accroissement entre a et a + h est le quotient (f(a + h) − f(a)) / h."
          },
          {
            "chunk_id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
            "text": "Quand h devient de plus en plus petit (h tend vers 0), si ce quotient se rapproche d'un nombre fini, ce nombre s'appelle le nombre dérivé de f en a et se note f'(a)."
          },
          {
            "chunk_id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
            "text": "Exemple : pour f(x) = x², le taux d'accroissement vaut (2ah + h²) / h = 2a + h, qui tend vers 2a quand h tend vers 0."
          }
        ]
      }
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 1.3
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "midsize"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 1100,
  "tokens_out": 60,
  "route_reason": "table: explain -> midsize; completes after the learner's deadline",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "retrieved": [
      {
        "id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
        "score": 11.666,
        "coverage": 1.0
      }
    ],
    "sources": [
      "derivee-definition.fr#nombre-derive-et-taux-d-accroissement"
    ],
    "budget": {
      "tokens": 598,
      "limit": 2520
    },
    "dropped": [],
    "formulas_verified": 0
  }
}
```

Cache hit (same question and same retrieved chunks):

*explain.cache_hit: real engine output*

```json
{
  "task": "explain",
  "request_id": "a1b2c3d4e5f6",
  "text": "Voici l'essentiel du cours sur ce point.\n\nLe taux d'accroissement mesure la pente de la sécante entre deux points ; quand h tend vers 0, il tend vers le nombre dérivé, c'est-à-dire la pente de la tangente.\n\nÀ toi : Soit f(x) = x². Calculer f'(3).",
  "data": {
    "body": "Le taux d'accroissement mesure la pente de la sécante entre deux points ; quand h tend vers 0, il tend vers le nombre dérivé, c'est-à-dire la pente de la tangente.",
    "opener": "Voici l'essentiel du cours sur ce point.",
    "exercise": {
      "id": "d10",
      "concept": "nombre dérivé",
      "level": 1,
      "statement": "Soit f(x) = x². Calculer f'(3).",
      "expression": "x²"
    }
  },
  "model_class_used": "midsize",
  "model": "mid-a",
  "from_cache": true,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "retrieving",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "cache_hit",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: explain -> midsize",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "retrieved": [
      {
        "id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
        "score": 11.666,
        "coverage": 1.0
      }
    ],
    "sources": [
      "derivee-definition.fr#nombre-derive-et-taux-d-accroissement"
    ],
    "budget": {
      "tokens": 598,
      "limit": 2520
    },
    "dropped": [],
    "formulas_verified": 0
  }
}
```

Fast mode, mid-size saturated:

*explain.fast_extractive: real engine output*

```json
{
  "task": "explain",
  "request_id": "a1b2c3d4e5f6",
  "text": "Voici les passages du cours qui répondent à ta question :\n\n- Pour une fonction f et un nombre a, le taux d'accroissement entre a et a + h est le quotient (f(a + h) − f(a)) / h.\n- Quand h devient de plus en plus petit (h tend vers 0), si ce quotient se rapproche d'un nombre fini, ce nombre s'appelle le nombre dérivé de f en a et se note f'(a).\n- Exemple : pour f(x) = x², le taux d'accroissement vaut (2ah + h²) / h = 2a + h, qui tend vers 2a quand h tend vers 0.\n\nÀ toi : Soit f(x) = x². Calculer f'(3).",
  "data": {
    "framing": "Voici les passages du cours qui répondent à ta question :",
    "excerpts": [
      {
        "chunk_id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
        "text": "Pour une fonction f et un nombre a, le taux d'accroissement entre a et a + h est le quotient (f(a + h) − f(a)) / h."
      },
      {
        "chunk_id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
        "text": "Quand h devient de plus en plus petit (h tend vers 0), si ce quotient se rapproche d'un nombre fini, ce nombre s'appelle le nombre dérivé de f en a et se note f'(a)."
      },
      {
        "chunk_id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
        "text": "Exemple : pour f(x) = x², le taux d'accroissement vaut (2ah + h²) / h = 2a + h, qui tend vers 2a quand h tend vers 0."
      }
    ],
    "exercise": {
      "id": "d10",
      "concept": "nombre dérivé",
      "level": 1,
      "statement": "Soit f(x) = x². Calculer f'(3).",
      "expression": "x²"
    }
  },
  "model_class_used": "midsize",
  "model": null,
  "from_cache": false,
  "degraded": true,
  "degrade_reason": "midsize_saturated",
  "eta_seconds": null,
  "events": [
    {
      "name": "retrieving",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "partial_result",
      "t_ms": 0.0,
      "data": {
        "excerpts": [
          {
            "chunk_id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
            "text": "Pour une fonction f et un nombre a, le taux d'accroissement entre a et a + h est le quotient (f(a + h) − f(a)) / h."
          },
          {
            "chunk_id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
            "text": "Quand h devient de plus en plus petit (h tend vers 0), si ce quotient se rapproche d'un nombre fini, ce nombre s'appelle le nombre dérivé de f en a et se note f'(a)."
          },
          {
            "chunk_id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
            "text": "Exemple : pour f(x) = x², le taux d'accroissement vaut (2ah + h²) / h = 2a + h, qui tend vers 2a quand h tend vers 0."
          }
        ]
      }
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "midsize"
      }
    },
    {
      "name": "retry_after_interval",
      "t_ms": 0.0,
      "data": {
        "error": "saturated",
        "seconds": 4.0,
        "attempt": 1
      }
    },
    {
      "name": "queued",
      "t_ms": 4000.0,
      "data": {
        "position": 0,
        "eta_s": 1.6
      }
    },
    {
      "name": "generating",
      "t_ms": 4000.0,
      "data": {
        "attempt": 2,
        "model_class": "midsize"
      }
    },
    {
      "name": "gave_up",
      "t_ms": 4000.0,
      "data": {
        "error": "saturated",
        "retry_after_s": 4.0
      }
    },
    {
      "name": "falling_back_to_compact",
      "t_ms": 4000.0,
      "data": {
        "reason": "midsize_saturated"
      }
    },
    {
      "name": "fast_extractive",
      "t_ms": 4000.0,
      "data": {
        "reason": "midsize_saturated"
      }
    },
    {
      "name": "queued",
      "t_ms": 4000.0,
      "data": {
        "position": 0,
        "eta_s": 1.3
      }
    },
    {
      "name": "generating",
      "t_ms": 4000.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    },
    {
      "name": "gave_up",
      "t_ms": 4000.0,
      "data": {
        "error": "saturated",
        "retry_after_s": 4.0
      }
    }
  ],
  "latency_ms": 4000.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "midsize saturated after retries -> fallback compact",
  "repaired": false,
  "safe_default": false,
  "mode": "fast_extractive",
  "followup_available": true,
  "notes": {
    "retrieved": [
      {
        "id": "derivee-definition.fr#nombre-derive-et-taux-d-accroissement",
        "score": 11.666,
        "coverage": 1.0
      }
    ],
    "sources": [
      "derivee-definition.fr#nombre-derive-et-taux-d-accroissement"
    ],
    "budget": {
      "tokens": 598,
      "limit": 2520
    },
    "dropped": []
  }
}
```

Fast mode, the 25 s deadline (canned: needs 25 real seconds):

*explain.deadline: canned (FakeEngine)*

```json
{
  "task": "explain",
  "request_id": "a1b2c3d4e5f6",
  "text": "Je n'ai pas pu rédiger l'explication complète à l'instant ; voici ce que dit le cours :\n\n- Le nombre dérivé de f en a est la limite, quand h tend vers 0, du taux d'accroissement (f(a+h) − f(a))/h.\n- Géométriquement, c'est le coefficient directeur de la tangente à la courbe au point d'abscisse a.\n- Pour tout entier n ≥ 1, (xⁿ)' = n·xⁿ⁻¹.",
  "data": {
    "framing": "Je n'ai pas pu rédiger l'explication complète à l'instant ; voici ce que dit le cours :",
    "excerpts": [
      {
        "chunk_id": "derivee-definition.fr#nombre-derive",
        "text": "Le nombre dérivé de f en a est la limite, quand h tend vers 0, du taux d'accroissement (f(a+h) − f(a))/h."
      },
      {
        "chunk_id": "derivee-definition.fr#nombre-derive",
        "text": "Géométriquement, c'est le coefficient directeur de la tangente à la courbe au point d'abscisse a."
      },
      {
        "chunk_id": "derivee-formules.fr#puissance",
        "text": "Pour tout entier n ≥ 1, (xⁿ)' = n·xⁿ⁻¹."
      }
    ],
    "exercise": {
      "id": "d01",
      "concept": "puissance",
      "level": 1,
      "statement": "Dérive f(x) = x⁴.",
      "expression": "x⁴"
    },
    "opener": "Bonne question, on la prend étape par étape."
  },
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": false,
  "degraded": true,
  "degrade_reason": "deadline",
  "eta_seconds": null,
  "events": [
    {
      "name": "retrieving",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "expanding_query",
      "t_ms": 40.0,
      "data": {
        "glossary": [
          "nombre dérivé"
        ]
      }
    },
    {
      "name": "checking_cache",
      "t_ms": 50.0,
      "data": {}
    },
    {
      "name": "partial_result",
      "t_ms": 60.0,
      "data": {
        "excerpts": [
          {
            "chunk_id": "derivee-definition.fr#nombre-derive",
            "text": "Le nombre dérivé de f en a est la limite, quand h tend vers 0, du taux d'accroissement (f(a+h) − f(a))/h."
          },
          {
            "chunk_id": "derivee-definition.fr#nombre-derive",
            "text": "Géométriquement, c'est le coefficient directeur de la tangente à la courbe au point d'abscisse a."
          }
        ]
      }
    },
    {
      "name": "queued",
      "t_ms": 70.0,
      "data": {
        "position": 0,
        "eta_s": 6.0
      }
    },
    {
      "name": "generating",
      "t_ms": 80.0,
      "data": {
        "attempt": 1,
        "model_class": "midsize"
      }
    },
    {
      "name": "deadline",
      "t_ms": 25000.0,
      "data": {
        "deadline_s": 25.0,
        "generation_continues": true
      }
    },
    {
      "name": "fast_extractive",
      "t_ms": 25000.0,
      "data": {
        "reason": "deadline"
      }
    }
  ],
  "latency_ms": 25000.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "mid-size by table; interactive deadline",
  "repaired": false,
  "safe_default": false,
  "mode": "fast_extractive",
  "followup_available": true,
  "notes": {
    "sources": [
      "derivee-definition.fr#nombre-derive",
      "derivee-formules.fr#puissance"
    ]
  }
}
```

Not in the course (0 requests):

*explain.not_in_course: real engine output*

```json
{
  "task": "explain",
  "request_id": "a1b2c3d4e5f6",
  "text": "Je n'ai pas trouvé ce sujet dans le cours dont je dispose. Peux-tu reformuler ta question, ou la poser à ton enseignant ?",
  "data": null,
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "retrieving",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "no_relevant_context",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: explain -> midsize",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "retrieval": "no_relevant_chunks"
  }
}
```

### 3.4 `quiz`

An exercise from the bank. No model.

* inputs: `message`; `concept`; `difficulty` (1-3); `exclude` (ids already served); `diagnostic` (true: three exercises of
  increasing level); `intro: "llm"` (opt-in one compact line).
* `data`: `{exercises: [{id, concept, level, statement, expression}], requested_concepts, diagnostic, intro}`; `text`
  is intro + statements. `mode="bank"`.
* cost: 0.

*quiz.ok: real engine output*

```json
{
  "task": "quiz",
  "request_id": "a1b2c3d4e5f6",
  "text": "Voici un exercice pour t'entraîner :\n\nCalculer la dérivée de f(x) = x⁴.",
  "data": {
    "exercises": [
      {
        "id": "d01",
        "concept": "puissance",
        "level": 1,
        "statement": "Calculer la dérivée de f(x) = x⁴.",
        "expression": "x⁴"
      }
    ],
    "requested_concepts": [
      "puissance"
    ],
    "diagnostic": false,
    "intro": "Voici un exercice pour t'entraîner :"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "picking_exercise",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: quiz -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": "bank",
  "followup_available": false,
  "notes": {
    "source": "bank"
  }
}
```

*quiz.diagnostic: real engine output*

```json
{
  "task": "quiz",
  "request_id": "a1b2c3d4e5f6",
  "text": "Trois exercices courts, du plus simple au plus difficile. Réponds au premier :\n\n1) Calculer la dérivée de f(x) = x⁴.\n2) Calculer la dérivée de f(x) = 5x⁴ − 3x² + x.\n3) Calculer la dérivée de f(x) = (2x + 1)(x − 4).",
  "data": {
    "exercises": [
      {
        "id": "d01",
        "concept": "puissance",
        "level": 1,
        "statement": "Calculer la dérivée de f(x) = x⁴.",
        "expression": "x⁴"
      },
      {
        "id": "d04",
        "concept": "polynôme",
        "level": 2,
        "statement": "Calculer la dérivée de f(x) = 5x⁴ − 3x² + x.",
        "expression": "5x⁴ − 3x² + x"
      },
      {
        "id": "d06",
        "concept": "produit",
        "level": 3,
        "statement": "Calculer la dérivée de f(x) = (2x + 1)(x − 4).",
        "expression": "(2x + 1)(x − 4)"
      }
    ],
    "requested_concepts": [],
    "diagnostic": true,
    "intro": "Trois exercices courts, du plus simple au plus difficile. Réponds au premier :"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "picking_exercise",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: quiz -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": "bank",
  "followup_available": false,
  "notes": {
    "source": "bank"
  }
}
```

### 3.5 `grade`

Grades an answer attempt against a stored solution. Sympy first, compact JSON only when the answer is not an expression.

* inputs: `problem`, `solution`, `attempt` (all required); `common_mistakes` (`[{expr, feedback}]`).
* `data`: `{verdict: CORRECT|INCORRECT|PARTIAL|UNKNOWN, mistake, feedback, graded_by: sympy|llm}`. A wrong answer equal
  to a listed mistake returns that mistake's feedback. The feedback never contains the solution.
* cost: 0 (sympy) or 1 compact.

*grade.correct: real engine output*

```json
{
  "task": "grade",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "verdict": "CORRECT",
    "mistake": null,
    "feedback": "",
    "graded_by": "sympy"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": null,
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "source": "rules"
  }
}
```

*grade.incorrect: real engine output*

```json
{
  "task": "grade",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "verdict": "INCORRECT",
    "mistake": "x³",
    "feedback": "Tu as baissé l'exposant mais oublié de multiplier par l'exposant.",
    "graded_by": "sympy"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": null,
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "source": "rules"
  }
}
```

Text answer, graded by the model:

*grade.llm: real engine output*

```json
{
  "task": "grade",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "verdict": "INCORRECT",
    "mistake": null,
    "feedback": "Que devient l'exposant quand tu dérives ?",
    "graded_by": "llm"
  },
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 1.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 300,
  "tokens_out": 30,
  "route_reason": "table: grade -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {}
}
```

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

*ocr_transcribe.ok: real engine output*

```json
{
  "task": "ocr_transcribe",
  "request_id": "a1b2c3d4e5f6",
  "text": "Dériver f(x) = 3x² + 5x\nf'(x) = 6x + 5",
  "data": {
    "text": "Dériver f(x) = 3x² + 5x\nf'(x) = 6x + 5",
    "latex": "Dériver f(x) = 3x^{2} + 5x \\\\ f'(x) = 6x + 5",
    "confidence_note": ""
  },
  "model_class_used": "vision",
  "model": "deepseek-ocr",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "resizing_image",
      "t_ms": 0.0,
      "data": {
        "from": "2048x1536",
        "to": "1154x866"
      }
    },
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "vision"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 820,
  "tokens_out": 40,
  "route_reason": "table: ocr_transcribe -> vision",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "image": {
      "sha": "c316e3c47db2",
      "size": "1154x866",
      "resized": true,
      "est_tokens": 1000
    }
  }
}
```

*ocr_transcribe.low_confidence: real engine output*

```json
{
  "task": "ocr_transcribe",
  "request_id": "a1b2c3d4e5f6",
  "text": "f(x) = (3x + [?]\nf'(x) = 6x + 5",
  "data": {
    "text": "f(x) = (3x + [?]\nf'(x) = 6x + 5",
    "latex": "f(x) = (3x + [?] \\\\ f'(x) = 6x + 5",
    "confidence_note": "Ligne 1 : un symbole n'a pas pu être lu ; vérifie-la."
  },
  "model_class_used": "vision",
  "model": "deepseek-ocr",
  "from_cache": false,
  "degraded": true,
  "degrade_reason": "low_confidence",
  "eta_seconds": null,
  "events": [
    {
      "name": "resizing_image",
      "t_ms": 0.0,
      "data": {
        "from": "1200x900",
        "to": "1154x866"
      }
    },
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "vision"
      }
    },
    {
      "name": "guarded",
      "t_ms": 0.0,
      "data": {
        "reason": "low_confidence",
        "issues": [
          {
            "kind": "unreadable",
            "line": 1
          }
        ]
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 820,
  "tokens_out": 40,
  "route_reason": "table: ocr_transcribe -> vision",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "issues": [
      {
        "kind": "unreadable",
        "line": 1
      }
    ],
    "image": {
      "sha": "09f7dee26e51",
      "size": "1154x866",
      "resized": true,
      "est_tokens": 1000
    }
  }
}
```

*ocr_transcribe.invalid_image: real engine output*

```json
{
  "task": "ocr_transcribe",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": null,
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": true,
  "degrade_reason": "invalid_image",
  "eta_seconds": null,
  "events": [
    {
      "name": "unavailable",
      "t_ms": 0.0,
      "data": {
        "reason": "invalid_image",
        "eta_s": null,
        "error": "image"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "invalid image",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "error": "not a readable image (UnidentifiedImageError)"
  }
}
```

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

*check_claim.CONTRADICTED: real engine output*

```json
{
  "task": "check_claim",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "verdict": "CONTRADICTED",
    "correction": "Plutôt : (x³)' = 3x²",
    "source_chunk_id": null,
    "checked_by": "sympy"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": null,
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "source": "rules"
  }
}
```

Verified by computation (real):

*check_claim.SUPPORTED: real engine output*

```json
{
  "task": "check_claim",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "verdict": "SUPPORTED",
    "correction": "",
    "source_chunk_id": null,
    "checked_by": "sympy"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": null,
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "source": "rules"
  }
}
```

Half right, half refuted by computation (real):

*check_claim.PARTLY: real engine output*

```json
{
  "task": "check_claim",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "verdict": "PARTLY",
    "correction": "Rather: (x³)' = 3x²",
    "source_chunk_id": null,
    "checked_by": "sympy"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": null,
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "source": "rules"
  }
}
```

Judged by the model against the course (real):

*check_claim.CONTRADICTED_llm: real engine output*

```json
{
  "task": "check_claim",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "verdict": "CONTRADICTED",
    "correction": "La dérivée d'un produit est (u·v)' = u'·v + u·v', pas u'·v'.",
    "source_chunk_id": "derivee-erreurs.fr#erreurs-avec-le-produit",
    "checked_by": "llm"
  },
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "retrieving",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 640,
  "tokens_out": 40,
  "route_reason": "table: check_claim -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "retrieved": [
      {
        "id": "derivee-erreurs.fr#erreurs-avec-le-produit",
        "score": 3.596,
        "coverage": 1.0
      }
    ],
    "sources": [
      "derivee-erreurs.fr#erreurs-avec-le-produit"
    ],
    "budget": {
      "tokens": 764,
      "limit": 2520
    },
    "dropped": []
  }
}
```

Not in the course (real, 0 requests):

*check_claim.NOT_IN_COURSE: real engine output*

```json
{
  "task": "check_claim",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "verdict": "NOT_IN_COURSE",
    "correction": "",
    "source_chunk_id": null,
    "checked_by": "rules"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "retrieving",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "no_relevant_context",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: check_claim -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "retrieval": "no_relevant_chunks"
  }
}
```

The model failed twice (real; safe default):

*check_claim.llm_failed: real engine output*

```json
{
  "task": "check_claim",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "verdict": "PARTLY",
    "correction": "",
    "source_chunk_id": "derivee-erreurs.fr#erreurs-avec-le-produit",
    "checked_by": "llm"
  },
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": false,
  "degraded": true,
  "degrade_reason": "json_invalid",
  "eta_seconds": null,
  "events": [
    {
      "name": "retrieving",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    },
    {
      "name": "repairing_json",
      "t_ms": 0.0,
      "data": {
        "error": "invalid JSON: Expecting value"
      }
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 1.6
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    },
    {
      "name": "using_safe_default",
      "t_ms": 0.0,
      "data": {
        "error": "invalid JSON: Expecting value"
      }
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 1280,
  "tokens_out": 10,
  "route_reason": "table: check_claim -> compact",
  "repaired": false,
  "safe_default": true,
  "mode": null,
  "followup_available": false,
  "notes": {
    "retrieved": [
      {
        "id": "derivee-erreurs.fr#erreurs-avec-le-produit",
        "score": 3.596,
        "coverage": 1.0
      }
    ],
    "sources": [
      "derivee-erreurs.fr#erreurs-avec-le-produit"
    ],
    "budget": {
      "tokens": 764,
      "limit": 2520
    },
    "dropped": [],
    "error": "invalid JSON: Expecting value"
  }
}
```

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

*check_answer.ok: real engine output*

```json
{
  "task": "check_answer",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "steps": [
      {
        "index": 0,
        "verdict": "OK",
        "note": ""
      },
      {
        "index": 1,
        "verdict": "OK",
        "note": ""
      }
    ],
    "first_wrong": null,
    "final_verdict": "CORRECT",
    "feedback": "",
    "graded_by": "sympy"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "parsing_steps",
      "t_ms": 0.0,
      "data": {
        "steps": 2
      }
    },
    {
      "name": "checking_steps",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: check_answer -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": "rules",
  "followup_available": false,
  "notes": {
    "target": "derive"
  }
}
```

A stored common mistake, caught on the right line (real, 0 requests):

*check_answer.wrong_step: real engine output*

```json
{
  "task": "check_answer",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "steps": [
      {
        "index": 0,
        "verdict": "OK",
        "note": ""
      },
      {
        "index": 1,
        "verdict": "WRONG",
        "note": "Cette ligne ne correspond pas à la précédente ni à la dérivée."
      }
    ],
    "first_wrong": 1,
    "final_verdict": "INCORRECT",
    "feedback": "Tu as baissé l'exposant mais oublié de multiplier par l'exposant.",
    "graded_by": "rules"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "parsing_steps",
      "t_ms": 0.0,
      "data": {
        "steps": 2
      }
    },
    {
      "name": "checking_steps",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: check_answer -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": "rules",
  "followup_available": false,
  "notes": {
    "target": "derive",
    "diagnosis": "bank"
  }
}
```

A buggy rule recognised (real, 0 requests):

*check_answer.rule: real engine output*

```json
{
  "task": "check_answer",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "steps": [
      {
        "index": 0,
        "verdict": "OK",
        "note": ""
      },
      {
        "index": 1,
        "verdict": "WRONG",
        "note": "Cette ligne ne correspond pas à la précédente ni à la dérivée."
      }
    ],
    "first_wrong": 1,
    "final_verdict": "INCORRECT",
    "feedback": "Tu as multiplié par l'exposant mais tu n'as pas baissé l'exposant de 1.",
    "graded_by": "rules"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "parsing_steps",
      "t_ms": 0.0,
      "data": {
        "steps": 2
      }
    },
    {
      "name": "checking_steps",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: check_answer -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": "rules",
  "followup_available": false,
  "notes": {
    "target": "derive",
    "diagnosis": "rule:POW_NO_DECREMENT"
  }
}
```

A wrong line nothing explains: one compact guiding question (real):

*check_answer.llm_feedback: real engine output*

```json
{
  "task": "check_answer",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "steps": [
      {
        "index": 0,
        "verdict": "OK",
        "note": ""
      },
      {
        "index": 1,
        "verdict": "WRONG",
        "note": "Cette ligne ne correspond pas à la précédente ni à la dérivée."
      }
    ],
    "first_wrong": 1,
    "final_verdict": "INCORRECT",
    "feedback": "Que devient l'exposant de x quand on dérive ?",
    "graded_by": "llm"
  },
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "parsing_steps",
      "t_ms": 0.0,
      "data": {
        "steps": 2
      }
    },
    {
      "name": "checking_steps",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 0.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 0.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    },
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 260,
  "tokens_out": 20,
  "route_reason": "table: check_answer -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "target": "derive",
    "diagnosis": "llm"
  }
}
```

Nothing readable (real, 0 requests):

*check_answer.unchecked: real engine output*

```json
{
  "task": "check_answer",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "steps": [
      {
        "index": 0,
        "verdict": "UNCHECKED",
        "note": "Ligne non reconnue : je ne peux pas la vérifier."
      }
    ],
    "first_wrong": null,
    "final_verdict": "UNKNOWN",
    "feedback": "",
    "graded_by": "sympy"
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "parsing_steps",
      "t_ms": 0.0,
      "data": {
        "steps": 1
      }
    },
    {
      "name": "checking_steps",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": "table: check_answer -> compact",
  "repaired": false,
  "safe_default": false,
  "mode": "rules",
  "followup_available": false,
  "notes": {
    "target": null
  }
}
```

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

*prep_plan.ok: real engine output*

```json
{
  "task": "prep_plan",
  "request_id": "a1b2c3d4e5f6",
  "text": null,
  "data": {
    "focus_points": [
      {
        "concept_id": "puissance",
        "day_index": 0,
        "reason": "Prérequis de « Dérivée de l'inverse, Dérivée de la racine carrée » ; Maîtrise faible (15%)",
        "reason_code": "prerequisite",
        "page_kind": "learn",
        "minutes": 20
      },
      {
        "concept_id": "inverse",
        "day_index": 1,
        "reason": "Prérequis de « Dérivée d'un quotient » ; Maîtrise faible (0%)",
        "reason_code": "prerequisite",
        "page_kind": "learn",
        "minutes": 20
      },
      {
        "concept_id": "racine carrée",
        "day_index": 2,
        "reason": "Maîtrise faible (0%)",
        "reason_code": "low_mastery",
        "page_kind": "learn",
        "minutes": 20
      },
      {
        "concept_id": "constante",
        "day_index": 3,
        "reason": "Prérequis de « Dérivée d'un polynôme, Dérivée d'un produit » ; Maîtrise faible (40%)",
        "reason_code": "prerequisite",
        "page_kind": "learn",
        "minutes": 20
      },
      {
        "concept_id": "polynôme",
        "day_index": 4,
        "reason": "Prérequis de « Dérivée d'un produit, Dérivée d'un quotient » ; Maîtrise faible (0%)",
        "reason_code": "prerequisite",
        "page_kind": "learn",
        "minutes": 20
      },
      {
        "concept_id": "produit",
        "day_index": 5,
        "reason": "Prérequis de « Dérivée d'un quotient » ; Maîtrise faible (0%)",
        "reason_code": "prerequisite",
        "page_kind": "learn",
        "minutes": 20
      },
      {
        "concept_id": "quotient",
        "day_index": 6,
        "reason": "Maîtrise faible (0%)",
        "reason_code": "low_mastery",
        "page_kind": "learn",
        "minutes": 20
      },
      {
        "concept_id": "variations",
        "day_index": 6,
        "reason": "Prérequis de « Extremum » ; Maîtrise faible (0%)",
        "reason_code": "prerequisite",
        "page_kind": "learn",
        "minutes": 20
      },
      {
        "concept_id": "extremum",
        "day_index": 6,
        "reason": "Maîtrise faible (0%)",
        "reason_code": "low_mastery",
        "page_kind": "learn",
        "minutes": 20
      },
      {
        "concept_id": "nombre dérivé",
        "day_index": 6,
        "reason": "Déjà maîtrisé (90%) : révision rapide",
        "reason_code": "review",
        "page_kind": "review",
        "minutes": 8
      },
      {
        "concept_id": "tangente",
        "day_index": 6,
        "reason": "Déjà maîtrisé (88%) : révision rapide",
        "reason_code": "review",
        "page_kind": "review",
        "minutes": 8
      }
    ],
    "assessment_exercise_ids": [
      "d01",
      "d08",
      "d09",
      "d03",
      "d02",
      "d05",
      "d07",
      "d14",
      "d15",
      "d11"
    ]
  },
  "model_class_used": null,
  "model": null,
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "answered_by_rules",
      "t_ms": 0.0,
      "data": {}
    }
  ],
  "latency_ms": 0.0,
  "tokens_in": 0,
  "tokens_out": 0,
  "route_reason": null,
  "repaired": false,
  "safe_default": false,
  "mode": "rules",
  "followup_available": false,
  "notes": {
    "source": "rules",
    "skipped_draft": [
      "limite"
    ],
    "days": 7,
    "minutes_needed": 196,
    "minutes_available": 315,
    "pace_min_per_day": 45,
    "start": "2026-11-05",
    "deadline": "2026-11-12"
  }
}
```

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

*generate_page.ok: real engine output*

```json
{
  "concept_id": "puissance",
  "page_kind": "learn",
  "title": "Dérivée d'une puissance",
  "language": "FRENCH",
  "explanation": "Voici l'essentiel du cours sur ce point.\n\nLa dérivée d'une puissance s'obtient en multipliant par l'exposant puis en baissant l'exposant de 1.",
  "explanation_sections": {
    "body": "La dérivée d'une puissance s'obtient en multipliant par l'exposant puis en baissant l'exposant de 1.",
    "opener": "Voici l'essentiel du cours sur ce point."
  },
  "explain_request_id": "a1b2c3d4e5f6",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "worked_example": {
    "source": "bank",
    "exercise_id": "d01",
    "statement": "Calculer la dérivée de f(x) = x⁴.",
    "steps": [
      "Quelle règle donne la dérivée de xⁿ ?",
      "(xⁿ)' = n·xⁿ⁻¹ : ici, que vaut n ?",
      "Multiplie par l'exposant, puis baisse l'exposant de 1."
    ],
    "solution": "f'(x) = 4x³"
  },
  "quiz": [
    {
      "id": "d10",
      "concept": "nombre dérivé",
      "level": 1,
      "statement": "Soit f(x) = x². Calculer f'(3).",
      "expression": "x²",
      "source": "bank"
    },
    {
      "id": "d11",
      "concept": "nombre dérivé",
      "level": 2,
      "statement": "Soit f(x) = x³ − 2x. Calculer f'(1).",
      "expression": "x³ − 2x",
      "source": "bank"
    },
    {
      "id": "d01~v1",
      "concept": "puissance",
      "level": 1,
      "statement": "Dérive f(x) = 2x⁴.",
      "expression": "2x⁴",
      "source": "variant",
      "solution": "f'(x) = 8x³"
    }
  ]
}
```

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

## 10. HTTP service (for the app side)

`services/engine_server.py` puts `Engine.run` behind HTTP and WebSocket so a Node app can call it. It is a thin front: the
bodies are exactly the arguments of `engine.run`, and the answers are exactly its `Result` (sections 1 to 7).

```bash
export ENGINE_SERVICE_TOKEN=change-me                       # required; every route checks it
python -m services.engine_server                            # real engine, reads .env / ENV_FILE as everything else
python -m services.engine_server --fake --fake-mode slow    # FakeEngine behind the identical API (no key, no provider)
```

* **Local only.** It binds `127.0.0.1` (there is no option for anything else), refuses a `Host` header that is not
  `127.0.0.1` or `localhost`, and serves no OpenAPI page. Port: `--port` or `ENGINE_SERVICE_PORT` (default 8765).
* **Auth.** Send `X-Engine-Token: <ENGINE_SERVICE_TOKEN>` on every call, WebSocket handshake included (compared in constant
  time). Without it: HTTP 401 `{"error": "unauthorized", ...}`; a WebSocket is closed with code 1008 (HTTP 403 to a browser).
  Never put the token in a URL.
* **One process per API key.** The real engine takes a lock file (`runtime/engine-<digest>.lock`, directory from
  `ENGINE_SERVICE_LOCK_DIR`) holding its pid; a second start for the same key sees a live pid and exits with status 3. The
  quota is per key (20 a minute, 2 in flight), so two processes would fight over it. A lock left by a crashed process is
  taken over. `--fake` uses no key and no lock.
* **A run is not tied to its connection.** If the HTTP request is dropped or the WebSocket closes, the run finishes and
  fills the cache; nothing is cancelled. A reconnect with the same body is then a cache hit.
* **Errors.** Every error body is `{"error": ..., "detail": ...}`: 401 `unauthorized`, 413 too large (25 MB), 422
  `invalid_request` (malformed body, unknown task, missing input) or `ingest_error`. A provider problem is never an HTTP error:
  it is a 200 with a degraded `Result`.

### POST /run

Body `{task, inputs, context, priority}` (`priority`: `INTERACTIVE` by default, or `BACKGROUND`; case does not matter).
Answer: the `Result`. For `ocr_transcribe` the image goes either as base64 or a data URL in `inputs.image`, or as a
multipart upload (`image` file part; `task`, `priority` and the JSON strings `inputs` and `context` as form fields).

```bash
curl -s -H "X-Engine-Token: $ENGINE_SERVICE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"task":"hint","inputs":{"problem":"Dérive f(x) = 3x² + 5x"},"context":{"language":"FRENCH","learner_id":"L42"}}' \
  http://127.0.0.1:8765/run
curl -s -H "X-Engine-Token: $ENGINE_SERVICE_TOKEN" -F task=ocr_transcribe -F image=@work.jpg \
  -F 'context={"language":"FRENCH"}' http://127.0.0.1:8765/run
```

*POST /run -> 200*

```json
{
  "task": "hint",
  "request_id": "a1b2c3d4e5f6",
  "text": "Quelle règle donne la dérivée de xⁿ ? Commence par regarder l'exposant.",
  "data": null,
  "model_class_used": "compact",
  "model": "compact-a",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 10.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    },
    {
      "name": "generating",
      "t_ms": 20.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    }
  ],
  "latency_ms": 3800.0,
  "tokens_in": 260,
  "tokens_out": 38,
  "route_reason": "compact by table",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {}
}
```

*POST /run (multipart, task ocr_transcribe) -> 200*

```json
{
  "task": "ocr_transcribe",
  "request_id": "a1b2c3d4e5f6",
  "text": "Dériver f(x) = 3x² + 5x\nf'(x) = 6x + 5",
  "data": {
    "text": "Dériver f(x) = 3x² + 5x\nf'(x) = 6x + 5",
    "latex": "Dériver f(x) = 3x^{2} + 5x \\\\ f'(x) = 6x + 5",
    "confidence_note": ""
  },
  "model_class_used": "vision",
  "model": "deepseek-ocr",
  "from_cache": false,
  "degraded": false,
  "degrade_reason": null,
  "eta_seconds": null,
  "events": [
    {
      "name": "resizing_image",
      "t_ms": 0.0,
      "data": {
        "from": "2048x1536",
        "to": "1154x866"
      }
    },
    {
      "name": "checking_cache",
      "t_ms": 150.0,
      "data": {}
    },
    {
      "name": "queued",
      "t_ms": 200.0,
      "data": {
        "position": 0,
        "eta_s": 4.0
      }
    },
    {
      "name": "generating",
      "t_ms": 210.0,
      "data": {
        "attempt": 1,
        "model_class": "vision"
      }
    }
  ],
  "latency_ms": 14000.0,
  "tokens_in": 820,
  "tokens_out": 64,
  "route_reason": "vision by table",
  "repaired": false,
  "safe_default": false,
  "mode": null,
  "followup_available": false,
  "notes": {
    "image": {
      "sha": "c0ffee123456",
      "size": "1154x866",
      "resized": true,
      "est_tokens": 1000
    }
  }
}
```

*POST /run (a task without its input) -> 422*

```json
{
  "error": "invalid_request",
  "detail": "task 'hint' needs inputs ['problem']"
}
```

*POST /run (no token) -> 401*

```json
{
  "error": "unauthorized",
  "detail": "missing or wrong X-Engine-Token"
}
```

### WS /run/stream

Connect (with the token header), send **one text message with the same body as `POST /run`**, and read frames until the
server closes. Each `Event` arrives as it happens, then the `Result` (which repeats the events in `result.events`):

| frame | meaning |
|---|---|
| `{"type": "event", "event": {name, t_ms, data}}` | one progress event (section 7), in order |
| `{"type": "result", "result": <Result>}` | the answer; the server then closes with code 1000 |
| `{"type": "error", "error": ..., "detail": ...}` | the body was refused (`invalid_request`), close code 1003; or `internal`, 1011 |

A client that sends nothing for 10 s gets an `invalid_request` error. Closing the socket early is safe (see above).

```bash
# prints each frame; needs `npm i ws` (the Node example at the end of this section is the same call as a function)
node --input-type=module -e '
import WebSocket from "ws";
const ws = new WebSocket("ws://127.0.0.1:8765/run/stream", { headers: { "X-Engine-Token": process.env.ENGINE_SERVICE_TOKEN } });
ws.on("open", () => ws.send(JSON.stringify({ task: "hint", inputs: { problem: "Dérive f(x) = 3x² + 5x" }, context: { language: "FRENCH" } })));
ws.on("message", (m) => console.log(m.toString()));
ws.on("close", () => process.exit());'
```

*WS /run/stream (frames, in order)*

```json
[
  {
    "type": "event",
    "event": {
      "name": "checking_cache",
      "t_ms": 0.0,
      "data": {}
    }
  },
  {
    "type": "event",
    "event": {
      "name": "queued",
      "t_ms": 10.0,
      "data": {
        "position": 0,
        "eta_s": 2.0
      }
    }
  },
  {
    "type": "event",
    "event": {
      "name": "generating",
      "t_ms": 20.0,
      "data": {
        "attempt": 1,
        "model_class": "compact"
      }
    }
  },
  {
    "type": "result",
    "result": {
      "task": "hint",
      "request_id": "a1b2c3d4e5f6",
      "text": "Quelle règle donne la dérivée de xⁿ ? Commence par regarder l'exposant.",
      "data": null,
      "model_class_used": "compact",
      "model": "compact-a",
      "from_cache": false,
      "degraded": false,
      "degrade_reason": null,
      "eta_seconds": null,
      "events": [
        {
          "name": "checking_cache",
          "t_ms": 0.0,
          "data": {}
        },
        {
          "name": "queued",
          "t_ms": 10.0,
          "data": {
            "position": 0,
            "eta_s": 2.0
          }
        },
        {
          "name": "generating",
          "t_ms": 20.0,
          "data": {
            "attempt": 1,
            "model_class": "compact"
          }
        }
      ],
      "latency_ms": 3800.0,
      "tokens_in": 260,
      "tokens_out": 38,
      "route_reason": "compact by table",
      "repaired": false,
      "safe_default": false,
      "mode": null,
      "followup_available": false,
      "notes": {}
    }
  },
  {
    "type": "websocket.close",
    "code": 1000,
    "reason": ""
  }
]
```

*WS /run/stream (no token)*

```json
[
  {
    "type": "websocket.close",
    "code": 1008,
    "reason": ""
  }
]
```

### POST /materials and DELETE /materials/{material_id}

Multipart upload of a PDF, DOCX or Markdown file: `file` and `course_id` are required; `unit_id`, `owner`, `visibility`
(`public`, `class`, `private`), `class_id`, `source` (`professor`, `learner`, `course`), `language` and `ocr` (`true` by
default) are optional and mean what they mean in section 4. The service runs `engine.ingest_file(..., add=True)` (read,
tag, OCR the scanned pages at BACKGROUND, add to the live index), keeping the upload's own file name, and answers with a
summary of the chunks, not their text. `material_id` is opaque; DELETE with it removes that upload's chunks
(`engine.remove_material`) and says how many.

```bash
curl -s -H "X-Engine-Token: $ENGINE_SERVICE_TOKEN" -F file=@polycopie.pdf -F course_id=analyse -F owner=prof-7 \
  -F visibility=class -F class_id=C1 http://127.0.0.1:8765/materials
curl -s -X DELETE -H "X-Engine-Token: $ENGINE_SERVICE_TOKEN" http://127.0.0.1:8765/materials/$MATERIAL_ID
```

*POST /materials -> 200*

```json
{
  "material_id": "WyJhbmFseXNlIiwgInByb2YtNyIsICJhc3ltcHRvdGVzLm1kIl0",
  "file": "asymptotes.md",
  "course_id": "analyse",
  "chunks_added": 2,
  "pages": 0,
  "warnings": [],
  "needs_ocr": [],
  "ocr": {},
  "chunks": [
    {
      "chunk_id": "professor/prof-7/asymptotes#asymptotes",
      "title": "asymptotes",
      "concept": "",
      "confidence": 1.0,
      "page": 0
    },
    {
      "chunk_id": "professor/prof-7/asymptotes#branches-infinies",
      "title": "asymptotes",
      "concept": "",
      "confidence": 1.0,
      "page": 0
    }
  ],
  "low_confidence": []
}
```

*DELETE /materials/{material_id} -> 200*

```json
{
  "material_id": "WyJhbmFseXNlIiwgInByb2YtNyIsICJhc3ltcHRvdGVzLm1kIl0",
  "removed": 2
}
```

### POST /cache/invalidate

Body `{"request_id": ...}`: `engine.cache.invalidate` (section 5). Answer `{request_id, removed}`.

```bash
curl -s -H "X-Engine-Token: $ENGINE_SERVICE_TOKEN" -H 'Content-Type: application/json' \
  -d '{"request_id":"a1b2c3d4e5f6"}' http://127.0.0.1:8765/cache/invalidate
```

*POST /cache/invalidate -> 200*

```json
{
  "request_id": "a1b2c3d4e5f6",
  "removed": 1
}
```

### GET /health

No request is made to the provider. `window.remaining` is how many more sends fit in the 60 s window right now,
`breaker.<class>.state` is `closed`, `open` or `half_open` (with `retry_in_s` until the next probe), and `queue.depth` is how
many requests wait for a slot. With `--fake` the numbers are canned by mode and `fake` is true.

```bash
curl -s -H "X-Engine-Token: $ENGINE_SERVICE_TOKEN" http://127.0.0.1:8765/health
```

*GET /health -> 200*

```json
{
  "status": "ok",
  "engine_version": "1.3",
  "fake": true,
  "fake_mode": "normal",
  "breaker": {
    "midsize": {
      "state": "closed",
      "retry_in_s": 0.0,
      "trips": 0
    }
  },
  "window": {
    "limit": 20,
    "window_s": 60.0,
    "remaining": 20,
    "in_flight": 0,
    "max_concurrent": 2
  },
  "queue": {
    "depth": 0
  }
}
```

### Professor dashboard (data only)

Four read/decide routes for the professor's screen. They return data; there is no HTML. The ids come from the `context`
of the original `engine.run` (`class_id`, `course_id`, `learner_id`), so the app filters without keeping its own copy.
`since` is epoch seconds or an ISO-8601 date / datetime (no zone = UTC); a bad value is a 422. Every route needs the token.

What is stored: only two kinds of answer, in a small SQLite table next to the cache (same file, `CACHE_PATH`): answers a
check flagged, and questions the course could not answer. The metrics JSONL stays ids and numbers.

**`GET /review/flagged?class_id=&course_id=&since=&limit=50&offset=0`.** `explain`, `check_claim` and `check_answer`
results degraded by `wrong_math` or `ungrounded_formula`, that have no decision yet, newest first. `limit` is 1..200.
`spans` are the flagged formulas (`notes.wrong_math` + `notes.ungrounded`), `answer` is the text, or the result's `data`
as JSON for the JSON tasks. A result served from the cache is not listed again (the original is).
`next_offset` is `null` on the last page.

*GET /review/flagged?class_id=C1&limit=20 -> 200*

```json
{
  "items": [
    {
      "request_id": "a1b2c3d4e5f6",
      "task": "explain",
      "course_id": "analyse",
      "class_id": "C1",
      "learner_id": "L42",
      "question": "Quelle est la dérivée de x³ ?",
      "answer": "Bonne question, on la prend étape par étape.\n\nLe nombre dérivé mesure la pente de la courbe en un point. On regarde le taux d'accroissement (f(a+h) − f(a))/h entre a et a+h ; quand h devient très petit, ce taux se stabilise : sa limite est f'(a). Par exemple, pour f(x) = x², le taux vaut 2a + h, donc f'(a) = 2a.\n\nÀ toi : Dérive f(x) = x⁴.",
      "spans": [
        "f'(x) = 5x"
      ],
      "reasons": [
        "wrong_math"
      ],
      "created_at": "2026-11-02T06:13:20Z"
    }
  ],
  "total": 1,
  "limit": 20,
  "offset": 0,
  "next_offset": null
}
```

**`POST /review/flagged/{request_id}`** with `{"action": "approve" | "reject"}`. Both record the decision, so the item
leaves the queue. `reject` also calls the cache invalidation of that request (`removed` = entries dropped; a degraded
answer is never cached, so it is usually 0: the call matters when the same request id was also cached). Deciding twice the
same way is a 200 with `already: true`; the opposite way is a 409 `already_decided`; an unknown id is a 404
`unknown_item`; a bad action is a 422.

*POST /review/flagged/{request_id} -> 200*

```json
{
  "request_id": "a1b2c3d4e5f6",
  "action": "reject",
  "decided_at": "2026-11-02T06:17:20Z",
  "already": false,
  "removed": 1
}
```

**`GET /review/content-gaps?class_id=&course_id=&since=`.** Questions answered "not in the course" (`explain` with
`notes.retrieval = "no_relevant_chunks"`, `check_claim` with verdict `NOT_IN_COURSE`), grouped by the nearest concept: the
best BM25 hit with the relevance gate off (`notes.nearest_concept` of the original result). `concept: null` = no course
chunk shares a term with the question. Most asked first, at most 5 distinct example questions each (newest first).
A `check_claim` that retrieved a chunk and the model judged outside the course uses that chunk's concept.

*GET /review/content-gaps?class_id=C1&course_id=analyse -> 200*

```json
{
  "gaps": [
    {
      "concept": "nombre dérivé",
      "count": 2,
      "examples": [
        "Explique la continuité en un point",
        "C'est quoi la continuité d'une fonction ?"
      ],
      "last_at": "2026-11-02T06:15:20Z"
    },
    {
      "concept": "produit",
      "count": 1,
      "examples": [
        "Une fonction continue est dérivable."
      ],
      "last_at": "2026-11-02T06:16:20Z"
    }
  ],
  "total": 3
}
```

**`GET /metrics/summary?since=&class_id=&course_id=`.** What the team shows judges, computed from the metrics records on
every call (10,000 records take about 50 ms, so there is no index). `runs` = engine calls, `provider_requests` = what
reached the provider (cache hits excluded) by model class and model, `cache.hit_rate` = cached runs / runs,
`degraded.by_reason` shares are of all runs, `latency_ms` is p50 / p95 per task (nearest rank), `server_429` = provider
429s by outcome.

*GET /metrics/summary -> 200*

```json
{
  "since": null,
  "filters": {
    "class_id": null,
    "course_id": null
  },
  "runs": {
    "total": 7,
    "by_task": {
      "check_claim": 1,
      "explain": 3,
      "hint": 2,
      "ocr_transcribe": 1
    }
  },
  "provider_requests": {
    "total": 0,
    "by_class": {},
    "by_model": {}
  },
  "cache": {
    "hits": 0,
    "runs": 7,
    "hit_rate": 0.0
  },
  "degraded": {
    "count": 1,
    "share": 0.1429,
    "by_reason": {
      "wrong_math": {
        "count": 1,
        "share": 0.1429
      }
    }
  },
  "latency_ms": {
    "check_claim": {
      "count": 1,
      "p50": 50.0,
      "p95": 50.0
    },
    "explain": {
      "count": 3,
      "p50": 50.0,
      "p95": 9500.0
    },
    "hint": {
      "count": 2,
      "p50": 3800.0,
      "p95": 3800.0
    },
    "ocr_transcribe": {
      "count": 1,
      "p50": 14000.0,
      "p95": 14000.0
    }
  },
  "server_429": {
    "count": 0,
    "by_outcome": {}
  }
}
```

### From Node (18 or later; `npm i ws`)

```js
import WebSocket from "ws";
const BASE = "127.0.0.1:8765", H = { "X-Engine-Token": process.env.ENGINE_SERVICE_TOKEN };

// one request, one answer
const run = (task, inputs, context = {}, priority = "INTERACTIVE") =>
  fetch(`http://${BASE}/run`, { method: "POST", headers: { ...H, "Content-Type": "application/json" },
    body: JSON.stringify({ task, inputs, context, priority }) }).then((r) => r.json());

// the same call with staged progress: onEvent(name, data) per event; resolves with the Result
const stream = (body, onEvent) => new Promise((resolve, reject) => {
  const ws = new WebSocket(`ws://${BASE}/run/stream`, { headers: H });
  ws.on("open", () => ws.send(JSON.stringify(body)));
  ws.on("message", (m) => {
    const f = JSON.parse(m);
    if (f.type === "event") onEvent(f.event.name, f.event.data);
    else if (f.type === "result") resolve(f.result);
    else reject(new Error(`${f.error}: ${f.detail}`));
  });
  ws.on("error", reject);
});

const body = { task: "explain", inputs: { question: "C'est quoi une dérivée ?" }, context: { language: "FRENCH" } };
console.log((await stream(body, (name) => console.log("…", name))).text);
console.log((await run("hint", { problem: "Dérive f(x) = 3x² + 5x" })).text);
```
