import { Fragment, memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { MASTERY, masteryLevel, courseMastery } from "../../lib/demoData";
import { refreshCourseMap, useCourses } from "../../lib/courseMap";
import { useI18n } from "../../lib/i18n";
import { fitOrbits, planetPositions, placeLabels, shortName, SUN_LABEL_H } from "../../lib/mapLayout";
import Icon from "../Icons";
import { Meter } from "./Widgets";

// The map's own colours (it is the one place Glow marks a concept being worked on).
const MAP_COLOR = { mastered: "var(--spark)", learning: "var(--glow)", weak: "var(--rose)", new: "rgba(255, 255, 255, .35)" };

const pct = (v) => Math.round(v * 100);
const ARC = 5; // what a progress arc adds around a planet

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

/** The canvas in CSS pixels, so a planet of 16px is 16px on screen at every width. Layout size: a CSS tilt must not change it. */
function useCanvasSize(ref) {
  const [size, setSize] = useState(null);
  useLayoutEffect(() => {
    const el = ref.current;
    const set = (width, height) => width > 0 && height > 0 && setSize((s) => (s && s.width === width && s.height === height ? s : { width, height }));
    set(el.clientWidth, el.clientHeight);
    const observer = new ResizeObserver(([entry]) => set(Math.round(entry.contentRect.width), Math.round(entry.contentRect.height)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return size;
}

let measurer = null;
function textWidth(text, font) {
  measurer = measurer || document.createElement("canvas").getContext("2d");
  measurer.font = font;
  return Math.ceil(measurer.measureText(text).width);
}
const LABEL_FONT = "600 12px Inter, sans-serif";
const SUN_FONT = "600 16px Outfit, sans-serif";

function Defs({ COURSES }) {
  return (
    <defs>
      {Object.entries({ ...MAP_COLOR, new: "var(--lumi-slate)" }).map(([level, color]) => (
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
      <radialGradient id="pm-nebula" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stopColor="var(--lumi-iris)" stopOpacity=".22" />
        <stop offset="100%" stopColor="var(--lumi-iris)" stopOpacity="0" />
      </radialGradient>
    </defs>
  );
}

const STARS = (() => {
  let seed = 11;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  return Array.from({ length: 90 }, () => ({ x: rand(), y: rand(), r: rand() * 1.2 + 0.2, o: 0.15 + rand() * 0.5 }));
})();

/** A planet's status, by colour and by shape. Drawn around (0, 0); the legend uses the same drawing. */
function Glyph({ level, r, value = 0 }) {
  if (level === "new") return <circle r={r - 0.75} fill="var(--void)" stroke={MAP_COLOR.new} strokeWidth="1.5" />;
  if (level === "learning") {
    return (
      <>
        <circle r={r} fill={MAP_COLOR.learning} />
        <circle r={r + 3.5} fill="none" stroke={MAP_COLOR.learning} strokeOpacity=".25" strokeWidth="2" />
        <circle r={r + 3.5} fill="none" stroke={MAP_COLOR.learning} strokeWidth="2" strokeLinecap="round" pathLength="100" strokeDasharray={`${pct(value)} 100`} transform="rotate(-90)" />
      </>
    );
  }
  if (level === "weak") {
    return (
      <>
        <circle r={r} fill={MAP_COLOR.weak} />
        <rect x="-1.1" y={-r * 0.62} width="2.2" height={r * 0.72} rx="1.1" fill="var(--void)" />
        <circle cy={r * 0.5} r="1.3" fill="var(--void)" />
      </>
    );
  }
  return (
    <>
      <circle r={r} fill={MAP_COLOR.mastered} />
      <path d={`M${-r * 0.45} 0 L${-r * 0.1} ${r * 0.36} L${r * 0.5} ${-r * 0.36}`} fill="none" stroke="var(--void)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </>
  );
}

function Planet({ item, at, label, level, onSelect }) {
  const { t: tr } = useI18n();
  // A burst of gold on the one occasion a concept becomes mastered while it is on screen.
  const previous = useRef(level);
  const [burst, setBurst] = useState(false);
  useEffect(() => {
    if (previous.current === level) return undefined;
    previous.current = level;
    if (level !== "mastered") return undefined;
    setBurst(true);
    const id = setTimeout(() => setBurst(false), 600);
    return () => clearTimeout(id);
  }, [level]);
  const status = tr(MASTERY[level].key).toLowerCase();
  return (
    <g
      className={`pm-body ${level}` + (onSelect ? " clickable" : "")}
      transform={`translate(${at.x} ${at.y})`}
      onClick={onSelect}
      onKeyDown={(e) => onSelect && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect())}
      role={onSelect ? "button" : "img"}
      tabIndex={0}
      aria-label={`${item.name} — ${status}, ${pct(item.value)}%`}
    >
      <title>{item.name}</title>
      <circle className="pm-hit" r="22" />
      <circle className="pm-halo" r={item.r + ARC + 6} fill={MAP_COLOR[level]} filter="url(#pm-glow)" />
      <g className="pm-glyph"><Glyph level={level} r={item.r} value={item.value} /></g>
      {burst && <circle className="pm-burst" r={item.r * 2} fill="var(--spark)" />}
      <text className={"pm-label" + (label.visible ? "" : " off")} x={label.x - at.x} y={label.y - at.y + 4} textAnchor="middle">
        {item.short}
        <tspan className="pm-pct" x={label.x - at.x} dy="15">{pct(item.value)}%</tspan>
      </text>
    </g>
  );
}

function Sun({ course, x, y, r, onSelect, showLabel = true, t }) {
  const { t: tr } = useI18n();
  const pulse = 1 + Math.sin(t * 1.6) * 0.04;
  const mastery = pct(courseMastery(course));
  return (
    <g
      className={"pm-sun" + (onSelect ? " clickable" : "")}
      onClick={onSelect}
      onKeyDown={(e) => onSelect && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect())}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      aria-label={`${course.name}, ${tr("map.mastery", { n: mastery })}`}
    >
      <circle cx={x} cy={y} r={r * 2.6 * pulse} fill={`url(#pm-sun-${course.id})`} opacity=".45" />
      <circle cx={x} cy={y} r={r} fill={`url(#pm-sun-${course.id})`} />
      <circle cx={x} cy={y} r={r * 0.55} fill="#ffffff" opacity=".9" />
      {showLabel && (
        <text x={x} y={y + r + 22} textAnchor="middle" className="pm-sun-name">
          {course.name}
          <tspan x={x} dy="18" className="pm-sun-pct">{tr("map.mastery", { n: mastery })}</tspan>
        </text>
      )}
    </g>
  );
}

const sunRadiusFor = ({ width, height }) => Math.max(16, Math.min(24, Math.min(width, height) * 0.05));

/** Orbits, the body at their centre, and the planets. Planets keep one DOM order, so keyboard focus survives their motion. */
function Orbits({ size, t, items, centerRadius, sunLabel, renderCenter, onSelect }) {
  const previous = useRef([]);
  const named = useMemo(
    () => items.map((item) => { const short = shortName(item.name); return { ...item, short, labelWidth: textWidth(short, LABEL_FONT) }; }),
    [items]
  );
  const layout = useMemo(() => fitOrbits({
    ...size,
    count: named.length,
    sunRadius: centerRadius,
    sunLabelHeight: sunLabel ? SUN_LABEL_H : 0,
    sunLabelHalf: sunLabel ? sunLabel.width / 2 : 0,
    extent: Math.max(...named.map((n) => n.r)) + ARC,
    labelWidth: Math.max(...named.map((n) => n.labelWidth)),
    labelHeight: 16
  }), [size, named, centerRadius, sunLabel]);
  const positions = planetPositions(layout, t);
  const planets = positions.map((p, i) => ({ ...p, extent: named[i].r + ARC, labelWidth: named[i].labelWidth }));
  const labels = placeLabels({
    planets,
    width: size.width,
    height: size.height,
    circles: [{ x: layout.cx, y: layout.cy, r: centerRadius + 4 }],
    boxes: sunLabel ? [{ x0: layout.cx - sunLabel.width / 2, x1: layout.cx + sunLabel.width / 2, y0: layout.cy + centerRadius + 4, y1: layout.cy + centerRadius + SUN_LABEL_H }] : [],
    previous: previous.current
  });
  previous.current = labels;
  return (
    <>
      {layout.orbits.map((o, i) => <ellipse key={i} cx={layout.cx} cy={layout.cy} rx={o.rx} ry={o.ry} className="pm-orbit" />)}
      {renderCenter(layout.cx, layout.cy)}
      {named.map((item, i) => (
        <Planet key={item.key} item={item} at={positions[i]} label={labels[i]} level={masteryLevel(item.value)} onSelect={onSelect ? () => onSelect(i) : undefined} />
      ))}
    </>
  );
}

/** Several courses side by side: a small system each, with no planet labels (a click on a sun opens it). */
function Galaxy({ t, onCourse, COURSES, size }) {
  const cell = size.width / COURSES.length;
  return COURSES.map((course, ci) => {
    const r = sunRadiusFor({ width: cell, height: size.height });
    const half = textWidth(course.name, SUN_FONT) / 2;
    const layout = fitOrbits({ width: size.width, height: size.height, cx: cell * (ci + 0.5), count: course.units.length, sunRadius: r, sunLabelHeight: SUN_LABEL_H, sunLabelHalf: half, extent: 7 });
    return (
      <g key={course.id}>
        {layout.orbits.map((o, i) => <ellipse key={i} cx={layout.cx} cy={layout.cy} rx={o.rx} ry={o.ry} className="pm-orbit" />)}
        <Sun course={course} x={layout.cx} y={layout.cy} r={r} t={t + ci} onSelect={() => onCourse(ci)} />
        {planetPositions(layout, t).map((p, i) => (
          <g key={i} transform={`translate(${p.x} ${p.y})`} aria-hidden="true"><Glyph level={masteryLevel(course.units[i].mastery)} r={7} value={course.units[i].mastery} /></g>
        ))}
      </g>
    );
  });
}

function System({ course, t, onUnit, size }) {
  const r = sunRadiusFor(size);
  const items = useMemo(() => course.units.map((u) => ({ key: u.id, name: u.name, value: u.mastery, r: 7 + Math.min(2, (u.lessons.length - 1)) })), [course]);
  const sunLabel = useMemo(() => ({ width: textWidth(course.name, SUN_FONT) }), [course.name]);
  return <Orbits size={size} t={t} items={items} centerRadius={r} sunLabel={sunLabel} onSelect={onUnit} renderCenter={(x, y) => <Sun course={course} x={x} y={y} r={r} t={t} />} />;
}

function Moons({ unit, t, size }) {
  const level = masteryLevel(unit.mastery);
  const r = Math.max(26, Math.min(42, Math.min(size.width, size.height) * 0.1));
  const items = useMemo(() => unit.lessons.map((l) => ({ key: l.id, name: l.name, value: l.mastery, r: 8 })), [unit]);
  return (
    <Orbits
      size={size}
      t={t}
      items={items}
      centerRadius={r}
      renderCenter={(x, y) => (
        <g>
          <circle cx={x} cy={y} r={r * 1.6} fill="var(--lumi-glow)" opacity=".22" filter="url(#pm-glow)" />
          <circle cx={x} cy={y} r={r} fill={`url(#pm-${level})`} />
          <text x={x} y={y + 6} textAnchor="middle" className="pm-core">{pct(unit.mastery)}%</text>
        </g>
      )}
    />
  );
}

/** What the map knows about the selected concept. */
function ConceptPanel({ unit }) {
  const { t } = useI18n();
  const level = masteryLevel(unit.mastery);
  return (
    <aside className="pm-details glass-ai" aria-label={unit.name}>
      <h3 dir="auto">{unit.name}</h3>
      {level === "mastered"
        ? <span className="chip chip-mastered"><Icon name="check" size={13} /> {t(MASTERY.mastered.key)}</span>
        : <p>{t(MASTERY[level].key)}</p>}
      <div className="pm-details-meter"><Meter value={unit.mastery} /><small>{pct(unit.mastery)}%</small></div>
      <ul>
        {unit.lessons.map((l) => (
          <li key={l.name}>
            <span dir="auto">{l.name}</span>
            <small>{t(MASTERY[masteryLevel(l.mastery)].key)} · {pct(l.mastery)}%</small>
          </li>
        ))}
      </ul>
    </aside>
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
  const canvasRef = useRef(null);
  const size = useCanvasSize(canvasRef);
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
    <div className={"progress-map lumi-dark" + (compact ? " compact" : "") + (unit && !compact ? " has-details" : "")} ref={ref}>
      <div className="pm-bar">
        <nav className="pm-crumbs glass" aria-label="Map level">
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
      <svg ref={canvasRef} viewBox={size ? `0 0 ${size.width} ${size.height}` : undefined} className="pm-svg" dir="ltr" role="group" aria-label={t("map.title")}>
        {size && (
          <>
            <Defs COURSES={COURSES} />
            <rect width={size.width} height={size.height} fill="url(#pm-nebula)" />
            {STARS.map((s, i) => (
              <circle key={i} cx={s.x * size.width} cy={s.y * size.height} r={s.r} fill="#ffffff" opacity={s.o * (0.75 + Math.sin(time * 0.9 + i) * 0.25)} />
            ))}
            {!course && <Galaxy t={time} onCourse={setCourseIndex} COURSES={COURSES} size={size} />}
            {course && !unit && <System course={course} t={time} onUnit={setUnitIndex} size={size} />}
            {unit && <Moons key={unit.id} unit={unit} t={time} size={size} />}
          </>
        )}
      </svg>
      {unit && !compact && <ConceptPanel unit={unit} />}
      <div className="pm-legend">
        {Object.entries(MASTERY).map(([level, { key }]) => (
          <span key={level} className="pm-legend-item">
            <svg width="24" height="24" viewBox="-12 -12 24 24" aria-hidden="true"><Glyph level={level} r={6} value={0.6} /></svg>
            {t(key)}
          </span>
        ))}
        <span className="pm-hint">{t("map.sub")}</span>
      </div>
    </div>
  );
}

export default memo(ProgressMap);
