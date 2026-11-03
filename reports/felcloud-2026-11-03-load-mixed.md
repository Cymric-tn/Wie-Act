# Mixed load, real (FelCloud) (engine v1.3), cache off

Live run against FelCloud: every request spent the team's quota.

- duration 267 s; 65 interactive requests, 5 prep pages (1 degraded), 3 requests held for the reserve
- interactive latency: p50 2.48 s, **p95 25.01 s**, max 32.37 s; degraded 7 {'superseded': 2, 'deadline': 4, 'low_confidence': 1}
- provider: 64 chat requests {'llama3.1-8b': 20, 'llama3.2-3b': 41, 'deepseek-ocr': 3}; non-200 none; **server 429s 0**; densest 60 s window **19** sends
- explain: 15 interactive, 4 degraded by `deadline` (share 0.267)
- exceptions out of engine.run: **0**

Interactive p95 is judged against the engine's own 25 s deadline (+0.5 s of measurement slack): an explain that is not ready by then is answered in fast mode, so a p95 AT 25 s means one learner in twenty got fast mode.

| task | n | p50 s | p95 s | max s |
|---|---|---|---|---|
| check_answer | 5 | 1.47 | 1.7 | 1.7 |
| check_claim | 5 | 0.01 | 6.66 | 6.66 |
| classify | 25 | 1.92 | 10.7 | 11.42 |
| explain | 15 | 21.2 | 25.01 | 25.01 |
| hint | 10 | 2.28 | 3.61 | 3.61 |
| ocr_transcribe | 5 | 19.91 | 32.37 | 32.37 |

| check | result |
|---|---|
| interactive_p95_within_25s | PASS |
| zero_server_429 | PASS |
| zero_exceptions | PASS |
| window_at_most_20 | PASS |
