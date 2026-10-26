| section | metric | capacity-felcloud-v1.1 |
|---|---|---|
| volume | engine.run calls (caller-visible) | 69 |
|  | nested compress runs | 0 |
|  | provider requests (all attempts) | 23 |
|  | provider requests ok | 23 |
|  | duration (s) | 358.6 |
|  | provider requests / min | 3.8 |
| calls per model | llama3.1-8b (midsize) ok / attempts | 5 / 5 |
|  | llama3.2-3b (compact) ok / attempts | 18 / 18 |
| class split (ok calls) | compact | 18 (78%) |
|  | midsize | 5 (22%) |
|  | purpose: task | 23 |
| tokens | input tokens | 14399 |
|  | output tokens | 1264 |
|  | input tokens / ok call | 626 |
|  | output tokens / ok call | 55.0 |
|  | outputs cut at the cap (completion_tokens >= cap) | 0 |
| limiter | attempts that waited on the limiter (> 100 ms) | 1 |
|  | max limiter wait (s) | 1.3 |
| cache | cache hits / runs | 43 / 69 |
|  | cache hit rate | 62% |
|  | negative cache hits (fast mode, 0 requests) | 0 |
|  | repeated identical provider requests | 0 |
| refusals -> handling | refused attempts (total) | 0 |
| fallbacks / degradation | degraded runs | 0 |
|  | explain served fast_extractive | 0 |
|  | explain served full (generated) | 9 |
| circuit breaker | trips (closed -> open) | 0 |
|  | re-opened after failed probe | 0 |
|  | closed after probe | 0 |
| latency (uncached runs, ms) | classify p50 / p95 (n) | 1978 / 10430 (13) |
|  | explain p50 / p95 (n) | 8818 / 15125 (8) |
|  | hint p50 / p95 (n) | 2343 / 2366 (5) |
|  | prio: BACKGROUND p50 / p95 (n) | 2343 / 10272 (3) |
|  | prio: INTERACTIVE p50 / p95 (n) | 1978 / 12225 (23) |
| capacity (per learner turn) | learner turns | 40 |
|  | engine calls / turn | 1.73 |
|  | provider requests / turn (all attempts) | 0.57 |
|  | ok provider calls / turn | 0.57 |
|  | cold turns (no cache hit): n / requests per turn | 13 / 1.77 |
|  | learner wait per turn p50 / p95 (ms) | 2 / 13254 |
| capacity estimate (one key, 20 req/min) | mean provider latency (s) | 5.4 |
|  | concurrency ceiling (2 x 60 / latency, req/min) | 22.3 |
|  | binding limit (req/min) | 20.0 (quota) |
|  | active learners @ 1 turn / 120 s: quota-bound (observed / cold) | 70 / 23 |
|  | active learners @ 1 turn / 120 s: at observed latency (observed / cold) | 70 / 23 |
|  | active learners @ 1 turn / min: quota-bound (observed / cold) | 35 / 11 |
|  | active learners @ 1 turn / min: at observed latency (observed / cold) | 35 / 11 |
|  | active learners @ 1 turn / 30 s: quota-bound (observed / cold) | 17 / 6 |
|  | active learners @ 1 turn / 30 s: at observed latency (observed / cold) | 17 / 6 |
