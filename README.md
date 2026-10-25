# EdNova LLM Engine

LLM/platform layer for the IEEE WIE ACT 5.0 EdNova challenge. It sits between a feature layer and an
OpenAI-compatible provider (FelCloud in production, local Ollama in development) and handles
routing, rate limiting, retries, caching, retrieval and metrics. It has no pedagogy or UI of its own.

```python
from engine import Engine, Priority

async with Engine.create() as engine:
    result = await engine.run("explain", {"question": "C'est quoi la dérivée d'un produit ?"},
                              {"language": "FRENCH"}, Priority.INTERACTIVE)
```

Full integration and customization guide: `docs/engine_guide/ednova_engine_guide.pdf`
(LaTeX source alongside; build with `pdflatex` twice). Design choices: `DECISIONS.md`. Status: `CLAUDE.md`.

## Setup

```bash
python3 -m venv myenv && myenv/bin/pip install -r requirements.txt
cp .env.example .env            # fill in placeholders; never commit real keys
ollama pull llama3.2:3b && ollama pull llama3.1:8b   # models named in your env file
```

Env profiles (all git-ignored, create from `.env.example`): `.env.ollama` (Ollama + in-process constraint simulator), `.env` (standalone `mock_server.py`),
`.env.real` (FelCloud). Select one with `ENV_FILE=.env.ollama`.

## Commands

| Command | What it does |
|---|---|
| `pytest -q` | unit tests (no LLM needed) |
| `pytest -q -m integration` | integration tests (needs Ollama) |
| `python -m scripts.smoke` | one call per task |
| `python -m scripts.load_test --n 50` | concurrent load run and metrics summary |
| `ENV_FILE=.env.ollama python -m scripts.index_docs` | rebuild the retrieval index from `data/docs/` |

## Layout

| Path | Contents |
|---|---|
| `engine/` | `engine.run`, tasks, grounding checks, exercise bank |
| `gatekeeper/` | client, limiter, retry, errors, queue, cache (exact + negative) |
| `router/` | compact vs mid-size policy and circuit breaker |
| `rag/` | BM25 retrieval, glossary expansion, context compression |
| `simulator/` | constraint simulator (latency, quota, 503, 400) |
| `metrics/` | one record per call (runtime logs are git-ignored) |
| `prompts/` | versioned prompt templates |
| `data/` | course docs, exercise bank, glossary, templates (the runtime cache is ignored) |
| `scripts/` | smoke, load test, evals, recon, indexing |
| `tests/` | unit tests plus `tests/eval/` datasets |
| `reports/` | load and eval evidence cited in `DECISIONS.md` |
| `docs/` | engine guide (PDF + LaTeX), research notes |
| `_legacy/` | superseded prototype files (kept, not deleted) |
