// Assessment Prep: a list of exams, one exam's plan, and a study page for one plan item.
// Exams, plans and ticks live in this browser (lib/exams.js); every engine call is the one this page always made.
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Icon from "../Icons";
import { useI18n } from "../../lib/i18n";
import { ai, ENGINE_COURSE, engineLanguage, replyLanguage } from "../../lib/engine";
import { courseMastery, masteryLevel, MASTERY } from "../../lib/demoData";
import { refreshCourseMap, useCourses } from "../../lib/courseMap";
import { readStored, store } from "../../lib/identity";
import { addExam, daysLeft, getExam, inDays, listExams, removeExam, updateExam } from "../../lib/exams";
import { AnswerText, Degraded, ExerciseCard, Progress, errorText, useProgress } from "../ai/AIBits";
import "./prep.css";

const LEVEL_DONE_KEY = "ednova-prep-level-done";
const FINAL = "final"; // the final assessment's key among an exam's ticks
const pct = (v) => Math.round(v * 100);

/** Context for prep calls: the learner's reply language, else the UI language. */
function usePrepContext() {
  const { lang } = useI18n();
  const pref = replyLanguage();
  return { language: pref === "auto" ? engineLanguage(lang) : pref };
}

/** Runs one engine task with staged progress and keeps its result. */
function useTask() {
  const { t } = useI18n();
  const [progress, onEvent, reset] = useProgress();
  const [state, setState] = useState({ busy: false, result: null, error: null });
  async function run(task, inputs, options = {}) {
    reset();
    setState((s) => ({ ...s, busy: true, error: null }));
    try {
      const result = await ai.run(task, inputs, { ...options, onEvent });
      setState({ busy: false, result, error: null });
      return result;
    } catch (error) {
      setState({ busy: false, result: null, error: errorText(t, error) });
      return null;
    }
  }
  return { ...state, progress, run };
}

function useDates() {
  const { t, lang } = useI18n();
  const format = new Intl.DateTimeFormat(lang === "ar" ? "ar-TN" : lang, { day: "numeric", month: "short", year: "numeric" });
  return {
    date: (iso) => {
      const [y, m, d] = iso.split("-").map(Number);
      return format.format(new Date(y, m - 1, d));
    },
    left: (iso) => {
      const n = daysLeft(iso);
      return n < 0 ? t("prep.passed") : n === 0 ? t("prep.today") : n === 1 ? t("prep.tomorrow") : t("prep.inDays", { n });
    }
  };
}

function BackLink({ onClick, children }) {
  return <button className="pn-back" onClick={onClick}><span className="pn-arrow" aria-hidden="true">←</span> {children}</button>;
}

function Status({ task, onRetry }) {
  return (
    <>
      {task.busy && <Progress state={task.progress} />}
      {task.error && <div className="ai-notice bad"><span>{task.error}</span></div>}
      <Degraded result={task.result} onRetry={onRetry} />
    </>
  );
}

// ---------- prep home ----------
function NewExam({ onAdd }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(null);
  if (!draft) return <button className="pn-add" onClick={() => setDraft({ title: "", date: inDays(7) })}><Icon name="plus" size={15} /> {t("prep.newExam")}</button>;
  const ready = draft.title.trim() && draft.date;
  return (
    <form className="pn-new" onSubmit={(e) => { e.preventDefault(); if (ready) onAdd(draft); }}>
      <input className="input" autoFocus dir="auto" maxLength={80} placeholder={t("prep.examTitle")} aria-label={t("prep.examTitle")} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
      <input className="input" type="date" dir="ltr" aria-label={t("prep.examDate")} value={draft.date} onChange={(e) => setDraft({ ...draft, date: e.target.value })} />
      <button className="btn btn-primary small" disabled={!ready}>{t("prep.create")}</button>
      <button type="button" className="pn-quiet" onClick={() => setDraft(null)}>{t("prep.cancel")}</button>
    </form>
  );
}

function PrepHome({ readiness, onOpen }) {
  const { t } = useI18n();
  const dates = useDates();
  const [exams, setExams] = useState(listExams);
  const [armed, setArmed] = useState(null); // the exam whose delete was clicked once
  const add = (draft) => onOpen(addExam(draft).id);
  const remove = (id) => {
    removeExam(id);
    setExams(listExams());
    setArmed(null);
  };
  return (
    <div className="pn">
      <h1>{t("prep.title")}</h1>
      <p className="pn-lead">{t("prep.sub")}</p>
      {exams.length > 0 ? (
        <ul className="pn-exams">
          {exams.map((exam) => (
            <li key={exam.id} onMouseLeave={() => setArmed(null)}>
              <button className="pn-exam" onClick={() => onOpen(exam.id)}>
                <span className="pn-exam-title" dir="auto">{exam.title}</span>
                <span>{dates.date(exam.date)}</span>
                <span>{dates.left(exam.date)}</span>
                <span className="pn-num">{pct(readiness)}%</span>
              </button>
              <button className="pn-reveal" aria-label={t("prep.delete")} title={t("prep.delete")} onClick={() => (armed === exam.id ? remove(exam.id) : setArmed(exam.id))}>
                {armed === exam.id ? t("prep.deleteConfirm") : <Icon name="trash" size={15} />}
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="pn-empty">{t("prep.noExam")}</p>}
      <NewExam onAdd={add} />
    </div>
  );
}

// ---------- exam page ----------
const itemKey = (day, concept) => `${day}:${concept}`;

function ExamPage({ examId, readiness, conceptName, masteryOf, context, onBack, onStudy, onTest, levelDone }) {
  const { t } = useI18n();
  const dates = useDates();
  const [exam, setExam] = useState(() => getExam(examId));
  const plan = useTask();
  const patch = (change) => setExam(updateExam(examId, change));

  async function buildPlan(from = exam) {
    const result = await plan.run("prep_plan", { course_id: ENGINE_COURSE, days_left: Math.max(1, daysLeft(from.date)), minutes_per_day: from.minutes }, { context });
    if (result?.data?.sessions) patch({ plan: { sessions: result.data.sessions } });
  }
  useEffect(() => {
    if (!exam.plan) buildPlan();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sessions = exam.plan?.sessions || [];
  const toggle = (key) => patch({ done: { ...exam.done, [key]: !exam.done[key] } });
  const all = sessions.flatMap((s) => s.items.map((item) => itemKey(s.day, item.concept)));
  const active = [...all, FINAL].find((key) => !exam.done[key]);

  return (
    <div className="pn">
      <BackLink onClick={onBack}>{t("prep.title")}</BackLink>
      <h1 dir="auto">{exam.title}</h1>
      <p className="pn-props">
        <input type="date" dir="ltr" aria-label={t("prep.examDate")} value={exam.date} onChange={(e) => e.target.value && patch({ date: e.target.value })} />
        <span>{dates.left(exam.date)}</span>
        <label><input type="number" min={15} max={180} step={5} aria-label={t("prep.minutes")} value={exam.minutes} onChange={(e) => patch({ minutes: Number(e.target.value) || 15 })} /> {t("prep.perDay")}</label>
        <span>{t("prep.readiness")} {pct(readiness)}%</span>
      </p>

      {!levelDone && (
        <aside className="pn-callout">
          <div>
            <b>{t("prep.diag")}</b>
            <p>{t("prep.diagSub")}</p>
          </div>
          <button className="btn btn-primary small" onClick={() => onTest("level", exam)}>{t("prep.startDiag")}</button>
        </aside>
      )}

      <div className="pn-head">
        <h2>{t("prep.plan")}</h2>
        <button className="pn-reveal text" disabled={plan.busy} onClick={() => buildPlan()}>{t("prep.rebuild")}</button>
      </div>
      <p className="pn-lead small">{t("prep.planSub")}</p>
      <Status task={plan} onRetry={() => buildPlan()} />

      <div className="pn-days">
        {sessions.map((s) => {
          const keys = s.items.map((item) => itemKey(s.day, item.concept));
          const ticked = keys.filter((k) => exam.done[k]).length;
          return (
            <details key={s.day} open={keys.includes(active)}>
              <summary>
                <span className="pn-chevron" aria-hidden="true" />
                <b>{t("prep.day", { n: s.day })}</b>
                <small>{t("prep.min", { n: s.minutes })}</small>
                <small className="pn-count">{ticked}/{keys.length}</small>
              </summary>
              <ul className="pn-items">
                {s.items.map((item) => {
                  const key = itemKey(s.day, item.concept);
                  const level = masteryLevel(masteryOf(item.concept));
                  return (
                    <li key={key} className={(key === active ? "active " : "") + (exam.done[key] ? "done" : "")}>
                      <label className="pn-tick"><input type="checkbox" checked={Boolean(exam.done[key])} onChange={() => toggle(key)} aria-label={conceptName(item.concept)} /></label>
                      <button className="pn-item" onClick={() => onStudy(item.concept)}>
                        <span dir="auto">{conceptName(item.concept) || item.title}</span>
                      </button>
                      <span className="pn-reveal label" aria-hidden="true">{t("prep.study")}</span>
                      <small>{t("prep.min", { n: item.minutes })}</small>
                      <i className={`pn-dot ${level}`} title={t(MASTERY[level].key)} />
                    </li>
                  );
                })}
              </ul>
            </details>
          );
        })}
      </div>

      {sessions.length > 0 && (
        <ul className="pn-items pn-final">
          <li className={(active === FINAL ? "active " : "") + (exam.done[FINAL] ? "done" : "")}>
            <label className="pn-tick"><input type="checkbox" checked={Boolean(exam.done[FINAL])} onChange={() => toggle(FINAL)} aria-label={t("prep.final")} /></label>
            <button className="pn-item" onClick={() => onTest("final", exam)}><span>{t("prep.final")}</span></button>
            <span className="pn-reveal label" aria-hidden="true">{t("prep.study")}</span>
          </li>
        </ul>
      )}
    </div>
  );
}

// ---------- one exercise at a time ----------
function Practice({ items, setItems, concept, context, onGraded, onFinish }) {
  const { t } = useI18n();
  const more = useTask();
  const [index, setIndex] = useState(0);
  const [ended, setEnded] = useState(false);
  const current = items[index];

  async function next() {
    if (index + 1 < items.length) return setIndex(index + 1);
    if (!concept) return onFinish?.();
    const result = await more.run("quiz", { concept, exclude: items.map((e) => e.id), intro: false }, { context });
    const fresh = (result?.data?.exercises || []).find((e) => !items.some((x) => x.id === e.id));
    if (!fresh) return setEnded(true);
    setItems([...items, fresh]);
    setIndex(items.length);
  }

  if (!current) return null;
  const last = !concept && index + 1 >= items.length;
  return (
    <div className="pn-practice">
      <ExerciseCard key={current.id} exercise={current} context={context} onGraded={onGraded} />
      {more.busy && <Progress state={more.progress} />}
      {more.error && <div className="ai-notice bad"><span>{more.error}</span></div>}
      {ended ? <p className="pn-lead small">{t("prep.noMore")}</p> : (
        <div className="pn-next">
          {!concept && <small>{index + 1} / {items.length}</small>}
          <button className="btn btn-secondary small" disabled={more.busy} onClick={next}>{t(last ? "prep.finish" : "prep.next")}</button>
        </div>
      )}
    </div>
  );
}

// ---------- study page ----------
const answerOf = (result) => {
  const data = result?.data || {};
  if (data.body) return [data.opener, data.body].filter(Boolean).join("\n\n");
  if (data.excerpts) return [data.framing, data.excerpts.map((e) => `• ${e.text}`).join("\n")].filter(Boolean).join("\n\n");
  return result?.text;
};

/** The selected text inside `box`, with where to put the button, or null. */
function selectionIn(box) {
  const selection = window.getSelection();
  const text = selection?.toString().trim();
  if (!selection || selection.isCollapsed || !text || text.length < 3 || !box.contains(selection.anchorNode) || !box.contains(selection.focusNode)) return null;
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  const outer = box.getBoundingClientRect();
  return { text: text.slice(0, 600), top: rect.top - outer.top - 40, center: rect.left + rect.width / 2 - outer.left };
}

function StudyPage({ concept, title, context, onBack, onGraded }) {
  const { t } = useI18n();
  const page = useTask();
  const confused = useTask();
  const [items, setItems] = useState([]);
  const [selection, setSelection] = useState(null);
  const [asked, setAsked] = useState(null);
  const boxRef = useRef(null);

  async function load() {
    const result = await page.run("generate_page", { concept }, { priority: "BACKGROUND", context });
    setItems(result?.data?.quiz || []);
  }
  useEffect(() => {
    load();
  }, [concept]); // eslint-disable-line react-hooks/exhaustive-deps

  const askAgain = (text) => {
    setAsked(text);
    setSelection(null);
    window.getSelection()?.removeAllRanges();
    confused.run("explain", { question: `${t("prep.confusedQ")} « ${text} »`, passage: text }, { context: { ...context, confusion: "HIGH" } });
  };
  const readSelection = () => boxRef.current && setSelection(selectionIn(boxRef.current));

  const data = page.result?.data;
  return (
    <div className="pn">
      <BackLink onClick={onBack}>{t("prep.back")}</BackLink>
      <h1 dir="auto">{title || data?.title || t("prep.page")}</h1>
      <Status task={page} onRetry={load} />
      {data && (
        <>
          <div className="pn-explain" ref={boxRef} onMouseUp={readSelection} onKeyUp={readSelection} onTouchEnd={() => setTimeout(readSelection, 300)}>
            <AnswerText text={data.explanation} />
            {selection && (
              <button className="pn-confused" style={{ top: Math.max(0, selection.top), left: selection.center }} onMouseDown={(e) => e.preventDefault()} onClick={() => askAgain(selection.text)}>
                {t("prep.confused")}
              </button>
            )}
          </div>

          {asked && (
            <aside className="pn-callout column">
              <b>{t("prep.confusedTitle")}</b>
              <Status task={confused} onRetry={() => askAgain(asked)} />
              {!confused.busy && <AnswerText text={answerOf(confused.result)} />}
            </aside>
          )}

          {data.worked_example && (
            <details className="pn-toggle">
              <summary><span className="pn-chevron" aria-hidden="true" /><b>{t("prep.worked")}</b></summary>
              <div className="pn-worked">
                <p dir="auto">{data.worked_example.statement}</p>
                <ol dir="auto">{data.worked_example.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
                <p>{t("prep.answer")}: <b dir="ltr">{data.worked_example.answer}</b></p>
              </div>
            </details>
          )}

          {items.length > 0 && (
            <>
              <h2>{t("prep.practiceItems")}</h2>
              <Practice items={items} setItems={setItems} concept={concept} context={context} onGraded={onGraded} />
            </>
          )}
        </>
      )}
    </div>
  );
}

// ---------- level test and final assessment ----------
function TestPage({ kind, exam, context, masteryOf, onBack, onGraded, onDone }) {
  const { t } = useI18n();
  const task = useTask();
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const final = kind === "final";

  /** Final assessment: one bank exercise per concept of the plan, harder where mastery is higher. */
  async function loadFinal() {
    setLoading(true);
    const concepts = [...new Set((exam.plan?.sessions || []).flatMap((s) => s.items.map((i) => i.concept)))].slice(0, 6);
    const found = [];
    for (const concept of concepts) {
      const m = masteryOf(concept);
      const result = await task.run("quiz", { concept, difficulty: m < 0.4 ? 1 : m < 0.75 ? 2 : 3, exclude: found.map((e) => e.id), intro: false }, { context });
      const ex = result?.data?.exercises?.find((e) => !found.some((x) => x.id === e.id));
      if (ex) found.push(ex);
    }
    setItems(found);
    setLoading(false);
  }
  async function load() {
    if (final) return loadFinal();
    const result = await task.run("quiz", { diagnostic: true, intro: false }, { context });
    setItems(result?.data?.exercises || []);
  }
  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="pn">
      <BackLink onClick={onBack}>{t("prep.back")}</BackLink>
      <h1>{t(final ? "prep.final" : "prep.diag")}</h1>
      <p className="pn-lead">{t(final ? "prep.finalSub" : "prep.diagSub")}</p>
      {!loading && <Status task={task} onRetry={load} />}
      {loading && <Progress state={task.progress} />}
      {!loading && <Practice items={items} setItems={setItems} context={context} onGraded={onGraded} onFinish={onDone} />}
    </div>
  );
}

// ---------- the page ----------
export default function Prep() {
  const { lang } = useI18n();
  const context = usePrepContext();
  const courses = useCourses();
  const readiness = courseMastery(courses[0]);
  const [course, setCourse] = useState(null);
  const [view, setView] = useState({ name: "home" });
  const [levelDone, setLevelDone] = useState(() => readStored(LEVEL_DONE_KEY, "") === "1");

  useEffect(() => {
    ai.course().then(setCourse).catch(() => {});
  }, []);
  useLayoutEffect(() => {
    window.scrollTo(0, 0);
  }, [view]);

  const conceptName = (id) => {
    const title = course?.concepts?.[id]?.title;
    return title?.[lang] || title?.fr || courses[0].units.find((u) => u.id === id)?.name || id;
  };
  const masteryOf = (id) => courses[0].units.find((u) => u.id === id)?.mastery ?? 0;
  const toExam = (id) => setView({ name: "exam", id });
  const toHome = () => setView({ name: "home" });
  const graded = () => refreshCourseMap();

  if (view.name === "study")
    return <StudyPage key={view.concept} concept={view.concept} title={conceptName(view.concept)} context={context} onBack={() => toExam(view.id)} onGraded={graded} />;

  if (view.name === "test")
    return (
      <TestPage kind={view.kind} exam={getExam(view.id)} context={context} masteryOf={masteryOf} onBack={() => toExam(view.id)} onGraded={graded}
        onDone={() => {
          if (view.kind === "level") {
            store(LEVEL_DONE_KEY, "1");
            setLevelDone(true);
          } else {
            updateExam(view.id, { done: { ...getExam(view.id).done, [FINAL]: true } });
          }
          toExam(view.id);
        }} />
    );

  if (view.name === "exam")
    return (
      <ExamPage key={view.id} examId={view.id} readiness={readiness} conceptName={conceptName} masteryOf={masteryOf} context={context} levelDone={levelDone} onBack={toHome}
        onStudy={(concept) => setView({ name: "study", id: view.id, concept })}
        onTest={(kind, exam) => setView({ name: "test", id: exam.id, kind })} />
    );

  return <PrepHome readiness={readiness} onOpen={toExam} />;
}
