# Engine v1.1 on FelCloud, 2026-10-26 (about 20:00 Tunis time)

Branch `engine-v1.1`, not merged. Real provider, simulator off, models `llama3.2-3b,qwen2.5-3b` (compact), `llama3.1-8b` (mid-size). Raw logs: `metrics/smoke_felcloud_v11.jsonl`, `metrics/load_felcloud_v11.jsonl`; full report table `reports/load-felcloud-v11.md` / `.csv`. Compared with the v1 engine's FelCloud run of the same day (`reports/load-felcloud-2026-10-26.md`, n=30 at 30/min, token bucket).

## Verdict

| Expected | Result |
|---|---|
| `pytest -q` passes | **444 passed** (5 integration deselected), run once with `timeout 600` |
| each task's completion_tokens at or under its cap | **yes**: smoke classify 42/50, hint 46/80, explain 161/350; load max classify 45/50, hint 47/80, explain 158/350 |
| local limiter binds | **yes**: the densest 60 s window held exactly 20 sends (25 sends over 84.7 s, send time = record time minus latency); 21 of 25 requests waited > 100 ms locally, longest 9.2 s |
| 0 server 429s | **yes**: 0 refused attempts, every provider request returned 200 |
| 0 exceptions reach the caller | **yes**: 60 results, 0 exceptions |
| 0 repeated identical requests | **yes**: 0 |
| 0 `input_truncated` | **yes**: 0 (no degraded run at all) |

## Smoke (`--no-cache`)

| task | class | provider latency | tokens in / out | cap | cut |
|---|---|---|---|---|---|
| classify | compact (llama3.2-3b) | 10.5 s (first call after idle) | 866 / 42 | 50 | no |
| hint | compact | 6.9 s | 313 / 46 | 80 | no |
| explain | mid-size (llama3.1-8b) | 19.7 s | 356 / 161 | 350 | no |

The classify reply of 42 tokens would have been cut by a cap of 40 (the original brief) and would have gone to the repair ladder; the 50 chosen in `config.py` is justified by this. In the load run the largest classify reply was 45.

## Load: n=60 at 40 arrivals/min (Poisson, 80% interactive / 20% background, seed 1)

| metric | v1 (n=30, 30/min) | v1.1 (n=60, 40/min) |
|---|---|---|
| engine.run calls / exceptions | 30 / 0 | 60 / 0 |
| provider requests (all attempts) | 16 | 25 |
| provider requests / min | 15.5 | 16.3 |
| class split (ok) | 62% compact / 38% mid | 68% compact / 32% mid |
| cache hit rate | 37% | 58% |
| refused attempts | 0 | 0 |
| degraded runs | 4 (ungrounded_formula) | 0 |
| output tokens / ok call | 71.7 | 63.1 |
| outputs cut at the cap | not recorded | 0 |
| classify provider latency p50 / p95 | 1.6 / 1.9 s | 1.9 / 10.7 s |
| hint provider latency p50 / p95 | 1.8 / 2.1 s | 1.9 / 2.8 s |
| explain provider latency p50 / p95 | 13.8 / 16.7 s | 12.8 / 18.6 s |
| `engine.run` latency p50 / p95, classify | 1.6 / 11.9 s | 13.4 / 31.1 s |
| `engine.run` latency p50 / p95, explain | 10.0 / 22.8 s | 17.8 / 41.6 s |
| `engine.run` latency p50 / p95, hint | 1.7 / 11.1 s | 10.0 / 30.4 s |
| INTERACTIVE / BACKGROUND p95 (run) | 22.8 / 16.7 s | 26.8 / 41.6 s |

(Provider latency = one successful request as the provider took it, from the chat records. `engine.run` latency includes the local queue and limiter wait; the report script prints this one.)

Output per task, ok requests: classify mean 39.6 (v1: 38.3), hint 30.7 (21.5), explain 120.8 (138.5). Mean input per ok call 541 tokens (v1: 525).

## Reading the numbers

- The provider is not slower: per-request latency is within noise of the v1 run (explain 12.8 s vs 13.8 s, hint and classify p50 1.9 s). What rose is the wait before sending: at 40 arrivals/min with a 20-per-minute window and 2 concurrent slots, requests queue locally (p50 wait of classify and hint requests about 1.8 s, up to 9.2 s). That is the intended behaviour: it replaces server refusals by local waiting, and none of the 25 requests was refused. The v1 run sent 16 requests at 15.5/min, below the 20-per-minute limit; this run sent 25 in 85 s and reached 20 inside one minute.
- The window bound only at its limit once. The wait columns do not separate the rate wait from the concurrency wait (both are in `wait_ms`); the "queued" progress events are not in the metrics file. The window guarantee itself is covered by the virtual-clock tests (`tests/test_limiter_window.py`).
- The cache hit rate (58% vs 37%) is a property of the question pool and arrival order of this seed, not an effect of v1.1.
- The 4 `ungrounded_formula` degradations of the v1 run did not recur; with 8 generated explains that is not evidence of a change.

## What this run did not exercise

- **The output-cut path on the live provider.** No reply reached its cap (largest 45/50, 158/350), so trimming to the last sentence and `notes.truncated_output` were verified only by unit tests with a fake provider. `options.num_predict` itself was verified by the probes of the morning (exactly N tokens), and is sent in every request here.
- **A server 429.** None occurred, so the stated-delay waits (65 s BACKGROUND, 30 s INTERACTIVE, 2 s concurrency) ran only against the real recorded responses in `tests/test_errors.py` and the virtual-clock tests.
- **The input-truncation detector** never fired live (as expected: the largest input was well under the window); it is covered by unit tests.
- **Fixed vs sliding server window**: still undecided by the probes; the local sliding log is safe under both.

## Requests used

3 (smoke) + 25 (load) = 28 provider requests, in total (the key allows 20 per minute).
