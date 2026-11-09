import { useEffect, useRef, useState } from "react";
import { COURSES, STATUS, courseForRoom } from "../lib/courses";
import { LumiLogo } from "./LumiSpark";

const SUGGESTED_ROOMS = ["classroom", "study-group", "physics-lab", "brainstorm"];

export default function Lobby({ status, initialName, initialRoom, error, onJoin }) {
  const [name, setName] = useState(initialName);
  const [room, setRoom] = useState(initialRoom);
  const [code, setCode] = useState("");
  const [showCode, setShowCode] = useState(false);
  const codeRef = useRef(null);
  const needsCode = error?.code === "code-required" || error?.code === "code-wrong";

  useEffect(() => {
    if (needsCode) {
      setShowCode(true);
      setTimeout(() => codeRef.current?.focus(), 0);
    }
  }, [needsCode, error]);

  function submit(event) {
    event.preventDefault();
    if (!name.trim() || !room.trim()) return;
    onJoin({ name: name.trim(), room: room.trim(), code: code.trim() });
  }

  const online = status === "Connected";
  const course = courseForRoom(room);

  return (
    <div className="lobby">
      <section className="lobby-card">
        <div className="brand lobby-brand"><LumiLogo height={30} /></div>
        <div className="eyebrow">COLLABORATIVE WHITEBOARD</div>
        <h1>Sketch ideas<br /><em>together, in real time</em></h1>
        <p className="lobby-copy">
          Draw, annotate PDFs, solve equations and vote on ideas with your class.
          Boards are saved, so you can come back to them anytime.
        </p>

        <form onSubmit={submit} className="lobby-form">
          <label>
            <span>Your name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={24} placeholder="e.g. Amira" autoFocus={!initialName} />
          </label>
          <label>
            <span>Room</span>
            <input className="input" value={room} onChange={(e) => setRoom(e.target.value)} maxLength={80} placeholder="Enter a room name" autoFocus={Boolean(initialName)} />
          </label>
          {course && (
            <div className="course-hint" style={{ "--c": STATUS[course.status].color }}>
              <i className="orb-mini" /> Study room for <b>{course.id} · {course.name}</b>
            </div>
          )}
          <div className="room-chips" role="group" aria-label="Suggested rooms">
            {SUGGESTED_ROOMS.map((r) => (
              <button type="button" key={r} className={"filter" + (room === r ? " active" : "")} onClick={() => setRoom(r)}>{r}</button>
            ))}
          </div>

          <details className="course-rooms">
            <summary>Course study rooms</summary>
            <div className="course-grid">
              {COURSES.map((c) => (
                <button
                  type="button"
                  key={c.id}
                  className={"course-btn" + (room === c.id ? " active" : "")}
                  style={{ "--c": STATUS[c.status].color }}
                  onClick={() => setRoom(c.id)}
                  aria-label={`${c.name} · ${STATUS[c.status].label}`} title={`${c.name} · ${STATUS[c.status].label}`}
                >
                  <i className="orb-mini" />
                  <b>{c.id}</b>
                  <small>{c.name}</small>
                </button>
              ))}
            </div>
          </details>

          {showCode ? (
            <label>
              <span>Access code {needsCode ? "" : "(optional — makes a new room private)"}</span>
              <input className="input" ref={codeRef} value={code} onChange={(e) => setCode(e.target.value)} maxLength={64} placeholder="Room access code" type="password" autoComplete="off" />
            </label>
          ) : (
            <button type="button" className="link-btn" onClick={() => setShowCode(true)}>
              Private room? Add an access code
            </button>
          )}

          {error && <p className="form-error" role="alert">{error.message}</p>}

          <button className="btn btn-primary" type="submit" disabled={!online || !name.trim() || !room.trim()}>
            Enter the board
          </button>
        </form>

        <div className={"status-line" + (online ? " online" : "")}>
          <i className="dot" /> {online ? "Server connected" : status}
        </div>
      </section>
    </div>
  );
}
