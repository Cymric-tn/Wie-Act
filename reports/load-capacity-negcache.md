| section | metric | capacity_v1 | capacity_negcache |
|---|---|---|---|
| volume | engine.run calls (caller-visible) | 69 | 69 |
|  | nested compress runs | 0 | 0 |
|  | provider requests (all attempts) | 27 | 28 |
|  | provider requests ok | 24 | 24 |
|  | duration (s) | 358.6 | 358.6 |
|  | provider requests / min | 4.5 | 4.7 |
| calls per model | llama3.1:8b (midsize) ok / attempts | 6 / 9 | 5 / 9 |
|  | llama3.2:3b (compact) ok / attempts | 18 / 18 | 19 / 19 |
| class split (ok calls) | compact | 18 (75%) | 19 (79%) |
|  | midsize | 6 (25%) | 5 (21%) |
|  | purpose: framing | 1 | 2 |
|  | purpose: task | 23 | 22 |
| tokens | input tokens | 14848 | 14383 |
|  | output tokens | 1349 | 1235 |
|  | input tokens / ok call | 619 | 599 |
| cache | cache hits / runs | 42 / 69 | 41 / 69 |
|  | cache hit rate | 61% | 59% |
|  | negative cache hits (fast mode, 0 requests) | 0 | 0 |
|  | repeated identical provider requests | 1 | 0 |
| refusals -> handling | refused attempts (total) | 3 | 4 |
|  | saturated -> raise | 1 | 2 |
|  | saturated -> retry_after_interval | 2 | 2 |
| fallbacks / degradation | degraded runs | 2 | 3 |
|  | degraded: midsize_saturated | 1 | 2 |
|  | degraded: ungrounded_formula | 1 | 0 |
|  | explain served fast_extractive | 1 | 3 |
|  | explain served full (generated) | 10 | 8 |
|  | route decision: breaker_open | 1 | 2 |
|  | route decision: midsize_saturated | 1 | 2 |
|  | degraded: breaker_open | 0 | 1 |
| circuit breaker | trips (closed -> open) | 1 | 1 |
|  | re-opened after failed probe | 0 | 0 |
|  | closed after probe | 1 | 1 |
| latency (uncached runs, ms) | classify p50 / p95 (n) | 5588 / 7946 (13) | 5670 / 13500 (13) |
|  | explain p50 / p95 (n) | 26785 / 41136 (10) | 17314 / 49169 (11) |
|  | hint p50 / p95 (n) | 3189 / 5160 (4) | 3190 / 5881 (4) |
|  | prio: BACKGROUND p50 / p95 (n) | 4522 / 5423 (3) | 4545 / 25855 (4) |
|  | prio: INTERACTIVE p50 / p95 (n) | 5648 / 38315 (24) | 5670 / 29292 (24) |
| capacity (per learner turn) | learner turns | 40 | 40 |
|  | engine calls / turn | 1.73 | 1.73 |
|  | provider requests / turn (all attempts) | 0.68 | 0.7 |
|  | ok provider calls / turn | 0.6 | 0.6 |
|  | cold turns (no cache hit): n / requests per turn | 13 / 2.00 | 13 / 2.00 |
|  | learner wait per turn p50 / p95 (ms) | 2 / 37980 | 4 / 31926 |
| capacity estimate (one key, 20 req/min) | mean provider latency (s) | 11.5 | 11.0 |
|  | concurrency ceiling (2 x 60 / latency, req/min) | 10.4 | 10.9 |
|  | binding limit (req/min) | 10.4 (concurrency) | 10.9 (concurrency) |
|  | active learners @ 1 turn / 120 s: quota-bound (observed / cold) | 59 / 20 | 57 / 20 |
|  | active learners @ 1 turn / 120 s: at observed latency (observed / cold) | 31 / 10 | 31 / 11 |
|  | active learners @ 1 turn / min: quota-bound (observed / cold) | 30 / 10 | 29 / 10 |
|  | active learners @ 1 turn / min: at observed latency (observed / cold) | 15 / 5 | 16 / 5 |
|  | active learners @ 1 turn / 30 s: quota-bound (observed / cold) | 15 / 5 | 14 / 5 |
|  | active learners @ 1 turn / 30 s: at observed latency (observed / cold) | 8 / 3 | 8 / 3 |
