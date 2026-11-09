// The diagram generator: a sentence in, an exact curve, variation table or concept map out, ready for the whiteboard.
import { useEffect, useRef, useState } from "react";
import Icon from "../Icons";
import { Card, openBoard } from "./Widgets";
import { errorText } from "../ai/AIBits";
import { useI18n } from "../../lib/i18n";
import { mountDiagram } from "../../lib/diagrams/interactive";
import { toBoardPrimitives } from "../../lib/diagrams/primitives";
import { buildDiagram, checkDiagram, setPendingDiagram } from "../../lib/diagrams/api";

const EXAMPLES = ["diagrams.ex.graph", "diagrams.ex.table", "diagrams.ex.map"];
const LEGEND = [["new", "var(--lumi-iris)"], ["bad", "var(--lumi-bad)"], ["warn", "var(--lumi-warn)"], ["ok", "var(--lumi-spark)"]];

function messageOf(t, error) {
  return error.message === "diagram_timeout" ? t("diagrams.timeout") : errorText(t, error);
}

function DiagramView({ spec, lang }) {
  const { t } = useI18n();
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    mountDiagram(el, spec, { checker: (s, answer) => checkDiagram(s, answer, lang), errorText: (e) => messageOf(t, e) });
    return () => { el.innerHTML = ""; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- a new spec is a new drawing; changing language must not reset a half-filled table
  }, [spec]);
  return <div className="dg-stage" ref={ref} />;
}

export default function Diagrams() {
  const { t, lang } = useI18n();
  const [text, setText] = useState("");
  const [practice, setPractice] = useState(false);
  const [spec, setSpec] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function draw(request = text) {
    if (!request.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      setSpec(await buildDiagram(request.trim(), { lang, practice }));
    } catch (e) {
      setSpec(null);
      setError(messageOf(t, e));
    } finally {
      setBusy(false);
    }
  }

  function send() {
    if (setPendingDiagram(toBoardPrimitives(spec))) openBoard();
  }

  const tryExample = (key) => {
    setText(t(key));
    draw(t(key));
  };
  const hasMastery = spec?.type === "concept_map" && spec.payload.nodes.some((n) => n.mastery != null);
  const howTo = spec?.payload.mode === "practice" ? "diagrams.fill" : spec?.payload.tangent_at != null ? "diagrams.drag" : null;

  return (
    <div className="d-page dg-page">
      <Card icon="diagram" title={t("diagrams.title")} sub={t("diagrams.sub")}>
        <form className="dg-form" onSubmit={(e) => { e.preventDefault(); draw(); }}>
          <label className="dg-field" htmlFor="dg-request">{t("diagrams.input")}</label>
          <div className="dg-row">
            <input className="input" id="dg-request" value={text} onChange={(e) => setText(e.target.value)} maxLength={300} dir="auto" autoComplete="off" placeholder={t("diagrams.placeholder")} />
            <button className="btn btn-primary" disabled={!text.trim() || busy}>{busy ? t("diagrams.drawing") : t("diagrams.draw")}</button>
          </div>
          <label className="dg-check"><input type="checkbox" checked={practice} onChange={(e) => setPractice(e.target.checked)} /> {t("diagrams.practice")}</label>
          <div className="dg-examples">
            <span>{t("diagrams.examples")}</span>
            {EXAMPLES.map((key) => <button type="button" key={key} dir="auto" disabled={busy} onClick={() => tryExample(key)}>{t(key)}</button>)}
          </div>
        </form>
      </Card>

      {error && <div className="ai-notice bad" role="alert"><span dir="auto">{error}</span></div>}
      {!spec && !error && <p className="dg-note">{t("diagrams.empty")}</p>}

      {spec && (
        <Card>
          <div className="dg-form">
            <DiagramView spec={spec} lang={lang} />
            {howTo && <p className="dg-note">{t(howTo)}</p>}
            {spec.warnings.map((w) => <p className="dg-warning" key={w} dir="auto">{w}</p>)}
            <div className="dg-foot">
              {hasMastery ? (
                <div className="dg-legend">{LEGEND.map(([key, color]) => <span key={key}><i style={{ background: color }} />{t(`diagrams.legend.${key}`)}</span>)}</div>
              ) : <span />}
              <button className="btn btn-secondary small" onClick={send}><Icon name="board" size={15} /> {t("diagrams.send")}</button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
