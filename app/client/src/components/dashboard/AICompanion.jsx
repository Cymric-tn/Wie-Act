import { QUESTIONS, getStyle, saveStyle, scoreStyle } from "../../lib/learningStyle";
import { useEffect, useRef, useState } from "react";
import Icon from "../Icons";
import { useI18n } from "../../lib/i18n";
import { ai, AI_LANGUAGES, replyLanguage, setReplyLanguage } from "../../lib/engine";
import { LumiSpark } from "../LumiSpark";
import { masteryLevel } from "../../lib/demoData";
import {
  AnswerText, Degraded, EngineBadge, ExerciseCard, Progress, ReportButton, ResultMeta, Sources,
  errorText, useAiStatus, useProgress
} from "../ai/AIBits";

let seq = 0;
const nextId = () => `m${Date.now().toString(36)}${(seq++).toString(36)}`;

/** The body of one AI reply, by the task that produced it. */
function Reply({ msg, onRetry, onFull, onGraded }) {
  const { t } = useI18n();
  const { follow, result, classification } = msg;
  const task = follow?.task;
  const data = result?.data || {};

  let body;
  if (!task) {
    body = <AnswerText reveal text={t(classification?.data?.intent === "OFF_TOPIC" ? "ai.offtopic" : "ai.chitchat")} />;
  } else if (task === "explain" && data.body) {
    body = <AnswerText reveal text={`${data.opener || ""}\n\n${data.body}`} />;
  } else if (task === "explain" && data.excerpts) {
    body = <AnswerText reveal text={[data.opener, data.framing, data.excerpts.map((e) => `• ${e.text}`).join("\n")].filter(Boolean).join("\n\n")} />;
  } else if (task === "quiz") {
    body = data.intro ? <AnswerText reveal text={data.intro} /> : null;
  } else if (task === "grade") {
    body = (
      <div className={`ai-ex-verdict v-${String(data.verdict || "unknown").toLowerCase()}`}>
        <b>{t(`ex.${data.verdict || "UNKNOWN"}`)}</b>
        {data.feedback && <span dir="auto">{data.feedback}</span>}
      </div>
    );
  } else if (task === "hint" && result?.text) {
    body = (
      <p className="ai-ex-hint" dir="auto">
        <b>{data.hints_total ? t("ex.hintN", { n: data.hint_level, total: data.hints_total }) : t("ex.hint")}</b> {result.text}
      </p>
    );
  } else {
    body = <AnswerText reveal text={result?.text} />;
  }

  const exercises = task === "quiz" ? data.exercises || [] : data.exercise ? [data.exercise] : [];
  return (
    <>
      {data.not_found && <span className="ai-scope">{t("ai.notFound")}</span>}
      {body}
      <Degraded result={result} onRetry={onRetry} onFull={onFull} />
      {exercises.map((ex) => <ExerciseCard key={ex.id} exercise={ex} onGraded={onGraded} />)}
      <Sources notes={result?.notes} />
      {result?.request_id && (task === "explain" || task === "hint") && (
        <div className="ai-verdict-line"><ReportButton requestId={result.request_id} /><ResultMeta result={result} /></div>
      )}
    </>
  );
}

/**
 * The always-available AI study peer. Each message is one tutor turn on the
 * engine: classify → follow-up task (explain, hint, quiz, or grade when it
 * answers the open exercise).
 */
/**
 * The diagnostic step: four questions the first time the companion opens (never again once answered or skipped).
 * The result is sent with every later request as the engine's `style`.
 */
export function StyleDiagnostic({ force = false, onDone }) {
  const { t } = useI18n();
  const [state, setState] = useState(() => (force || getStyle() === null ? { step: 0, answers: {} } : null));
  const [result, setResult] = useState(null);
  if (result) return <div className="ai-msg ai">{t("style.result", { style: t(`style.${result}`) })}</div>;
  if (!state) return null;
  const q = QUESTIONS[state.step];
  const finish = (style) => {
    saveStyle(style);
    setState(null);
    if (style) setResult(style);
    onDone?.(style);
  };
  const answer = (option) => {
    const answers = { ...state.answers, [q.id]: option };
    if (state.step + 1 < QUESTIONS.length) setState({ step: state.step + 1, answers });
    else finish(scoreStyle(answers));
  };
  return (
    <div className="ai-msg ai wide ai-style">
      {state.step === 0 && <p>{t("style.intro")}</p>}
      <small>{t("style.step", { n: state.step + 1, total: QUESTIONS.length })}</small>
      <b>{t(`style.${q.id}`)}</b>
      <div className="ai-style-options">
        {Object.keys(q.options).map((o) => <button key={o} className="chip d-chip" onClick={() => answer(o)}>{t(`style.${q.id}.${o}`)}</button>)}
      </div>
      <button className="link-btn" onClick={() => finish(null)}>{t("style.skip")}</button>
    </div>
  );
}

export default function AICompanion({ open, onClose, user, seed }) {
  const { t, lang } = useI18n();
  const status = useAiStatus();
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [replyLang, setReplyLang] = useState(replyLanguage);
  const [progress, onEvent, resetProgress] = useProgress();
  const [justMastered, setJustMastered] = useState(false);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const handledSeed = useRef(null);
  const turnRef = useRef(0);
  const served = useRef([]);
  const openExercise = useRef(null);
  const history = useRef([]);
  const lastLanguage = useRef(null); // a bare answer like "9" has no language of its own

  const push = (m) => setMessages((list) => [...list, { id: nextId(), ...m }]);

  function trackExercises(out) {
    const data = out.result?.data || {};
    const list = out.follow?.task === "quiz" ? data.exercises || [] : data.exercise ? [data.exercise] : [];
    list.forEach((ex) => served.current.includes(ex.id) || served.current.push(ex.id));
    if (list[0]) openExercise.current = { id: list[0].id, hints: 0 };
    if (out.follow?.task === "hint" && openExercise.current && data.source === "bank") openExercise.current.hints = data.hint_level || 1;
    if (out.follow?.task === "grade" && data.verdict === "CORRECT") openExercise.current = null;
  }

  async function send(text, { echo = true } = {}) {
    const value = text.trim();
    if (!value) return;
    if (echo) push({ from: "me", text: value });
    setDraft("");
    const turn = ++turnRef.current;
    setBusy(true);
    resetProgress();
    try {
      const out = await ai.turn(value, {
        history: history.current.slice(-6),
        exclude: served.current,
        exerciseId: openExercise.current?.id,
        hintsUsed: openExercise.current?.hints,
        context: openExercise.current && replyLang === "auto" && lastLanguage.current ? { language: lastLanguage.current } : undefined,
        onEvent: (event, stage) => turn === turnRef.current && onEvent(event, stage)
      });
      if (turn !== turnRef.current || out.superseded || out.result?.degrade_reason === "superseded") return; // a newer message replaced this one
      trackExercises(out);
      const detected = out.classification?.data;
      if (detected?.language && detected.intent !== "ANSWER_ATTEMPT") lastLanguage.current = detected.language;
      history.current.push({ role: "user", content: value });
      if (out.result?.text) history.current.push({ role: "assistant", content: out.result.text.slice(0, 900) });
      push({ from: "ai", kind: "turn", question: value, ...out });
    } catch (error) {
      if (turn === turnRef.current) push({ from: "ai", kind: "error", text: errorText(t, error), question: value });
    } finally {
      if (turn === turnRef.current) setBusy(false);
    }
  }

  async function fullExplanation(msg) {
    setBusy(true);
    resetProgress();
    const turn = ++turnRef.current;
    try {
      const result = await ai.run("explain", { question: msg.question, exclude_exercises: served.current }, {
        context: { escalate: true, confusion: msg.classification?.data?.confusion, language: replyLang === "auto" ? msg.classification?.data?.language : replyLang },
        onEvent
      });
      if (turn !== turnRef.current) return;
      push({ from: "ai", kind: "turn", question: msg.question, follow: { task: "explain" }, classification: msg.classification, result });
    } catch (error) {
      push({ from: "ai", kind: "error", text: errorText(t, error), question: msg.question });
    } finally {
      if (turn === turnRef.current) setBusy(false);
    }
  }

  function newChat() {
    turnRef.current++;
    setBusy(false);
    setMessages([]);
    served.current = [];
    openExercise.current = null;
    history.current = [];
    lastLanguage.current = null;
  }

  // A suggestion clicked elsewhere on the dashboard arrives as a seed message.
  useEffect(() => {
    if (open && seed && handledSeed.current !== seed.id) {
      handledSeed.current = seed.id;
      send(seed.text);
    }
  }, [open, seed]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 200);
    const onKey = (e) => e.key === "Escape" && open && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, progress, busy]);

  // The companion turns gold for a moment when an answer brings a concept to mastered.
  useEffect(() => {
    if (!justMastered) return undefined;
    const id = setTimeout(() => setJustMastered(false), 2400);
    return () => clearTimeout(id);
  }, [justMastered]);

  const [styleAsked, setStyleAsked] = useState(() => getStyle() !== null); // the suggestions wait for the diagnostic
  const langLabel = (id) => (id === "auto" ? t("lang.auto") : t(`lang.${id}`));

  return (
    <>
      <div className={"ai-scrim" + (open ? " open" : "")} onClick={onClose} aria-hidden="true" />
      <aside className={"ai-panel lumi-dark glass-ai" + (open ? " open" : "")} aria-label={t("ai.title")} aria-hidden={!open} inert={!open}>
        <header className="ai-head">
          <LumiSpark size={44} halo={96} state={justMastered ? "mastered" : busy ? "thinking" : "rest"} label={t("ai.title")} />
          <div>
            <h2>{t("ai.title")}</h2>
            <p>{t("ai.scope")}</p>
          </div>
          <button className="d-icon-btn" onClick={onClose} aria-label={t("common.close")}><Icon name="close" size={18} /></button>
        </header>

        <div className="ai-tools">
          <label>
            {t("ai.replyIn")}{" "}
            <select className="input" value={replyLang} onChange={(e) => { setReplyLang(e.target.value); setReplyLanguage(e.target.value); }}>
              {AI_LANGUAGES.map((id) => <option key={id} value={id}>{langLabel(id)}</option>)}
            </select>
          </label>
          {messages.length > 0 && <button className="chip d-chip" onClick={newChat}>{t("ai.newChat")}</button>}
          <EngineBadge status={status} />
        </div>

        <div className="ai-messages" ref={listRef} aria-live="polite">
          <div className="ai-msg ai">{t("ai.hello", { name: user.name.split(" ")[0] })}</div>
          {messages.length === 0 && <StyleDiagnostic onDone={() => setStyleAsked(true)} />}
          {messages.map((m) => (
            m.from === "me"
              ? <div key={m.id} className="ai-msg me" dir="auto">{m.text}</div>
              : (
                <div key={m.id} className={"ai-msg ai" + (m.kind === "turn" ? " wide" : "")}>
                  {m.kind === "error"
                    ? (
                      <div className="ai-notice bad">
                        <Icon name="bolt" size={14} /><span>{m.text}</span>
                        <button className="chip d-chip" onClick={() => send(m.question, { echo: false })}>{t("deg.retry")}</button>
                      </div>
                    )
                    : (
                      <Reply
                        msg={m}
                        onRetry={() => send(m.question, { echo: false })}
                        onFull={() => fullExplanation(m)}
                        onGraded={(r, mastery) => {
                          if (r?.data?.verdict === "CORRECT" && openExercise.current?.id === r.data.exercise_id) openExercise.current = null;
                          if (r?.data?.verdict === "CORRECT" && mastery && masteryLevel(mastery.value) === "mastered") setJustMastered(true);
                        }}
                      />
                    )}
                </div>
              )
          ))}
          {busy && <div className="ai-msg pending"><Progress state={progress} /></div>}
        </div>

        {messages.length === 0 && styleAsked && (
          <div className="ai-suggest">
            {["ai.s1", "ai.s2", "ai.s3", "ai.s4"].map((k) => (
              <button key={k} className="chip d-chip" onClick={() => send(t(k))} dir="auto">{t(k)}</button>
            ))}
          </div>
        )}

        <form
          className="ai-input"
          onSubmit={(e) => {
            e.preventDefault();
            send(draft);
          }}
        >
          <input className="input"
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={t("ai.placeholder")}
            aria-label={t("ai.placeholder")}
            dir="auto"
            lang={lang}
            maxLength={2000}
          />
          <button className="btn btn-primary icon" type="submit" disabled={!draft.trim()} aria-label={t("common.send")}>
            <Icon name="send" size={17} />
          </button>
        </form>
      </aside>
    </>
  );
}
