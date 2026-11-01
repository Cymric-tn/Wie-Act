# Mixed load, virtual (simulated FelCloud) (engine v1.3), cache off

Simulated: the real engine through the constraint simulator (compact 2-5 s, mid-size 8-15 s, vision 10-18 s, 20 / min, 2 in flight, one mid-size slot, random mid-size saturation) with canned model replies and a virtual clock. It checks the engine's scheduling, not the models, and not FelCloud.

- duration 274 s; 75 interactive requests, 3 prep pages (0 degraded), 1 requests held for the reserve
- interactive latency: p50 4.16 s, **p95 14.65 s**, max 24.6 s; degraded 7 {'superseded': 2, 'quota_low': 5}
- provider: 74 chat requests {'compact-a': 59, 'mid-a': 12, 'deepseek-ocr': 3}; non-200 none; **server 429s 0**; densest 60 s window **20** sends
- exceptions out of engine.run: **0**

Interactive p95 is judged against the engine's own 25 s deadline (+0.5 s of measurement slack): an explain that is not ready by then is answered in fast mode, so a p95 AT 25 s means one learner in twenty got fast mode.

| task | n | p50 s | p95 s | max s |
|---|---|---|---|---|
| check_answer | 5 | 3.87 | 7.35 | 7.35 |
| check_claim | 5 | 4.35 | 7.89 | 7.89 |
| classify | 30 | 3.61 | 7.23 | 7.29 |
| explain | 14 | 9.3 | 16.54 | 18.75 |
| hint | 16 | 4.13 | 6.15 | 6.24 |
| ocr_transcribe | 5 | 13.08 | 24.6 | 24.6 |

| check | result |
|---|---|
| interactive_p95_within_25s | PASS |
| zero_server_429 | PASS |
| zero_exceptions | PASS |
| window_at_most_20 | PASS |
