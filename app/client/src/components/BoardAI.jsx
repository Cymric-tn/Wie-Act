// The board's AI tab: ask about the board, fact-check a selected claim, check a
// worked solution line by line, or read handwriting from a selection. Each call
// carries the room id, so the engine keeps one AI request per room at a time.
import { useEffect, useState } from "react";
import Icon from "./Icons";
import { ai, engineLanguage, replyLanguage } from "../lib/engine";
import { renderToCanvas } from "../lib/exporters";
import { useI18n } from "../lib/i18n";
import { AnswerText, Degraded, Progress, ReportButton, Sources, errorText, useAiStatus, useProgress } from "./ai/AIBits";
import "./dashboard/dashboard.css";
import { LumiSpark } from "./LumiSpark";

const INK_TYPES = new Set(["path", "shape", "image"]);
const VERDICTS = {
  SUPPORTED: { label: "Supported by the course", tone: "good" },
  CONTRADICTED: { label: "Contradicted", tone: "bad" },
  PARTLY: { label: "Partly right", tone: "warn" },
  NOT_IN_COURSE: { label: "Not in the course material", tone: "muted" }
};
const LINE_STATUS = { OK: { icon: "check", tone: "good" }, WRONG: { icon: "close", tone: "bad" }, UNCHECKED: { icon: "help", tone: "muted" } };

export function itemText(item) {
  if (item.type === "text" || item.type === "note") return item.text || "";
  if (item.type === "equation") return item.latex || "";
  return "";
}

/** Reading order: top to bottom, then left to right. */
const readingOrder = (items) =>
  [...items].sort((a, b) => (Math.abs((a.y ?? a.y0 ?? 0) - (b.y ?? b.y0 ?? 0)) > 30 ? (a.y ?? a.y0 ?? 0) - (b.y ?? b.y0 ?? 0) : (a.x ?? a.x0 ?? 0) - (b.x ?? b.x0 ?? 0)));

export const textOfItems = (items) => readingOrder(items).map(itemText).filter((s) => s.trim()).join("\n");

function EngineLine() {
  const status = useAiStatus();
  if (!status) return null;
  const kind = status.offline || !status.connected ? "offline" : status.backend === "fake" ? "fake" : "connected";
  const label = { offline: "AI engine offline", fake: "Preview engine", connected: "AI engine connected" }[kind];
  return <span className={`ai-engine ${kind}`}><i />{label}</span>;
}

function ClaimResult({ data }) {
  const v = VERDICTS[data.verdict] || { label: data.verdict, tone: "muted" };
  return (
    <div className="bai-claim">
      <span className={`bai-verdict ${v.tone}`}>{v.label}</span>
      {(data.parts || []).length > 1 && (
        <ul className="bai-parts">
          {data.parts.map((p, i) => {
            const pv = VERDICTS[p.verdict] || { label: p.verdict, tone: "muted" };
            return (
              <li key={i} className={pv.tone}>
                <span className={`bai-verdict ${pv.tone}`}>{pv.label}</span> <span dir="auto">{p.claim}</span>
                {p.correction && <small dir="auto"> → {p.correction}</small>}
              </li>
            );
          })}
        </ul>
      )}
      {!(data.parts || []).length && data.correction && <p className="bai-correct">Correction: <b dir="auto">{data.correction}</b></p>}
      {(data.evidence || []).map((e) => <blockquote key={e.chunk_id} className="ai-partial" dir="auto"><small>{e.chunk_id}</small>{e.text}</blockquote>)}
    </div>
  );
}

function WorkResult({ data, steps = [] }) {
  return (
    <div className="bai-work">
      <ol>
        {(data.steps || []).map((step) => {
          const s = LINE_STATUS[step.verdict] || LINE_STATUS.UNCHECKED;
          return (
            <li key={step.index} className={s.tone}>
              <Icon name={s.icon} size={13} />
              <code dir="ltr">{steps[step.index]}</code>
              <small dir="auto">{step.verdict === "UNCHECKED" ? "couldn’t check" : step.verdict === "OK" ? "ok" : step.note || "check this line"}</small>
            </li>
          );
        })}
      </ol>
      {data.feedback && <p className="ai-ex-hint" dir="auto"><b>Think about it:</b> {data.feedback}</p>}
      {data.final_verdict === "CORRECT" && <p className="bai-good">Every line checks out.</p>}
    </div>
  );
}

export default function BoardAI({ room, courseId, pageItems, selected, background, canEdit, onPin, onAddText, onBusyChange }) {
  const { t, lang } = useI18n();
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(null);
  const [out, setOut] = useState(null); // { kind, result, input }
  const [error, setError] = useState(null);
  const [progress, onEvent, resetProgress] = useProgress();

  useEffect(() => { onBusyChange?.(Boolean(busy)); return () => onBusyChange?.(false); }, [busy]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectionText = textOfItems(selected);
  const boardText = textOfItems(pageItems);
  const selectionLines = selectionText.split("\n").map((s) => s.trim()).filter(Boolean);
  const hasInk = selected.some((i) => INK_TYPES.has(i.type));
  const context = { room_id: room, ...(courseId ? { course_id: courseId } : {}) };

  async function go(kind, task, inputs) {
    setBusy(kind);
    setError(null);
    setOut(null);
    resetProgress();
    try {
      // Questions carry their own language; formulas and photos don't, so those use the UI language.
      const pref = replyLanguage();
      const language = pref !== "auto" ? pref : task === "explain" ? undefined : engineLanguage(lang);
      const result = await ai.run(task, inputs, { context: { ...context, ...(language ? { language } : {}) }, onEvent });
      if (result.degrade_reason !== "superseded") setOut({ kind, result, inputs });
    } catch (e) {
      setError(errorText(t, e));
    } finally {
      setBusy(null);
    }
  }

  const ask = () => question.trim() && go("explain", "explain", { question: question.trim(), passage: selectionText || undefined, board_text: boardText || undefined });
  const factCheck = (claim = selectionText) => claim.trim() && go("claim", "check_claim", { claim });
  const checkWork = () => {
    // first line = the problem (e.g. f(x) = 3x^2 + 5x), the next lines = the learner's steps
    const [problem, ...steps] = selectionLines;
    go("work", "check_answer", steps.length ? { problem, steps } : { problem, steps: [problem] });
  };
  async function readHandwriting() {
    setBusy("ocr");
    setError(null);
    try {
      const canvas = await renderToCanvas(selected, background, { maxSide: 1400, scale: 2 });
      await go("ocr", "ocr_transcribe", { image: canvas.toDataURL("image/png") });
    } catch (e) {
      setError(errorText(t, e));
      setBusy(null);
    }
  }
  const retry = () => out && go(out.kind, { explain: "explain", claim: "check_claim", work: "check_answer", ocr: "ocr_transcribe" }[out.kind], out.inputs);

  const result = out?.result;
  const data = result?.data || {};
  const answerText = out?.kind === "explain"
    ? data.body ? `${data.opener || ""}\n\n${data.body}`
      : data.excerpts ? [data.framing, data.excerpts.map((e) => `• ${e.text}`).join("\n")].join("\n\n")
        : result?.text
    : null;
  const pinText = out?.kind === "explain" ? answerText
    : out?.kind === "claim" ? `${VERDICTS[data.verdict]?.label || data.verdict}: ${out.inputs.claim}${data.correction ? `\n→ ${data.correction}` : ""}`
      : out?.kind === "work" ? (data.feedback || (data.final_verdict === "CORRECT" ? "✓ Every line checks out" : null))
        : out?.kind === "ocr" ? data.text || data.latex : null;

  return (
    <div className="bai lumi-dark">
      <div className="card panel-card glass">
        <div className="bai-head">
          <div className="eyebrow bai-name"><LumiSpark size={20} state={busy ? "thinking" : "rest"} /> LUMI</div>
          <EngineLine />
        </div>
        <p className="panel-copy">
          {selected.length
            ? <>{selected.length} selected{selectionLines.length ? ` · ${selectionLines.length} line${selectionLines.length > 1 ? "s" : ""} of text` : ""}{hasInk ? " · handwriting" : ""}</>
            : "Select notes, text, equations or handwriting to use them. Questions also read the text on this page."}
        </p>
        <form className="bai-ask" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <textarea className="input" value={question} onChange={(e) => setQuestion(e.target.value)} rows={2} maxLength={2000} dir="auto"
            placeholder={selectionText ? "Ask about the selection…" : "Ask the AI about this board…"}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } e.stopPropagation(); }} />
          <button className="btn btn-primary small" disabled={!question.trim() || Boolean(busy)}><Icon name="send" size={14} /> Ask</button>
        </form>
        <div className="bai-actions">
          <button className="chip" disabled={!selectionText || Boolean(busy)} onClick={() => factCheck()} aria-label="Is this claim true in the course?" title="Is this claim true in the course?"><Icon name="shield" size={14} /> Fact-check</button>
          <button className="chip" disabled={!selectionLines.length || Boolean(busy)} onClick={checkWork} aria-label="First line: the problem, e.g. f(x) = 3x^2 + 5x. Next lines: your steps." title="First line: the problem, e.g. f(x) = 3x^2 + 5x. Next lines: your steps."><Icon name="check" size={14} /> Check my work</button>
          <button className="chip" disabled={!hasInk || Boolean(busy)} onClick={readHandwriting} aria-label="Turn the selected handwriting or photo into text" title="Turn the selected handwriting or photo into text"><Icon name="edit" size={14} /> Read handwriting</button>
        </div>
      </div>

      {(busy || out || error) && (
        <div className="card panel-card glass bai-result" aria-live="polite">
          {busy && <Progress state={progress} />}
          {error && <div className="ai-notice bad"><span>{error}</span></div>}
          {out && (
            <>
              {out.kind === "explain" && <AnswerText text={answerText} />}
              {out.kind === "explain" && data.exercise && <p className="ai-ex-hint" dir="auto"><b>Try:</b> {data.exercise.statement}</p>}
              {out.kind === "claim" && result.data && <ClaimResult data={data} />}
              {out.kind === "work" && result.data && <WorkResult data={data} steps={out.inputs.steps} />}
              {out.kind === "ocr" && (data.text || data.latex) && (
                <div className="bai-ocr">
                  {data.text && <AnswerText text={data.text} />}
                  {data.latex && <code dir="ltr">{data.latex}</code>}
                </div>
              )}
              {out.kind === "ocr" && data.confidence_note && <p className="panel-copy">{data.confidence_note}</p>}
              <Degraded result={result} onRetry={retry} onFull={() => go("explain", "explain", { ...out.inputs })} />
              <Sources notes={result.notes} />
              <div className="bai-foot">
                {pinText && canEdit && <button className="chip" onClick={() => onPin(pinText)}><Icon name="note" size={14} /> Pin as note</button>}
                {out.kind === "ocr" && data.text && canEdit && <button className="chip" onClick={() => onAddText(data.text)}><Icon name="text" size={14} /> Add as text</button>}
                {out.kind === "ocr" && data.text && <button className="chip" onClick={() => factCheck(data.text)}><Icon name="shield" size={14} /> Fact-check it</button>}
                {(out.kind === "explain" || out.kind === "claim") && <ReportButton requestId={result.request_id} />}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
