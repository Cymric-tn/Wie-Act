# FelCloud: priority, capacity and explain quality, 2026-10-26 (engine v1.1)

Real provider, simulator off, `.env.real`, models `llama3.2-3b` / `qwen2.5-3b` (compact) and `llama3.1-8b` (mid-size). Merged into `main` and tagged `engine-v1.1` before these runs (secret scan clean: the real key appears in no commit; the only hits were placeholders and code). The mid-size model was down from about 20:30 to 21:10; see `reports/midsize-outage-2026-10-26.md`. Parts 1 and 2 ran before the outage, Part 3 after it.

Request count (provider requests on the key this evening, after the v1.1 verification): Part 1 0; Part 2 23; Part 3: 36 for the eval, 4 for the loop re-check; the aborted eval attempts during the outage and the outage test are counted in the outage report.

## Part 1. Priority under overload (`metrics/load_felcloud_v11.jsonl`, n=60 at 40/min, 50 INTERACTIVE + 10 BACKGROUND, 25 uncached runs)

`engine.run` latency = limiter wait (`wait_ms`) + provider latency + queue wait in the priority queue (the remainder, not recorded as such). Uncached runs only; seconds.

| priority / task | n | run p50 / p95 | limiter wait p50 / p95 / max | provider p50 / p95 |
|---|---|---|---|---|
| INTERACTIVE, all | 21 | 13.1 / 26.8 | 1.6 / 9.0 / 9.2 | 2.8 / 13.1 |
| INTERACTIVE classify | 8 | 13.4 / 28.7 | 2.4 / 9.0 / 9.0 | 2.0 / 10.7 |
| INTERACTIVE explain | 7 | 17.8 / 26.8 | 0.2 / 5.5 / 5.5 | 11.9 / 18.6 |
| INTERACTIVE hint | 6 | 6.4 / 23.6 | 1.9 / 9.2 / 9.2 | 1.9 / 2.8 |
| BACKGROUND, all | 4 | 31.1 / 41.5 | 1.4 / 1.7 / 1.7 | 1.9 / 12.8 |
| BACKGROUND classify / hint / explain | 2 / 1 / 1 | 22.1-31.1 / 30.4 / 41.5 | 1.4-1.7 / 0.2 / 0.2 | 1.6-1.7 / 1.9 / 12.8 |

Queue wait (run latency minus limiter wait minus provider latency): INTERACTIVE p50 0.8 s, p95 12.2 s, max 20.1 s; BACKGROUND p50 28.4 s, p95 28.5 s (all four about 28 s, n=4).

- **Background absorbed the queueing:** the four uncached background runs each waited about 28 s in the priority queue and 0.2 to 1.7 s at the limiter, against a median 0.8 s queue wait for interactive requests. The priority order worked.
- **Did interactive requests wait behind background ones?** No. For the six interactive requests that waited more than 3 s at the limiter, none had a background request ahead in the system in 4 of the 6 cases (the other 2 had 4). Their wait came from other interactive requests (2 to 6 others in the system) and from the 20-per-minute window, not from background traffic.
- **Interactive p95 is above 25 s** (26.8 s run latency, 28.7 s for classify). Provider time is not the main cause (provider p95 10.7 s for classify, 18.6 s for explain); limiter plus queue wait is (up to 9.2 s at the limiter, up to 20 s in the queue). The sample is small (n=21 interactive, 4 background).

## Part 2. Real capacity (`metrics/capacity_felcloud_v11.jsonl`, 40 learner turns, offered load 15 engine calls/min)

| metric | FelCloud v1.1 (this run) | local v1 (Ollama + simulator, `reports/load-engine-v1.md`) |
|---|---|---|
| learner turns / engine calls | 40 / 69 | 40 / 69 |
| engine calls per turn | 1.73 | 1.73 |
| provider requests per turn, observed (cached + uncached) | **0.57** (23 requests) | 0.68 |
| provider requests per cold turn (13 of 40 turns) | **1.77** (10 turns of 2, 3 of 1) | 2.00 |
| cache hit rate | 62% | 61% |
| refused attempts / degraded runs / exceptions | 0 / 0 / 0 | 3 / 2 / 0 |
| class split (ok) | 78% compact / 22% mid-size | 75% / 25% |
| mean provider latency | 5.4 s | 11.5 s |
| cold-turn latency (classify, then the follow-up task) p50 / p95 / max | **10.3 / 16.6 / 22.0 s** | n/a (per call, interactive p95 49.6 s) |
| all turns p50 / p95 (62% served from cache) | 0.0 / 13.3 s | n/a |
| binding limit | 20 req/min quota (concurrency ceiling 2 × 60 / 5.4 s = 22.3 req/min) | concurrency, 10.4 req/min |

Sustained rate at 20 requests/min: **11.3 cold turns/min** (20 / 1.77) or 34.8 turns/min at this run's cache rate (20 / 0.575).

| active learners per key | cold turns (planning figure) | at this run's 62% cache rate |
|---|---|---|
| 1 message / minute | **11** | 35 |
| 1 message / 2 minutes | **23** | 70 |
| 1 message / 30 s | 6 | 17 |

Comparison with the guide's local estimate (about 20 learners at one message per 2 minutes, 10 at one per minute, bound by concurrency at the local 8B latency): the cold-turn figure on FelCloud is **23 and 11**, the same order, but now bound by the quota instead of concurrency because FelCloud's compact calls take 2 s and mid-size 12 s. The quota binds only slightly (22.3 vs 20 req/min), so a mid-size outage or slower mid-size calls (as seen later this evening) moves the limit back to concurrency. The 62% cache rate comes from a 14-message question pool and is not a prediction for real learners; use the cold-turn column.

## Part 3. Explain quality on the 8B (`reports/eval_explain_felcloud.json`, 35 questions, BACKGROUND priority, sequential, fresh cache)

Set: the 25 on-topic questions of the retrieval sets plus 10 new ones (`tests/eval/explain_extra.jsonl`): FRENCH 10, ENGLISH 6, ARABIC 5, DARIJA 6, ARABIZI 8. Every indexed chunk is the expected source of at least one question (15 of 15). `limite.fr.md` and `derivee-definition.en.md` are still `status: draft`, so they are not indexed and no question can reach them.

| metric | FelCloud llama3.1-8b (35 answers) | local run (91 answers, Ollama 8B) |
|---|---|---|
| answers with new examples | 3 (9%) | 10 (11%) |
| formulas verified by sympy | 21 formulas in 16 answers | n/a |
| `wrong_math` | 1 (3%) | 1 (1%) |
| `ungrounded_formula` (answers) | 3 (9%), counting x-fr-4 which is also wrong_math | 2 (2%) |
| loop rate | **4 (11%)**, 1 salvaged, 3 to fast mode | 2/50 (4%), salvage 1/2 |
| language drift / contamination | 0 / 0 | n/a |
| truncated output (cap 350) | 0 | n/a |
| output tokens per generated answer | mean 110.1 (n=34, one cache hit) | n/a |
| latency (generated) p50 / p95 | 9.3 / 12.2 s | n/a |

Outcome counts: ok 28, `explain_guard:degenerate` 3, `ungrounded_formula` 2, `explain_guard:loop_truncated` 1, `wrong_math` 1. The numbers are one run at temperature 0.3 on 35 questions; a difference of 1 or 2 answers is noise.

### Every flagged formula

| id | flagged text | verdict |
|---|---|---|
| x-fr-4 (wrong_math) | "f'(x) = 0 + 5 + 6x²" in "si f(x) = 3x² + 5x − 7, on a f'(x) = 0 + 5 + 6x² = 6x² + 5" | **real model error.** The derivative of 3x² is 6x, not 6x²; the final answer 6x² + 5 is wrong too. |
| x-fr-4 (ungrounded) | "f(x) = a +", "f'(x) = a' + b'x + c'x²" | **real model error** for the second ("derivatives of the coefficients a', b', c'" is nonsense). The first is a **checker limitation**: the span is cut at "a +" although the answer wrote "a + bx + cx²". |
| ar-2 (ungrounded) | "= x^2" twice, in "m( x ) = x^2 … الدالة f( x ) = x^2 تزايدية على [0, 2]" | **real model error** (mild): the answer calls x² a function m that must be "positive", mixing the function with its derivative; the example is also not in the course. The checker's span is a right-to-left parsing artifact but the underlying text is muddled. |
| x-en-3 (ungrounded) | "f(0) = 1" for f(x) = 2x² + 1 | **checker false positive** on this formula: f(0) = 1 is correct (the checker verifies derivative claims, not function values, and the course has no such example, a data gap). **But the answer contains a real error the checker cannot see:** "f'(x) is positive for x < 0 and negative for x > 0, f has a local minimum at x = 0". For f'(x) = 4x the signs are reversed. |
| xdar-3, xazi-4 (new examples only) | u(x) = x², v(x) = x + 1 with (u·v)' = 3x² + x ; f(t) = 2t + 1, f'(t) = 2 | verified correct, not flagged as errors. |

### Loop rate

4 of 35 answers (11%) were guarded as repetition: fr-3, fr-6 and azi-1 `degenerate`, xazi-4 `loop_truncated`. The raw text of those four was not saved by the eval, so I asked the same four questions again (4 requests, temperature 0.3, different samples): fr-3 and xazi-4 came back clean, fr-6 gave a wrong\_math answer (the model applied "the rule for f(x) = x² + 7" to x(x+1) and wrote contradictory steps), and **azi-1 was guarded as `degenerate` again although its text is correct and does not loop**:

> La dérivée de u est 6x, puisque la dérivée de x² est 2x. La dérivée de v est 5, puisque la dérivée de x est 1. … La dérivée de 3x² est 3 × 2x = 6x.

`loop_start` sees "la dérivée de" three times within 12 words each and calls it a loop. That is a **detector false positive on well-structured parallel sentences**, reproduced twice; it sends a correct answer to fast mode. The true loop rate on FelCloud is therefore below 11%, but the other three cases cannot be classified without their raw text (the eval should save `notes.model_text`).

### Reading

- The FelCloud 8B is not worse than the local 8B on the checked quantities (wrong\_math 1 vs 1, within noise) but it makes mistakes the formula check does not see: the sign error in x-en-3, and the x(x+1) answer in fr-6. The check covers formulas, not prose reasoning.
- No language drift, no contamination, no truncation at 350 tokens (mean 110), latency 9.3 s p50 and 12.2 s p95, in line with the earlier FelCloud explain latency (12.8 s p50).

## Engine and content changes these results justify (listed, not implemented)

1. **Make the loop detector ignore parallel structure** (for example require the repeated phrase to continue past 3 words, or 4+ repeats of the same 4-gram). Evidence: azi-1 reproduced twice as a false positive; this inflates the loop rate and sends correct answers to fast mode.
2. **Save `notes.model_text` for every guarded answer in `eval_explain`** so loop cases can be classified. Evidence: 3 of 4 loop cases here are unclassifiable.
3. **Verify function values and sign claims in the formula check** (`f(a) = v` from a defined `f`; "positive for x < 0" against the derivative's actual sign), or add a second compact judge call only for explain answers with a derivative. Evidence: x-en-3's false positive and its missed sign error; x-fr-4's truncated span "f(x) = a +".
4. **Add worked examples to the course** for the extremum and the polynomial derivative (`derivative-variations.en`, `derivee-formules.fr`), so the model copies rather than invents. Evidence: both x-fr-4 and x-en-3 invented their examples. Review and index the two drafts (`limite.fr.md`, `derivee-definition.en.md`).
5. **Plan capacity on cold turns**: 11 learners at one message per minute and 23 at one per 2 minutes per key; the quota binds only barely (concurrency ceiling 22.3 req/min), so the mid-size outage changes the limit (see the outage report).
6. **Deadline-aware downgrade and priority tuning** for interactive requests: interactive p95 is 26.8 s (above 25 s) at 40 arrivals/min, caused by limiter and queue wait. A rule that sends an interactive explain to fast mode when the queue ETA plus the expected latency exceeds about 25 s would cut the tail. Evidence: Part 1.
7. The eight outage changes (breaker on timeouts, 35 s interactive timeout, fall back after one timeout, reserved compact slot, ...) are in `reports/midsize-outage-2026-10-26.md`.
