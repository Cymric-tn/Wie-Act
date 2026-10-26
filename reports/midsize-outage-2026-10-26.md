# Mid-size outage on FelCloud, 2026-10-26 (about 20:46 Tunis time)

`llama3.1-8b` stopped answering at about 20:30: every request, down to a 5-token prompt, ends in `ReadTimeout` (85 s in the engine, 40 s in the pings), while `llama3.2-3b` answers normally (ping 4.9 s). The test below is `scripts/outage_test.py` (new; observation only, no engine change), run through `engine.run` at INTERACTIVE priority with the real settings (85 s request timeout, 4 attempts, 2 concurrent slots, breaker threshold 3). Raw data: `reports/midsize-outage-raw.json` (the key hash in the error text is redacted), `metrics/outage_midsize.jsonl` (git-ignored; its 429 `detail` still carries the hash).

Cut at the cap: the test stopped when 10 provider attempts had finished (phase 1) / 12 (with the follow-up explain), as asked ("about 10"). It therefore **did not run until the engine gave up**: see answer (a).

## Timeline (seconds since start; `wait` is the local queue/limiter wait)

| t | event |
|---|---|
| 0.1 | explain1, explain2 (distinct questions, retrieval hit, no cache) and classify1..3 enter the queue; explain1+2 take both slots (`generating`, mid-size attempt 1); classify1..3 queued at positions 2, 3, 4 |
| 10.0 | classify4 arrives, queued behind them |
| 85.3 | both explains time out (`ReadTimeout`, 85.2 s) → `backoff` 0.3 / 0.2 s, no fallback event; **the two slots go to classify1 and classify2** (they were queued before the retries came back) |
| 100.1 / 100.4 | classify2, classify1 answered (compact, 14.8 s and 15.1 s provider latency, slower than the usual 2 s); the explains retry mid-size (attempt 2) and take both slots again |
| 185.1 / 185.4 | explain attempt 2 times out on both → `backoff` 0.6 / 0.0 s; slots go to classify3 and classify4 |
| 187.5 / 194.2 | classify3 answered (2.4 s), classify4 answered (8.8 s) |
| 187.5 / 194.2 | explain attempt 3 starts on both (mid-size) |
| 272.5 | explain1 attempt 3 times out → attempt 4 starts immediately |
| 279.2 | explain2 attempt 3 times out → `backoff`; cap (10 finished attempts) reached at 279.5 s, both explains cancelled while still retrying |
| 279.5 | phase 2: one more explain (`explain_after`) is dispatched to mid-size at once (the router still routes it there) |
| 279.7, 281.7 | both of its attempts get **429 `max_parallel_requests`** (the key's 2 slots are still held server-side by the requests the engine just abandoned), `wait_slot` 2 s each; cap (12) reached at 282.5 s, cancelled |

Per call:

| call | time to Result | degraded | attempts (finished) | events seen | local wait |
|---|---|---|---|---|---|
| classify1 | 100.4 s | no | 1 | queued, generating | 85.2 s |
| classify2 | 100.1 s | no | 1 | queued, generating | 0 s (own wait_ms; it had waited 85 s for the slot: the record's `wait_ms` is the last wait only) |
| classify3 | 187.5 s | no | 1 | queued, generating | 84.7 s on the last wait, 187 s in total |
| classify4 (+10 s) | 184.2 s after its arrival | no | 1 | queued, generating | 0.3 s on the last wait, 184 s in total |
| explain1 | none (cancelled at 279.5 s, in attempt 4) | n/a | 3 timeouts finished + attempt 4 in flight | generating ×4, backoff ×3, queued ×3 | 15 s, 2.7 s between attempts |
| explain2 | none (cancelled at 279.5 s, in attempt 4) | n/a | 3 timeouts finished | generating ×3, backoff ×3 | 0.6 s, 6.7 s between attempts |
| explain_after | none (cancelled at 282.5 s) | n/a | 2 refused with 429 concurrency | generating ×2, wait_slot ×2 | 0 / 2.0 s |

No call produced `falling_back_to_compact`, `breaker_open`, `fast_extractive` or `gave_up`.

## Answers

**a. How long before an explain degrades to fast mode?** Not within the 279 s observed, and the data cannot say when. The retry layer spends 4 attempts of 85 s on a hung mid-size request, so the explain cannot give up before about 4 × 85.2 + backoffs ≈ **345 s (5.7 min)**; what happens after `gave_up` (fallback to compact, or fast mode) was not observed because the cap stopped the test. A learner who asked for an explanation would have seen only the partial-result excerpts (the `partial_result` event with the retrieved chunks arrives at once) for at least 4.6 minutes.

**b. Does the breaker open on timeouts?** No. Six mid-size timeouts finished (3 per explain) and the follow-up explain was still routed to mid-size with no `breaker_open` record. In `router/policy.py::Router.observe` only `ModelSaturated` counts as a failure; `TransientError` (timeout, 5xx) is ignored, so a hung model never trips the breaker.

**c. Are the classify calls blocked behind the hung explains?** Yes, for as long as the explains hold the slots. The 2 concurrent slots are the bottleneck, and a hung explain holds one for 85 s per attempt. classify1 and classify2 waited **85 s** (then took 15 s); classify3 and classify4 waited **a further cycle**, answering after **187.5 s** and **184 s** (compact, which was healthy). The retried explains keep their queue position (the queue reuses the sequence number across retries, by design), so they re-take both slots ahead of classify3/4 at t=100 s. A learner turn is classify first, so every turn would have been blocked 85 to 190 s by two hung explains even though compact worked.

**d. A fresh explain right after: fails fast or hangs?** Neither cleanly. It was routed to mid-size immediately (no breaker), and its two attempts were refused in 0.1 to 0.2 s with a concurrency 429, because the server still counts the abandoned requests against the key's 2 parallel slots for some time after the client disconnects. After the 2 s `wait_slot` it would have hung 85 s like the others. The engine gave no early signal that mid-size was down.

Side findings:
- The background watcher's pings were part of the load: ping#2 at 20:46:38 (t ≈ 49 s) got a 429 in 0.2 s for the same reason (both slots held by the hung explains).
- Compact classify took 15 s twice while mid-size requests were hung (usual 2 s); it may be a shared upstream effect, one observation.
- A 429 error `detail` in the metrics record contains the key's hash from the provider's message; the metrics file is git-ignored, but `gatekeeper/errors.py` should scrub it.

## Requests used by this test

12 finished provider attempts (6 mid-size timeouts of explain, 4 classify answers, 2 concurrency 429s) plus the attempts in flight when the test was cut (explain1 attempt 4, and possibly explain2 attempt 4): **13 to 14 provider requests**. The watcher sent 3 more pings in the same period (1 answered 429). Evening total on the key, rough count: Part 2 capacity 23, aborted explain evals about 13, pings and probes 6, this test 14.

## Engine changes this justifies (listed, not implemented)

1. **Timeouts and 5xx on mid-size count toward the breaker** (`Router.observe` ignores `TransientError`). Evidence: 6 timeouts in 280 s, no breaker, the next explain still sent to the dead class. With the threshold of 3, the breaker would have opened at t=185 s at the latest (after the first 3 timeouts, 85 s, with two concurrent attempts); a half-open probe every 30 s would cost one request.
2. **Shorter per-class timeout for interactive mid-size explain**, for example 35 s (the healthy p95 is about 23 s on FelCloud; the 85 s value is the server's 90 s limit). Evidence: every attempt here burned 85.2 s of a slot. With 35 s and 4 attempts the stall would cost 140 s instead of 340 s; combined with change 3, 35 s.
3. **No retry on a mid-size timeout before falling back**: after one timeout fall back to compact explain or fast mode (degraded `midsize_timeout`, visible). Evidence: attempts 2 to 4 all timed out too; the hung request is not transient on a 85 s scale. The learner then waits about 35 s instead of 5.7 min.
4. **Do not let mid-size hold both slots**: reserve one concurrency slot for compact (or cap mid-size in flight at 1). Evidence: classify1..4 waited 85, 85, 187 and 184 s behind two hung explains while compact worked; this is what a learner turn feels like during an outage.
5. **Let a retry go to the back of its priority class for a different class** (or give compact calls precedence when mid-size is unhealthy): at t=100 s the retried explains re-took both slots ahead of classify3 and classify4 because the queue preserves their place across retries.
6. **Treat an abandoned request as still occupying a slot**: after a client timeout or cancel, count the slot as busy for a short grace period (about 10 s) so the next request is not refused with a concurrency 429; or shorten the wait after a concurrency 429 on timeout-abandoned requests. Evidence: explain_after got two 0.1 s refusals right after the cap cancelled its predecessors.
7. **Scrub the key hash** from provider error messages in `gatekeeper/errors.py` before they reach `CallRecord.detail`.
8. **Deadline-aware downgrade** for INTERACTIVE explain: if the estimated wait (queue ETA plus the timeout) exceeds a deadline (for example 40 s), answer in fast mode at once and keep generating in the background when mid-size returns (`followup_available`).
