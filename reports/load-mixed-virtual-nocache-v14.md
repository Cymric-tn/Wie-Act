# Mixed load, virtual (simulated FelCloud) (engine v1.3), cache off

Simulated: the real engine through the constraint simulator (compact 2-5 s, mid-size 8-15 s, vision 10-18 s, 20 / min, 2 in flight, one mid-size slot, random mid-size saturation) with canned model replies and a virtual clock. It checks the engine's scheduling, not the models, and not FelCloud.

- duration 272 s; 64 interactive requests, 4 prep pages (0 degraded), 1 requests held for the reserve
- interactive latency: p50 4.58 s, **p95 25.0 s**, max 25.0 s; degraded 7 {'superseded': 3, 'deadline': 4}
- provider: 61 chat requests {'compact-a': 39, 'mid-a': 20, 'deepseek-ocr': 2}; non-200 none; **server 429s 0**; densest 60 s window **19** sends
- explain: 16 interactive, 4 degraded by `deadline` (share 0.25)
- exceptions out of engine.run: **0**

Interactive p95 is judged against the engine's own 25 s deadline (+0.5 s of measurement slack): an explain that is not ready by then is answered in fast mode, so a p95 AT 25 s means one learner in twenty got fast mode.

| task | n | p50 s | p95 s | max s |
|---|---|---|---|---|
| check_answer | 5 | 0.0 | 7.35 | 7.35 |
| check_claim | 6 | 2.48 | 7.89 | 7.89 |
| classify | 24 | 4.39 | 7.23 | 7.29 |
| explain | 16 | 19.53 | 25.0 | 25.0 |
| hint | 8 | 4.16 | 6.24 | 6.24 |
| ocr_transcribe | 5 | 7.61 | 24.6 | 24.6 |

| check | result |
|---|---|
| interactive_p95_within_25s | PASS |
| zero_server_429 | PASS |
| zero_exceptions | PASS |
| window_at_most_20 | PASS |
