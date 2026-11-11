# Lumi — Group Voice Chat with AI: Implementation Spec

> Hand this file to Claude Code. It describes everything needed to add group voice chat (human ↔ human) plus an AI participant ("Lumi") to the existing app. Deadline-driven: build **Phase A** fully; Phase B items are optional.

---

## 0. Instructions for Claude Code

1. **Explore the repo first.** Find:
   - The Node/Express server entry and where the Socket.IO server (`io`) is created.
   - How rooms/groups work today (room codes, professor-assigned groups) and how the whiteboard joins a room.
   - Auth/session handling (users have roles: learner / professor) and the user tier (free vs premium) if it exists.
   - The React client structure (the group/room page where the whiteboard lives).
   - The separate Python LLM engine: framework (FastAPI/Flask?), how Node calls it (URL/env var), and the existing FelCloud call helper + any course-context/RAG retrieval function.
2. **Reuse existing patterns** (naming, folder layout, auth middleware, env var conventions). Do not introduce new frameworks.
3. Where this spec's names conflict with existing code, **adapt to the existing code** and note it in the final summary.
4. Do not break the whiteboard or any existing Socket.IO events. Namespace all new events with `voice:` and `lumi:`.
5. When finished, output a summary: files changed, new env vars, how to run, manual test steps, and any deviations from this spec.

---

## 1. Feature summary

- Members of a group room can **join a voice channel** for that room and talk to each other (WebRTC audio mesh; Socket.IO is the signaling channel).
- Any member can **hold an "Ask Lumi" button**, speak a question, and release. The audio is transcribed (faster-whisper), answered by the **FelCloud LLM** with the room's course context and whiteboard state, and the answer is **broadcast to everyone in the room**: shown as text in a transcript strip and **spoken aloud** in each client via browser `speechSynthesis`.
- Languages: French, English, Arabic; Darija accepted on input (transcript passed to the LLM as-is; the LLM interprets it).
- **Freemium rule:** free tier gets voice chat between humans, but **no Lumi button** (server also enforces it).

### Constraints
- The main model must be FelCloud's. FelCloud has **no TTS and no embedding model**; STT/TTS are allowed via other local models.
- Groups are small (≤ 6 people) → full mesh WebRTC, no media server.

---

## 2. Architecture

```
┌──────────── Browser (each member) ────────────┐
│ getUserMedia(mic) ──► RTCPeerConnection × N   │◄──── audio P2P ────► other members
│ Hold "Ask Lumi" ──► MediaRecorder (webm/opus) │
│ lumi:reply ──► transcript strip + speechSynth │
└───────────────┬───────────────────────────────┘
                │ Socket.IO  (voice:* signaling, lumi:* events)
┌───────────────▼───────────────┐
│ Node / Express / Socket.IO    │  auth, room membership, tier check,
│                               │  rate limit, per-room lock
└───────────────┬───────────────┘
                │ HTTP multipart POST /voice/ask
┌───────────────▼───────────────┐
│ Python LLM engine             │  faster-whisper STT → context build →
│                               │  FelCloud chat → {transcript, reply, lang}
└───────────────────────────────┘
```

---

## 3. Socket.IO event contract

All events are scoped to the voice room `voice:<roomId>`.

| Direction | Event | Payload | Notes |
|---|---|---|---|
| C→S | `voice:join` | `{ roomId }` | Server verifies user is a member of the room. Replies with ack `{ ok, peers: [{socketId, userId, name}] }`. |
| S→C (others) | `voice:peer-joined` | `{ socketId, userId, name }` | |
| C→S | `voice:leave` | `{ roomId }` | Also triggered on disconnect. |
| S→C (others) | `voice:peer-left` | `{ socketId }` | |
| C→S→C | `voice:offer` | `{ to, data: RTCSessionDescriptionInit }` | Server relays to `to` as `{ from, data }`. Only relay if both sockets are in the same voice room. |
| C→S→C | `voice:answer` | same | |
| C→S→C | `voice:ice` | `{ to, data: RTCIceCandidateInit }` | |
| C→S | `voice:mute` | `{ roomId, muted }` | Broadcast `voice:peer-muted { socketId, muted }`. |
| C→S | `lumi:ask` | `{ roomId, audio: ArrayBuffer, mimeType }` | Ack `{ ok, error? }`. |
| S→C (room) | `lumi:thinking` | `{ askedBy: {userId, name} }` | Show "Lumi is thinking…". |
| S→C (room) | `lumi:reply` | `{ id, askedBy, transcript, reply, lang, ts }` | |
| S→C (asker) | `lumi:error` | `{ message }` | |

### Signaling convention (avoids glare)
- The **newcomer does NOT send offers.** When a peer joins, **every existing member** creates an `RTCPeerConnection` toward the newcomer and sends an offer. The newcomer answers each.

---

## 4. Server (Node) — implementation

Create `server/voice/` (adapt path to repo) with:

### 4.1 `voiceSignaling.js`
- `registerVoiceHandlers(io, socket)` called from the existing `io.on('connection')`.
- Keep an in-memory map: `voiceRooms: Map<roomId, Map<socketId, {userId, name}>>`.
- `voice:join`:
  - Validate auth (reuse existing socket auth / session) and room membership (reuse existing room lookup). Reject otherwise.
  - Cap at **6** participants; reject with `{ ok:false, error:'Room full' }`.
  - `socket.join('voice:' + roomId)`, store in map, ack with current peer list (excluding self), emit `voice:peer-joined` to others.
- `voice:offer|answer|ice`: relay **only if** `to` is in the same voice room as sender. Emit to `to` with `{ from: socket.id, data }`.
- `voice:leave` and `disconnect`: remove from map, `socket.leave`, emit `voice:peer-left`. Delete empty rooms.
- `voice:mute`: broadcast state.

### 4.2 `lumiVoice.js`
- `lumi:ask` handler:
  1. Verify membership in the voice room.
  2. **Tier check:** if user is free tier → ack `{ ok:false, error:'Lumi voice is a premium feature' }`. (If tiers don't exist yet in the codebase, add a single helper `canUseLumiVoice(user)` returning `true` and leave a TODO.)
  3. Validate audio: `ArrayBuffer`/`Buffer`, size ≤ **2 MB**, `mimeType` starts with `audio/`.
  4. **Per-room lock:** only one Lumi request in flight per room. If busy → ack `{ ok:false, error:'Lumi is answering, wait a moment' }`.
  5. **Rate limit:** max 1 request / 5 s per user (simple in-memory timestamp map).
  6. Emit `lumi:thinking` to the room.
  7. Build multipart form and POST to `${LLM_ENGINE_URL}/voice/ask` with fields: `audio` (file), `room_id`, `user_id`, `user_name`, `whiteboard_summary` (see 4.3), `history` (JSON of last 6 Lumi turns for this room). Timeout **30 s** (`AbortController`).
  8. On success: build reply object with `id` (uuid), `askedBy`, `ts`; push to per-room history (keep last 20, in memory; also persist to SQLite if a messages table pattern exists — optional); emit `lumi:reply` to room; ack `{ ok:true }`.
  9. On failure: emit `lumi:error` to the asker, ack `{ ok:false, error }`. Always release the room lock (`finally`).
- Use Node 18+ global `fetch`, `FormData`, `Blob`. If Node < 18, use `undici` or the HTTP client already in the repo.

### 4.3 Whiteboard context
- Find where the whiteboard state per room is held server-side. Write `getWhiteboardSummary(roomId)` that returns plain text: all text elements / sticky notes / labels, concatenated, truncated to **2000 chars**. If no server-side state exists, return `''` and add a TODO.

### 4.4 Env vars
- `LLM_ENGINE_URL` (reuse existing var if present).
- `VOICE_MAX_PEERS=6`, `LUMI_AUDIO_MAX_BYTES=2097152` (optional, with defaults).
- Expose to the client (via existing config endpoint or Vite env): `ICE_SERVERS` JSON, default `[{"urls":"stun:stun.l.google.com:19302"}]`. Optional TURN creds later.

---

## 5. Python engine — implementation

### 5.1 Dependencies
Add to requirements: `faster-whisper`, `python-multipart` (if FastAPI). System dep: `ffmpeg` (document it in README).

### 5.2 STT module `stt.py`
```python
from faster_whisper import WhisperModel
import os

_model = None
def get_model():
    global _model
    if _model is None:
        _model = WhisperModel(
            os.getenv("WHISPER_MODEL", "small"),
            device=os.getenv("WHISPER_DEVICE", "auto"),
            compute_type=os.getenv("WHISPER_COMPUTE", "int8"),
        )
    return _model

def transcribe(path: str) -> tuple[str, str]:
    segments, info = get_model().transcribe(path, vad_filter=True, beam_size=1)
    text = " ".join(s.text.strip() for s in segments).strip()
    return text, info.language  # e.g. 'fr', 'en', 'ar'
```
- Load the model at startup (warm-up) so the first demo request isn't slow.
- Do NOT pin `language=`; let Whisper detect (FR/EN/AR mix, Darija usually detected as `ar`).

### 5.3 Endpoint `POST /voice/ask`
Fields: `audio` (file), `room_id`, `user_id`, `user_name`, `whiteboard_summary`, `history` (JSON string).

Steps:
1. Save upload to a temp file (`tempfile.NamedTemporaryFile(suffix=".webm", delete=False)`), always delete in `finally`.
2. `text, lang = transcribe(path)`. If `text` is empty or < 2 chars → return `400 {"error": "Didn't catch that, try again"}`.
3. Retrieve course context for `room_id` using the **existing** retrieval/context function in the engine (professor uploads take priority — reuse existing logic). Limit to ~3000 chars.
4. Build messages (see 5.4) and call the **existing FelCloud chat helper**.
5. Post-process reply with `clean_for_speech()` (5.5).
6. Return `{"transcript": text, "reply": reply, "lang": reply_lang}` where `reply_lang` = detected language of the question mapped to `fr|en|ar` (default `en`; Darija → `ar`).

Run STT in a thread pool (`run_in_threadpool` / `asyncio.to_thread`) so it doesn't block the event loop.

### 5.4 Prompt
System prompt (adapt to Lumi's existing persona if one exists in the engine):

```
You are Lumi, a friendly peer learning companion speaking out loud in a group study voice call.
Rules:
- Your answer will be read aloud by text-to-speech. Answer in 2 to 4 short spoken sentences.
- No markdown, no bullet points, no code blocks, no emojis, no URLs. Write math in words.
- Reply in the same language the student used. If they spoke Tunisian Darija, reply in simple Modern Standard Arabic.
- Act like a peer, not a lecturer: guide with a hint or a question when the student is solving an exercise; give the direct answer for factual questions.
- Base answers on the course material below when relevant. If unsure, say so briefly.
- If the question is unrelated to studying, steer back to the session in one sentence.

Course material:
{course_context}

Current group whiteboard content:
{whiteboard_summary}
```
Then append `history` turns as alternating user/assistant messages (`"{user_name}: {transcript}"` / reply), then the current user message `"{user_name}: {text}"`.

Generation params: low max tokens (~200), temperature ~0.5.

### 5.5 `clean_for_speech(text)`
Strip markdown (`*`, `#`, backticks, `- ` bullets, `[x](y)` → `x`), collapse whitespace, truncate to ~600 chars at a sentence boundary.

---

## 6. Client (React) — implementation

### 6.1 Files (adapt paths)
- `client/src/voice/useVoiceRoom.js` — hook managing mic, peers, signaling.
- `client/src/voice/useLumiAsk.js` — push-to-talk recording + `lumi:*` events + TTS.
- `client/src/voice/VoicePanel.jsx` — UI.
- Mount `VoicePanel` on the group room page next to the whiteboard.

### 6.2 `useVoiceRoom(socket, roomId)`
State: `joined`, `peers` (array `{socketId, userId, name, muted, speaking}`), `muted`, `error`.

- `join()`:
  - Must be called from a **click** (autoplay + permission).
  - `getUserMedia({ audio: { echoCancellation:true, noiseSuppression:true, autoGainControl:true }, video:false })`. On error, set a friendly error (permission denied / no mic / needs HTTPS).
  - `socket.emit('voice:join', {roomId}, ack)`. On ack, store peers; **do not** create offers (existing members will offer).
- On `voice:peer-joined` → `createPeer(socketId)`, add local tracks, `createOffer` → `setLocalDescription` → emit `voice:offer`.
- On `voice:offer` → `createPeer(from)`, `setRemoteDescription`, flush queued ICE, `createAnswer` → `setLocalDescription` → emit `voice:answer`.
- On `voice:answer` → `setRemoteDescription`, flush queued ICE.
- On `voice:ice` → if `remoteDescription` not set yet, **queue** the candidate; else `addIceCandidate`.
- `createPeer(id)`:
  - `new RTCPeerConnection({ iceServers })`.
  - `onicecandidate` → emit `voice:ice`.
  - `ontrack` → attach `e.streams[0]` to a hidden `<audio autoplay playsInline>` element keyed by peer id (keep refs in a Map; render elements in `VoicePanel` or create detached `Audio` objects).
  - `onconnectionstatechange` → on `failed`/`closed`, clean up; on `failed` try `pc.restartIce()` once.
- Speaking indicator: for each remote stream and local stream, an `AnalyserNode` (shared `AudioContext`) polled ~10×/s; `speaking = rms > threshold`. Keep it light.
- `toggleMute()` → `track.enabled = !track.enabled`, emit `voice:mute`.
- `leave()` and unmount cleanup: close all PCs, stop local tracks, remove audio elements, close AudioContext, emit `voice:leave`, remove all socket listeners registered by the hook.
- Expose `setLocalMicSuppressed(bool)` used by Lumi TTS (6.4) — disables the local track temporarily **without** changing the user's own mute state.

### 6.3 `useLumiAsk(socket, roomId, { canUseLumi, setLocalMicSuppressed })`
State: `recording`, `thinking` (+ who asked), `messages` (transcript strip), `error`.

- Recording: separate `getUserMedia` stream is NOT needed — reuse the voice room's local stream via a cloned track (`stream.clone()`), so recording works even if the user is muted to the group? → **Decision: clone the track and force `enabled = true` on the clone** so a muted user can still ask Lumi privately-to-AI (the question audio is not sent to peers, only the reply is broadcast).
- `startAsk()` on pointerdown/touchstart (and keydown of Space when focus is the button):
  - `MediaRecorder` with `mimeType` `audio/webm;codecs=opus` if supported, else let browser choose; record chunks.
  - Hard cap: auto-stop after **20 s**.
- `stopAsk()` on pointerup/pointerleave/touchend/keyup:
  - If duration < **400 ms** → discard, show "Hold to talk".
  - Build Blob → `arrayBuffer()` → `socket.emit('lumi:ask', { roomId, audio, mimeType }, ack)`; show ack errors.
- `lumi:thinking` → show "Lumi is thinking… (asked by X)".
- `lumi:reply` → append to `messages` (show `askedBy.name: transcript` then `Lumi: reply`), clear thinking, call `speak(reply, lang)`.
- `lumi:error` → show error toast/inline.

### 6.4 TTS `speak(text, lang)`
```js
const LANG_MAP = { fr: 'fr-FR', en: 'en-US', ar: 'ar-SA' };
function pickVoice(bcp47) {
  const voices = speechSynthesis.getVoices();
  return voices.find(v => v.lang === bcp47) || voices.find(v => v.lang.startsWith(bcp47.slice(0, 2))) || null;
}
```
- Load voices: `getVoices()` may be empty initially — listen for `speechSynthesis.onvoiceschanged`.
- Cancel any ongoing utterance first (`speechSynthesis.cancel()`).
- Set `utterance.lang`, `utterance.voice` (if found), `rate = 1`.
- **Echo control:** on `onstart` → `setLocalMicSuppressed(true)`; on `onend`/`onerror` → `setLocalMicSuppressed(false)`. Also add a safety timeout (text length × 90 ms + 3 s) to un-suppress.
- If no voice for the language exists → still show text, and show a small note "No {lang} voice on this device, reading as text".
- Per-user setting "Read Lumi aloud" (toggle, default on, stored in `localStorage` with try/catch).

### 6.5 `VoicePanel.jsx` UI
- Collapsed state: button **"Join voice"** (mic icon).
- Joined state:
  - Participant row: avatar/initials + name, mute icon, green ring when speaking. Include Lumi as a pseudo-participant with a distinct style, glowing when thinking/speaking.
  - Controls: Mute/Unmute, **Hold to ask Lumi** (large, accessible: `aria-pressed`, keyboard Space support, visible "Recording…" state with timer), "Read aloud" toggle, Leave.
  - If `!canUseLumi`: hide the Lumi button, show small "Lumi in voice is a premium feature" text.
  - Transcript strip: scrollable list of last ~20 Lumi exchanges, auto-scroll to bottom; RTL direction when `lang === 'ar'` (`dir="auto"` on each message).
- Match the app's existing styling system/components. Must work at phone width.

### 6.6 Client config
- Read `ICE_SERVERS` from existing config mechanism; fallback to Google STUN.
- Note in README: `getUserMedia` requires **HTTPS or localhost**. For LAN demos use an HTTPS tunnel or serve with a dev cert.

---

## 7. Security & robustness checklist
- [ ] `voice:join` and `lumi:ask` require an authenticated user who is a member of the room (professor-assigned or joined via room code).
- [ ] Signaling relays only between sockets in the same voice room.
- [ ] Audio size cap, rate limit, per-room lock, 30 s engine timeout.
- [ ] Free-tier enforcement on the **server**, not just hidden in UI.
- [ ] Temp audio files deleted in `finally`; audio never persisted.
- [ ] All socket listeners removed on unmount (no duplicate handlers after navigation).
- [ ] Disconnects clean up `voiceRooms` and notify peers.

---

## 8. Manual test plan
1. Two browsers (normal + incognito, different users) join the same group → both appear in participant list; audio flows both ways (use headphones).
2. Third user joins → hears both; first two hear them. Leave/rejoin works; refresh tab cleans up the ghost participant.
3. Mute → others see mute icon and stop hearing.
4. Hold "Ask Lumi", ask in **English** a question about uploaded course material → all clients see transcript + reply, reply is spoken.
5. Repeat in **French** and **Arabic**; try a **Darija** question → Arabic reply.
6. Write something on the whiteboard, ask "what do you think of what we wrote?" → reply references whiteboard content.
7. Second user presses Lumi while the first answer is in flight → gets "Lumi is answering" message.
8. Tap (< 400 ms) → discarded with hint. Hold > 20 s → auto-stops and sends.
9. Free-tier user: no Lumi button; forged `lumi:ask` via console is rejected.
10. Kill the Python engine → asker gets a clean error, room lock released, next ask works after restart.
11. Deny mic permission → friendly error, rest of the room page still works.

---

## 9. Phase B (only if time remains — otherwise list in the report as roadmap)
- **Piper TTS** in the Python engine: synthesize the reply once (FR/EN/AR voices), return an audio URL, clients play it instead of `speechSynthesis` → consistent voice quality across devices.
- **TURN server** (coturn) for users behind strict NATs / university networks.
- **SFU** (e.g., mediasoup/LiveKit) for groups > 6.
- **Proactive Lumi:** opt-in continuous transcription with VAD; Lumi chimes in when the group is stuck (long silence after a question, repeated wrong answer).
- Persist Lumi voice exchanges to SQLite and surface them in the progress map / professor dashboard (aggregated, privacy-respecting).
- Streaming replies (sentence-by-sentence TTS) to cut perceived latency.

---

## 10. Definition of done (Phase A)
- Group voice works for 2–6 members in one room, with join/leave/mute and speaking indicators.
- Push-to-talk Lumi: transcript + FelCloud reply broadcast to the room, shown as text and spoken in FR/EN/AR.
- Free tier blocked from Lumi voice (server-side).
- README section: new deps (`faster-whisper`, `ffmpeg`), env vars, HTTPS note, how to run, test steps.
