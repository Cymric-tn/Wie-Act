| section | metric | normal | saturated | quota_low | capacity |
|---|---|---|---|---|---|
| volume | engine.run calls (caller-visible) | 50 | 50 | 50 | 69 |
|  | nested compress runs | 0 | 0 | 0 | 0 |
|  | provider requests (all attempts) | 28 | 35 | 33 | 27 |
|  | provider requests ok | 21 | 21 | 21 | 24 |
|  | duration (s) | 111.1 | 95.5 | 137.7 | 358.6 |
|  | provider requests / min | 15.1 | 22.0 | 14.4 | 4.5 |
| calls per model | llama3.1:8b (midsize) ok / attempts | 2 / 9 | 0 / 10 | 3 / 9 | 6 / 9 |
|  | llama3.2:3b (compact) ok / attempts | 19 / 19 | 21 / 25 | 18 / 24 | 18 / 18 |
| class split (ok calls) | compact | 19 (90%) | 21 (100%) | 18 (86%) | 18 (75%) |
|  | midsize | 2 (10%) | 0 (0%) | 3 (14%) | 6 (25%) |
|  | purpose: framing | 4 | 6 | 3 | 1 |
|  | purpose: task | 17 | 15 | 18 | 23 |
| tokens | input tokens | 9551 | 8958 | 9867 | 14848 |
|  | output tokens | 835 | 624 | 935 | 1349 |
|  | input tokens / ok call | 455 | 427 | 470 | 619 |
| cache | cache hits / runs | 22 / 50 | 14 / 50 | 20 / 50 | 42 / 69 |
|  | cache hit rate | 44% | 28% | 40% | 61% |
|  | repeated identical provider requests | 0 | 0 | 0 | 1 |
| refusals -> handling | refused attempts (total) | 7 | 14 | 12 | 3 |
|  | saturated -> raise | 3 | 7 | 2 | 1 |
|  | saturated -> retry_after_interval | 4 | 7 | 3 | 2 |
|  | quota_exhausted -> wait_stated_delay | 0 | 0 | 7 | 0 |
| fallbacks / degradation | degraded runs | 11 | 21 | 12 | 2 |
|  | degraded: breaker_open | 8 | 16 | 0 | 0 |
|  | degraded: midsize_saturated | 3 | 5 | 2 | 1 |
|  | explain served fast_extractive | 11 | 21 | 12 | 1 |
|  | explain served full (generated) | 10 | 0 | 9 | 10 |
|  | route decision: breaker_open | 9 | 16 | 0 | 1 |
|  | route decision: midsize_saturated | 3 | 5 | 2 | 1 |
|  | degraded: quota_low | 0 | 0 | 10 | 0 |
|  | route decision: quota_low | 0 | 0 | 15 | 0 |
|  | degraded: ungrounded_formula | 0 | 0 | 0 | 1 |
| circuit breaker | trips (closed -> open) | 1 | 1 | 1 | 1 |
|  | re-opened after failed probe | 0 | 2 | 0 | 0 |
|  | closed after probe | 1 | 0 | 1 | 1 |
| latency (uncached runs, ms) | classify p50 / p95 (n) | 29231 / 65068 (8) | 4464 / 13698 (8) | 38562 / 88222 (8) | 5588 / 7946 (13) |
|  | explain p50 / p95 (n) | 26292 / 95342 (13) | 5110 / 15535 (21) | 17269 / 80012 (15) | 26785 / 41136 (10) |
|  | hint p50 / p95 (n) | 24979 / 63532 (7) | 4359 / 5643 (7) | 39297 / 85564 (7) | 3189 / 5160 (4) |
|  | prio: BACKGROUND p50 / p95 (n) | 52903 / 65068 (5) | 5262 / 13698 (8) | 79494 / 88222 (5) | 4522 / 5423 (3) |
|  | prio: INTERACTIVE p50 / p95 (n) | 27107 / 49640 (23) | 4359 / 15535 (28) | 34291 / 53118 (25) | 5648 / 38315 (24) |
| capacity (per learner turn) | learner turns | - | - | - | 40 |
|  | engine calls / turn | - | - | - | 1.73 |
|  | provider requests / turn (all attempts) | - | - | - | 0.68 |
|  | ok provider calls / turn | - | - | - | 0.6 |
|  | cold turns (no cache hit): n / requests per turn | - | - | - | 13 / 2.00 |
|  | learner wait per turn p50 / p95 (ms) | - | - | - | 2 / 37980 |
| capacity estimate (one key, 20 req/min) | mean provider latency (s) | - | - | - | 11.5 |
|  | concurrency ceiling (2 x 60 / latency, req/min) | - | - | - | 10.4 |
|  | binding limit (req/min) | - | - | - | 10.4 (concurrency) |
|  | active learners @ 1 turn / 120 s: quota-bound (observed / cold) | - | - | - | 59 / 20 |
|  | active learners @ 1 turn / 120 s: at observed latency (observed / cold) | - | - | - | 31 / 10 |
|  | active learners @ 1 turn / min: quota-bound (observed / cold) | - | - | - | 30 / 10 |
|  | active learners @ 1 turn / min: at observed latency (observed / cold) | - | - | - | 15 / 5 |
|  | active learners @ 1 turn / 30 s: quota-bound (observed / cold) | - | - | - | 15 / 5 |
|  | active learners @ 1 turn / 30 s: at observed latency (observed / cold) | - | - | - | 8 / 3 |
