| section | metric | v1.1-felcloud |
|---|---|---|
| volume | engine.run calls (caller-visible) | 60 |
|  | nested compress runs | 0 |
|  | provider requests (all attempts) | 25 |
|  | provider requests ok | 25 |
|  | duration (s) | 92.0 |
|  | provider requests / min | 16.3 |
| calls per model | llama3.1-8b (midsize) ok / attempts | 8 / 8 |
|  | llama3.2-3b (compact) ok / attempts | 17 / 17 |
| class split (ok calls) | compact | 17 (68%) |
|  | midsize | 8 (32%) |
|  | purpose: task | 25 |
| tokens | input tokens | 13519 |
|  | output tokens | 1577 |
|  | input tokens / ok call | 541 |
|  | output tokens / ok call | 63.1 |
|  | outputs cut at the cap (completion_tokens >= cap) | 0 |
| limiter | attempts that waited on the limiter (> 100 ms) | 21 |
|  | max limiter wait (s) | 9.2 |
| cache | cache hits / runs | 35 / 60 |
|  | cache hit rate | 58% |
|  | negative cache hits (fast mode, 0 requests) | 0 |
|  | repeated identical provider requests | 0 |
| refusals -> handling | refused attempts (total) | 0 |
| fallbacks / degradation | degraded runs | 0 |
|  | explain served fast_extractive | 0 |
|  | explain served full (generated) | 23 |
|  | route decision: quota_low | 2 |
| circuit breaker | trips (closed -> open) | 0 |
|  | re-opened after failed probe | 0 |
|  | closed after probe | 0 |
| latency (uncached runs, ms) | classify p50 / p95 (n) | 13368 / 31062 (10) |
|  | explain p50 / p95 (n) | 17824 / 41550 (8) |
|  | hint p50 / p95 (n) | 9994 / 30416 (7) |
|  | prio: BACKGROUND p50 / p95 (n) | 30416 / 41550 (4) |
|  | prio: INTERACTIVE p50 / p95 (n) | 13085 / 26769 (21) |
