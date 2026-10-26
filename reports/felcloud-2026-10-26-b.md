# FelCloud slot follow-up probes, 2026-10-26 (b)

Same rules as the first run: nothing under `engine/`, `router/`, `gatekeeper/`, `rag/` or `config.py` changed; the only
code change is the extension of `scripts/probe_felcloud.py` (new subcommands `window`, `outlen`, `ceiling`, `chars`, `ocr`,
`compare`). Raw exchanges are appended to `reports/felcloud-raw.jsonl` (git-ignored; key redacted).
Pillow was installed into the local `myenv` for probe E (not added to `requirements.txt`).

**Requests sent: 90 in this run** (A 32, B 6, C 5, D 4, E 3, F 40), **167 for the day** (77 before).

## Corrections to `reports/felcloud-2026-10-26.md`

1. **Truncation is confirmed, at about 4,096 tokens, not 2,048.** The first report said it was not confirmed because it only tested up to about 2,500 tokens. See probe C.
2. **`Limit resets at` in the 429 body is not the real reset time.** In the first report I said it was about 60 s after the first request of the window. In probe A every refusal reported its own send time plus 60 s. Use `retry-after` only.

## A. Rate window shape (32 requests: 20 burst, 7 probes, 5 rapid after success)

T0 = 17:15:29.18 UTC (first request); the 20 requests, 2 in parallel, were all sent by T0+9.2 s and all returned 200.

| Request | Time | Status | `Limit resets at` (relative to T0) |
|---|---|---|---|
| probe 1 | T0+10 s | 429 | T0+69.9 s |
| probe 2 to 5 | T0+20, 30, 40, 50 s | 429 | each is its own send time plus 60 s |
| probe 6 | T0+59.96 s | 429 | T0+119.9 s |
| probe 7 | T0+70 s | **200** | n/a |
| 5 rapid requests right after | T0+70.4 to 72.2 s | all 200 | n/a |

- Refused for the whole 60 s from the first request, accepted on the next probe (T0+70 s), and the full capacity came back at once (6 successes in 1 s).
- **Conclusion:** the data cannot separate a fixed window from a sliding one. Both predict a refusal at T0+59.96 s (the first request was 39 ms short of expiring) and a success at T0+70 s, because the whole burst had expired by then. A calendar-minute window is ruled out (requests at hh:16:09 and hh:16:19 were still refused). A deciding test would spread the 20 requests over 30 s and probe every 2 s from T0+55 s (about 30 more requests).
- Refusals did not extend the block (7 refused probes, no delay added).
- `Limit resets at` is always refusal time plus 60 s, so it is useless for timing; `retry-after: 60` was accurate as an upper bound.

## B. Output-length controls (6 requests; prompt "Explain derivatives in detail.")

| Control | compact `llama3.2-3b` | mid-size `llama3.1-8b` |
|---|---|---|
| `stop: ["."]` | no effect (multi-sentence answer, longer than the 4,000 characters stored per body) | not tested |
| `max_completion_tokens: 30` | no effect: 729 tokens, `finish=stop` | not tested |
| `num_predict: 30` (top-level, as the SDK merges `extra_body`) | no effect: 790 tokens | not tested |
| `options: {"num_predict": 30}` (top-level) | **works: exactly 30 tokens**, cut mid-sentence | **works: exactly 30 tokens** |
| JSON prompt with `stop: ["}"]` | no effect: 100 tokens, output contains `}` | not tested |

- Literal `extra_body` wrapper forms were not needed (the top-level `options` form already worked).
- `finish_reason` is `stop` even when `num_predict` cut the answer, so truncation shows only as `completion_tokens == N`.
- `stop` is ignored entirely.

## C. Context ceiling (4 planned requests plus 1 extra at about 4,200 tokens)

| Target (real tokens) | `usage.prompt_tokens` | Start-anchored (PAPAYA) | End-anchored (MANGUE) | Latency |
|---|---|---|---|---|
| about 4,000 | 3,883 / 3,899 | obeyed | obeyed | 30 to 31 s |
| about 4,200 (extra) | **2,050** | **lost** ("I couldn't find a word") | not run | 1.9 s |
| about 7,500 | **2,050** | **lost** | obeyed | 15 to 16 s |

- No 400 at any size. The server accepts the request and silently truncates.
- Above about 4,096 tokens the prompt is cut to about 2,050 tokens, **keeping the tail and dropping the start**. Because the system prompt and the instructions sit at the start, they are what gets lost.
- The truncation is visible: `usage.prompt_tokens` reports the kept length (2,050), far below what was sent.
- Ceiling is between 3,899 (whole) and about 4,200 (cut), consistent with a 4,096-token window.

## D. Characters per token (4 requests; baseline for an empty prompt "." = 26 tokens)

| Text (1,000 chars) | `prompt_tokens` | Chars per token (baseline removed) | Engine estimator |
|---|---|---|---|
| Arabic (MSA) | 378 | 2.84 | 2.00 |
| Darija in Arabic script | 419 | 2.54 | 2.00 |
| Arabizi | 458 | 2.31 | 2.00 |
| French (from the first report, 3,200 chars, 846 tokens) | 846 | 3.90 | 2.00 |

The estimator is conservative for every language. The smallest margin is Arabizi (13%).

## E. deepseek-ocr (3 requests)

Images rendered with PIL (script-style font Z003, 900x260 px); the photo-like one is rotated 3 degrees with blur, noise and a lighting gradient. Sent as an OpenAI `image_url` base64 data URL in `/chat/completions`.

| Input | Accepted | Transcription | Latency | `prompt_tokens` |
|---|---|---|---|---|
| clean `f'(x) = 6x + 5` | yes (200) | `\( f'(x) = 6x + 5 \)` (exact) | 13.5 s | 591 |
| photo-like, same text | yes (200) | `\( f(x) = 6x + 5 \)` (**prime dropped**, though visible in the image) | 13.1 s | 591 |
| text only ("Say hello in one word.") | yes (200) | `"Hello"` | n/a | n/a |

The output is LaTeX in `\( ... \)`. The image costs about 560 prompt tokens. The synthetic images are not real handwriting.

## F. Compact model comparison (`scripts.eval_classify`, 19 cases, FelCloud, simulator off)

| Metric | `llama3.2-3b` | `qwen2.5-3b` |
|---|---|---|
| Intent accuracy, all (n=19) | 79% | 79% |
| Intent accuracy, original set (n=15) | 73% | 73% |
| Language accuracy, model raw | 74% | 63% |
| Language accuracy, final (after rules) | 95% | 95% |
| JSON repair rate | 0% (0 repairs) | 11% (2 repairs) |
| Safe-default rate | 0% | 5% |
| Latency p50 / avg / max | 1.59 / 2.67 / 7.27 s | 2.97 / 3.96 / 9.41 s |
| Mean completion tokens | 38.9 | 39.5 |
| Provider requests | 19 | 21 |

Both models miss the same cases: `dar-3` (ANSWER_ATTEMPT), `azi-1` (EXPLAIN read as DIAGNOSTIC), `azi-2` (HINT read as QUIZ) and `azi-3` (OFF_TOPIC). The earlier local Ollama run of `llama3.2:3b` scored 87% intent on the original set; the FelCloud copy scores 73% (n=15, so about 2 cases).

## What this means for the engine (listed, not made)

1. **A real output cap exists: `options: {"num_predict": N}`** in the request body works on both classes, while `max_tokens`, `max_completion_tokens`, `stop` and a bare `num_predict` do not. Sending it with the existing 300 / 550 limits would bound latency and tokens (mid-size answers otherwise reach 800+ tokens and 45 s). It is provider-specific, so it should be a config setting, not a hard-coded field. Detect capping by `completion_tokens == N`, since `finish_reason` stays `stop`.
2. **The input window is about 4,096 tokens, and overflow silently drops the start of the prompt**, including the system prompt. `INPUT_TOKEN_BUDGET=1800` estimated tokens is about 950 real French tokens or about 1,560 real Arabizi tokens, so it is safe with a wide margin. Add a guard that flags any response whose `tokens_in` is near 2,050 and far below the estimate, then treats the answer as unreliable.
3. **Do not parse `Limit resets at`.** Keep using `retry-after`. After a refusal, a 60 s wait restores full capacity at once. The local limiter (at most 20 requests per sliding minute) is safe under either window type.
4. **Do not rely on `stop` for JSON or short outputs.** Keep the existing tolerant JSON parsing and repair retry.
5. **Keep the 2.0 chars-per-token estimator.** It holds for all four languages; Arabizi leaves the least margin.
6. **`deepseek-ocr` works through the same endpoint** (about 13 s per image). Transcriptions of noisy images can silently drop symbols that change the math (the derivative prime), so any use needs a confirm-with-the-learner step or a sympy parse check before grading. The engine has no task or model class for it; it would be a new task and class, and it spends the same 20 requests per minute.
7. **Keep `llama3.2-3b` first and `qwen2.5-3b` as fallback.** The final accuracy is identical, but qwen needs repairs and has lower raw language accuracy and about twice the median latency. Darija and Arabizi intent errors are shared, so the weakness is the model size, not the choice.
