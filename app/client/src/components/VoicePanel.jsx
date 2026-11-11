// The room's voice channel, floating over the board: join / mute / leave, who is in the call (a ring while someone
// speaks), "Hold to ask Lumi" and the strip of Lumi's answers (read aloud on every member's device).
import { useEffect, useRef, useState } from "react";
import { socket } from "../socket";
import Icon from "./Icons";
import { LumiSpark } from "./LumiSpark";
import { useVoiceRoom } from "../lib/voice/useVoiceRoom";
import { useLumiAsk } from "../lib/voice/useLumiAsk";

const initials = (name = "?") => name.trim().split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() || "").join("") || "?";
const seconds = (ms) => `0:${String(Math.min(99, Math.floor(ms / 1000))).padStart(2, "0")}`;

function Person({ name, color, muted, speaking, state, you }) {
  const trouble = state === "failed" ? "can't connect" : state === "connecting" || state === "new" ? "connecting…" : null;
  return (
    <li className={"vc-person" + (speaking && !muted ? " speaking" : "") + (trouble ? " pending" : "")}>
      <span className="avatar vc-avatar" style={{ "--c": color }} aria-hidden="true">{initials(name)}</span>
      <span className="vc-name">{you ? "You" : name}</span>
      {muted && <span className="vc-muted" title="Muted"><Icon name="micOff" size={13} /><span className="sr-only">muted</span></span>}
      {!you && trouble && <span className="vc-state">{trouble}</span>}
    </li>
  );
}

export default function VoicePanel({ room, me }) {
  const voice = useVoiceRoom(socket, room);
  const lumi = useLumiAsk(socket, room, {
    joined: voice.joined,
    canUseLumi: voice.welcome.lumi,
    getLocalStream: voice.getLocalStream,
    setLocalMicSuppressed: voice.setLocalMicSuppressed,
    initialHistory: voice.welcome.history
  });
  const [open, setOpen] = useState(true);
  const stripRef = useRef(null);

  // Arrived from "Join voice" on the dashboard: join this room's call once, straight away.
  useEffect(() => {
    let wanted = null;
    try {
      wanted = window.sessionStorage.getItem("lumi-voice-autojoin");
      if (wanted) window.sessionStorage.removeItem("lumi-voice-autojoin");
    } catch {
      wanted = null;
    }
    if (wanted && wanted.toLowerCase() === String(room).toLowerCase()) voice.join();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room]);

  useEffect(() => {
    const el = stripRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lumi.messages.length, lumi.thinking, open]);

  if (!voice.joined) {
    return (
      <div className="vc vc-idle">
        <button className="vc-join glass" onClick={voice.join} disabled={voice.joining}>
          <Icon name="mic" size={16} /> {voice.joining ? "Connecting…" : "Join voice"}
        </button>
        {voice.error && (
          <p className="vc-error glass" role="alert">
            {voice.error}
            <button className="icon-chip" onClick={() => voice.setError(null)} aria-label="Dismiss"><Icon name="close" size={14} /></button>
          </p>
        )}
      </div>
    );
  }

  const lumiBusy = Boolean(lumi.thinking) || lumi.sending;
  const lumiState = lumi.thinking ? "thinking" : lumi.speaking ? "speaking" : null;
  const askLabel = lumi.recording ? `Recording… ${seconds(lumi.elapsed)}` : lumi.sending ? "Sending…" : lumiBusy ? "Lumi is answering…" : "Hold to ask Lumi";
  const holdKey = (e) => e.key === " " || e.key === "Enter";

  const askButton = voice.welcome.lumi ? (
    <button
      className={"vc-ask" + (lumi.recording ? " recording" : "")}
      aria-pressed={lumi.recording}
      aria-label={lumi.recording ? `Recording, ${Math.floor(lumi.elapsed / 1000)} seconds. Release to send.` : "Hold to ask Lumi (or hold Space)"}
      disabled={lumiBusy && !lumi.recording}
      onPointerDown={(e) => {
        if (e.button !== 0) return;
        e.currentTarget.setPointerCapture?.(e.pointerId);
        lumi.startAsk();
      }}
      onPointerUp={lumi.stopAsk}
      onPointerCancel={lumi.stopAsk}
      onLostPointerCapture={lumi.stopAsk}
      onKeyDown={(e) => {
        if (!holdKey(e)) return;
        e.preventDefault();
        if (!e.repeat) lumi.startAsk();
      }}
      onKeyUp={(e) => {
        if (!holdKey(e)) return;
        e.preventDefault();
        lumi.stopAsk();
      }}
      onBlur={() => lumi.recording && lumi.stopAsk()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <LumiSpark size={18} state={lumi.recording || lumiBusy ? "thinking" : "rest"} />
      <span>{askLabel}</span>
      {lumi.recording && <i className="vc-progress" style={{ "--p": Math.min(1, lumi.elapsed / lumi.maxMs) }} aria-hidden="true" />}
    </button>
  ) : (
    <p className="vc-premium">Lumi in voice is a premium feature.</p>
  );

  if (!open) {
    return (
      <div className="vc vc-mini glass" role="region" aria-label="Voice channel">
        <span className={"vc-dot" + (voice.selfSpeaking && !voice.muted ? " on" : "")} aria-hidden="true" />
        <span className="vc-count">{voice.peers.length + 1} in voice</span>
        {lumiState && <span className="vc-lumi-chip"><LumiSpark size={14} state="thinking" /> Lumi</span>}
        <button className={"icon-chip" + (voice.muted ? " active" : "")} onClick={voice.toggleMute} aria-pressed={voice.muted} aria-label={voice.muted ? "Unmute" : "Mute"}>
          <Icon name={voice.muted ? "micOff" : "mic"} size={16} />
        </button>
        <button className="icon-chip" onClick={() => setOpen(true)} aria-label="Open the voice panel" aria-expanded="false">
          <Icon name="chevronDown" size={16} />
        </button>
      </div>
    );
  }

  return (
    <section className="vc vc-open glass" aria-label="Voice channel">
      <header className="vc-head">
        <Icon name="mic" size={15} />
        <b>Voice</b>
        <span className="vc-count">{voice.peers.length + 1} of 6</span>
        <button className="icon-chip" onClick={() => setOpen(false)} aria-label="Minimise the voice panel" aria-expanded="true">
          <Icon name="minus" size={16} />
        </button>
      </header>

      <ul className="vc-people" aria-label="In the call">
        <Person you name={me?.name} color={me?.color} muted={voice.muted} speaking={voice.selfSpeaking} />
        {voice.peers.map((p) => <Person key={p.socketId} {...p} />)}
        <li className={"vc-person vc-lumi" + (lumiState ? ` ${lumiState}` : "")}>
          <span className="vc-avatar vc-lumi-avatar" aria-hidden="true"><LumiSpark size={20} state={lumiState ? "thinking" : "rest"} /></span>
          <span className="vc-name">Lumi</span>
        </li>
      </ul>

      <div className="vc-strip" ref={stripRef} aria-live="polite">
        {!lumi.messages.length && !lumi.thinking && (
          <p className="vc-empty">{voice.welcome.lumi ? "Hold the button and ask Lumi a question out loud. Everyone here hears the answer." : "Talk with your group. Lumi's answers would appear here."}</p>
        )}
        {lumi.messages.map((m) => (
          <article key={m.id} className={"vc-turn" + (m.degraded ? " degraded" : "")}>
            <p className="vc-q" dir="auto"><b style={{ "--c": m.askedBy?.color }}>{m.askedBy?.name}</b> {m.transcript}</p>
            <p className="vc-a" dir="auto" lang={m.lang}><b>Lumi</b> {m.reply}</p>
          </article>
        ))}
        {lumi.thinking && (
          <article className="vc-turn pending">
            {lumi.thinking.transcript && <p className="vc-q" dir="auto"><b style={{ "--c": lumi.thinking.askedBy?.color }}>{lumi.thinking.askedBy?.name}</b> {lumi.thinking.transcript}</p>}
            <p className="vc-a vc-thinking"><LumiSpark size={14} state="thinking" /> {lumi.thinking.stage === "listening" ? `Listening to ${lumi.thinking.askedBy?.name}…` : "Lumi is thinking…"}</p>
          </article>
        )}
      </div>

      {(lumi.error || lumi.note || voice.error) && (
        <p className={"vc-message" + (lumi.error || voice.error ? " bad" : "")} role={lumi.error || voice.error ? "alert" : "status"}>
          {lumi.error || voice.error || lumi.note}
          <button className="icon-chip" onClick={() => { lumi.clearError(); lumi.clearNote(); voice.setError(null); }} aria-label="Dismiss"><Icon name="close" size={13} /></button>
        </p>
      )}

      {askButton}

      <div className="vc-controls">
        <button className={"vc-btn" + (voice.muted ? " on" : "")} onClick={voice.toggleMute} aria-pressed={voice.muted}>
          <Icon name={voice.muted ? "micOff" : "mic"} size={15} /> {voice.muted ? "Unmute" : "Mute"}
        </button>
        {lumi.canRead && voice.welcome.lumi && (
          <label className="vc-toggle">
            <input type="checkbox" checked={lumi.readAloud} onChange={(e) => lumi.setReadAloud(e.target.checked)} /> Read aloud
          </label>
        )}
        <button className="vc-btn leave" onClick={voice.leave}><Icon name="exit" size={15} /> Leave</button>
      </div>
    </section>
  );
}
