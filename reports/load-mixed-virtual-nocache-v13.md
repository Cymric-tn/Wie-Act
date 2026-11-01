# Mixed load, virtual (simulated FelCloud) (engine v1.3), cache off

Simulated: the real engine through the constraint simulator (compact 2-5 s, mid-size 8-15 s, vision 10-18 s, 20 / min, 2 in flight, one mid-size slot, random mid-size saturation) with canned model replies and a virtual clock. It checks the engine's scheduling, not the models, and not FelCloud.

- duration 257 s; 73 interactive requests, 6 prep pages (0 degraded), 2 requests held for the reserve
- interactive latency: p50 4.16 s, **p95 17.09 s**, max 25.04 s; degraded 3 {'superseded': 2, 'deadline': 1}
- provider: 63 chat requests {'compact-a': 47, 'mid-a': 12, 'deepseek-ocr': 4}; non-200 none; **server 429s 0**; densest 60 s window **18** sends
- exceptions out of engine.run: **0**

| task | n | p50 s | p95 s |
|---|---|---|---|
| check_answer | 5 | 5.13 | 7.96 |
| check_claim | 5 | 0.0 | 5.23 |
| classify | 29 | 3.97 | 7.29 |
| explain | 15 | 0.0 | 19.61 |
| hint | 14 | 3.73 | 6.24 |
| ocr_transcribe | 5 | 16.79 | 25.04 |

| check | result |
|---|---|
| interactive_p95_under_25s | PASS |
| zero_server_429 | PASS |
| zero_exceptions | PASS |
| window_at_most_20 | PASS |
