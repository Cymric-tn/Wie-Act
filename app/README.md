# Lumi app

The Lumi web app: landing page, dashboards, the AI study features and the
real-time collaborative whiteboard. React + Canvas on the front end, Node.js +
Express + Socket.IO + SQLite on the back end, and a small Python bridge that
hosts the Lumi LLM engine.

## Run it

```bash
app/dev.sh            # bridge (the engine's fake: no key, no model) + Node server + client
app/dev.sh --real     # the real engine, ENV_FILE=.env.real: spends the team's FelCloud quota
```

| | Default port | Override |
|---|---|---|
| client (the app) | 5173 | `CLIENT_PORT` |
| Node server | 3000 | `SERVER_PORT` |
| engine bridge | 8765 | `BRIDGE_PORT` |

`dev.sh` checks the Node version, generates a local `ENGINE_SERVICE_TOKEN` when none is set, and writes logs to
`app/.dev-logs/`.

Boards, materials and AI history are saved under `server/data/` and survive
restarts. The AI features need the engine's bridge (see **AI engine** below); without it they answer
"service unavailable" and say so.

| Setting | Where | Default |
|---|---|---|
| `PORT` | server | `3000` |
| `CLIENT_ORIGINS` | server, comma-separated | `http://localhost:5173,http://127.0.0.1:5173` |
| `DB_PATH` | server | `server/data/whiteboard.db` |
| `VITE_SERVER_URL` | client | port 3000 on the host that served the page |
| `ENGINE_URL` | server: where the bridge listens | `http://127.0.0.1:8765` |
| `ENGINE_SERVICE_TOKEN` | server **and** bridge: the shared secret (header `X-Engine-Token`) | none: the bridge refuses to start without it |
| `ENGINE_ALLOW_FORCE` | server: let requests force a degraded state (`context.fake_outcome`) for demos | on only when the bridge runs `--fake` |
| `AI_COURSE` | server: the default course id | `analyse` |
| `ICE_SERVERS` | server: WebRTC ICE servers as JSON, sent to the browser when it joins voice (add TURN here) | `[{"urls":"stun:stun.l.google.com:19302"}]` |
| `VOICE_MAX_PEERS` | server: people per voice channel (full mesh) | `6` |
| `LUMI_AUDIO_MAX_BYTES` | server **and** bridge: the largest "Ask Lumi" recording | `2097152` |
| `LUMI_VOICE_TIMEOUT_MS` | server: deadline for one spoken question (STT + classify + answer) | `60000` |
| `WHISPER_MODEL` | bridge: faster-whisper model (`tiny`, `base`, `small`, `medium`, a path) or `off` | `small` |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE` | bridge | `auto` / `int8` |
| `WHISPER_LANGUAGES` | bridge: languages Whisper may pick from (empty = any) | `fr,en,ar` |

Node 22.5 or newer is required (the server uses the built-in `node:sqlite`); the team's machines run Node 24
(`app/.nvmrc`; under Node 20 `npm test` stops with "Could not find 'test/**/*.test.js'").

To run the three parts by hand (what `dev.sh` does):

```bash
export ENGINE_SERVICE_TOKEN=change-me                 # the same value for the bridge and the server
python app/bridge/bridge.py --fake                    # or without --fake: the real engine (reads the repo's .env / ENV_FILE)
cd app/server && npm run dev                          # http://localhost:3000
cd app/client && npm run dev                          # http://localhost:5173
```

Tests: `npm test` in `server/` (sockets, permissions, persistence, limits, and the AI API against `bridge.py --fake`,
run with `ENGINE_PYTHON`, else the repo's `myenv`, else `python3`; with none that can import the engine that suite is
listed as skipped, so check the count: 61 with it, 43 without) and in `client/` (geometry, undo, history replay,
templates, translations; `test/voice.test.js` covers voice signaling and "Ask Lumi" against a stub engine). The bridge
itself is tested against the real `Engine` in the repo's `tests/test_bridge.py`.

## Pages

| Path | What it is |
|---|---|
| `/` | Landing page (FR / EN / AR, right-to-left in Arabic) |
| `/login` | Sign in or create an account as a student or professor (preview: stored in the browser only) |
| `/app` | Main dashboard — student or professor view; sections at `/app/map`, `/app/rooms`, `/app/board`, `/app/voice`, `/app/prep`, `/app/materials`, `/app/diagrams`, `/app/groups`, `/app/insights`, `/app/review` (professors), `/app/settings` |
| `/board?room=CODE` | The collaborative whiteboard |

Features that aren't built yet (voice chat, group assignment) have their menu entries, cards and pages in place, marked **Soon**.
Cards marked *Example data* (progress map, rooms list, roster) use demo data.

## AI features

All of them run on the Lumi engine through the server (`/api/ai/*`):

- **AI Companion** (every dashboard page) — one tutor turn per message:
  classify → follow-up → explain, hint, quiz, or grade when the message answers
  the open exercise. Replies in FR / EN / AR / Darija / Arabizi (auto-detected
  or chosen), shows the engine's progress as it works (including the verbatim
  course sentences it found first), cites its sources, and handles every
  degraded state: simplified answers with "get the full explanation", busy /
  quota / room-busy notices with a retry countdown, and formula warnings.
  Students can report an answer.
- **Exercises** — graded on the server (solutions never reach the browser),
  three stored hints, "show solution"; each answer updates the learner's mastery.
- **Assessment Prep** — study plan (prerequisites first, weakest topics next,
  at the learner's pace), study pages (explanation, worked example, practice),
  a three-exercise level test, more practice on the weakest concept, and
  mastery per concept from real attempts.
- **Course Materials** — upload PDF / Word / Markdown / text; the engine indexes
  them (professor material ranks first in answers). Visibility: whole course or
  class (professors), private or class (students). Professors confirm or fix
  each section's concept tag.
- **Whiteboard AI tab** — ask about the board or the selection, fact-check a
  selected claim, check a worked solution line by line, read handwriting from a
  selection; pin any result to the board as a note. One AI request per room at
  a time.
- **AI review** (professors) — answers the engine couldn't verify and answers
  students reported; rejecting one removes it from the engine's cache. The
  page also shows which engine is answering and today's run statistics.

## AI engine

```
browser ──/api/ai/*──▶ Node server (feature layer) ──HTTP/NDJSON, X-Engine-Token──▶ bridge/bridge.py ──▶ Lumi engine
```

- `server/src/ai/routes.js` — the feature layer: per-task input whitelist, learner / room / course ids, the tutor loop,
  exercise grading, materials, run log and review queue. It adapts the app's vocabulary to the engine's contract
  (`check_answer` takes `problem` + `steps`; `prep_plan` takes a `scope`, a `deadline` date and a pace and answers
  `focus_points`, which become day sessions; study pages come from the bridge's `/generate_page`).
- `server/src/ai/http-engine.js` — talks to the bridge; never throws on a transport error (returns a
  `service_unavailable` result, like the engine).
- `bridge/bridge.py` — standard-library Python; the single service for the app: hosts one `Engine` per process on
  127.0.0.1, answers only with the shared token, holds the one-process-per-key lock (it shares it with the engine's own
  `services/engine_server.py`: never run both) and streams events. `--fake` serves the engine's own `FakeEngine`
  (same Result / Event shapes, forced outcomes with `context.fake_outcome`), which is what the AI tests use.
- The course is `analyse` (derivation), the engine's only course: the progress map draws its concepts as planets,
  generated from `data/courses/analyse.yaml` by `python app/scripts/gen_course.py` (a repo test fails if it drifts).

## Voice chat and "Ask Lumi"

Members of a board room can join its **voice channel** (button at the top right of the board) and talk to each other.
Any member can **hold "Ask Lumi"**, speak a question and release it. Everyone in the call sees the question and Lumi's
answer in the transcript strip, and each browser reads the answer aloud.

```
browser ──getUserMedia──▶ RTCPeerConnection × N ◀──audio, peer to peer──▶ other members (≤ 6, full mesh)
        ──Socket.IO voice:* (signaling only)──▶ server/src/voice/signaling.js
        ──Socket.IO lumi:ask (webm/opus)──▶ server/src/voice/lumi.js ──/voice/transcribe──▶ bridge (faster-whisper)
                                                                   ──engine.run classify → explain | hint | quiz──▶ bridge (engine)
        ◀──lumi:thinking / lumi:reply / lumi:error── to the whole voice channel; speechSynthesis reads the answer
```

- **Speech to text** runs in the bridge with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (`small`, int8,
  CPU: about 3 s for a short question on a 12-core laptop). It is not an LLM call and spends no FelCloud quota. Install it in the
  engine's python with `pip install faster-whisper`. It decodes webm/opus through PyAV, which ships its own ffmpeg
  libraries, so no system `ffmpeg` is needed. The model (~480 MB) downloads on first use and is loaded when the bridge
  starts. Without faster-whisper, voice chat still works and "Ask Lumi" says speech recognition is unavailable.
- **The answer goes through `engine.run`,** the same path as the tutor: classify, then the engine's follow-up (explain,
  hint or quiz), with the room's whiteboard text (text items, notes, equations; 2,000 characters) as explain's
  `board_text` and the last four voice turns as its `history`. Chit-chat and off-topic questions get a fixed line.
  Degraded states (quota, busy room, bridge down) become a short spoken message with the wait time.
- **Text to speech** is the browser's `speechSynthesis` (FelCloud has no TTS). The voice depends on the device. When
  there is no voice for the language, the panel says so and the answer stays as text. "Read aloud" can be turned off
  per browser. While an answer is read, the reader's microphone is muted to the group so Lumi is not heard twice.
- **Guards (server):** voice requires joining the board room first (its access code applies). Signals are relayed only
  between members of one voice channel. Each recording is capped at 2 MB and 20 s, each room handles one Lumi question
  at a time, and each user can ask once every 5 s. Recordings are never stored. Lumi in voice is premium:
  `canUseLumiVoice(user)` in `server/src/voice/lumi.js` enforces it on the server. The app has no tiers yet, so it
  currently allows everyone (TODO).
- **HTTPS:** browsers allow the microphone only on `https://` or `localhost`. For a demo across a LAN, serve the
  client over HTTPS (a tunnel, or Vite with a certificate) and set `VITE_SERVER_URL` / `CLIENT_ORIGINS` to match.
  Users behind strict NATs (some university networks) need a TURN server in `ICE_SERVERS`.

Manual test: two browsers (one private window) with headphones join the same room. Both appear in the call, and the
ring lights when the other speaks. Mute shows on the other side. Hold "Ask Lumi" and ask "what is the derivative of x
squared?": both see the question and the answer, and both hear it. A tap shorter than 0.4 s is ignored with a hint. A
second question while Lumi answers is refused. Stop the bridge and ask again: a clean error, and the next question
works once the bridge is back. Deny the microphone: a clear message, and the board keeps working.

## Whiteboard features

**Drawing** — pressure-sensitive pen, highlighter, object eraser, laser pointer,
line/arrow/rectangle/ellipse (Shift snaps), text, sticky notes, LaTeX equations
(MathJax), images and PDFs (each PDF page becomes a board page). *Shape assist*
turns rough lines, boxes and circles into clean shapes.

**Editing** — select, box-select, move, resize, recolour, re-width, fill,
duplicate, bring to front / send to back, copy/paste, arrow-key nudging, and
undo/redo for your own changes.

**Canvas** — infinite board with pan, zoom (Ctrl/⌘ + scroll, pinch, buttons),
zoom-to-fit, minimap, multiple pages, and four backgrounds (starfield, dot grid,
graph paper, paper).

**Classroom** — the room's creator is host and can lock the board, present
(everyone follows their view), spotlight a student, hand over host, set an
access code, run a shared timer and clear pages. Everyone can vote on sticky
notes; the top ideas are listed in the Room panel. Six templates: graph axes,
Venn diagram, mind map, KWL chart, Cornell notes, exit ticket.

**History & export** — scrub or replay every change in a room; export the page
as PNG or SVG, all pages as PDF, or copy an image to the clipboard.

**Presence** — live cursors, reactions, join/leave notices, connection status.

## Lumi pages

`client/public/lumi/` holds copies of the Academic Constellation and Planetary
Universe pages, wired to the board (study-board buttons, sidebar links, deep
links such as `/lumi/constellation.html?course=MATH102`). Regenerate them after
editing the originals in `design/EdNova-Dark-Experiences-FINAL/`:

```bash
cd client && npm run sync:lumi
```

Course codes such as `MATH102` open that course's study room.

## Limits

5,000 items and 40 MB of images per room, 2.5 MB per image, 50 pages, 5,000
history entries, and per-connection rate limiting. AI: 40 requests a minute per
address, 15 MB per material. This is a prototype: access codes protect rooms,
but there are no user accounts — roles (student / professor) are chosen in the
browser, so put it behind real authentication before deploying it publicly.
