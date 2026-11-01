# Engine v1.3: results, phase by phase

Branch `engine-v1.3` (from `main` at the v1.2 merge), 2026-11-01. **Not merged: waiting for review.**
18 commits, one per step. `pytest -q` → **768 passed** (v1.2 merge point: 487). No Ollama was started.

## What did not run, and why

2026-11-01 is a **Sunday**; the FelCloud slots are on weekdays, so step 18 (the live runs) could not happen. Nothing below
claims a FelCloud number.

| Step 18 item | State | How to run it in a slot |
|---|---|---|
| smoke of every task, incl. one OCR call | **not run** (script ready) | `ENV_FILE=.env.real python -m scripts.smoke` |
| `check_claim` eval, 30 claims | **not run**: no accuracy is claimed. The 11 claims the rules decide are all correct offline (tested). The other 19 need the model | `ENV_FILE=.env.real python -m scripts.eval_check_claim` |
| mixed load: 3 learners, 1 room, 1 prep plan | **not run live**. Run in virtual time through the constraint simulator (below) | `ENV_FILE=.env.real python -m scripts.load_mixed --real --probe-vision --no-cache` |
| does a vision request use the 20/min window | **not measured**. `--probe-vision` sends compact, vision, compact in sequence and reads the platform's remaining-requests header after each | same command |
| integration tests (Ollama) | not run (Ollama stays off) | |

Also unverified: **PyMuPDF could not be installed offline**, so the PDF reader is tested only through a stand-in `fitz` module
(page text, scanned pages, rendering). DOCX and Markdown are tested for real. `IMAGE_TOKENS_PER_MEGAPIXEL=1000` is an
assumption (marked `# ASSUMPTION(felcloud)`) until a real OCR call reports `prompt_tokens`.

## Phase 0: state check

| Item | State before | State now |
|---|---|---|
| v1.1 | merged into `main` | merged |
| v1.2 steps 1–7 (timeouts count toward the breaker, no mid-size timeout retry, per-class timeouts, ghost slots, reserved compact slot, interactive deadline) | committed on `engine-v1.2`, **not merged** | merged (`ff9046c`) |
| v1.2 key-hash scrubbing | **missing** (only in a probe script) | added in `gatekeeper/errors.py`, test |
| outage replay test | xfail (strict) on v1.1 | passes; marker removed |
| tests | one strict XPASS failure (the replay passes on v1.2) | 487 passed once the marker was removed |

## Phase 1: integration surface (target 30 Oct; done 1 Nov)

| Step | Result |
|---|---|
| 1 `docs/ENGINE_API.md` | every task with inputs, outputs, events, degrade reasons, request cost, and 53 JSON examples, **34 from the real engine** (the rest are outcomes that cannot be produced offline, such as the 25 s deadline). The doc is generated; four tests fail when it drifts from the code (examples, doc, event names, degrade reasons) |
| 2 `engine/fake.py` | `FakeEngine`: same `run` signature (asserted), every outcome of every task, modes normal / slow / degraded, `context["fake_outcome"]` to force a state, realistic events and timing (`speed` scales the sleeping), real `room_busy`, plans from the real planner. The generic refusals are checked against the real engine's examples |
| 3 context ids | in every metrics record by `ContextVar`, none in a cache key |
| 4 fairness | `superseded` (one waiting INTERACTIVE per learner) and `room_busy` (one run per room, with an ETA) |
| 5 reserve | `BACKGROUND_RESERVE=6`, event `waiting_reserve`, timer so the dispatcher wakes when window entries expire |
| 6 cache admin | `engine.cache.invalidate(request_id)`; links on writes and on hits; a request id names one generation of an entry |

Tests: 487 → 560.

## Phase 2: content model (target 31 Oct; done 1 Nov)

| Step | Result |
|---|---|
| 7 course graph | `data/courses/analyse.yaml`: the 11 derivative concepts as one unit, plus `limite` as a **draft** concept (it is tagged in a draft doc and has no exercise). Loader, trilingual titles, topological order, prerequisites, cycle and structure errors. The validation test checks graph, `data/docs` tags and bank both ways. To make `quotient`, `inverse`, `racine carrée` (in the bank, in no course text) pass, a **draft** doc was written for review (`derivee-quotient-inverse-racine.fr.md`; drafts are not indexed) |
| 8 dynamic retrieval | add / remove chunks at runtime, per-course persistence, filtering by course + visibility (public, class, private), professor boost 1.3 before the coverage gate with ties to the professor. **Retrieval evals unchanged**: set 13/15, 14/15, off-topic 4/4; cross-language 6/8; manual 2/2 (asserted by a test; baseline taken before the change) |
| 9 `rag/ingest.py` | PDF (PyMuPDF, lazy import), DOCX (standard library), Markdown; chunks by headings; concept tags by BM25 with a confidence; scanned pages flagged `needs_ocr`; no model call |

Tests: 560 → 592.

## Phase 3: new tasks (target 2 Nov; done 1 Nov)

| Step | Result |
|---|---|
| 10 vision class | `MODEL_VISION`, image content parts, resize to ≤ 1 MP, image tokens in the input budget, `TIMEOUT_VISION_S=40`, shares the window and slots; mid-size + vision share `MAX_CONCURRENT − 1` slots |
| 11 `ocr_transcribe` | `{text, latex, confidence_note}`, cache by image content, never grades, `low_confidence` / `invalid_image` degrades. Sample images: none in `reports/`, so they are generated with Pillow |
| 12 `check_claim` | sympy → coverage gate → one compact JSON call, repair ladder, safe default `PARTLY` degraded. 30-claim eval set and runner written; **accuracy not measured** (see above) |
| 13 `check_answer` | all 15 bank exercises: the correct derivation passes with no request, and **all 30 stored mistakes** are caught with their stored feedback (24 as a wrong line, 6 as a wrong final verdict). Buggy rules PRODUCT_AS_PRODUCT, POW_NO_DECREMENT, CHAIN_NO_INNER, CONST_KEPT precomputed per function. Guiding question guarded against leaking |
| 14 `prep_plan` + `generate_page` | rules plan (prerequisites, mastery, even spread at the pace, review pages); page = explain at BACKGROUND via the tutor's cache + bank worked example + 3 quiz items (variants fill gaps) |
| 15 explain `passage` / `board_text` | in the prompt, retrieval on the passage, trimmed to a budget share, hashed into the cache key |
| 16 scanned pages | `Engine.ingest_file`: up to 20 pages per file through `ocr_transcribe` at BACKGROUND, one at a time, stop after two failures |

Tests: 592 → 762, then 768 after the fixes below.

## Phase 4: verification

| Step | Result |
|---|---|
| 17 docs and version | version 1.3; DECISIONS.md (v1.2 and v1.3, with evidence); CLAUDE.md; `.env.example`; the engine guide (new section, tables, file map; PDF rebuilt with `pdflatex` twice, 29 pages, no errors) |
| 18 live runs | not possible today (above). The scripts exist and work |
| 19 this report | |

### Simulated mixed load (virtual time, constraint simulator, canned model replies)

`reports/load-mixed-virtual-v13.md` (cache on) and `reports/load-mixed-virtual-nocache-v13.md` (cache off, the harsher mix). 3 learners, 1 room
(check_claim, check_answer, ocr_transcribe in turn), 1 prep plan generating pages at BACKGROUND, 240 s, FelCloud-like latencies
(compact 2–5 s, mid-size 8–15 s, vision 10–18 s), 20/min, 2 in flight, random mid-size saturation. It tests the engine's
**scheduling**, not the models, and not FelCloud.

| | cache on | cache off |
|---|---|---|
| interactive requests | 96 | 75 |
| interactive p50 / **p95** / max | 0.0 / **17.8** / 25.0 s | 4.2 / **14.7** / 24.6 s |
| provider chat requests | 32 (compact 17, mid-size 11, vision 4) | 74 (compact 59, mid-size 12, vision 3) |
| **server 429s** | **0** | **0** |
| densest 60 s window | 15 | **20** (at the limit, not over) |
| exceptions out of `engine.run` | **0** | **0** |
| degraded interactive | 3 (superseded 2, deadline 1) | 7 (superseded 2, quota_low 5) |
| prep pages generated (degraded) | 6 (0) | 3 (0): the plan slows under load instead of hurting learners |
| requests held for the reserve | 0 | 1 |

All four acceptance checks pass in both runs. In the cache-off run the window reaches exactly 20 and five explains are served in
`quota_low` fast mode: that is the engine protecting the quota, and what a class of learners without cache would look like.

## Engine changes beyond the spec, and why

1. **A real bug found by the mixed load: the interactive deadline did not cover identical requests.** An INTERACTIVE explain that
   found its key already being generated waited for the leader with no limit, and if the leader's answer was not cached it then ran
   its own generation with no deadline (one explain took 41 s). Fixed: the wait is bounded by the learner's own remaining
   deadline, and a follower becomes a leader through the normal deadline path. Three tests.
2. **`check_claim`: half verified, half refuted is `PARTLY`**, not `CONTRADICTED` (the contract only says refuted → CONTRADICTED).
   Also: only connectives may surround a formula for sympy to settle it (a first version would have called
   `(x²)' = 2x et la dérivée d'une constante est 1` SUPPORTED), and `source_chunk_id` is null for computed verdicts.
3. **`check_formulas` could not refute `(x²)' = x`** (it files `(2x) = x` under "equations to solve"), so `engine/claims.py` decides
   the derivative-on-the-left case itself; explain's behaviour is untouched. `parse_expression` could not read `2√x`: **fixed in
   `engine/mathcheck.py`, which explain's grounding checker also uses**, so explain may now verify or refute a few formulas it used
   to leave unparsed. The explain eval was not rerun (it needs a model).
4. **`check_answer`: a labelled line must be what its label says** (`y = 2x` for a tangent problem is the derivative, not the tangent).
5. **Heavy-class slot budget**: mid-size and vision together hold at most `MAX_CONCURRENT − 1` slots. Observed cost: an interactive
   photo can queue behind a mid-size generation that is still running into the cache (24.6 s in the cache-off run).
6. `invalid_image` is a degraded result, not a `ValueError` (it is the learner's photo); `generate_page` returns a dict, not a
   `Result`; prep focus points carry `reason_code` and `minutes` besides the contract fields.
7. The OCR output cap no longer shrinks the room left for text prompts (it had taken 350 tokens from every explain).
8. Tests were writing runtime material into the real `data/index/courses/`, which silently changed an unrelated retrieval test
   (found by bisecting). Each settings object a test builds now has its own temporary course-index directory.

## For review

- Merge `engine-v1.3` into `main` when you are satisfied (not done).
- Review the draft `derivee-quotient-inverse-racine.fr.md`: it is course text I wrote so the graph, the docs and the bank agree.
- Next weekday slot: run the three commands in the table at the top and attach the numbers; the `check_claim` accuracy and the
  vision-window measurement are the two things the contracts document needs from them.
