# Handoff to the feature team (design)

## Run it
```bash
app/dev.sh            # fake engine: no key, no model, no quota (default)
app/dev.sh --real     # the real engine on FelCloud: every AI request spends the team's quota (20 / min)
```
App **http://127.0.0.1:5173**, server `:3000` (`/health`), engine bridge `:8765`; override with `CLIENT_PORT`, `SERVER_PORT`,
`BRIDGE_PORT`. Needs Node ≥ 22.5 (24 recommended) and a python with the repo's `requirements.txt` (`ENGINE_PYTHON`).
Logs: `app/.dev-logs/`. Ctrl-C stops all three.

**Force a state (fake engine only):** add `context.fake_outcome` to an `/api/ai/run` body (the app passes `context` through),
e.g. `{"task":"explain","inputs":{"question":"x"},"context":{"fake_outcome":"quota_exhausted"},"learner_id":"me"}`. A name the task
does not have is a 400.

| task | outcomes |
|---|---|
| explain | `ok` `cache_hit` `fast_extractive` `deadline` `quota_exhausted` `not_in_course` `wrong_math` `quota_wait` `superseded` `room_busy` |
| hint | `ok` `stored` `quota_exhausted` `superseded` `room_busy` |
| grade | `correct` `incorrect` `quota_exhausted` |
| classify | `ok` `cache_hit` `safe_default` `quota_exhausted` `superseded` `room_busy` |
| check_claim | `SUPPORTED` `CONTRADICTED` `PARTLY` `COMPOUND` `NOT_IN_COURSE` `CONTRADICTED_llm` `llm_failed` `quota_exhausted` `superseded` `room_busy` |
| check_answer | `ok` `wrong_step` `rule` `llm_feedback` `unchecked` `quota_exhausted` `room_busy` |
| ocr_transcribe | `ok` `math` `low_confidence` `invalid_image` `quota_exhausted` `superseded` `room_busy` |
| quiz / prep_plan | `ok` `diagnostic` / `ok` `all_mastered` |

The fake answers every question with the same canned text; for realistic content use `--real`. Reference of every state and
example JSON: `docs/ENGINE_API.md` (repo root).

## Contract: do not rename (restyle freely)
The screens read these fields of the engine's result (`/api/ai/run`, `/api/ai/turn`); changing a name breaks them.
- Every result: `text`, `data`, `degraded`, `degrade_reason`, `eta_seconds`, `mode` (`fast_extractive`), `followup_available`,
  `from_cache`, `model_class_used`, `request_id`, `events[].name` / `.data.eta_s` / `.data.excerpts`, `notes.sources` (chunk ids;
  `professor/…` = the professor's upload).
- Tutor turn: `classification.data.{intent,language,confusion}`, `follow.task`; explain `data.{opener,body,excerpts[],framing,exercise}`;
  quiz `data.{exercises[],intro}`; grade `data.{verdict,feedback}`; hint `text`, `data.{hint_level,hints_total,source}`.
- Board: check_claim `data.{verdict,correction,parts[{claim,verdict,correction}]}`; check_answer `data.{steps[{index,verdict,note}],first_wrong,final_verdict,feedback}`;
  OCR `data.{text,latex,confidence_note}`.
- Prep: plan `data.{sessions[{day,minutes,items[{concept,kind,minutes,reason}]}],focus_points,assessment_exercise_ids}`; page
  `data.{title,explanation,worked_example{statement,steps,answer},quiz[]}`; course graph `title` objects `{fr,en,ar}`; mastery `{concept:{value,attempts}}`.
- Materials: `status` (`processing` `indexed` `needs_ocr` `empty` `failed`), `chunks[{id,title,concept,confidence,low_confidence,confirmed}]`,
  `visibility`, `source` (`professor` / `upload`), `mine`. Review: `flags[{request_id,reason,question,answer,reports[]}]`.
- Degrade reasons the UI maps today: `quota_low` `breaker_open` `midsize_saturated` `midsize_unavailable` `deadline` `explain_guard:*` (fast),
  `service_busy` `quota_exhausted` `service_unavailable` `room_busy` (wait + countdown), `ungrounded_formula` `wrong_math` `low_confidence`
  `invalid_image`, `superseded` (silent). Anything else falls back to a generic notice.

## Do not edit
`app/server/src/ai/*`, `app/bridge/*`, `app/diagrams/*`, `client/src/lib/diagrams/*`, `client/src/lib/engine.js`, `client/src/lib/api.js`. Design changes go in `client/src/components/**`
and the CSS. Need another field or a new state? Ask the engine side; do not patch the feature layer.

## Design system
Colours, type and base components come from `client/src/styles/lumi-tokens.css`; app-level additions are in `styles/lumi-app.css`. Use `var(--lumi-*)`, never a hex. The app has a light theme (`html[data-theme="light"]`): write overlays as `rgb(var(--tint) / a)`, scrims as `rgb(var(--base) / a)`, the strongest fill as `var(--contrast)` with `var(--on-contrast)`, text on a bright accent as `var(--on-accent)`, and an accent used as text as `var(--glow-ink)` / `--spark-ink` / `--rose-ink`. Never write literal white or #05061A, or the screen breaks in one of the two themes. Glow is for AI activity, Spark for mastery. For the canvas use `lib/lumiColors.js`. Browser storage keys keep their `ednova-` prefix on purpose: renaming them signs people out.

## Diagram generator (`/app/diagrams`, no LLM, no quota, works with the fake engine too)
Screen: `client/src/components/dashboard/Diagrams.jsx`, styles under `.dg-*` in `dashboard.css`; colours of the drawing are the `--dg-*`
variables on `.dg-page`. The JSON contract is `docs/diagram.schema.json`. Trigger words (Darija, Arabizi...) are in
`app/diagrams/triggers/*.yaml`, no code change needed. "Send to whiteboard" opens the board and the items arrive selected, as ordinary items.

## Voice chat
`components/VoicePanel.jsx` and `styles/voice.css` (`.vc-*`) are design-owned. The hooks in `lib/voice/` (WebRTC, recording,
speechSynthesis) and `server/src/voice/*` are logic: ask before changing them. Event names `voice:*` / `lumi:*` and the
`lumi:reply` fields (`transcript`, `reply`, `spoken`, `lang`, `askedBy`, `degraded`) are a contract.

## Design priorities
1. **Degraded and waiting states** (the engine is slow and quota-limited by design): fast-mode extracts + "get the full explanation", busy / quota
   countdowns, room-busy, offline, "formula looks wrong" notices, progress while it works (`events`, incl. the verbatim course sentences).
2. **Progress map** (the concepts of `analyse` as planets; mastery from real attempts).
3. **Board AI tab and pinned results** (ask, fact-check with parts, check my work, read handwriting, pin as note).
4. **Professor review** (flagged answers, content gaps later) and materials (upload, confirm concept tags).
5. **FR / EN / AR and RTL on every screen** (`dir="auto"` on user and model text; check mixed formulas inside Arabic).

## Stale
`client/public/lumi/` (Atlas / Universe static pages) still list MATH102 / CS201 / PHYS101 and are no longer linked: redesign for the one
course or delete them.

## Known weak spots (engine side, not design)
- Long Arabizi questions can be misclassified (a hint without an open exercise is now routed to explain, but answer quality varies).
- Handwriting OCR has not been tried on a real photo.
- The wrong-math guard misses limits stated without a point ("la limite de … est égale à …"): they are listed as unchecked, not flagged.
- Only the course `analyse` exists; other topics answer "not in the course" by design.
