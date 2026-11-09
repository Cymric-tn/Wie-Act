import { Fragment, memo, useEffect, useRef, useState } from "react";
import { MASTERY, masteryLevel, courseMastery } from "../../lib/demoData";
import { refreshCourseMap, useCourses } from "../../lib/courseMap";
import { useI18n } from "../../lib/i18n";
import Icon from "../Icons";

// The map's own colours (it is the one place Glow marks a concept being worked on).
const MAP_COLOR = { mastered: "var(--lumi-spark)", learning: "var(--lumi-glow)", weak: "var(--lumi-bad)", new: "var(--lumi-slate)" };

const W = 800;
const H = 460;
const pct = (v) => Math.round(v * 100);

function useOrbitClock(running) {
  const [t, setT] = useState(0);
  useEffect(() => {
    if (!running || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let frame;
    let last = performance.now();
    const tick = (now) => {
      if (!document.hidden) setT((v) => v + Math.min(50, now - last) / 1000);
      last = now;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [running]);
  return t;
}

/** Positions on a tilted orbit; `front` tells whether the body passes in front of its star. */
function orbit(cx, cy, rx, ry, angle) {
  return { x: cx + Math.cos(angle) * rx, y: cy + Math.sin(angle) * ry, front: Math.sin(angle) >= 0 };
}

function Defs({ COURSES }) {
  return (
    <defs>
      {Object.entries(MAP_COLOR).map(([level, color]) => (
        <radialGradient key={level} id={`pm-${level}`} cx="35%" cy="32%" r="75%">
          <stop offset="0%" stopColor="#ffffff" stopOpacity=".9" />
          <stop offset="28%" stopColor={color} />
          <stop offset="100%" stopColor={color} stopOpacity=".35" />
        </radialGradient>
      ))}
      {COURSES.map((c) => (
        <radialGradient key={c.id} id={`pm-sun-${c.id}`} cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#ffffff" />
          <stop offset="35%" stopColor={c.color} />
          <stop offset="100%" stopColor={c.color} stopOpacity="0" />
        </radialGradient>
      ))}
      <filter id="pm-glow" x="-100%" y="-100%" width="300%" height="300%">
        <feGaussianBlur stdDeviation="6" />
      </filter>
      <radialGradient id="pm-nebula" cx="55%" cy="45%" r="60%">
        <stop offset="0%" stopColor="var(--lumi-iris)" stopOpacity=".22" />
        <stop offset="100%" stopColor="var(--lumi-iris)" stopOpacity="0" />
      </radialGradient>
    </defs>
  );
}

const STARS = (() => {
  let seed = 11;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  return Array.from({ length: 90 }, () => ({ x: rand() * W, y: rand() * H, r: rand() * 1.2 + 0.2, o: 0.15 + rand() * 0.5 }));
})();

function Body({ x, y, r, level, label, sub, onSelect, ariaLabel, labelBelow = true, dim }) {
  const color = MAP_COLOR[level];
  return (
    <g
      className={"pm-body" + (onSelect ? " clickable" : "") + (dim ? " dim" : "")}
      onClick={onSelect}
      onKeyDown={(e) => onSelect && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect())}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      aria-label={ariaLabel}
    >
      <circle cx={x} cy={y} r={r * 1.9} fill={color} opacity={level === "new" ? 0.08 : 0.22} filter="url(#pm-glow)" />
      <circle cx={x} cy={y} r={r} fill={`url(#pm-${level})`} />
      {level === "mastered" && <circle cx={x} cy={y} r={r + 4} fill="none" stroke={color} strokeOpacity=".55" strokeDasharray="2 4" />}
      {label && (
        <text x={x} y={labelBelow ? y + r + 16 : y - r - 10} textAnchor="middle" className="pm-label">
          {label}
          {sub && <tspan x={x} dy="13" className="pm-sub">{sub}</tspan>}
        </text>
      )}
    </g>
  );
}

function Sun({ course, x, y, r, onSelect, showLabel = true, t }) {
  const { t: tr } = useI18n();
  const pulse = 1 + Math.sin(t * 1.6) * 0.04;
  return (
    <g
      className={"pm-sun" + (onSelect ? " clickable" : "")}
      onClick={onSelect}
      onKeyDown={(e) => onSelect && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect())}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      aria-label={`${course.id} ${course.name}, ${tr("map.mastery", { n: pct(courseMastery(course)) })}`}
    >
      <circle cx={x} cy={y} r={r * 2.6 * pulse} fill={`url(#pm-sun-${course.id})`} opacity=".45" />
      <circle cx={x} cy={y} r={r} fill={`url(#pm-sun-${course.id})`} />
      <circle cx={x} cy={y} r={r * 0.55} fill="#ffffff" opacity=".9" />
      {showLabel && (
        <text x={x} y={y + r + 22} textAnchor="middle" className="pm-label strong">
          {course.name}
          <tspan x={x} dy="15" className="pm-sub">{course.id} · {tr("map.mastery", { n: pct(courseMastery(course)) })}</tspan>
        </text>
      )}
    </g>
  );
}

function Galaxy({ t, onCourse, COURSES }) {
  const spots = [[190, 205], [420, 130], [615, 255]];
  return COURSES.map((course, ci) => {
    const [cx, cy] = spots[ci % spots.length];
    const bodies = course.units.map((u, i) => ({
      u,
      ...orbit(cx, cy, 48 + i * 11, (48 + i * 11) * 0.42, t * (0.55 - i * 0.07) + i * 1.7 + ci)
    }));
    return (
      <g key={course.id}>
        {course.units.map((_, i) => (
          <ellipse key={i} cx={cx} cy={cy} rx={48 + i * 11} ry={(48 + i * 11) * 0.42} className="pm-orbit faint" />
        ))}
        {bodies.filter((b) => !b.front).map((b, i) => <Body key={`b${i}`} x={b.x} y={b.y} r={4.5} level={masteryLevel(b.u.mastery)} />)}
        <Sun course={course} x={cx} y={cy} r={20} t={t + ci} onSelect={() => onCourse(ci)} />
        {bodies.filter((b) => b.front).map((b, i) => <Body key={`f${i}`} x={b.x} y={b.y} r={4.5} level={masteryLevel(b.u.mastery)} />)}
      </g>
    );
  });
}

function System({ course, t, onUnit }) {
  const { t: tr } = useI18n();
  const cx = W / 2;
  const cy = H / 2 - 6;
  const bodies = course.units.map((u, i) => {
    const rx = 112 + i * 54;
    const ry = rx * 0.4;
    return { u, i, rx, ry, ...orbit(cx, cy, rx, ry, t * (0.38 - i * 0.045) + i * 1.25) };
  });
  const body = (b) => (
    <Body
      key={b.i}
      x={b.x}
      y={b.y}
      r={9 + b.u.lessons.length * 1.6}
      level={masteryLevel(b.u.mastery)}
      label={b.u.name}
      sub={tr("map.mastery", { n: pct(b.u.mastery) })}
      ariaLabel={`${b.u.name}, ${tr("map.mastery", { n: pct(b.u.mastery) })}`}
      onSelect={() => onUnit(b.i)}
    />
  );
  return (
    <>
      {bodies.map((b) => <ellipse key={b.i} cx={cx} cy={cy} rx={b.rx} ry={b.ry} className="pm-orbit" />)}
      {bodies.filter((b) => !b.front).map(body)}
      <Sun course={course} x={cx} y={cy} r={30} t={t} showLabel={false} />
      {bodies.filter((b) => b.front).map(body)}
    </>
  );
}

function Moons({ course, unit, t }) {
  const { t: tr } = useI18n();
  const cx = W / 2;
  const cy = H / 2 - 10;
  const level = masteryLevel(unit.mastery);
  const moons = unit.lessons.map((l, i) => {
    const rx = 135 + i * 62;
    return { l, i, rx, ry: rx * 0.42, ...orbit(cx, cy, rx, rx * 0.42, t * (0.42 - i * 0.07) + i * 2.1) };
  });
  const moon = (m) => (
    <Body
      key={m.i}
      x={m.x}
      y={m.y}
      r={9}
      level={masteryLevel(m.l.mastery)}
      label={m.l.name}
      sub={`${tr(MASTERY[masteryLevel(m.l.mastery)].key)} · ${pct(m.l.mastery)}%`}
      ariaLabel={`${m.l.name}, ${pct(m.l.mastery)}%`}
    />
  );
  return (
    <>
      {moons.map((m) => <ellipse key={m.i} cx={cx} cy={cy} rx={m.rx} ry={m.ry} className="pm-orbit" />)}
      {moons.filter((m) => !m.front).map(moon)}
      <g>
        <circle cx={cx} cy={cy} r={68} fill="var(--lumi-glow)" opacity=".22" filter="url(#pm-glow)" />
        <circle cx={cx} cy={cy} r={42} fill={`url(#pm-${level})`} />
        <ellipse cx={cx} cy={cy} rx={64} ry={12} fill="none" stroke={course.color} strokeOpacity=".55" strokeWidth="2" transform={`rotate(-14 ${cx} ${cy})`} />
        <text x={cx} y={cy + 4} textAnchor="middle" className="pm-core">{pct(unit.mastery)}%</text>
      </g>
      {moons.filter((m) => m.front).map(moon)}
    </>
  );
}

/** The interactive solar-system progress map. */
function ProgressMap({ compact = false, initialCourse = null, onOpenFull }) {
  const { t } = useI18n();
  const COURSES = useCourses();
  useEffect(() => { refreshCourseMap(); }, []);
  // one course: open its planets (the concepts) straight away
  const [courseIndex, setCourseIndex] = useState(initialCourse ?? 0);
  const [unitIndex, setUnitIndex] = useState(null);
  const ref = useRef(null);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);

  const time = useOrbitClock(visible);
  const course = courseIndex !== null ? COURSES[courseIndex] : null;
  const unit = course && unitIndex !== null ? course.units[unitIndex] : null;

  const crumbs = [
    { label: t("map.all"), onClick: course ? () => { setCourseIndex(null); setUnitIndex(null); } : null }
  ];
  if (course) crumbs.push({ label: course.name, onClick: unit ? () => setUnitIndex(null) : null });
  if (unit) crumbs.push({ label: unit.name });

  return (
    <div className={"progress-map lumi-dark" + (compact ? " compact" : "")} ref={ref}>
      <div className="pm-bar">
        <nav className="pm-crumbs" aria-label="Map level">
          {crumbs.map((c, i) => (
            <Fragment key={i}>
              {i > 0 && <span className="pm-sep" aria-hidden="true">›</span>}
              {c.onClick ? <button onClick={c.onClick}>{c.label}</button> : <span aria-current="true">{c.label}</span>}
            </Fragment>
          ))}
        </nav>
        <div className="pm-actions">
          {onOpenFull && <button className="btn btn-secondary small" onClick={onOpenFull}>{t("map.open")}</button>}
          {/* "Enter 3D universe" is hidden: the static page behind it still lists MATH102 / CS201 / PHYS101 (Phase 1 has one course) */}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="pm-svg" dir="ltr" role="group" aria-label={t("map.title")}>
        <Defs COURSES={COURSES} />
        <rect width={W} height={H} fill="url(#pm-nebula)" />
        {STARS.map((s, i) => (
          <circle key={i} cx={s.x} cy={s.y} r={s.r} fill="#ffffff" opacity={s.o * (0.75 + Math.sin(time * 0.9 + i) * 0.25)} />
        ))}
        {!course && <Galaxy t={time} onCourse={setCourseIndex} COURSES={COURSES} />}
        {course && !unit && <System course={course} t={time} onUnit={setUnitIndex} />}
        {unit && <Moons course={course} unit={unit} t={time} />}
      </svg>
      <div className="pm-legend">
        {Object.entries(MASTERY).map(([level, { key }]) => (level === "mastered"
          ? <span key={level} className="chip chip-mastered"><Icon name="check" size={13} /> {t(key)}</span>
          : (
          <span key={level}><i style={{ background: MAP_COLOR[level], color: MAP_COLOR[level] }} />{t(key)}</span>
          )))}
        <span className="pm-hint">{t("map.sub")}</span>
      </div>
    </div>
  );
}

export default memo(ProgressMap);
