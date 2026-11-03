# EdNova app

The EdNova web app: landing page, dashboards, the AI study features and the
real-time collaborative whiteboard. React + Canvas on the front end, Node.js +
Express + Socket.IO + SQLite on the back end, and a small Python bridge that
hosts the EdNova LLM engine.

## Run it

```bash
cd server && npm install && npm run dev      # http://localhost:3000
cd client && npm install && npm run dev      # http://localhost:5173
```

Boards, materials and AI history are saved under `server/data/` and survive
restarts. Without the engine, AI features run on a built-in preview engine
(see **AI engine** below).

| Setting | Where | Default |
|---|---|---|
| `PORT` | server | `3000` |
| `CLIENT_ORIGINS` | server, comma-separated | `http://localhost:5173,http://127.0.0.1:5173` |
| `DB_PATH` | server | `server/data/whiteboard.db` |
| `VITE_SERVER_URL` | client | port 3000 on the host that served the page |
| `ENGINE` | server: `auto`, `engine` or `fake` | `auto` |
| `ENGINE_URL` | server: where the bridge listens | `http://127.0.0.1:8765` |
| `ENGINE_FAKE_MODE` | server: preview engine speed, `normal`, `slow` or `degraded` | `normal` |
| `ENGINE_ALLOW_FORCE` | server: let requests force a degraded state (`context.fake_outcome`) for demos | on unless `ENGINE=engine` |

Tests: `npm test` in `server/` (sockets, permissions, persistence, limits, the
AI API, and the Python bridge when `python3` is installed) and in `client/`
(geometry, undo, history replay, templates, translations).

## Pages

| Path | What it is |
|---|---|
| `/` | Landing page (FR / EN / AR, right-to-left in Arabic) |
| `/login` | Sign in or create an account as a student or professor (preview: stored in the browser only) |
| `/app` | Main dashboard — student or professor view; sections at `/app/map`, `/app/rooms`, `/app/board`, `/app/voice`, `/app/prep`, `/app/materials`, `/app/diagrams`, `/app/groups`, `/app/insights`, `/app/review` (professors), `/app/settings` |
| `/board?room=CODE` | The collaborative whiteboard |

Features that aren't built yet (voice chat, group assignment, diagram
generator) have their menu entries, cards and pages in place, marked **Soon**.
Cards marked *Example data* (progress map, rooms list, roster) use demo data.

## AI features

All of them run on the EdNova engine through the server (`/api/ai/*`):

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
browser ──/api/ai/*──▶ Node server (feature layer) ──HTTP/NDJSON──▶ bridge/bridge.py ──▶ EdNova engine
                         └─ built-in preview engine when the bridge is down (ENGINE=auto)
```

- `server/src/ai/routes.js` — the feature layer: per-task input whitelist,
  learner / room / course ids, the tutor loop, exercise grading, materials,
  run log and review queue.
- `server/src/ai/http-engine.js` — talks to the bridge; never throws on a
  transport error (returns a `service_unavailable` result, like the engine).
- `server/src/ai/fake/` — the preview engine: same `run()` contract, Result /
  Event shapes, degrade reasons and per-room / per-learner fairness, over a
  small Derivatives course in FR / EN / AR with a Darija / Arabizi glossary.
  Grading uses a whitelisted math parser, never `eval`.
- `bridge/bridge.py` — standard-library Python; hosts one `Engine` per process
  on localhost and streams events. `bridge/test/stand_in/` is a minimal
  stand-in with the engine's interface, used only by the tests.

To plug in the real engine, see [`../engine/README.md`](../engine/README.md).

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

## EdNova pages

`client/public/ednova/` holds copies of the Academic Constellation and Planetary
Universe pages, wired to the board (study-board buttons, sidebar links, deep
links such as `/ednova/constellation.html?course=MATH102`). Regenerate them after
editing the originals in `design/EdNova-Dark-Experiences-FINAL/`:

```bash
cd client && npm run sync:ednova
```

Course codes such as `MATH102` open that course's study room.

## Limits

5,000 items and 40 MB of images per room, 2.5 MB per image, 50 pages, 5,000
history entries, and per-connection rate limiting. AI: 40 requests a minute per
address, 15 MB per material. This is a prototype: access codes protect rooms,
but there are no user accounts — roles (student / professor) are chosen in the
browser, so put it behind real authentication before deploying it publicly.
