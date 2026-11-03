# Engine ↔ app integration: audit and fix plan

Revision of 2026-11-03 (replaces the 2 Nov draft, which assumed the app had no AI layer). Read from `app/` as it stands:
`server/src/ai/*` (feature layer, preview engine, bridge client), `bridge/bridge.py` (hosts the real engine), `client/src/lib/engine.js`,
the dashboards (Prep, Materials, Review) and `BoardAI.jsx`. The bridge was run against the **real** `Engine` (a scratch copy of this
repo, an unreachable provider URL, no network): `/health`, `/exercises`, `/courses`, `/follow_up`, `/run`, `/materials` and `DELETE`.
Nothing here was run against FelCloud or through a browser.

## 1. What the app built

Browser → `/api/ai/*` (Node feature layer: input whitelist per task, ids, tutor loop classify → follow-up → explain / hint / quiz / grade,
exercise grading with solutions kept server-side, mastery per learner from attempts, run log, review queue, materials store) →
`http-engine.js` → `bridge/bridge.py` (stdlib HTTP, NDJSON stream, one `Engine` on a loop thread) → engine. When the bridge is down
(`ENGINE=auto`) a **JavaScript preview engine** (`server/src/ai/fake/`) answers. Client: AI Companion, exercise cards, Prep (plan, study
pages, level test), Materials (upload, concept tags), Whiteboard AI tab (ask, fact-check, check work, read handwriting, pin to board),
professor AI review. This is most of the plan I wrote on 2 Nov. The remaining work is making it match the real engine.

## 2. Findings (ordered by what breaks the demo)

**The preview engine defines a contract the real engine does not have, so the app works on the preview and breaks on the engine.**
Each row was reproduced against the real engine through the bridge, except the shape rows, which are read from `docs/examples/`.

| # | App sends / reads | Real engine | Effect |
|---|---|---|---|
| 1 | `check_answer` input `lines` (BoardAI "Check my work") | requires `problem` + **`steps`**; answers `{steps:[{index,verdict,note}], first_wrong, final_verdict, feedback}`; the app reads `{lines, verdict, question}` | HTTP 400 "needs inputs ['steps']"; and the UI would not read the answer |
| 2 | `prep_plan` input `{course_id, days_left, minutes_per_day, mastery}`; reads `{sessions, order, assessment, days}` (Prep.jsx) | requires **`scope`, `deadline`, `pace_min_per_day`**; answers `{focus_points[], assessment_exercise_ids}` + notes | HTTP 400; Prep plan screen cannot render the real shape |
| 3 | `generate_page` as a task through `/run` (study pages, "more practice") | not a task: **`Engine.generate_page(plan, focus_point, context)`** (a method) | `unknown or not yet implemented task 'generate_page'`; the bridge has no route for it |
| 4 | `check_claim` read as `{verdict, explanation, correct_form}` | `{verdict, correction, source_chunk_id, checked_by, parts?}` (v1.4: `parts` for compound claims) | verdict label works, the correction never shows |
| 5 | Materials upload: bridge passes `learner_id`, `material_id`, `priority`, and `source: "upload"` for students | `ingest_file(file, course_id, unit_id, owner, visibility, source, *, class_id, ocr, add, **kw)`: `owner` not `learner_id`; `source` is `professor` / `learner` / `course`; extra keywords reach `ingest()` | reproduced: `TypeError: ingest() got an unexpected keyword argument 'learner_id'`, every upload ends `failed` |
| 6 | The bridge never passes **`add=True`** | chunks join the live index only with `add=True` (or `add_material`) | once #5 is fixed, uploaded material is still not searchable |
| 7 | `DELETE /materials/:id` → bridge `remove_material(course_id=…)` | removes by `chunk_ids` or by `source` file (+ `course_id`, `owner`) | reproduced: a no-op that returns `ok`; deleted files stay searchable. (Not the wipe I feared: with no source or ids nothing is removed.) Fix: `remove_material(source="<stored file name>", course_id, owner)` |
| 8 | `PATCH /materials/:id/chunks/:chunk` (professor confirms a concept tag) | updates the app's own store only | the engine keeps the old tag: no engine method yet (my earlier item C1) |
| 9 | `GET /health` → `version: "1.3"` | `engine.__version__` was never bumped after v1.3 | cosmetic: fix to `1.4` on the engine side |
| 10 | Bridge default port `8765` | `services/engine_server.py` defaults to `8765` too | run one or the other, never both; I recommend the app keeps its bridge |

What works (reproduced): `/health`, `/exercises/<id>` (full solution, hints, mistakes: the engine's own bank), `/courses/analyse`,
`/follow_up` (the router's real rule: a lost learner is taught before being tested), `/run` NDJSON streaming of real events
(`retrieving`, `checking_cache`, `partial_result`, `queued`, …), the degraded `breaker_open` / fast-mode path when the provider is
unreachable (the app's "simplified answer" state), and `classify` → follow-up → `explain` / `hint` / `quiz` / `grade` inputs
(`question`, `problem`/`attempt`/`stored_hints`/`hint_level`, `message`/`concept`/`exclude`, `problem`/`solution`/`attempt`/`common_mistakes`).

**Other observations**
- **Content:** the engine has one course (`analyse`: derivatives). The dashboards still use `MATH102` / `CS201` / `PHYS101`, and
  `store.mastery` / exercise attempts default to `MATH101`. A question outside the derivatives course answers "not in the course".
  The preview engine ships a derivatives course too, so the demo is consistent only if the app's course is `analyse`.
- **Review:** the app keeps its own run log and review queue (reject → `cache.invalidate` works). The engine's `/review/*` and
  `/metrics/summary` (v1.3.2) are not used; fine, but the numbers shown to judges should come from one place: the engine's
  metrics are the ones that include provider requests, cache hits and 429s.
- **Ids:** `learner_id`, `room_id`, `course_id`, `class_id` reach the engine context. Good; `class_id` is what the engine's
  visibility rule needs for class-only material.
- **Privacy:** private learner uploads need `owner` = the learner and `visibility: private`; the app's `source: "upload"` must become `learner`.
- **Rate limit:** 40 AI requests per minute per address in Node; the engine's own window is 20 sends per minute for the whole key,
  with fairness per learner. The engine will answer 429-safe (`quota_exhausted` with an ETA); the UI handles that state.
- `app/server/data/whiteboard.db*` is not git-ignored.

## 3. Fix plan

Ownership proposal: engine-side items are mine; the rest touch `app/` and need the app owner's go-ahead (or a branch I may write on).

| Step | Where | What | Verifies |
|---|---|---|---|
| 1 | `bridge.py` | `add_material`: call `ingest_file(path, course_id, owner=learner_id, source=professor\|learner, visibility, class_id, add=True)`; map `source: "upload"` → `learner`; keep the stored file name for removal; return `chunks[].{chunk_id, concept, confidence}`, `needs_ocr`, `low_confidence`, `ocr`; `remove_material(source=<file>, course_id, owner)`; `GET /health` real version | upload then ask a question answerable only from the file; delete then ask again |
| 2 | `bridge.py` + `http-engine.js` | `POST /generate_page` → `Engine.generate_page(plan, focus_point, context)`; Node `run("generate_page")` calls it | study page on the real engine |
| 3 | `routes.js` + Prep.jsx | `prep_plan`: send `scope {unit_ids}`, `deadline` (date), `pace_min_per_day`, `mastery`; render `focus_points[]` and `assessment_exercise_ids` (exercise statements fetched from the bank) | plan for a learner with real mastery |
| 4 | `routes.js` + BoardAI.jsx | `check_answer`: send `steps` (first line = the problem if no `problem`); render `steps[].verdict/note`, `first_wrong`, `final_verdict`, `feedback`; `check_claim`: show `correction` and `parts` | the three board chips |
| 5 | `server/src/ai/fake/` | make the preview engine return the real shapes (or drop it and use `bridge.py --fake`, the engine's own `FakeEngine`, which is generated from the contract and tested against it); run the app's AI tests against that | the preview can no longer disagree with the engine |
| 6 | engine | `Engine.confirm_chunk(chunk_id, concept)`; `__version__ = "1.4"`; (small, needs your go-ahead: the engine is frozen) | professor tag confirmation reaches the retriever |
| 7 | engine data | demo course decision (adopt `analyse` or write content), review of the three draft docs | the demo questions answer from the course |
| 8 | both | one end-to-end pass on FelCloud in a slot with the app on top: 3 learners, a room, a prep plan, an upload; read `/metrics/summary` and the run log | no 429, p95 inside the 25 s deadline |

Order: 1 → 2 → 3 → 4 (a day, mostly mechanical, each with a bridge-level test against the real `Engine` over an unreachable
provider, which needs no network and no model), then 5, then 8 in the next slot. 6 and 7 can run in parallel.

## 4. Decisions needed
1. May I patch `app/bridge/bridge.py` and the Node/React call sites in steps 1–5, on a branch, or does the app owner do it from this audit?
2. Replace the JavaScript preview engine by `bridge.py --fake` (step 5), or keep both and align the JS one?
3. Demo course: `analyse` for every dashboard, or content for `MATH102` / `CS201` / `PHYS101`?
4. Go-ahead for step 6 (one small engine method plus the version string)?
