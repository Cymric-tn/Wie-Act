import { memo, useState } from "react";
import Icon from "./Icons";
import { TimerBadge, formatClock, timerRemaining } from "./TopBar";
import { initials, formatTime } from "../lib/format";
import { REACTIONS, BACKGROUNDS, TIMER_PRESETS } from "../lib/theme";
import { TEMPLATES } from "../lib/templates";

const TABS = [
  { id: "people", label: "People", icon: "users" },
  { id: "room", label: "Room", icon: "layout" },
  { id: "history", label: "History", icon: "history" },
  { id: "ai", label: "AI", icon: "sparkle" },
  { id: "help", label: "Help", icon: "help" }
];

function Toggle({ on, onChange, label, hint, icon }) {
  return (
    <button className={"toggle-row" + (on ? " on" : "")} onClick={() => onChange(!on)} role="switch" aria-checked={on}>
      <Icon name={icon} size={18} />
      <span>
        <b>{label}</b>
        <small>{hint}</small>
      </span>
      <i className="switch" />
    </button>
  );
}

function AccessCode({ hasCode, onSetCode }) {
  const [code, setCode] = useState("");
  return (
    <form
      className="code-row"
      onSubmit={(e) => {
        e.preventDefault();
        if (code.trim()) onSetCode(code.trim());
        setCode("");
      }}
    >
      <Icon name="key" size={17} />
      <input className="input"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        placeholder={hasCode ? "Change access code" : "Set an access code"}
        maxLength={64}
        aria-label="Access code"
      />
      <button className="chip" type="submit" disabled={!code.trim()}>Set</button>
      {hasCode && <button className="chip danger" type="button" onClick={() => onSetCode(null)}>Remove</button>}
    </form>
  );
}

function PeopleTab(p) {
  const { me, users, meta, isHost } = p;
  return (
    <>
      {isHost && (
        <div className="card panel-card">
          <div className="eyebrow">TEACHER CONTROLS</div>
          <div className="toggles">
            <Toggle icon={meta.locked ? "lock" : "unlock"} on={meta.locked} onChange={p.onLock} label="Lock board" hint="Only you can draw. Others can still vote and react." />
            <Toggle icon="present" on={meta.presenting} onChange={p.onPresent} label="Present" hint="Everyone follows your page and view." />
          </div>
          <AccessCode hasCode={meta.hasCode} onSetCode={p.onSetCode} />
          <p className="muted">{meta.hasCode ? "Private room: new people need the access code." : "Anyone with the room name can join."}</p>
        </div>
      )}

      <div className="card panel-card">
        <div className="eyebrow">IN THIS ROOM · {users.length}</div>
        <ul className="people">
          {users.map((u) => {
            const host = u.clientId === meta.host;
            const spot = u.clientId === meta.spotlight;
            return (
              <li key={u.id} className={spot ? "spotlit" : ""}>
                <span className="avatar" style={{ "--c": u.color }}>{initials(u.name)}</span>
                <span className="person-name">
                  {u.name}
                  {u.id === me.id && <small> · you</small>}
                  {host && <em className="role">HOST</em>}
                </span>
                {isHost && u.id !== me.id ? (
                  <span className="person-actions">
                    <button className={"icon-chip" + (spot ? " active" : "")} onClick={() => p.onSpotlight(u.clientId)} aria-label={spot ? "Remove spotlight" : "Spotlight"} title={spot ? "Remove spotlight" : "Spotlight"}>
                      <Icon name="spotlight" size={15} filled={spot} />
                    </button>
                    <button className="icon-chip" onClick={() => p.onTransfer(u.clientId)} aria-label="Make host" title="Make host">
                      <Icon name="crown" size={15} />
                    </button>
                  </span>
                ) : (
                  spot ? <Icon name="spotlight" size={15} filled /> : <i className="dot" style={{ background: u.color, color: u.color }} />
                )}
              </li>
            );
          })}
        </ul>
        <button className="btn btn-secondary small" onClick={p.onCopyInvite}>
          <Icon name="link" size={15} /> Copy invite link
        </button>
      </div>

      <div className="card panel-card">
        <div className="eyebrow">REACT</div>
        <div className="reaction-row">
          {REACTIONS.map((e) => (
            <button key={e} className="reaction-btn" onClick={() => p.onReact(e)} aria-label={`Send ${e}`}>{e}</button>
          ))}
        </div>
        <p className="muted">Reactions float up where your cursor last was.</p>
      </div>
    </>
  );
}

function PageRow({ page, index, active, count, canEdit, canDelete, onPage, onRename, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(page.name);
  if (editing) {
    return (
      <li className="page-row active">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) onRename(page.id, name.trim());
            setEditing(false);
          }}
        >
          <input className="input" autoFocus value={name} maxLength={40} onChange={(e) => setName(e.target.value)} onBlur={() => setEditing(false)} aria-label="Page name" />
        </form>
      </li>
    );
  }
  return (
    <li className={"page-row" + (active ? " active" : "")}>
      <button className="page-pick" onClick={() => onPage(page.id)} onDoubleClick={() => canEdit && (setName(page.name), setEditing(true))}>
        <span className="page-index">{index + 1}</span>
        <span className="page-name">{page.name}</span>
        <small>{count}</small>
      </button>
      {canEdit && (
        <button className="icon-chip" onClick={() => (setName(page.name), setEditing(true))} aria-label="Rename" title="Rename"><Icon name="edit" size={14} /></button>
      )}
      {canDelete && (
        <button className="icon-chip danger" onClick={() => onDelete(page.id)} aria-label="Delete page" title="Delete page"><Icon name="trash" size={14} /></button>
      )}
    </li>
  );
}

function TimerCard({ timer, offset, isHost, onTimer }) {
  const running = Boolean(timer.endsAt);
  return (
    <div className="card panel-card">
      <div className="eyebrow">CLASS TIMER</div>
      <div className="timer-readout">
        <TimerBadge timer={timer} offset={offset} compact />
        {!isHost && <small className="muted">Controlled by the host</small>}
      </div>
      {isHost && (
        <>
          <div className="preset-row">
            {TIMER_PRESETS.map((s) => (
              <button key={s} className={"filter" + (timer.duration === s ? " active" : "")} onClick={() => onTimer("set", s)}>
                {s >= 60 ? `${s / 60} min` : `${s}s`}
              </button>
            ))}
          </div>
          <div className="timer-actions">
            {running ? (
              <button className="btn btn-secondary small" onClick={() => onTimer("pause")}><Icon name="pause" size={15} /> Pause</button>
            ) : (
              <button className="btn btn-primary small" onClick={() => onTimer("start")} disabled={timerRemaining(timer, offset) <= 0}>
                <Icon name="play" size={15} /> Start {formatClock(timer.remaining)}
              </button>
            )}
            <button className="btn btn-secondary small" onClick={() => onTimer("reset")}>Reset</button>
          </div>
        </>
      )}
    </div>
  );
}

function RoomTab(p) {
  return (
    <>
      <div className="card panel-card">
        <div className="eyebrow">PAGES · {p.pages.length}</div>
        <ul className="pages">
          {p.pages.map((page, i) => (
            <PageRow
              key={page.id}
              page={page}
              index={i}
              active={page.id === p.pageId}
              count={p.counts[page.id] || 0}
              canEdit={p.canEdit}
              canDelete={p.isHost && p.pages.length > 1}
              onPage={p.onPage}
              onRename={p.onRenamePage}
              onDelete={p.onDeletePage}
            />
          ))}
        </ul>
        <div className="row-actions">
          <button className="btn btn-secondary small" onClick={p.onAddPage} disabled={!p.canEdit}><Icon name="plus" size={15} /> Add page</button>
          {p.isHost && <button className="btn btn-secondary small danger-outline" onClick={p.onClearPage}><Icon name="trash" size={15} /> Clear page</button>}
        </div>
      </div>

      <TimerCard timer={p.meta.timer} offset={p.timerOffset} isHost={p.isHost} onTimer={p.onTimer} />

      <div className="card panel-card">
        <div className="eyebrow">TEMPLATES</div>
        <div className="template-grid">
          {TEMPLATES.map((t) => (
            <button key={t.id} className={`template-btn tpl-${t.id}`} onClick={() => p.onTemplate(t.id)} disabled={!p.canEdit} aria-label={t.hint} title={t.hint}>
              <span className="tpl-thumb" />
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="card panel-card">
        <div className="eyebrow">BOARD STYLE</div>
        <div className="bg-options">
          {BACKGROUNDS.map((b) => (
            <button
              key={b.id}
              className={"bg-option bg-" + b.id + (p.background === b.id ? " active" : "")}
              onClick={() => p.onBackground(b.id)}
              aria-pressed={p.background === b.id}
            >
              <span className="bg-thumb" />
              {b.label}
            </button>
          ))}
        </div>
      </div>

      <div className="card panel-card">
        <div className="eyebrow">TOP IDEAS</div>
        {p.topNotes.length ? (
          <ol className="top-notes">
            {p.topNotes.map((n) => (
              <li key={n.id}>
                <button onClick={() => p.onJumpTo(n)}>
                  <span className="votes">▲ {n.votes?.length || 0}</span>
                  <span className="note-text" style={{ "--c": n.color }}>{n.text || "Untitled note"}</span>
                </button>
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">Add sticky notes and click ▲ to vote. The favourites show up here.</p>
        )}
      </div>
    </>
  );
}

function HistoryTab({ history, onLoad, onScrub, onPlay, onExit }) {
  if (history.state !== "ready") {
    return (
      <div className="card panel-card">
        <div className="eyebrow">BOARD HISTORY</div>
        <p className="panel-copy">Scrub through every change made in this room and watch the board being built, step by step.</p>
        <button className="btn btn-primary small" onClick={onLoad} disabled={history.state === "loading"}>
          <Icon name="history" size={15} /> {history.state === "loading" ? "Loading…" : "Open timeline"}
        </button>
      </div>
    );
  }
  const { count, index, entry } = history;
  return (
    <div className="card panel-card">
      <div className="eyebrow">BOARD HISTORY</div>
      {count === 0 ? (
        <p className="panel-copy">Nothing has been drawn in this room yet.</p>
      ) : (
        <>
          <div className="history-now">
            <b>{entry ? formatTime(entry.ts) : "Start"}</b>
            <span>{entry ? entry.label : "Before the first change"}</span>
          </div>
          <input className="input"
            type="range"
            min="0"
            max={count}
            value={index}
            onChange={(e) => onScrub(Number(e.target.value))}
            className="history-slider"
            aria-label="Timeline position"
          />
          <div className="history-meta"><span>{index} / {count} changes</span></div>
          <div className="timer-actions">
            <button className="btn btn-primary small" onClick={onPlay}>
              <Icon name={history.playing ? "pause" : "play"} size={15} /> {history.playing ? "Pause" : "Replay"}
            </button>
            <button className="icon-chip" onClick={() => onScrub(Math.max(0, index - 1))} aria-label="Step back" title="Step back"><Icon name="left" size={16} /></button>
            <button className="icon-chip" onClick={() => onScrub(Math.min(count, index + 1))} aria-label="Step forward" title="Step forward"><Icon name="right" size={16} /></button>
          </div>
        </>
      )}
      <button className="btn btn-secondary small" onClick={onExit}>Back to the live board</button>
    </div>
  );
}

const SHORTCUTS = [
  ["Tools", [["V", "Select"], ["H / Space", "Pan"], ["P", "Pen"], ["I", "Highlighter"], ["E", "Eraser"], ["X", "Laser"], ["L A R O", "Line, arrow, rect, ellipse"], ["T", "Text"], ["N", "Sticky note"], ["K", "Equation"]]],
  ["Editing", [["Ctrl/⌘ Z", "Undo"], ["⇧ Ctrl/⌘ Z", "Redo"], ["Ctrl/⌘ D", "Duplicate"], ["Ctrl/⌘ C / V", "Copy / paste"], ["Del", "Delete selection"], ["Arrows", "Nudge (⇧ = 10×)"], ["Shift", "Snap shapes & keep ratio"], ["[ ]", "Thinner / thicker"]]],
  ["View", [["+ / −", "Zoom"], ["Shift 1", "Zoom to fit"], ["Shift 0", "100%"], ["PgUp / PgDn", "Previous / next page"], ["Ctrl + scroll", "Zoom at cursor"]]]
];

function HelpTab() {
  return (
    <>
      {SHORTCUTS.map(([group, rows]) => (
        <div className="card panel-card shortcuts" key={group}>
          <div className="eyebrow">{group.toUpperCase()}</div>
          <dl>
            {rows.map(([keys, what]) => (
              <div key={keys}><dt><kbd>{keys}</kbd></dt><dd>{what}</dd></div>
            ))}
          </dl>
        </div>
      ))}
      <div className="card panel-card">
        <div className="eyebrow">TIPS</div>
        <p className="panel-copy">Drop or paste images and PDFs onto the board — each PDF page becomes a board page you can annotate. Turn on Shape assist with the pen to straighten rough boxes, circles and lines.</p>
      </div>
    </>
  );
}

function SidePanel(props) {
  const { tab, setTab } = props;
  return (
    <aside className="side-panel" aria-label="Room details">
      <div className="panel-tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={"panel-tab" + (tab === t.id ? " active" : "")} onClick={() => setTab(t.id)}>
            <Icon name={t.icon} size={16} />
            <span>{t.label}</span>
          </button>
        ))}
      </div>
      <div className="panel-body">
        {tab === "people" && <PeopleTab {...props} />}
        {tab === "room" && <RoomTab {...props} />}
        {tab === "history" && (
          <HistoryTab history={props.history} onLoad={props.onLoadHistory} onScrub={props.onScrub} onPlay={props.onPlayHistory} onExit={props.onExitHistory} />
        )}
        {tab === "help" && <HelpTab />}
        {tab === "ai" && props.aiPanel}
      </div>
    </aside>
  );
}

export default memo(SidePanel);
