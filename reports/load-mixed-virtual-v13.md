# Mixed load, virtual (simulated FelCloud) (engine v1.3), cache on

Simulated: the real engine through the constraint simulator (compact 2-5 s, mid-size 8-15 s, vision 10-18 s, 20 / min, 2 in flight, one mid-size slot, random mid-size saturation) with canned model replies and a virtual clock. It checks the engine's scheduling, not the models, and not FelCloud.

- duration 254 s; 96 interactive requests, 6 prep pages (0 degraded), 0 requests held for the reserve
- interactive latency: p50 0.0 s, **p95 17.75 s**, max 25.0 s; degraded 3 {'superseded': 2, 'deadline': 1}
- provider: 32 chat requests {'compact-a': 17, 'mid-a': 11, 'deepseek-ocr': 4}; non-200 none; **server 429s 0**; densest 60 s window **15** sends
- exceptions out of engine.run: **0**

Interactive p95 is judged against the engine's own 25 s deadline (+0.5 s of measurement slack): an explain that is not ready by then is answered in fast mode, so a p95 AT 25 s means one learner in twenty got fast mode.

| task | n | p50 s | p95 s | max s |
|---|---|---|---|---|
| check_answer | 6 | 0.0 | 2.66 | 2.66 |
| check_claim | 6 | 0.0 | 3.65 | 3.65 |
| classify | 39 | 0.0 | 4.98 | 7.29 |
| explain | 22 | 0.0 | 20.22 | 25.0 |
| hint | 17 | 0.0 | 4.82 | 6.24 |
| ocr_transcribe | 6 | 13.92 | 19.92 | 19.92 |

| check | result |
|---|---|
| interactive_p95_within_25s | PASS |
| zero_server_429 | PASS |
| zero_exceptions | PASS |
| window_at_most_20 | PASS |
