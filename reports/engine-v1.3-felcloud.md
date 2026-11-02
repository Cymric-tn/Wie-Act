# Engine v1.3 on FelCloud: slot of Monday 2026-11-02

First live run of the v1.3 sequence (`scripts/slot_run.sh`), plus one extra check_claim run with the 8B as the compact
class (this run only, `MODEL_COMPACT=llama3.1-8b`). Raw files: `reports/felcloud-2026-11-02-*` (the 8B run is
`-check-claim-8b.*`). Secret scan: 0 hits. Across all runs: **0 server 429s, 0 exceptions**, no request after 11:45.
Part 3 below was done offline from those files and the older FelCloud metrics in `metrics/`.

## 1. Results of the sequence

| step | result |
|---|---|
| smoke, every task + 1 OCR | 7 chat requests, 0 429s. OCR 18.6 s, read a screenshot of a LaTeX-converter page (not handwriting) |
| vision-window probe | **a vision request uses the 20/min window**: remaining 19 → 18 (vision) → 17 |
| check_claim eval, 3B | 23/30 overall; **11/18 (61.1%)** on the claims the model decides |
| check_claim eval, 8B | 25/30 overall; **13/18 (72.2%)** on the claims the model decides |
| mixed load, 264 s, cache off | 79 interactive requests, p50 2.62 s, **p95 18.04 s**, max 25.01 s; 73 provider requests, densest 60 s window 20; 0 429s; 20 of 79 degraded (quota_low 10, superseded 4, deadline 3, ungrounded_formula 2, low_confidence 1) |
| explain degraded by deadline | 3 of 17 (17.6%) |

## 2. check_claim: 3B vs 8B (item 2, side by side)

The user's brief says 19 model-decided claims; the eval file says **18** (`checked_by == llm`; the other 12 are sympy 6 and
rules 6, all 12 correct in both runs). Numbers below are on the 18.

| | 3B (llama3.2-3b) | 8B (llama3.1-8b) |
|---|---|---|
| model-decided, correct | 11/18 = 61.1% | 13/18 = 72.2% |
| overall (30) | 23 = 76.7% | 25 = 83.3% |
| French | 3/5 | 3/5 |
| English | 3/6 | 4/6 |
| Arabic | 5/7 | 6/7 |
| true SUPPORTED | 4/8 | 7/8 |
| true CONTRADICTED | 6/7 | 6/7 |
| true PARTLY | 1/3 | **0/3** |
| p50 latency per claim | 2.85 s | 3.98 s (+40%) |
| slowest | 7.8 s | 14.6 s |

Read with care: 18 claims, a gap of 2. The 8B gains come from SUPPORTED claims the 3B called PARTLY (3 of them); on
PARTLY claims it is worse. Not shown here: NOT_IN_COURSE (all 6 are decided by rules and correct). Two caveats on the
files: the 8B run's per-row `requests` field and summary "requests: 14" undercount (4 Arabic rows show 0 although they
generated); the guard file's count is the true one (18 chat requests in both runs). The per-row attribution in
`scripts/eval_check_claim.py` should be checked; not changed.

## 3a. The 7 wrong verdicts of the 3B run, and the 8B on the same claims

| id | claim | expected | 3B | 8B |
|---|---|---|---|---|
| fr-05 | Si f'(a) = 0, alors f a forcément un extremum en a. | CONTRADICTED | NOT_IN_COURSE | PARTLY (wrong) |
| fr-09 | La tangente au point d'abscisse a a pour équation y = f'(a)(x − a) + f(a). | SUPPORTED | PARTLY | SUPPORTED (fixed) |
| en-04 | A local maximum happens where the derivative changes from positive to negative. | SUPPORTED | CONTRADICTED | PARTLY (wrong) |
| en-06 | The derivative of a sum is the sum of the derivatives, and the derivative of a product is the product of the derivatives. | PARTLY | SUPPORTED | SUPPORTED (wrong) |
| en-09 | The slope of the tangent at x = a is the derivative f'(a). | SUPPORTED | PARTLY | SUPPORTED (fixed) |
| ar-01 | مشتقة الدالة الثابتة تساوي صفراً. | SUPPORTED | PARTLY | SUPPORTED (fixed) |
| ar-05 | مشتقة x هي 1 ومشتقة x² هي x. | PARTLY | CONTRADICTED | CONTRADICTED (wrong) |

The 8B fixes 3 of the 7, leaves 4, and breaks one the 3B had right: **fr-06** (the French twin of en-06, expected PARTLY)
became SUPPORTED. The compound claim "sum right, product wrong" (fr-06, en-06) is called SUPPORTED by the 8B both times: the
PARTLY class is the systematic weakness, not model size. Two of the 8B's wrong answers (fr-05, en-04) are one step off
(PARTLY for CONTRADICTED / SUPPORTED); fr-05 ("f'(a)=0 implies an extremum") is one the 3B sent to NOT_IN_COURSE.

## 3b. The `f(x) = x² +` ungrounded flag

- **Not caused by `num_predict`.** The flag comes from the formula extractor, not from cut output. `equations()` reads
  the right-hand side of an equation up to the first token that is not math; a letter outside `{x, y, f, a, h, n, k, u, v, t,
  g}` ends it. A model sentence such as "par exemple f(x) = x² + c" or "x² + bx + c" is read as `x² +`, which is in no
  source, hence `ungrounded_formula`. Reproduced offline: `f(x) = x² + c` → `['f(x) = x² +']`, `f(x) = x² + k` is read
  whole. Also: across the 60 FelCloud 8B explain generations on record the largest output is 198 tokens (cap 350) and none
  has `truncated_output`, so no explain in the record was trimmed.
- The raw model text of that answer was not saved (`model_text` is kept only for the loop/degenerate guards), so the
  exact sentence is not proven; "a parameter letter such as c" is the only mechanism that produces this span in the tests.
- **Trimming can leave an unfinished formula**, in the two cases where `trim_to_last_sentence` returns the whole cut text
  (no sentence end, or the last one is in the first 30%): "… on prend f(x) = x² +". Fixed as asked: when it returns the
  whole text and the text ends on an operator or open bracket, the trailing math run is dropped (never everything).
  Test: `test_a_cut_reply_returned_whole_loses_a_trailing_unfinished_formula` (6 cases). Nothing else in the engine was
  changed. The extractor itself (the `c` case) is left as is: it needs a decision (allow single-letter parameters, or
  treat an unknown letter as a reason to skip the span, not to flag it).
- Side effect: `docs/examples/prep_plan.all_mastered.json` regenerated, since its `start` date is today's date (the
  example drifts every day; unrelated to this change).

## 3c. The 10 `quota_low` results

What the code and the report prove:
- `quota_low` is raised only in `Router.decide`, for a task whose default class is mid-size and has a fallback: of the
  tasks, only **explain**. So all 10 are interactive explains, 10 of the 17 (59%) sent to the 3B instead of the 8B. The
  3 prep pages (BACKGROUND explains) were not degraded.
- It fires when the engine's own window holds fewer than `QUOTA_LOW_THRESHOLD` = 2 sends, i.e. at least 19 of the last
  20 sends in 60 s were already spent when the explain arrived.
- **Interactive load alone is enough, and the prep pages did not do it.** With `BACKGROUND_RESERVE` = 6, a background
  send waits until at least 6 sends remain, so it never takes the window below 5; reaching under 2 needs interactive sends.
  The reserve worked: 2 background requests were held (`waited_for_reserve`: 2). The load: 79 interactive requests with
  the cache off (every classify is a provider request, 31 of them) produced about 70 sends in 264 s, 16 per minute on
  average, and the densest 60 s window was exactly 20.
- **Not recoverable:** at which times, and with how many sends remaining. `load_mixed` keeps its per-run list and its
  metrics in memory (`MemorySink`) and writes only the aggregates, so there is no per-request record of this run (the
  `metrics/calls.jsonl` entries of that day are the smoke and the two check_claim runs). The argument above is derived
  from the code and the totals, not measured per request. Next slot: dump `rec.runs` and the sink records into the report.
- The profile is the stress case (`--no-cache`). Real traffic repeats questions and the cache absorbs them; the quota_low
  rate here is an upper bound for that reason.

## 3d. Explain p95 at the 25 s deadline; EXPLAIN_MAX_TOKENS 350 vs 250

Data: the 60 FelCloud explain generations on the 8B in `metrics/` (Oct 26 to Nov 2; the per-request records of today's
load were not kept, see 3c).

| | |
|---|---|
| completion tokens | min 32, median 113.5, mean 117, **max 198**; 0 reached the cap, 0 `truncated_output` |
| latency | median 10.1 s, max 20.6 s |
| seconds per output token | median 0.092, mean 0.097 |
| fit (least squares) | latency ≈ **4.8 s + 0.050 s × tokens_out** |

- A 350-token generation would take about 22.4 s by the fit; a 250-token one about 17.4 s. The cap changes the worst case
  by 5 s, but **no observed output was near 250**: 198 is the longest, so a cap of 250 would have changed 0 of 60 outputs,
  and the body is already short (the prompt asks for at most 120 words, which is the 113-token median).
- Estimate of the deadline rate with `EXPLAIN_MAX_TOKENS` = 250: **unchanged, about the 17.6% measured** (3 of 17; small
  sample). The fit says a 198-token output takes about 14.8 s, so the three deadline cases (25 s or more) were not long
  outputs; the extra seconds were waiting (the window, the 2-concurrent limit) or a slow server (today's smoke explain: 149
  tokens, 18.7 s, 6 s above the fit). Lowering the cap helps only a generation that runs away (a loop), which the loop
  guard already cuts.
- What could lower the rate, for you to decide: fewer mid-size sends competing for the window (the quota_low fallback
  already moves explains to the 3B under pressure, so the deadline cases are the ones that did get through to the 8B), a
  longer deadline for explain, or serving the cached generation: the generation "continues into the cache", so the
  second ask of the same question is fast.

## What was not done / not verified
- No engine change except the dangling-formula trim (3b). The extractor's single-letter rule, the quota_low behaviour and
  EXPLAIN_MAX_TOKENS are untouched.
- Per-request records of the mixed load, the exact text behind the `x² +` flag, a handwriting OCR test, and the
  `IMAGE_TOKENS_PER_MEGAPIXEL` assumption remain unmeasured. Run counts are small (18 claims, 17 explains).
