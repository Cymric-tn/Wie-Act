import { memo, useEffect, useRef, useState } from "react";
import Icon from "./Icons";
import { initials } from "../lib/format";
import { courseColor } from "../lib/courses";
import { LumiLogo } from "./LumiSpark";

export function timerRemaining(timer, offset) {
  if (!timer) return 0;
  if (!timer.endsAt) return timer.remaining;
  return Math.max(0, (timer.endsAt - (Date.now() + offset)) / 1000);
}

export const formatClock = (seconds) => {
  const s = Math.ceil(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

/** Live countdown chip; hidden while the timer is idle. */
export function TimerBadge({ timer, offset, onFinish, compact = false }) {
  const [, tick] = useState(0);
  const finishedRef = useRef(false);
  const remaining = timerRemaining(timer, offset);
  const running = Boolean(timer?.endsAt);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => tick((n) => n + 1), 250);
    return () => clearInterval(id);
  }, [running]);

  useEffect(() => {
    if (running && remaining <= 0 && !finishedRef.current) {
      finishedRef.current = true;
      onFinish?.();
    }
    if (remaining > 0) finishedRef.current = false;
  });

  if (!timer || (!running && timer.remaining >= timer.duration && !compact)) return null;
  const done = running && remaining <= 0;
  const pct = timer.duration ? Math.max(0, Math.min(1, remaining / timer.duration)) : 0;
  return (
    <span
      className={"timer-badge" + (done ? " done" : running ? " running" : " paused")}
      style={{ "--pct": pct }}
      role="timer"
      aria-label={`Timer ${formatClock(remaining)}${running ? "" : ", paused"}`}
    >
      <Icon name="timer" size={16} />
      <b>{done ? "Time’s up" : formatClock(remaining)}</b>
      {!running && !done && <small>{timer.remaining < timer.duration ? "paused" : "ready"}</small>}
    </span>
  );
}

function ExportMenu({ onExport }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = (e) => !e.target.closest?.(".export-menu") && setOpen(false);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);
  const pick = (kind) => {
    setOpen(false);
    onExport(kind);
  };
  return (
    <div className="export-menu">
      <button className="iconbtn" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}>
        <Icon name="download" size={17} /> <span>Export</span>
      </button>
      {open && (
        <div className="menu" role="menu">
          <button role="menuitem" onClick={() => pick("png")}><Icon name="image" size={16} /> PNG · this page</button>
          <button role="menuitem" onClick={() => pick("svg")}><Icon name="layout" size={16} /> SVG · this page</button>
          <button role="menuitem" onClick={() => pick("pdf")}><Icon name="page" size={16} /> PDF · all pages</button>
          <button role="menuitem" onClick={() => pick("copy")}><Icon name="copy" size={16} /> Copy image</button>
        </div>
      )}
    </div>
  );
}

function TopBar(props) {
  const {
    room, course, hasCode, status, users, me, hostId, timer, timerOffset, onTimerFinish,
    presenting, isHost, following, onRejoin, onExport, panelOpen, onTogglePanel, onLeave, onCopyInvite
  } = props;
  const online = status === "Connected";
  return (
    <header className="top glass">
      <div className="top-left">
        <div className="brand"><LumiLogo height={26} /></div>
        <button className="room-pill" onClick={onCopyInvite} aria-label="Copy invite link" title="Copy invite link">
          <span className="eyebrow">ROOM</span>
          <b>{room}</b>
          {hasCode && <Icon name="lock" size={14} />}
          <Icon name="link" size={15} />
        </button>
        {course && (
          <span className="course-chip" style={{ "--c": courseColor(course) }} title={`Study room for ${course.id}`}>
            <i className="orb-mini" /> {course.name}
          </span>
        )}
      </div>

      <div className="top-center">
        <TimerBadge timer={timer} offset={timerOffset} onFinish={onTimerFinish} />
        {presenting && isHost && <span className="live-chip"><Icon name="present" size={15} /> Presenting</span>}
        {presenting && !isHost && (
          following
            ? <span className="live-chip"><Icon name="present" size={15} /> Following host</span>
            : <button className="live-chip action" onClick={onRejoin}><Icon name="present" size={15} /> Rejoin presenter</button>
        )}
      </div>

      <div className="topright">
        <span className={"status" + (online ? " online" : "")}><i className="dot" />{status}</span>
        <div className="avatars" aria-label={`${users.length} people in this room`}>
          {users.slice(0, 5).map((u) => (
            <span
              key={u.id}
              className={"avatar" + (u.clientId === hostId ? " host" : "")}
              style={{ "--c": u.color }}
              title={`${u.name}${u.id === me.id ? " (you)" : ""}${u.clientId === hostId ? " · host" : ""}`}
            >
              {initials(u.name)}
            </span>
          ))}
          {users.length > 5 && <span className="avatar more">+{users.length - 5}</span>}
        </div>
        <ExportMenu onExport={onExport} />
        <button className="iconbtn panel-toggle" onClick={onTogglePanel} aria-pressed={panelOpen} aria-label="Room panel" title="Room panel">
          <Icon name="users" size={17} />
        </button>
        <button className="iconbtn" onClick={onLeave}><Icon name="exit" size={17} /> <span>Leave</span></button>
      </div>
    </header>
  );
}

export default memo(TopBar);
