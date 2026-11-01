# Mixed load, virtual (simulated FelCloud) (engine v1.3), cache on

Simulated: the real engine through the constraint simulator (compact 2-5 s, mid-size 8-15 s, vision 10-18 s, 20 / min, 2 in flight, one mid-size slot, random mid-size saturation) with canned model replies and a virtual clock. It checks the engine's scheduling, not the models, and not FelCloud.

- duration 264 s; 100 interactive requests, 6 prep pages (0 degraded), 1 requests held for the reserve
- interactive latency: p50 0.0 s, **p95 14.92 s**, max 27.03 s; degraded 2 {'superseded': 1, 'deadline': 1}
- provider: 32 chat requests {'compact-a': 18, 'mid-a': 9, 'deepseek-ocr': 5}; non-200 none; **server 429s 0**; densest 60 s window **15** sends
- exceptions out of engine.run: **0**

| task | n | p50 s | p95 s |
|---|---|---|---|
| check_answer | 6 | 0.0 | 3.27 |
| check_claim | 6 | 0.0 | 4.33 |
| classify | 41 | 0.0 | 4.54 |
| explain | 28 | 0.0 | 18.63 |
| hint | 13 | 0.0 | 4.52 |
| ocr_transcribe | 6 | 10.86 | 27.03 |

| check | result |
|---|---|
| interactive_p95_under_25s | PASS |
| zero_server_429 | PASS |
| zero_exceptions | PASS |
| window_at_most_20 | PASS |
