| section | metric | normal | saturated | quota_low | capacity |
|---|---|---|---|---|---|
| volume | engine.run calls (caller-visible) | 50 | 50 | 50 | 75 |
|  | nested compress runs | 0 | 0 | 0 | 0 |
|  | provider requests (all attempts) | 38 | 38 | 43 | 29 |
|  | provider requests ok | 23 | 22 | 21 | 25 |
|  | duration (s) | 150.8 | 108.6 | 132.7 | 358.6 |
|  | provider requests / min | 15.1 | 21.0 | 19.4 | 4.9 |
| calls per model | llama3.1:8b (midsize) ok / attempts | 3 / 18 | 2 / 14 | 1 / 5 | 5 / 9 |
|  | llama3.2:3b (compact) ok / attempts | 20 / 20 | 20 / 24 | 20 / 38 | 20 / 20 |
| class split (ok calls) | compact | 20 (87%) | 20 (91%) | 20 (95%) | 20 (80%) |
|  | midsize | 3 (13%) | 2 (9%) | 1 (5%) | 5 (20%) |
|  | purpose: framing | 5 | 5 | 5 | 3 |
|  | purpose: task | 18 | 17 | 16 | 22 |
| tokens | input tokens | 6262 | 5816 | 5465 | 8541 |
|  | output tokens | 1273 | 1110 | 876 | 1363 |
|  | input tokens / ok call | 272 | 264 | 260 | 342 |
| cache | cache hits / runs | 15 / 50 | 18 / 50 | 14 / 50 | 41 / 75 |
|  | cache hit rate | 30% | 36% | 28% | 55% |
|  | repeated identical provider requests | 0 | 0 | 0 | 0 |
| refusals -> handling | refused attempts (total) | 15 | 16 | 22 | 4 |
|  | saturated -> raise | 7 | 5 | 2 | 2 |
|  | saturated -> retry_after_interval | 8 | 11 | 2 | 2 |
|  | quota_exhausted -> wait_stated_delay | 0 | 0 | 18 | 0 |
| fallbacks / degradation | degraded runs | 13 | 11 | 16 | 3 |
|  | degraded: breaker_open | 6 | 6 | 1 | 1 |
|  | degraded: midsize_saturated | 7 | 5 | 2 | 2 |
|  | explain served fast_extractive | 13 | 11 | 16 | 3 |
|  | explain served full (generated) | 4 | 6 | 1 | 8 |
|  | route decision: breaker_open | 7 | 11 | 1 | 2 |
|  | route decision: midsize_saturated | 7 | 5 | 2 | 2 |
|  | degraded: quota_low | 0 | 0 | 13 | 0 |
|  | route decision: quota_low | 0 | 0 | 16 | 0 |
| circuit breaker | trips (closed -> open) | 2 | 2 | 1 | 1 |
|  | re-opened after failed probe | 0 | 0 | 0 | 0 |
|  | closed after probe | 1 | 1 | 0 | 1 |
| latency (uncached runs, ms) | classify p50 / p95 (n) | 11164 / 51252 (8) | 9991 / 18360 (8) | 26948 / 75876 (8) | 4219 / 5671 (13) |
|  | explain p50 / p95 (n) | 12918 / 67216 (20) | 9 / 56193 (17) | 4442 / 74339 (21) | 8 / 51492 (17) |
|  | hint p50 / p95 (n) | 10640 / 18085 (7) | 12203 / 30752 (7) | 30868 / 70835 (7) | 5161 / 5882 (4) |
|  | prio: BACKGROUND p50 / p95 (n) | 18085 / 67216 (7) | 12771 / 24160 (7) | 15466 / 81556 (8) | 5413 / 28399 (5) |
|  | prio: INTERACTIVE p50 / p95 (n) | 10655 / 60632 (28) | 7056 / 44708 (25) | 22678 / 34171 (28) | 4009 / 35072 (29) |
| capacity (per learner turn) | learner turns | - | - | - | 40 |
|  | engine calls / turn | - | - | - | 1.88 |
|  | provider requests / turn (all attempts) | - | - | - | 0.72 |
|  | ok provider calls / turn | - | - | - | 0.62 |
|  | cold turns (no cache hit): n / requests per turn | - | - | - | 13 / 2.08 |
|  | learner wait per turn p50 / p95 (ms) | - | - | - | 2 / 35073 |
| capacity estimate (one key, 20 req/min) | mean provider latency (s) | - | - | - | 10.6 |
|  | concurrency ceiling (2 x 60 / latency, req/min) | - | - | - | 11.3 |
|  | binding limit (req/min) | - | - | - | 11.3 (concurrency) |
|  | active learners @ 1 turn / 120 s: quota-bound (observed / cold) | - | - | - | 55 / 19 |
|  | active learners @ 1 turn / 120 s: at observed latency (observed / cold) | - | - | - | 31 / 11 |
|  | active learners @ 1 turn / min: quota-bound (observed / cold) | - | - | - | 28 / 10 |
|  | active learners @ 1 turn / min: at observed latency (observed / cold) | - | - | - | 16 / 5 |
|  | active learners @ 1 turn / 30 s: quota-bound (observed / cold) | - | - | - | 14 / 5 |
|  | active learners @ 1 turn / 30 s: at observed latency (observed / cold) | - | - | - | 8 / 3 |
