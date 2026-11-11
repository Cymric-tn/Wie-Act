# Lumi: a GenAI peer learning companion

Lumi is our entry for the IEEE WIE ACT 5.0 **EdNova** technical challenge. It is a working web application in which
students learn alone or in a group from their professor's course material, in the language they think in (French,
English, Arabic, Tunisian Darija or Arabizi), while the professor sees and corrects what the AI says.

It runs on the challenge's inference service (FelCloud): two categories of language models on CPU, 20 requests a
minute, two at a time, no streaming. Most of the engineering is about using that service well.

## What it does

| For | Feature |
|---|---|
| A learner | **AI companion**: explanations grounded in the course with their sources, three progressive hints before a solution, answers graded with symbolic maths, a practice exercise after each explanation |
| A learner | **Diagnostic step**: four questions find how the learner takes in a new idea (pictures, analogies, steps, examples, repetition, practice); explanations follow that style. A level test (three exercises of rising difficulty) sets the starting point |
| A learner | **Progress map**: the course as a prerequisite graph with mastery per concept and a "next up" |
| A learner | **Assessment prep**: a revision plan from the exam date, the minutes available and the weak concepts, with a study page per concept |
| A group | **Real-time whiteboard** with pages, history, PDF import and teacher controls; **Lumi on the board** answers about what is written, fact-checks a claim, checks a worked solution line by line and reads handwriting |
| A group | **Voice chat** (WebRTC), with push-to-talk questions to Lumi that are answered aloud for the whole room |
| A learner | **Diagram generator**: exact curves, variation tables and concept maps from one sentence, computed with sympy (no model call) |
| A professor | **Course material** uploads (PDF, Word, Markdown, scans read by OCR), **AI review** of flagged or reported answers, concept-tag confirmation, class overview |

Course content today: first-year calculus (derivatives), in French with English and Arabic pages. Other subjects are
answered from what a professor uploads, or the companion says the topic is not in the course.

## How the GenAI module works

Everything that touches a model goes through one function, `engine.run(task, inputs, context, priority)`.

```
browser ── /api/ai/* ──▶ Node server ──▶ bridge ──▶ engine.run
                                                      │ rules first: sympy, exercise bank, templates   (0 requests)
                                                      │ retrieval: BM25 + glossary, relevance gate      (0 requests)
                                                      ▼
                                              router ─▶ compact model  or  larger model
                                                      ▼
                                              gatekeeper: cache · priority queue · rate window · retry
                                                      ▼
                                              FelCloud (OpenAI-compatible, whole responses)
```

**Which model category does what** (the rule lives in `router/policy.py`):

| Step | Category | Why |
|---|---|---|
| Classify a message: intent, language, confusion, complexity, in one JSON call | compact | short, well-defined |
| Hint, grading feedback, context compression, fact-check verdict, guiding question | compact | short outputs with a schema |
| Explanation of a concept from retrieved course text | larger | multi-source synthesis and careful writing |
| Reading a photo or a scanned page | vision | the only model that sees images |
| Exercise choice, grading, revision plan, diagrams, formula verification | none | rules and sympy are exact and free |

**When capacity is short** the application keeps answering and says what it is doing:

- a sliding-window limiter keeps the app under 20 sends in any 60 s, with interactive requests ahead of background ones;
- a quota refusal is retried after the delay the service states; a saturated larger model falls back to the compact one;
- an explanation that is not ready in 25 s is answered in "fast mode": the course passages themselves, at once;
- every fallback is visible (`degraded` and a reason), and the interface shows staged progress while it waits;
- identical requests are served from a cache, and an answer that failed a check is not regenerated for 10 minutes.

**Checks on what the model writes:** every formula in an explanation is verified with sympy or matched against the
sources; an answer with a wrong or unsupported formula is flagged for the professor and never cached as good.

## Run it

Requirements: Python 3.10+, Node 22.5+ (the server uses the built-in `node:sqlite`; `app/.nvmrc` says 24).

```bash
python3 -m venv myenv && myenv/bin/pip install -r requirements.txt
app/dev.sh                 # the whole app on a built-in preview engine: no key, no model, no quota
```

Open <http://127.0.0.1:5173>. The preview engine returns canned answers with the real shapes and states; it is what the
tests and screenshots use.

To use real models, copy the configuration template and fill it in. **No credential is in this repository**: `.env*`
files are git-ignored and `.env.example` holds placeholders only.

```bash
cp .env.example .env.real  # set LLM_BASE_URL, LLM_API_KEY and the model names of each category
app/dev.sh --real          # every AI request spends the team's quota
app/dev.sh --ollama        # or local models through Ollama (.env.ollama), with the constraint simulator on
```

More settings (ports, voice, speech recognition): [`app/README.md`](app/README.md).

## Tests

```bash
myenv/bin/python -m pytest -q        # engine: 1153 tests, no model needed
cd app/server && npm test            # server: sockets, permissions, plans, security, AI API on the preview engine
cd app/client && npm test            # client: geometry, history, diagrams, translations
python -m scripts.load_mixed --virtual   # a simulated classroom on the constraint simulator
```

## Repository layout

| Path | Contents |
|---|---|
| `engine/`, `router/`, `gatekeeper/`, `rag/`, `metrics/`, `simulator/` | the GenAI engine: tasks, routing, limits and cache, retrieval, records, constraint simulator |
| `prompts/` | versioned prompt templates, one per task |
| `data/` | course text, course graph, exercise bank, glossary |
| `app/client/` | React web app |
| `app/server/` | Node server: rooms, real-time sync, voice signaling, the feature layer over the engine |
| `app/bridge/`, `app/diagrams/` | Python bridge that hosts the engine; the diagram generator |
| `tests/`, `app/*/test/` | tests and evaluation sets |

## Privacy and safeguards

- No real accounts: a learner picks a display name and a role, and the browser gets a random id. The display name is
  shown to the other members of a room and stored with a file the learner uploads; the email typed at sign-in stays in
  the browser and no password is stored. Tests, fixtures and course text are synthetic or public.
- Voice is peer to peer and is not recorded. A spoken question to Lumi is transcribed on our own server by a local
  Whisper model, used once and discarded.
- The inference service receives course text, the question with the recent turns of the conversation, the text on the
  board, the text of an uploaded file when an answer uses it, and a photo or scanned page when it is read. It never
  receives a name, an email or an id: in voice, speakers are sent as "Student 1", "Student 2".
- The AI supports the teacher: professor material ranks first, uncertain answers are flagged for review, and a professor
  can remove an answer from the cache.
- Uploads are checked against their declared type and served back inert; the API sends strict security headers.

**External services, declared:** the challenge's inference service (FelCloud) for every model call; a public STUN
server (`stun.l.google.com`) so browsers can find each other for voice (no audio passes through it). Speech
recognition (faster-whisper) and all maths (sympy) run locally.

## Business model

A short note is in the technical report; the full canvas is
[`EdNova Business Model Canvas.md`](EdNova%20Business%20Model%20Canvas.md). The plans it describes (Free, Educator,
Campus, Enterprise) are enforced in the app as a preview: see Settings → Plan.
