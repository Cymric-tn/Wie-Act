import { useEffect, useState } from "react";
import Icon from "../Icons";
import { Card, Meter } from "./Widgets";
import { useI18n } from "../../lib/i18n";
import { ai, ENGINE_COURSE, engineLanguage, replyLanguage } from "../../lib/engine";
import { COURSE_COLOR, COURSE_ID, EXAMS } from "../../lib/demoData";
import { refreshCourseMap } from "../../lib/courseMap";
import { AnswerText, Degraded, ExerciseCard, Progress, errorText, useProgress } from "../ai/AIBits";

const courseById = (id) => (id === COURSE_ID ? { color: COURSE_COLOR } : null);
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

function MasteryCard({ course, version }) {
  const { t, lang } = useI18n();
  const [mastery, setMastery] = useState({});
  useEffect(() => {
    ai.mastery().then((r) => setMastery(r.mastery)).catch(() => {});
  }, [version]);
  const l = lang === "ar" ? "ar" : lang;
  return (
    <Card icon="chart" title={t("prep.mastery")} sub={t("prep.masterySub")} className="span-5 d-card-top">
      {!course ? <Progress state={{}} /> : (
        <div className="p-units">
          {course.units.map((unit) => (
            <div key={unit.id} className="p-unit">
              <h3>{unit.title[l] || unit.title.fr}</h3>
              <ul className="p-concepts">
                {unit.concepts.map((id) => {
                  const m = mastery[id];
                  return (
                    <li key={id}>
                      <span>{course.concepts[id].title[l] || course.concepts[id].title.fr}</span>
                      <Meter value={m ? m.value : 0} />
                      <small>{m ? `${pct(m.value)}% · ${t("prep.attempts", { n: m.attempts })}` : t("prep.noAttempts")}</small>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function StudyPage({ concept, context, onClose, onGraded }) {
  const { t } = useI18n();
  const page = useTask();
  useEffect(() => {
    page.run("generate_page", { concept }, { priority: "BACKGROUND", context });
  }, [concept]); // eslint-disable-line react-hooks/exhaustive-deps
  const data = page.result?.data;
  return (
    <Card icon="book" title={data?.title || t("prep.page")} sub={t("prep.page")} className="span-12 p-study d-card-top"
      actions={<button className="d-icon-btn" onClick={onClose} aria-label={t("common.close")}><Icon name="close" size={17} /></button>}>
      {page.busy && <Progress state={page.progress} />}
      {page.error && <div className="ai-notice bad"><span>{page.error}</span></div>}
      <Degraded result={page.result} onRetry={() => page.run("generate_page", { concept }, { priority: "BACKGROUND", context })} />
      {data && (
        <div className="p-study-grid">
          <div>
            <AnswerText text={data.explanation} />
            {data.worked_example && (
              <div className="p-worked">
                <h3>{t("prep.worked")}</h3>
                <p dir="auto">{data.worked_example.statement}</p>
                <ol dir="auto">{data.worked_example.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
                <p className="p-worked-answer">{t("prep.answer")}: <b dir="ltr">{data.worked_example.answer}</b></p>
              </div>
            )}
          </div>
          <div>
            <h3 className="d-subhead">{t("prep.practiceItems")}</h3>
            {(data.quiz || []).map((ex) => <ExerciseCard key={ex.id} exercise={ex} context={context} onGraded={onGraded} />)}
          </div>
        </div>
      )}
    </Card>
  );
}

export default function Prep({ onToast }) {
  const { t, lang } = useI18n();
  const context = usePrepContext();
  const [course, setCourse] = useState(null);
  const [days, setDays] = useState(3);
  const [minutes, setMinutes] = useState(40);
  const [studying, setStudying] = useState(null);
  const [version, setVersion] = useState(0);
  const [practice, setPractice] = useState([]);
  const plan = useTask();
  const diag = useTask();
  const more = useTask();
  const bump = () => { setVersion((v) => v + 1); refreshCourseMap(); };
  const l = lang === "ar" ? "ar" : lang;

  useEffect(() => {
    ai.course().then(setCourse).catch(() => {});
  }, []);

  const buildPlan = (d = days) => plan.run("prep_plan", { course_id: ENGINE_COURSE, days_left: d, minutes_per_day: minutes }, { context });
  const exams = [{ title: `Quiz · ${course?.title?.[l] || "Analyse"}`, course: ENGINE_COURSE, inDays: 3, readiness: 0.52 }, ...EXAMS];

  async function anotherExercise() {
    const mastery = (await ai.mastery().catch(() => ({ mastery: {} }))).mastery;
    const ids = course ? Object.keys(course.concepts) : [];
    const weakest = ids.sort((a, b) => (mastery[a]?.value ?? 0) - (mastery[b]?.value ?? 0))[0];
    const result = await more.run("quiz", { concept: weakest, exclude: practice.map((e) => e.id), intro: false }, { context });
    const ex = result?.data?.exercises?.[0];
    if (ex) setPractice((p) => [ex, ...p.filter((x) => x.id !== ex.id)].slice(0, 4));
  }

  return (
    <div className="d-grid">
      <Card icon="exam" title={t("prep.title")} sub={t("prep.sub")} className="span-7 d-card-top">
        <ul className="d-exams">
          {exams.map((exam) => {
            const c = courseById(exam.course);
            const live = exam.course === ENGINE_COURSE;
            return (
              <li key={exam.title} style={{ "--c": c?.color }}>
                <div className="d-exam-date">
                  <b>{exam.inDays === 1 ? t("prep.tomorrow") : t("prep.inDays", { n: exam.inDays })}</b>
                  <small>{exam.course}</small>
                </div>
                <div className="d-exam-main">
                  <b>{exam.title}</b>
                  <span className="d-exam-ready">
                    <small>{t("prep.readiness")}</small>
                    <Meter value={exam.readiness} />
                    <small>{pct(exam.readiness)}%</small>
                  </span>
                </div>
                <button
                  className={"d-btn small " + (live ? "ai" : "ghost")}
                  onClick={() => {
                    if (!live) return onToast(t("prep.engineOnly", { course: `${ENGINE_COURSE} · ${course?.title?.[l] || "Analyse"}` }));
                    setDays(exam.inDays);
                    buildPlan(exam.inDays);
                  }}
                >
                  {live && <Icon name="sparkle" size={14} />} {t("prep.practice")}
                </button>
              </li>
            );
          })}
        </ul>
      </Card>

      <MasteryCard course={course} version={version} />

      {studying && <StudyPage key={studying} concept={studying} context={context} onClose={() => setStudying(null)} onGraded={bump} />}

      <Card icon="calendar" title={t("prep.plan")} sub={t("prep.planSub")} className="span-7 d-card-top">
        <form className="p-plan-form" onSubmit={(e) => { e.preventDefault(); buildPlan(); }}>
          <label className="d-select">
            <span>{t("prep.days")}</span>
            <input type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value) || 1)} />
          </label>
          <label className="d-select grow">
            <span>{t("prep.minutes")} · {minutes}</span>
            <input type="range" min={15} max={180} step={5} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} />
          </label>
          <button className="d-btn ai" disabled={plan.busy}><Icon name="sparkle" size={15} /> {t("prep.build")}</button>
        </form>
        {plan.busy && <Progress state={plan.progress} />}
        {plan.error && <div className="ai-notice bad"><span>{plan.error}</span></div>}
        {plan.result?.data?.sessions && (
          <ol className="p-sessions">
            {plan.result.data.sessions.map((s) => (
              <li key={s.day}>
                <div className="p-day"><b>{t("prep.day", { n: s.day })}</b><small>{t("prep.min", { n: s.minutes })}</small></div>
                <ul>
                  {s.items.map((item) => (
                    <li key={item.concept}>
                      <em className={`p-kind ${item.kind}`}>{t(item.kind === "review" ? "prep.review" : "prep.learn")}</em>
                      <span>{course?.concepts?.[item.concept]?.title?.[l] || item.title || item.concept}</span>
                      <small>{t("prep.min", { n: item.minutes })}</small>
                      <button className="d-btn ghost small" onClick={() => { setStudying(item.concept); window.scrollTo({ top: 0, behavior: "smooth" }); }}>{t("prep.study")}</button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        )}
      </Card>

      <Card icon="sparkle" title={t("prep.diag")} sub={t("prep.diagSub")} className="span-5 d-card-top">
        {!diag.result && (
          <button className="d-btn ai full" disabled={diag.busy} onClick={() => diag.run("quiz", { diagnostic: true, intro: false }, { context })}>
            <Icon name="sparkle" size={15} /> {t("prep.startDiag")}
          </button>
        )}
        {diag.busy && <Progress state={diag.progress} />}
        {diag.error && <div className="ai-notice bad"><span>{diag.error}</span></div>}
        {(diag.result?.data?.exercises || []).map((ex) => <ExerciseCard key={ex.id} exercise={ex} context={context} onGraded={bump} />)}
        <h3 className="d-subhead">{t("prep.generate")}</h3>
        {practice.map((ex) => <ExerciseCard key={ex.id} exercise={ex} context={context} onGraded={bump} />)}
        {more.busy && <Progress state={more.progress} />}
        <button className="d-btn ghost full p-more" disabled={more.busy || !course} onClick={anotherExercise}><Icon name="plus" size={15} /> {t("prep.more")}</button>
      </Card>
    </div>
  );
}
