# Mixed load, real (FelCloud) (engine v1.3), cache off

Live run against FelCloud: every request spent the team's quota.

- duration 264 s; 79 interactive requests, 3 prep pages (0 degraded), 2 requests held for the reserve
- interactive latency: p50 2.62 s, **p95 18.04 s**, max 25.01 s; degraded 20 {'deadline': 3, 'superseded': 4, 'quota_low': 10, 'ungrounded_formula': 2, 'low_confidence': 1}
- provider: 73 chat requests {'llama3.2-3b': 63, 'llama3.1-8b': 8, 'deepseek-ocr': 2}; non-200 none; **server 429s 0**; densest 60 s window **20** sends
- explain: 17 interactive, 3 degraded by `deadline` (share 0.176)
- exceptions out of engine.run: **0**

Interactive p95 is judged against the engine's own 25 s deadline (+0.5 s of measurement slack): an explain that is not ready by then is answered in fast mode, so a p95 AT 25 s means one learner in twenty got fast mode.

| task | n | p50 s | p95 s | max s |
|---|---|---|---|---|
| check_answer | 6 | 0.96 | 7.31 | 7.31 |
| check_claim | 6 | 3.35 | 5.87 | 5.87 |
| classify | 31 | 1.85 | 7.21 | 18.43 |
| explain | 17 | 4.63 | 25.01 | 25.01 |
| hint | 14 | 2.17 | 11.51 | 17.31 |
| ocr_transcribe | 5 | 13.0 | 18.04 | 18.04 |

| check | result |
|---|---|
| interactive_p95_within_25s | PASS |
| zero_server_429 | PASS |
| zero_exceptions | PASS |
| window_at_most_20 | PASS |
