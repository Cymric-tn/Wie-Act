# Explain results degraded by `deadline`

Source: reports/felcloud-2026-11-03-load-mixed.json (real (FelCloud), cache off).

- interactive explain requests: **15**
- degraded by `deadline`: **4** (26.7%)
- explain latency: {'p50_s': 21.2, 'p95_s': 25.01, 'max_s': 25.01}

A `deadline` degrade is fast mode (course excerpts) after 25 s; the generation kept going into the cache.
