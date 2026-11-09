import { useEffect, useState } from "react";
import Icon from "../Icons";
import { Card } from "./Widgets";
import { useI18n } from "../../lib/i18n";
import { ai } from "../../lib/engine";
import { AnswerText, EngineBadge, errorText, useAiStatus } from "../ai/AIBits";

const REASON_KEYS = new Set(["wrong_math", "ungrounded_formula", "learner_report", "input_truncated"]);

/** Which backend answers, and how today's runs went (from the server's run log). */
export function EngineStatusCard({ className = "span-5 d-card-top" }) {
  const { t } = useI18n();
  const status = useAiStatus(10000);
  const tasks = status?.stats?.tasks || [];
  const reasons = status?.stats?.reasons || [];
  return (
    <Card icon="bolt" title={t("engine.title")} className={className} actions={<EngineBadge status={status} />}>
      {status && !status.offline && (
        <dl className="r-engine">
          <div><dt>{t("engine.backend")}</dt><dd>{t(`engine.backend.${status.backend}`)}{status.version ? ` · ${status.version}` : ""}</dd></div>
          {status.backend === "fake" && <p className="r-hint">{t("engine.fakeHint")}</p>}
        </dl>
      )}
      {tasks.length === 0 ? <p className="d-empty">{t("engine.none")}</p> : (
        <div className="d-table-wrap">
          <table className="d-table r-stats">
            <thead>
              <tr><th>{t("engine.task")}</th><th>{t("engine.runs")}</th><th>{t("engine.cached")}</th><th>{t("engine.zero")}</th><th>{t("engine.degraded")}</th><th>{t("engine.avg")}</th></tr>
            </thead>
            <tbody>
              {tasks.map((row) => (
                <tr key={row.task}>
                  <td><code>{row.task}</code></td><td>{row.runs}</td><td>{row.cached}</td><td>{row.zero_request}</td>
                  <td className={row.degraded ? "warn" : ""}>{row.degraded}</td><td>{(row.avg_ms / 1000).toFixed(1)} s</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {reasons.length > 0 && (
        <>
          <h3 className="d-subhead">{t("engine.reasons")}</h3>
          <div className="r-reasons">{reasons.map((r) => <span key={r.reason} className="ai-source"><code>{r.reason}</code> × {r.n}</span>)}</div>
        </>
      )}
    </Card>
  );
}

function FlagItem({ flag, onDone }) {
  const { t, lang } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const answer = (() => {
    try {
      const parsed = JSON.parse(flag.answer);
      return typeof parsed === "object" ? JSON.stringify(parsed, null, 1) : flag.answer;
    } catch {
      return flag.answer;
    }
  })();
  const reasonLabel = (r) => t(REASON_KEYS.has(r) ? `review.reason.${r}` : "review.reason.other");
  const reasons = [...new Set(flag.reports.map((r) => r.reason))];
  async function resolve(action) {
    setBusy(true);
    try {
      await ai.resolve(flag.request_id, action);
      onDone(flag.request_id, action);
    } catch (e) {
      setError(errorText(t, e));
      setBusy(false);
    }
  }
  return (
    <li className="r-flag">
      <div className="r-flag-head">
        {reasons.map((r) => <span key={r} className={"r-reason " + r}>{reasonLabel(r)}</span>)}
        {flag.reports.length > 1 && <small>{t("review.reports", { n: flag.reports.length })}</small>}
        <code>{flag.task}</code>
        <small className="r-time">{new Date(flag.created_at).toLocaleString(lang === "ar" ? "ar-TN" : lang, { dateStyle: "short", timeStyle: "short" })}</small>
      </div>
      {flag.question && <div className="r-q"><small>{t("review.question")}</small><p dir="auto">{flag.question}</p></div>}
      {answer && <div className="r-a"><small>{t("review.answer")}</small><AnswerText text={answer} /></div>}
      {flag.reports.filter((r) => r.note).map((r, i) => <p key={i} className="r-note" dir="auto">“{r.note}”</p>)}
      {error && <div className="ai-notice bad"><span>{error}</span></div>}
      <div className="r-actions">
        <button className="btn btn-secondary small" disabled={busy} onClick={() => resolve("approve")}><Icon name="check" size={14} /> {t("review.approve")}</button>
        <button className="btn btn-primary small r-reject" disabled={busy} onClick={() => resolve("reject")}><Icon name="trash" size={14} /> {t("review.reject")}</button>
      </div>
    </li>
  );
}

export default function Review({ onToast }) {
  const { t } = useI18n();
  const [flags, setFlags] = useState(null);
  const [error, setError] = useState(null);
  const load = () => ai.flags("open").then((f) => { setFlags(f); setError(null); }).catch((e) => setError(errorText(t, e)));
  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="d-grid">
      <Card icon="shield" title={t("review.title")} sub={t("review.sub")} className="span-7 d-card-top">
        {error && <div className="ai-notice bad"><span>{error}</span></div>}
        {flags && flags.length === 0 && <p className="d-empty">{t("review.empty")}</p>}
        {flags && flags.length > 0 && (
          <ul className="r-flags">
            {flags.map((f) => (
              <FlagItem key={f.request_id} flag={f} onDone={(id, action) => {
                setFlags((list) => list.filter((x) => x.request_id !== id));
                window.dispatchEvent(new Event("lumi:flags"));
                onToast(t(action === "reject" ? "review.rejected" : "review.kept"));
              }} />
            ))}
          </ul>
        )}
      </Card>
      <EngineStatusCard />
    </div>
  );
}
