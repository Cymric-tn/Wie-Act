// Shared pieces for every engine-backed screen: staged progress from engine
// events, degraded-result notices (with a retry countdown), sources, reporting,
// and the interactive exercise card (answers are graded on the server).
import { useEffect, useRef, useState } from "react";
import Icon from "../Icons";
import { useI18n } from "../../lib/i18n";
import { ai } from "../../lib/engine";
import { masteryLevel } from "../../lib/demoData";
import "./ai.css";
import { LumiSpark } from "../LumiSpark";

// ---------- engine status ----------
export function useAiStatus(pollMs = 20000) {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => ai.status().then((s) => alive && setStatus(s)).catch(() => alive && setStatus({ offline: true }));
    load();
    const id = setInterval(load, pollMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [pollMs]);
  return status;
}

export function EngineBadge({ status, showHint = false }) {
  const { t } = useI18n();
  if (!status) return null;
  const kind = status.offline || !status.connected ? "offline" : status.backend === "fake" ? "fake" : "connected";
  return (
    <span className={`ai-engine ${kind}`} title={kind === "fake" ? t("engine.fakeHint") : undefined}>
      <i />
      {t(`engine.${kind}`)}
      {showHint && kind === "fake" && <small>{t("engine.fakeHint")}</small>}
    </span>
  );
}

// ---------- progress ----------
const RETRY_EVENTS = new Set(["retry_after_interval", "wait_slot", "backoff", "fix_request"]);

export function eventLabel(t, event, stage) {
  if (!event) return t("ev.default");
  if (stage === "classify" && event.name === "generating") return t("ev.classify");
  if (RETRY_EVENTS.has(event.name)) return t("ev.retry");
  const key = `ev.${event.name}`;
  const text = t(key);
  if (text === key) return t("ev.default");
  if (event.name === "queued" && event.data?.eta_s) return `${text} ~${Math.round(event.data.eta_s)} s`;
  return text;
}

/** Keeps the latest event and any verbatim course sentences (partial_result). */
export function useProgress() {
  const [state, setState] = useState({ event: null, stage: null, partial: null });
  const onEvent = (event, stage) =>
    setState((s) => ({ event, stage, partial: event.name === "partial_result" ? event.data?.excerpts?.map((e) => e.text) || event.data?.sentences || null : s.partial }));
  const reset = () => setState({ event: null, stage: null, partial: null });
  return [state, onEvent, reset];
}

export function Progress({ state }) {
  const { t } = useI18n();
  return (
    <div className="ai-progress" role="status">
      <span className="ai-spark"><LumiSpark size={22} state="thinking" /></span>
      <span>{eventLabel(t, state.event, state.stage)}<i className="ai-cursor" aria-hidden="true" /></span>
      {state.partial?.length > 0 && (
        <blockquote className="ai-partial" dir="auto">
          <small>{t("ev.partial_result")}</small>
          {state.partial.join(" ")}
        </blockquote>
      )}
    </div>
  );
}

// ---------- answers ----------
const prefersStill = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Counts words up to `total`, in about 1.4 s however long the answer is. The engine returns whole answers, so this is presentation only. */
function useReveal(total, enabled) {
  const [shown, setShown] = useState(enabled && !prefersStill() ? 0 : total);
  useEffect(() => {
    if (shown >= total) return undefined;
    const id = setTimeout(() => setShown((n) => Math.min(total, n + Math.max(1, Math.ceil(total / 58)))), 24);
    return () => clearTimeout(id);
  }, [shown, total]);
  return shown;
}

const wordsOf = (line) => line.match(/\s*\S+\s*/g) || [];
const BULLET = /^\s*[•\-*]\s/;

/**
 * With `reveal`, the words appear progressively. The whole text is in the DOM from the start (the rest is only transparent),
 * so nothing reflows and a screen reader hears the answer once.
 */
export function AnswerText({ text, reveal = false }) {
  const blocks = String(text || "").split(/\n{2,}/).map((block) => {
    const lines = block.split("\n");
    const list = lines.every((l) => BULLET.test(l));
    return { list, lines: list ? lines.map((l) => l.replace(BULLET, "")) : lines };
  });
  const total = blocks.flatMap((b) => b.lines).reduce((n, line) => n + wordsOf(line).length, 0);
  const shown = useReveal(total, reveal);
  if (!text) return null;
  let seen = 0;
  const paint = (line) => {
    const words = wordsOf(line);
    const start = seen;
    seen += words.length;
    if (shown >= total) return line;
    const visible = Math.max(0, Math.min(words.length, shown - start));
    return (
      <>
        {words.slice(0, visible).join("")}
        {shown >= start && shown < seen && <i className="ai-cursor" aria-hidden="true" />}
        <span className="ai-unrevealed">{words.slice(visible).join("")}</span>
      </>
    );
  };
  return (
    <div className="ai-answer" dir="auto">
      {blocks.map(({ list, lines }, i) => (list
        ? <ul key={i}>{lines.map((l, k) => <li key={k}>{paint(l)}</li>)}</ul>
        : <p key={i}>{lines.map((l, k) => <span key={k}>{paint(l)}{k < lines.length - 1 && <br />}</span>)}</p>))}
    </div>
  );
}

function useCountdown(seconds) {
  const [left, setLeft] = useState(seconds ? Math.ceil(seconds) : 0);
  useEffect(() => {
    setLeft(seconds ? Math.ceil(seconds) : 0);
    if (!seconds) return;
    const id = setInterval(() => setLeft((s) => (s <= 1 ? (clearInterval(id), 0) : s - 1)), 1000);
    return () => clearInterval(id);
  }, [seconds]);
  return left;
}

const FAST_REASONS = new Set(["quota_low", "breaker_open", "midsize_saturated", "midsize_unavailable", "deadline", "explain_guard:degenerate", "explain_guard:empty"]);
const WAIT_REASONS = { service_busy: "deg.busy", quota_exhausted: "deg.quota", service_unavailable: "deg.unavailable", room_busy: "deg.room" };
const NOTICE = { ungrounded_formula: "deg.check", wrong_math: "deg.wrong", low_confidence: "deg.lowconf", invalid_image: "deg.invalidimage" };

/** True when a result is worth retrying after its ETA (nothing useful came back). */
export const isWaitResult = (r) => Boolean(r?.degraded && WAIT_REASONS[r.degrade_reason]);

export function Degraded({ result, onRetry, onFull }) {
  const { t } = useI18n();
  const left = useCountdown(result?.degraded && WAIT_REASONS[result.degrade_reason] ? result.eta_seconds || 10 : 0);
  if (!result?.degraded) return null;
  const reason = result.degrade_reason || "";
  if (reason === "superseded" || reason.startsWith("hint_guard")) return null;
  if (FAST_REASONS.has(reason) || result.mode === "fast_extractive") {
    return (
      <div className="ai-notice fast">
        <Icon name="bolt" size={14} />
        <span>{t("deg.fast")}</span>
        {result.followup_available && onFull && <button className="chip d-chip" onClick={onFull}>{t("deg.full")}</button>}
      </div>
    );
  }
  if (WAIT_REASONS[reason]) {
    return (
      <div className="ai-notice wait">
        <Icon name="timer" size={14} />
        <span>{t(WAIT_REASONS[reason], { s: left })}</span>
        {onRetry && <button className="chip d-chip" disabled={left > 0} onClick={onRetry}>{t("deg.retry")}</button>}
      </div>
    );
  }
  return (
    <div className={"ai-notice" + (reason === "wrong_math" ? " bad" : "")}>
      <Icon name="shield" size={14} />
      <span>{t(NOTICE[reason] || "deg.generic")}</span>
    </div>
  );
}

/**
 * The chunks an answer is grounded in (`notes.sources`, ids). Course pages look like `derivee-formules.fr#produit-et-quotient`,
 * a professor's upload like `professor/<owner>/<file>#<section>` and a learner's own like `learner/<owner>/<file>#<section>`.
 */
export function sourceChips(notes) {
  const ids = Array.isArray(notes?.sources) ? notes.sources : [];
  return ids.map((id) => {
    const [file, section = ""] = String(id).split("#");
    const professor = String(id).startsWith("professor/");
    return { id, professor, title: (section || file.split("/").pop()).replace(/[-_]+/g, " ") };
  });
}

export function Sources({ notes }) {
  const { t } = useI18n();
  const list = sourceChips(notes);
  if (!list.length) return null;
  return (
    <div className="ai-sources">
      <small>{t("ai.sources")}</small>
      {list.map((s) => (
        <span key={s.id} className={"ai-source" + (s.professor ? " prof" : "")} title={s.id}>
          {s.professor && <Icon name="shield" size={11} />}
          {s.title}
        </span>
      ))}
    </div>
  );
}

export function ResultMeta({ result }) {
  const { t } = useI18n();
  if (!result) return null;
  return (
    <span className="ai-meta">
      {result.from_cache && <em>{t("ai.cached")}</em>}
      {!result.from_cache && !result.model_class_used && !result.degraded && <em>{t("ai.rules")}</em>}
    </span>
  );
}

export function ReportButton({ requestId }) {
  const { t } = useI18n();
  const [done, setDone] = useState(false);
  if (!requestId) return null;
  return (
    <button className="ai-report" disabled={done} onClick={() => ai.report(requestId).then(() => setDone(true)).catch(() => {})} aria-label={t("ai.report")} title={t("ai.report")}>
      <Icon name="shield" size={12} /> {done ? t("ai.reported") : t("ai.report")}
    </button>
  );
}

export function errorText(t, error) {
  return error?.message === "offline" ? t("deg.offline") : error?.message || t("deg.generic");
}

// ---------- exercises ----------
/**
 * One bank exercise: answer, graded hints and "show solution". Grading runs on
 * the server (the solution never reaches the browser until the learner gives up).
 */
export function ExerciseCard({ exercise, onGraded, context }) {
  const { t } = useI18n();
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(null);
  const [hints, setHints] = useState([]);
  const [hintsTotal, setHintsTotal] = useState(3);
  const [grade, setGrade] = useState(null);
  const [solution, setSolution] = useState(null);
  const [mastery, setMastery] = useState(null);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  async function act(action, extra) {
    setBusy(action);
    setError(null);
    try {
      const out = await ai.exercise(exercise.id, action, extra, { context });
      if (action === "hint") {
        if (out.result?.text) setHints((h) => [...h, out.result.text]);
        if (out.result?.data?.hints_total) setHintsTotal(out.result.data.hints_total);
      } else if (action === "answer") {
        setGrade(out.result);
        if (out.mastery) setMastery(out.mastery);
        onGraded?.(out.result, out.mastery);
      } else {
        setSolution(out.result?.data?.solution || "—");
        onGraded?.({ data: { verdict: "INCORRECT", exercise_id: exercise.id } });
      }
    } catch (e) {
      setError(errorText(t, e));
    } finally {
      setBusy(null);
    }
  }

  const verdict = grade?.data?.verdict;
  const solved = verdict === "CORRECT" || solution;
  return (
    <div className={"ai-exercise" + (verdict ? ` v-${verdict.toLowerCase()}` : "")}>
      <div className="ai-ex-head">
        <span><Icon name="exam" size={14} /> {t("ex.label")}</span>
        <small>{t("ex.level", { n: exercise.level || 1 })}</small>
      </div>
      <p className="ai-ex-statement" dir="auto">{exercise.statement}</p>
      {hints.map((h, i) => (
        <p key={i} className="ai-ex-hint" dir="auto"><b>{t("ex.hintN", { n: i + 1, total: hintsTotal })}</b> {h}</p>
      ))}
      {!solved && (
        <form
          className="ai-ex-form"
          onSubmit={(e) => {
            e.preventDefault();
            if (answer.trim()) act("answer", { attempt: answer, hints_used: hints.length });
          }}
        >
          <input className="input" ref={inputRef} value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder={t("ex.placeholder")} aria-label={t("ex.answer")} dir="ltr" maxLength={200} />
          <button className="btn btn-primary small" disabled={!answer.trim() || busy}>{busy === "answer" ? "…" : t("ex.check")}</button>
        </form>
      )}
      {verdict && (
        <div className={`ai-ex-verdict v-${verdict.toLowerCase()}`} role="status">
          <b>{t(`ex.${verdict}`)}</b>
          {grade.data.feedback && <span dir="auto">{grade.data.feedback}</span>}
          {mastery && <small>{t("ex.mastery", { p: Math.round(mastery.value * 100) })}</small>}
          {verdict === "CORRECT" && mastery && masteryLevel(mastery.value) === "mastered" && (
            <span className="chip chip-mastered lumi-burst"><Icon name="check" size={13} /> {t("mastery.mastered")}</span>
          )}
        </div>
      )}
      {solution && <div className="ai-ex-verdict v-solution"><b>{t("ex.solution", { s: solution })}</b></div>}
      {error && <div className="ai-notice bad"><span>{error}</span></div>}
      {!solved && (
        <div className="ai-ex-actions">
          <button className="chip d-chip" disabled={Boolean(busy) || hints.length >= hintsTotal} onClick={() => act("hint", { level: hints.length + 1 })}>
            <Icon name="help" size={13} /> {busy === "hint" ? "…" : t("ex.hint")}
          </button>
          {(hints.length >= 2 || verdict === "INCORRECT") && (
            <button className="chip d-chip" disabled={Boolean(busy)} onClick={() => act("reveal")}>{t("ex.giveUp")}</button>
          )}
        </div>
      )}
    </div>
  );
}
