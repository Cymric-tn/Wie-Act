# Explain results degraded by `deadline`

Source: reports/felcloud-2026-11-02-load-mixed.json (real (FelCloud), cache off).

- interactive explain requests: **17**
- degraded by `deadline`: **3** (17.6%)
- explain latency: {'p50_s': 4.63, 'p95_s': 25.01, 'max_s': 25.01}

A `deadline` degrade is fast mode (course excerpts) after 25 s; the generation kept going into the cache.
