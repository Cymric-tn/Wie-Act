| section | metric | felcloud |
|---|---|---|
| volume | engine.run calls (caller-visible) | 30 |
|  | nested compress runs | 0 |
|  | provider requests (all attempts) | 16 |
|  | provider requests ok | 16 |
|  | duration (s) | 61.9 |
|  | provider requests / min | 15.5 |
| calls per model | llama3.1-8b (midsize) ok / attempts | 6 / 6 |
|  | llama3.2-3b (compact) ok / attempts | 10 / 10 |
| class split (ok calls) | compact | 10 (62%) |
|  | midsize | 6 (38%) |
|  | purpose: task | 16 |
| tokens | input tokens | 8403 |
|  | output tokens | 1147 |
|  | input tokens / ok call | 525 |
| cache | cache hits / runs | 11 / 30 |
|  | cache hit rate | 37% |
|  | negative cache hits (fast mode, 0 requests) | 3 |
|  | repeated identical provider requests | 0 |
| refusals -> handling | refused attempts (total) | 0 |
| fallbacks / degradation | degraded runs | 4 |
|  | degraded: ungrounded_formula | 4 |
|  | explain served fast_extractive | 3 |
|  | explain served full (generated) | 12 |
| circuit breaker | trips (closed -> open) | 0 |
|  | re-opened after failed probe | 0 |
|  | closed after probe | 0 |
| latency (uncached runs, ms) | classify p50 / p95 (n) | 1565 / 11897 (6) |
|  | explain p50 / p95 (n) | 10039 / 22844 (9) |
|  | hint p50 / p95 (n) | 1747 / 11057 (4) |
|  | prio: BACKGROUND p50 / p95 (n) | 1747 / 16691 (4) |
|  | prio: INTERACTIVE p50 / p95 (n) | 8441 / 22844 (15) |
