import { useState } from "react";
import Icon from "../Icons";
import { useI18n } from "../../lib/i18n";
import { navigate } from "../../lib/router";
import { COURSE_COLOR, COURSE_ID, ROOMS, MATERIALS, PEOPLE, CLASS, MASTERY, courseMastery, masteryLevel, weakTopics } from "../../lib/demoData";
import { daysLeft, listExams } from "../../lib/exams";
import { useCourses } from "../../lib/courseMap";
import { initials } from "../../lib/format";
import { store } from "../../lib/identity";
import { LumiSpark } from "../LumiSpark";

const courseById = (id) => (id === COURSE_ID ? { id: COURSE_ID, name: "Analyse", color: COURSE_COLOR } : null);
const pct = (v) => Math.round(v * 100);
export const openBoard = (room) => navigate(room ? `/board?room=${encodeURIComponent(room)}` : "/board");
/** Opens a room's board and joins its voice channel on arrival (VoicePanel reads the flag). The click that got here
 * counts as the user gesture the microphone and audio playback need: the app never reloads the page. */
export const openVoice = (room) => {
  try {
    window.sessionStorage.setItem("lumi-voice-autojoin", room);
  } catch {
    // storage blocked: the room opens and the learner presses "Join voice" themselves
  }
  openBoard(room);
};
/** Opens the board with its AI tab showing. */
export const openBoardAi = () => {
  store("ednova-board-tab", "ai");
  openBoard();
};

export function Card({ icon, title, sub, actions, example, className = "", children, accent }) {
  const { t } = useI18n();
  return (
    <section className={`card d-card glass ${className}`} style={accent ? { "--accent": accent } : undefined}>
      {(title || actions) && (
        <header className="d-card-head">
          {icon && <span className="d-card-icon"><Icon name={icon} size={18} /></span>}
          <div className="d-card-titles">
            <h2>{title}</h2>
            {sub && <p>{sub}</p>}
          </div>
          <div className="d-card-actions">
            {example && <span className="d-example">{t("common.example")}</span>}
            {actions}
          </div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Avatars({ ids, max = 4 }) {
  return (
    <span className="d-avatars">
      {ids.slice(0, max).map((id) => (
        <span key={id} className="d-avatar" style={{ "--c": PEOPLE[id].color }} title={PEOPLE[id].name}>{initials(PEOPLE[id].name)}</span>
      ))}
      {ids.length > max && <span className="d-avatar more">+{ids.length - max}</span>}
    </span>
  );
}

export function Meter({ value }) {
  return (
    <span className="progress" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct(value)}>
      <span className={masteryLevel(value) === "mastered" ? "mastered" : undefined} style={{ width: `${Math.max(3, pct(value))}%` }} />
    </span>
  );
}

// ---------- group rooms ----------
const ROOM_CODE_MAX = 80; // the server's limit (LIMITS.roomLength); a code has no fixed length

/**
 * Join by code. Codes are case-sensitive names on the server, so capitals are display only (CSS): what was typed is what
 * is sent, except that a room this list knows is matched whatever the case.
 */
export function JoinRoom() {
  const { t } = useI18n();
  const [code, setCode] = useState("");
  const [invalid, setInvalid] = useState(false);
  const value = code.trim();
  return (
    <form
      className="d-join"
      onSubmit={(e) => {
        e.preventDefault();
        if (!value) return;
        if (value.length > ROOM_CODE_MAX) return setInvalid(true);
        openBoard(ROOMS.find((r) => r.code.toLowerCase() === value.toLowerCase())?.code || value);
      }}
    >
      <div className="d-join-field">
        <Icon name="key" size={16} />
        <input
          value={code}
          onChange={(e) => { setCode(e.target.value); setInvalid(false); }}
          placeholder={t("rooms.enterCode")}
          maxLength={ROOM_CODE_MAX}
          aria-label={t("rooms.enterCode")}
          aria-invalid={invalid}
          aria-describedby={invalid ? "d-join-error" : undefined}
          autoCapitalize="characters"
          autoComplete="off"
          spellCheck={false}
        />
        <button className={"d-join-btn" + (value ? " ready" : "")} type="submit" disabled={!value}>{t("rooms.join")}</button>
      </div>
      {invalid && <p className="d-join-error" id="d-join-error" role="alert"><Icon name="close" size={14} /> {t("rooms.codeInvalid")}</p>}
    </form>
  );
}

export function RoomList() {
  const { t } = useI18n();
  return (
    <ul className="d-rooms">
      {ROOMS.map((room) => {
        const course = courseById(room.course);
        return (
          <li key={room.code}>
            <span className={"d-live" + (room.live ? " on" : "")} aria-label={room.live ? t("badge.live") : ""} />
            <div className="d-room-main">
              <b>{room.name}</b>
              <small>
                <span className="d-course-dot" style={{ "--c": course?.color }} />
                {room.assignedBy ? <em className="d-assigned">{t("rooms.assigned", { prof: room.assignedBy })}</em> : t("rooms.adhoc")}
              </small>
            </div>
            <div className="d-room-side">
              {room.online.length > 0 ? <Avatars ids={room.online} max={3} /> : null}
              <small>{t("rooms.online", { n: room.online.length })}</small>
            </div>
            <button className="btn btn-secondary small d-voice-btn" onClick={() => openVoice(room.code)} aria-label={`${t("voice.join")}: ${room.name}`}><Icon name="mic" size={15} /> <span>{t("voice.short")}</span></button>
            <button className="btn btn-secondary small" onClick={() => openBoard(room.code)}>{t("common.open")}</button>
          </li>
        );
      })}
    </ul>
  );
}

export function RoomsCard({ onSoon }) {
  const { t } = useI18n();
  return (
    <Card
      icon="users"
      title={t("rooms.title")}
      sub={t("rooms.sub")}
      example
      className="span-5"
      actions={<button className="btn btn-secondary small" onClick={() => openBoard(`room-${Math.random().toString(36).slice(2, 7)}`)}><Icon name="plus" size={15} /> {t("rooms.new")}</button>}
    >
      <JoinRoom />
      <RoomList onSoon={onSoon} />
    </Card>
  );
}

// ---------- whiteboard ----------
export function BoardPreview() {
  return (
    <div className="d-board-preview" aria-hidden="true">
      <svg viewBox="0 0 320 150">
        <path d="M24 110 C60 40, 100 40, 130 90 S 190 140, 230 60" fill="none" stroke="var(--lumi-iris)" strokeWidth="3.5" strokeLinecap="round" />
        <rect x="180" y="18" width="96" height="58" rx="9" fill="var(--lumi-iris-soft)" />
        <rect x="190" y="30" width="60" height="6" rx="3" fill="var(--lumi-line)" />
        <rect x="190" y="42" width="44" height="6" rx="3" fill="var(--lumi-line)" />
        <ellipse cx="88" cy="118" rx="44" ry="16" fill="none" stroke="var(--lumi-slate)" strokeWidth="2.5" />
        <text x="40" y="38" fill="var(--lumi-ink)" fontSize="17" fontFamily="serif" fontStyle="italic">∫ u dv = uv − ∫ v du</text>
        <g transform="translate(248 100)"><path d="M0 0l9 22 3.5-9 9-3.5z" fill="var(--lumi-iris)" stroke="var(--lumi-void)" /><rect x="12" y="20" width="44" height="15" rx="5" fill="#a279ff" /><text x="17" y="31" fontSize="9" fill="#050b1a" fontWeight="700">Youssef</text></g>
      </svg>
    </div>
  );
}

export function AiBoardChips() {
  const { t } = useI18n();
  return (
    <div className="d-ai-chips">
      <span className="d-ai-label"><Icon name="sparkle" size={14} /> {t("board.ai")}</span>
      <button className="chip d-chip" onClick={openBoardAi}><Icon name="shield" size={14} /> {t("board.fact")}</button>
      <button className="chip d-chip" onClick={openBoardAi}><Icon name="help" size={14} /> {t("board.ask")}</button>
    </div>
  );
}

export function BoardCard({ onSoon }) {
  const { t } = useI18n();
  return (
    <Card icon="board" title={t("board.title")} sub={t("board.sub")} className="span-7" actions={<span className="d-badge live">{t("badge.live")}</span>}>
      <BoardPreview />
      <AiBoardChips onSoon={onSoon} />
      <button className="btn btn-primary full" onClick={() => openBoard()}>
        <Icon name="board" size={17} /> {t("board.open")}
      </button>
    </Card>
  );
}

// ---------- voice ----------
export function VoicePreview() {
  const { t } = useI18n();
  return (
    <div className="d-voice">
      <div className="d-voice-orb" aria-hidden="true">
        <Icon name="sparkle" size={26} />
        <span className="ring r1" />
        <span className="ring r2" />
      </div>
      <div className="d-wave" aria-hidden="true">
        {Array.from({ length: 18 }, (_, i) => <i key={i} style={{ animationDelay: `${(i % 6) * -0.18}s` }} />)}
      </div>
      <small>{t("voice.peer")}</small>
    </div>
  );
}

// ---------- assessment prep ----------
export function ExamList({ onOpen }) {
  const { t } = useI18n();
  const courses = useCourses();
  const exams = listExams();
  const readiness = courseMastery(courses[0]);
  if (!exams.length) {
    return (
      <p className="d-exam-empty">
        {t("prep.noExam")} <button className="chip d-chip" onClick={onOpen}><Icon name="plus" size={13} /> {t("prep.newExam")}</button>
      </p>
    );
  }
  return (
    <ul className="d-exams">
      {exams.map((exam) => {
        const left = Math.max(0, daysLeft(exam.date));
        return (
          <li key={exam.id} style={{ "--c": COURSE_COLOR }}>
            <div className="d-exam-date">
              <b>{left === 1 ? t("prep.tomorrow") : t("prep.inDays", { n: left })}</b>
              <small>{exam.date}</small>
            </div>
            <div className="d-exam-main">
              <b>{exam.title}</b>
              <span className="d-exam-ready">
                <small>{t("prep.readiness")}</small>
                <Meter value={readiness} />
                <small>{pct(readiness)}%</small>
              </span>
            </div>
            <button className="btn btn-secondary small" onClick={onOpen}>{t("prep.practice")}</button>
          </li>
        );
      })}
    </ul>
  );
}

export function WeakTopics({ onSoon, limit = 4 }) {
  const { t } = useI18n();
  const courses = useCourses();
  return (
    <div className="d-weak">
      <h3>{t("prep.weak")}</h3>
      <div className="d-weak-chips">
        {weakTopics(courses, limit).map((l) => (
          <button key={l.course.id + l.name} className="chip d-chip weak" onClick={onSoon} style={{ "--c": l.course.color }}>
            <i />{l.name}<small>{pct(l.mastery)}%</small>
          </button>
        ))}
      </div>
    </div>
  );
}

export function PrepCard() {
  const { t } = useI18n();
  const toPrep = () => navigate("/app/prep");
  return (
    <Card icon="exam" title={t("prep.title")} className="span-4 prep-card">
      <ExamList onOpen={toPrep} />
      <WeakTopics onSoon={toPrep} limit={3} />
      <button className="btn btn-ai full" onClick={toPrep}><Icon name="sparkle" size={16} /> {t("prep.generate")}</button>
    </Card>
  );
}

// ---------- materials ----------
export function MaterialList({ items = MATERIALS }) {
  const { t } = useI18n();
  return (
    <ul className="d-materials">
      {items.map((m) => {
        const course = courseById(m.course);
        return (
          <li key={m.title}>
            <span className="d-file" style={{ "--c": course.color }}>{m.type}</span>
            <div className="d-mat-main">
              <b>{m.title}</b>
              <small>{course.id} · {m.by}</small>
            </div>
            {m.official
              ? <span className="d-badge official"><Icon name="shield" size={13} /> {t("materials.official")}</span>
              : <span className="d-badge supp">{t("materials.supplementary")}</span>}
          </li>
        );
      })}
    </ul>
  );
}

export function MaterialsCard() {
  const { t } = useI18n();
  return (
    <Card
      icon="book"
      title={t("materials.title")}
      sub={t("materials.sub")}
      example
      className="span-8"
      actions={<button className="btn btn-secondary small" onClick={() => navigate("/app/materials")}><Icon name="upload" size={15} /> {t("materials.upload")}</button>}
    >
      <MaterialList items={MATERIALS.slice(0, 4)} />
    </Card>
  );
}

// ---------- AI companion promo ----------
export function CompanionCard({ onOpen }) {
  const { t } = useI18n();
  return (
    <Card className="span-4 companion-card glass-ai">
      <div className="d-companion-promo">
        <span className="d-ai-avatar"><LumiSpark size={26} /></span>
        <h2>{t("ai.title")}</h2>
        <p>{t("feature.companion.desc")}</p>
        <div className="d-suggest">
          {["ai.s1", "ai.s2", "ai.s3"].map((k) => (
            <button key={k} className="chip d-chip" onClick={() => onOpen(t(k))}>{t(k)}</button>
          ))}
        </div>
      </div>
    </Card>
  );
}

// ---------- planned feature slot ----------
export function SlotPage({ icon, title, sub, plans, children }) {
  const { t } = useI18n();
  return (
    <div className="d-slot">
      <div className="d-slot-hero">
        <span className="d-slot-icon"><Icon name={icon} size={30} /></span>
        <div>
          <h1>{title}</h1>
          <p>{sub}</p>
        </div>
      </div>
      <div className="d-slot-grid">
        <Card title={t("slot.planned")} sub={t("slot.ready")}>
          <ul className="d-plan">
            {plans.map((key) => <li key={key}><Icon name="check" size={16} /> {t(key)}</li>)}
          </ul>
        </Card>
        {children}
      </div>
    </div>
  );
}

// ---------- professor ----------
export function ClassKpis() {
  const { t } = useI18n();
  const avg = CLASS.groups.reduce((s, g) => s + g.mastery, 0) / CLASS.groups.length;
  const attention = CLASS.roster.filter((s) => s.mastery < 0.4).length;
  const kpis = [
    { icon: "chart", label: t("prof.kpi.mastery"), value: `${pct(avg)}%`, color: "var(--lumi-iris)" },
    { icon: "users", label: t("prof.kpi.groups"), value: CLASS.groups.length, color: "var(--lumi-iris-strong)" },
    { icon: "bolt", label: t("prof.kpi.attention"), value: attention, color: "var(--lumi-bad)" },
    { icon: "shield", label: t("prof.kpi.materials"), value: CLASS.materials, color: "var(--lumi-ok)" }
  ];
  return (
    <div className="d-kpis span-12">
      {kpis.map((k) => (
        <div className="d-kpi glass" key={k.label} style={{ "--c": k.color }}>
          <span className="d-kpi-icon"><Icon name={k.icon} size={18} /></span>
          <b>{k.value}</b>
          <small>{k.label}</small>
        </div>
      ))}
    </div>
  );
}

export function GroupProgress({ className = "span-5" }) {
  const { t } = useI18n();
  return (
    <Card icon="users" title={t("prof.groups")} example className={className}>
      <ul className="d-groups">
        {CLASS.groups.map((g) => (
          <li key={g.name}>
            <div className="d-group-top">
              <b>{g.name}</b>
              <small>
                {g.online > 0 && <span className="d-live on" />}
                {t("prof.members", { n: g.members })}
              </small>
            </div>
            <div className="d-group-bar">
              <Meter value={g.mastery} />
              <small>{pct(g.mastery)}%</small>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function Roster() {
  const { t } = useI18n();
  const ago = (h) => (h === 0 ? t("prof.now") : h < 24 ? t("prof.hoursAgo", { n: h }) : t("prof.daysAgo", { n: Math.round(h / 24) }));
  return (
    <Card icon="users" title={t("prof.roster")} example className="span-7">
      <div className="d-table-wrap">
        <table className="d-table">
          <thead>
            <tr>
              <th>{t("prof.col.student")}</th>
              <th>{t("prof.col.group")}</th>
              <th>{t("prof.col.mastery")}</th>
              <th>{t("prof.col.active")}</th>
              <th>{t("prof.col.weak")}</th>
            </tr>
          </thead>
          <tbody>
            {CLASS.roster.map((s) => (
              <tr key={s.name}>
                <td><span className="d-avatar small" style={{ "--c": MASTERY[masteryLevel(s.mastery)].color }}>{initials(s.name)}</span>{s.name}</td>
                <td>{s.group}</td>
                <td>
                  <span className="d-cell-meter"><Meter value={s.mastery} /><small>{pct(s.mastery)}%</small></span>
                  {masteryLevel(s.mastery) === "mastered" && <span className="chip chip-mastered"><Icon name="check" size={13} /> {t("mastery.mastered")}</span>}
                </td>
                <td className={s.active === 0 ? "online" : ""}>{ago(s.active)}</td>
                <td><span className="d-weak-tag">{s.weak}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function WeakPoints({ className = "span-5" }) {
  const { t } = useI18n();
  return (
    <Card icon="chart" title={t("prof.weak")} sub={t("prof.weakSub")} example className={className}>
      <ul className="d-bars">
        {CLASS.weakPoints.map((w) => (
          <li key={w.topic}>
            <span>{w.topic}</span>
            <span className="d-bar"><i style={{ width: `${pct(w.share)}%` }} /></span>
            <b>{pct(w.share)}%</b>
          </li>
        ))}
      </ul>
    </Card>
  );
}

export function ProfActions() {
  const { t } = useI18n();
  return (
    <Card className="span-12 prof-actions">
      <div className="d-actions-row">
        {[
          ["upload", "prof.upload", () => navigate("/app/materials")],
          ["shield", "nav.review", () => navigate("/app/review")],
          ["sparkle", "prof.practice", () => navigate("/app/prep")]
        ].map(([icon, key, run]) => (
          <button key={key} className="d-action glass-hover" onClick={run}>
            <span className="d-action-icon"><Icon name={icon} size={20} /></span>
            <b>{t(key)}</b>
          </button>
        ))}
      </div>
    </Card>
  );
}
