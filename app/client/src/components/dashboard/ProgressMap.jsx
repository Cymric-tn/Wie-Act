// The progress map: the course as a constellation. Each concept is a star and each line means "builds on", so the
// map reads in learning order: step 1 on the left (on the right in Arabic), the last concepts at the far end.
// A star's colour and shape give its status (the legend uses the same drawing); a ring shows its mastery; lines light
// up once their prerequisite is mastered; "Next up" marks the concept the learner is ready for. Nothing moves except
// the Next-up pulse, so names stay readable. Narrow screens get the same map as a step-by-step list.
import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { MASTERY } from "../../lib/demoData";
import { refreshCourseMap, useCourses } from "../../lib/courseMap";
import { useI18n } from "../../lib/i18n";
import { navigate } from "../../lib/router";
import { layoutConstellation, graphOf, progressOf, ancestorsOf, descendantsOf } from "../../lib/constellation";
import Icon from "../Icons";
import { Meter } from "./Widgets";
import "./progress-map.css";

const COLOR = { mastered: "var(--spark)", learning: "var(--glow)", weak: "var(--rose)", new: "var(--text-3)" };
const LEVELS = ["mastered", "learning", "weak", "new"];
const NARROW = 560; // below this canvas width the map is a list
const pct = (v) => Math.round((v || 0) * 100);

// ---------- text measurement (the SVG cannot wrap by itself) ----------
let ctx2d = null;
function measure(text, font) {
  ctx2d = ctx2d || document.createElement("canvas").getContext("2d");
  ctx2d.font = font;
  return ctx2d.measureText(text).width;
}
function wrapper(font) {
  return (text, maxWidth, maxLines) => {
    const words = String(text).split(/\s+/).filter(Boolean);
    const lines = [];
    let line = "";
    for (const w of words) {
      const next = line ? `${line} ${w}` : w;
      if (line && measure(next, font) > maxWidth) {
        lines.push(line);
        line = w;
      } else line = next;
    }
    if (line) lines.push(line);
    if (lines.length <= maxLines) return lines;
    const kept = lines.slice(0, maxLines);
    let last = `${kept[maxLines - 1]} ${lines.slice(maxLines).join(" ")}`;
    while (last.length > 1 && measure(`${last}…`, font) > maxWidth) last = last.slice(0, -1).trimEnd();
    kept[maxLines - 1] = `${last}…`;
    return kept;
  };
}

function useWidth(ref) {
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

// ---------- the star: status by colour AND shape ----------
export function Star({ level, value = 0, r, ready = false }) {
  const ring = r + 4.5;
  return (
    <>
      {level !== "new" && (
        <>
          <circle r={ring} className="cm-track" />
          <circle r={ring} className="cm-arc" stroke={COLOR[level]} pathLength="100" strokeDasharray={`${Math.max(2, pct(value))} 100`} transform="rotate(-90)" />
        </>
      )}
      {level === "new" && <circle r={r - 0.75} className={"cm-hollow" + (ready ? " ready" : "")} />}
      {level === "mastered" && (
        <>
          <circle r={r} fill={COLOR.mastered} />
          <path d={`M${-r * 0.42} ${r * 0.02} L${-r * 0.1} ${r * 0.34} L${r * 0.46} ${-r * 0.3}`} className="cm-mark" />
        </>
      )}
      {level === "learning" && (
        <>
          <circle r={r} fill={COLOR.learning} fillOpacity=".22" stroke={COLOR.learning} strokeWidth="1.5" />
          <circle r={r * 0.42} fill={COLOR.learning} />
        </>
      )}
      {level === "weak" && (
        <>
          <circle r={r} fill={COLOR.weak} />
          <rect x={-1.2} y={-r * 0.55} width="2.4" height={r * 0.62} rx="1.2" fill="var(--on-accent)" />
          <circle cy={r * 0.42} r="1.4" fill="var(--on-accent)" />
        </>
      )}
    </>
  );
}

function Glyph({ level, value, ready, size = 26 }) {
  return (
    <svg className="cm-glyph" width={size} height={size} viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`} aria-hidden="true">
      <Star level={level} value={value} r={size / 2 - 6} ready={ready} />
    </svg>
  );
}

// ---------- one star on the canvas ----------
function Node({ node, layout, level, value, ready, next, selected, dim, interactive, onPick, onHover, label, tagWidth, nextLabel, statusLabel, font }) {
  // A burst of gold when a concept becomes mastered while the map is on screen.
  const was = useRef(level);
  const [burst, setBurst] = useState(false);
  useEffect(() => {
    if (was.current === level) return undefined;
    was.current = level;
    if (level !== "mastered") return undefined;
    setBurst(true);
    const id = setTimeout(() => setBurst(false), 700);
    return () => clearTimeout(id);
  }, [level]);

  const labelY = layout.ring + 6 + layout.line - 2;
  // A soft backing under the name so lines that pass behind it never cut through the text.
  const rows = node.lines.length + (layout.sub ? 1 : 0);
  const backWidth = Math.ceil(Math.max(...node.lines.map((l) => measure(l, font)), layout.sub ? measure(statusLabel, "500 11.5px Inter, sans-serif") : 0)) + 12;
  const cls = ["cm-node", level, ready && "ready", next && "next", selected && "selected", dim && "dim", interactive && "clickable"].filter(Boolean).join(" ");
  return (
    <g
      className={cls}
      transform={`translate(${node.x} ${node.y})`}
      style={{ "--i": node.step }}
      role={interactive ? "button" : "img"}
      tabIndex={0}
      aria-label={label}
      aria-pressed={interactive ? selected : undefined}
      onClick={interactive ? onPick : undefined}
      onKeyDown={(e) => interactive && (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onPick())}
      onPointerEnter={() => onHover(node.id)}
      onPointerLeave={() => onHover(null)}
      onFocus={() => onHover(node.id)}
      onBlur={() => onHover(null)}
    >
      <circle className="cm-hit" r={layout.ring + 12} />
      {next && <circle className="cm-pulse" r={layout.ring + 3} />}
      <circle className="cm-halo" r={layout.ring + 9} fill={COLOR[level]} />
      <g className="cm-star"><Star level={level} value={value} r={layout.r} ready={ready} /></g>
      {burst && <circle className="cm-burst" r={layout.r * 2.2} />}
      <rect className="cm-label-back" x={-backWidth / 2} y={labelY - layout.line + 1} width={backWidth} height={rows * layout.line + 5} rx="7" />
      <text className="cm-label" y={labelY} textAnchor="middle">
        {node.lines.map((l, i) => <tspan key={i} x="0" dy={i ? layout.line : 0}>{l}</tspan>)}
      </text>
      {layout.sub && (
        <text className={`cm-sub ${level}`} y={labelY + layout.line * node.lines.length + 1} textAnchor="middle">{statusLabel}</text>
      )}
      {next && (
        <g className="cm-tag" transform={`translate(0 ${-(layout.ring + 15)})`}>
          <rect x={-tagWidth / 2} y="-9" width={tagWidth} height="18" rx="9" />
          <text y="4" textAnchor="middle">{nextLabel}</text>
        </g>
      )}
    </g>
  );
}

const STARS = (() => {
  let seed = 7;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  return Array.from({ length: 46 }, () => ({ x: rand(), y: rand(), r: rand() * 0.9 + 0.3, o: 0.12 + rand() * 0.35 }));
})();

// ---------- the canvas: wide = constellation, narrow = list by step ----------
function Constellation({ course, progress, width, compact, rtl, font, focusId, selectedId, interactive, onPick, onHover, t }) {
  const layout = useMemo(
    () => layoutConstellation({ units: course.units, width, compact, rtl, wrap: wrapper(font) }),
    [course.units, width, compact, rtl, font]
  );
  // The focused star's chains: everything it builds on (up) and everything built on it (down).
  const focus = useMemo(() => {
    if (!focusId || !layout.graph.ids.has(focusId)) return null;
    const up = ancestorsOf(layout.graph, focusId);
    const down = descendantsOf(layout.graph, focusId);
    return { up, down, all: new Set([focusId, ...up, ...down]) };
  }, [focusId, layout]);
  const onChain = (e) => focus && (
    (up(e.to) && focus.up.has(e.from)) || (down(e.from) && focus.down.has(e.to)));
  const up = (id) => id === focusId || focus.up.has(id);
  const down = (id) => id === focusId || focus.down.has(id);
  const nextLabel = t("map.next");
  const tagWidth = Math.ceil(measure(nextLabel, "700 10.5px Inter, sans-serif")) + 18;

  return (
    <svg className="cm-svg" width={width} height={layout.height} viewBox={`0 0 ${width} ${layout.height}`} dir="ltr" role="group" aria-label={t("map.title")}>
      <g className="cm-sky" aria-hidden="true">
        {STARS.map((s, i) => <circle key={i} cx={s.x * width} cy={s.y * layout.height} r={s.r} fill="#fff" opacity={s.o} />)}
      </g>
      <g className="cm-edges" aria-hidden="true">
        {layout.edges.map((e) => {
          const lit = progress.level.get(e.from) === "mastered";
          const on = onChain(e);
          return <path key={`${e.from}>${e.to}`} d={e.d} className={"cm-edge" + (lit ? " lit" : "") + (on ? " on" : "") + (focus && !on ? " dim" : "")} />;
        })}
      </g>
      {layout.nodes.map((node) => {
        const level = progress.level.get(node.id);
        const value = progress.value.get(node.id);
        const ready = progress.ready.has(node.id);
        const status = t(MASTERY[level].key);
        return (
          <Node
            key={node.id}
            node={node}
            layout={layout}
            level={level}
            value={value}
            ready={ready}
            next={progress.next === node.id}
            selected={selectedId === node.id}
            dim={Boolean(focus) && !focus.all.has(node.id)}
            interactive={interactive}
            onPick={() => onPick(node.id)}
            onHover={onHover}
            label={`${node.unit.name}: ${status}, ${pct(value)}%${ready && level === "new" ? `, ${t("map.ready")}` : ""}${progress.next === node.id ? `, ${t("map.next")}` : ""}`}
            statusLabel={level === "new" ? (ready ? t("map.ready") : status) : `${status} · ${pct(value)}%`}
            tagWidth={tagWidth}
            nextLabel={nextLabel}
            font={font}
          />
        );
      })}
    </svg>
  );
}

function PathList({ course, progress, selectedId, interactive, onPick, t }) {
  const layout = useMemo(() => layoutConstellation({ units: course.units, width: 1000, wrap: (s) => [s] }), [course.units]);
  const byId = new Map(course.units.map((u) => [u.id, u]));
  return (
    <ol className="cm-path">
      {layout.cols.map((col, l) => (
        <li key={l} className="cm-step">
          <span className="cm-step-name">{t("map.stepShort", { n: l + 1 })}</span>
          <ul>
            {col.map((id) => {
              const level = progress.level.get(id);
              const value = progress.value.get(id);
              const ready = progress.ready.has(id);
              const Tag = interactive ? "button" : "div";
              return (
                <li key={id}>
                  <Tag className={`cm-row ${level}` + (selectedId === id ? " selected" : "") + (ready ? " ready" : "")} {...(interactive ? { onClick: () => onPick(id), "aria-pressed": selectedId === id } : {})}>
                    <Glyph level={level} value={value} ready={ready} />
                    <span className="cm-row-text">
                      <b dir="auto">{byId.get(id).name}</b>
                      <small className={level}>{level === "new" ? (ready ? t("map.ready") : t(MASTERY.new.key)) : `${t(MASTERY[level].key)} · ${pct(value)}%`}</small>
                    </span>
                    {progress.next === id && <span className="cm-next-chip">{t("map.next")}</span>}
                  </Tag>
                </li>
              );
            })}
          </ul>
        </li>
      ))}
    </ol>
  );
}

// ---------- side panel ----------
function ConceptList({ ids, course, progress, onPick, empty }) {
  if (!ids.length) return <p className="cm-muted">{empty}</p>;
  const byId = new Map(course.units.map((u) => [u.id, u]));
  return (
    <ul className="cm-links">
      {ids.map((id) => {
        const level = progress.level.get(id);
        return (
          <li key={id}>
            <button onClick={() => onPick(id)}>
              <Glyph level={level} value={progress.value.get(id)} ready={progress.ready.has(id)} size={22} />
              <span dir="auto">{byId.get(id).name}</span>
              <small>{pct(progress.value.get(id))}%</small>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function Details({ course, progress, graph, steps, id, onPick, onClose, onAsk, t, panelRef }) {
  const unit = course.units.find((u) => u.id === id);
  if (!unit) {
    const next = course.units.find((u) => u.id === progress.next);
    return (
      <aside className="cm-panel glass" aria-label={t("map.overview")}>
        <span className="eyebrow">{t("map.overview")}</span>
        <ul className="cm-counts">
          {LEVELS.map((level) => (
            <li key={level}><Glyph level={level} value={0.6} size={22} /><span>{t(MASTERY[level].key)}</span><b>{progress.counts[level]}</b></li>
          ))}
        </ul>
        {next ? (
          <button className="cm-next-card" onClick={() => onPick(next.id)}>
            <small>{t("map.next")}</small>
            <b dir="auto">{next.name}</b>
            <Icon name="right" size={16} />
          </button>
        ) : progress.counts.mastered === progress.total && progress.total > 0 && <p className="cm-done">{t("map.allDone")}</p>}
        <p className="cm-muted">{t("map.pick")}</p>
      </aside>
    );
  }
  const level = progress.level.get(id);
  return (
    <aside className="cm-panel glass-ai" aria-label={unit.name} ref={panelRef} tabIndex={-1}>
      <div className="cm-panel-head">
        <span className="eyebrow">{t("map.step", { n: graph.layer.get(id) + 1, total: steps })}</span>
        <button className="icon-chip" onClick={onClose} aria-label={t("map.close")}><Icon name="close" size={16} /></button>
      </div>
      <h3 dir="auto">{unit.name}</h3>
      <div className="cm-status">
        <span className={`cm-chip ${level}`}><Glyph level={level} value={progress.value.get(id)} size={20} /> {t(MASTERY[level].key)}</span>
        {progress.next === id && <span className="cm-next-chip">{t("map.next")}</span>}
      </div>
      <div className="cm-meter"><Meter value={unit.mastery} /><b>{pct(unit.mastery)}%</b></div>
      <h4>{t("map.buildsOn")}</h4>
      <ConceptList ids={graph.req.get(id)} course={course} progress={progress} onPick={onPick} empty={t("map.first")} />
      <h4>{t("map.unlocks")}</h4>
      <ConceptList ids={graph.next.get(id)} course={course} progress={progress} onPick={onPick} empty={t("map.last")} />
      <div className="cm-actions">
        <button className="btn btn-primary small" onClick={() => navigate(`/app/prep?study=${encodeURIComponent(id)}`)}>
          <Icon name="book" size={16} /> {t("map.study")}
        </button>
        {onAsk && (
          <button className="btn btn-ai small" onClick={() => onAsk(t("map.askText", { concept: unit.name }))}>
            <Icon name="sparkle" size={16} /> {t("map.ask")}
          </button>
        )}
      </div>
    </aside>
  );
}

function Legend({ t }) {
  return (
    <div className="cm-legend">
      {LEVELS.map((level) => (
        <span key={level} className="cm-legend-item"><Glyph level={level} value={0.6} size={22} />{t(MASTERY[level].key)}</span>
      ))}
      <span className="cm-legend-item"><Glyph level="new" ready size={22} />{t("map.ready")}</span>
      <span className="cm-legend-item line"><svg width="28" height="10" aria-hidden="true"><path d="M2 5h24" className="cm-edge lit" /></svg>{t("map.line")}</span>
    </div>
  );
}

const initialConcept = () => {
  try {
    return new URLSearchParams(window.location.search).get("concept");
  } catch {
    return null;
  }
};

/**
 * compact: the dashboard card and the landing page (no side panel; a click calls onConcept, or nothing).
 * Full page: a click selects a star and the panel shows it; onAsk(text) opens Lumi with a question.
 */
function ProgressMap({ compact = false, initialCourse = 0, onOpenFull, onConcept, onAsk }) {
  const { t, lang } = useI18n();
  const courses = useCourses();
  useEffect(() => { refreshCourseMap(); }, []);
  const [courseIndex, setCourseIndex] = useState(initialCourse || 0);
  const course = courses[courseIndex] || courses[0];
  const [selectedId, setSelectedId] = useState(() => (compact ? null : initialConcept()));
  const [hoverId, setHoverId] = useState(null);
  const canvasRef = useRef(null);
  const width = useWidth(canvasRef);
  const rtl = lang === "ar";
  const font = `600 ${compact ? 11.5 : 13}px ${rtl ? "'IBM Plex Sans Arabic', " : ""}Inter, sans-serif`;
  const graph = useMemo(() => graphOf(course.units), [course.units]);
  const progress = useMemo(() => progressOf(course.units, graph), [course.units, graph]);
  const steps = useMemo(() => Math.max(1, ...graph.layer.values()) + 1, [graph]);
  const nextUnit = course.units.find((u) => u.id === progress.next);

  useEffect(() => {
    if (compact || !selectedId) return undefined;
    const onKey = (e) => e.key === "Escape" && setSelectedId(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [compact, selectedId]);

  const panelRef = useRef(null);
  const pick = (id) => {
    if (compact) return onConcept?.(id);
    setSelectedId((cur) => (cur === id ? null : id));
  };
  // On a phone the panel sits under the list: bring it into view when a concept is picked from the list.
  const scrollToPanel = useRef(false);
  useEffect(() => {
    if (!scrollToPanel.current || !selectedId) return;
    scrollToPanel.current = false;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    panelRef.current?.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }, [selectedId]);
  const interactive = !compact || Boolean(onConcept);
  const narrow = width > 0 && width < NARROW;

  return (
    <div className={"progress-map cm" + (compact ? " compact" : " full")}>
      {courses.length > 1 && (
        <div className="cm-courses" role="tablist">
          {courses.map((c, i) => (
            <button key={c.id} role="tab" aria-selected={i === courseIndex} onClick={() => { setCourseIndex(i); setSelectedId(null); }}>{c.name}</button>
          ))}
        </div>
      )}

      <div className="cm-summary">
        <div className="cm-summary-main">
          {!compact && <b className="cm-course" dir="auto">{course.name}</b>}
          <span className="cm-meter"><Meter value={progress.overall} /><b>{pct(progress.overall)}%</b></span>
          <small>{t("map.count", { n: progress.counts.mastered, total: progress.total })}</small>
        </div>
        <div className="cm-summary-actions">
          {nextUnit && (
            <button className="cm-next-btn" onClick={() => pick(nextUnit.id)} disabled={!interactive}>
              <span className="cm-next-dot" aria-hidden="true" />
              <small>{t("map.next")}</small>
              <b dir="auto">{nextUnit.name}</b>
            </button>
          )}
          {onOpenFull && <button className="btn btn-secondary small" onClick={onOpenFull}>{t("map.open")}</button>}
        </div>
      </div>

      <div className="cm-body">
        <div className="cm-canvas" ref={canvasRef} onClick={(e) => !compact && e.target === e.currentTarget && setSelectedId(null)}>
          {width > 0 && (narrow
            ? <PathList course={course} progress={progress} selectedId={selectedId} interactive={interactive} onPick={(id) => { scrollToPanel.current = !compact; pick(id); }} t={t} />
            : <Constellation course={course} progress={progress} width={width} compact={compact} rtl={rtl} font={font}
                focusId={hoverId || selectedId} selectedId={selectedId} interactive={interactive} onPick={pick} onHover={setHoverId} t={t} />)}
        </div>
        {!compact && !(narrow && !selectedId) && (
          <Details course={course} progress={progress} graph={graph} steps={steps} id={selectedId} panelRef={panelRef}
            onPick={(id) => setSelectedId(id)} onClose={() => setSelectedId(null)} onAsk={onAsk} t={t} />
        )}
      </div>

      {!compact && <Legend t={t} />}
    </div>
  );
}

export default memo(ProgressMap);
