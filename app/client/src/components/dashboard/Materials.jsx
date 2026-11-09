import { useEffect, useRef, useState } from "react";
import Icon from "../Icons";
import { Card, MaterialList } from "./Widgets";
import { useI18n } from "../../lib/i18n";
import { ai, materials as api, MATERIAL_ACCEPT, mimeFor } from "../../lib/engine";
import { MATERIALS } from "../../lib/demoData";
import { errorText } from "../ai/AIBits";

const MAX_BYTES = 15_000_000;
const EXT = { "application/pdf": "PDF", "text/markdown": "MD", "text/plain": "TXT" };
const sizeLabel = (n) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

function UploadForm({ user, onUploaded }) {
  const { t } = useI18n();
  const professor = user.role === "professor";
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState("");
  const [visibility, setVisibility] = useState(professor ? "public" : "private");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  function choose(f) {
    setError(null);
    if (!f) return;
    if (f.size > MAX_BYTES) return setError(t("mat.tooBig"));
    if (!mimeFor(f) || !MATERIAL_ACCEPT.includes(`.${f.name.split(".").pop().toLowerCase()}`)) return setError(t("mat.drop"));
    setFile(f);
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " "));
  }

  async function submit(e) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      await api.upload(file, { title, visibility, user });
      setFile(null);
      setTitle("");
      if (inputRef.current) inputRef.current.value = "";
      onUploaded();
    } catch (err) {
      setError(errorText(t, err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="m-upload" onSubmit={submit}>
      <label
        className={"m-drop" + (file ? " has-file" : "")}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => { e.preventDefault(); choose(e.dataTransfer.files?.[0]); }}
      >
        <Icon name="upload" size={22} />
        <b>{file ? file.name : t("mat.choose")}</b>
        <small>{file ? sizeLabel(file.size) : t("mat.drop")}</small>
        <input ref={inputRef} type="file" accept={MATERIAL_ACCEPT} onChange={(e) => choose(e.target.files?.[0])} />
      </label>
      <label className="d-select">
        <span>{t("mat.titleField")}</span>
        <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={160} dir="auto" />
      </label>
      <label className="d-select">
        <span>{t("mat.visibility")}</span>
        <select className="input" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
          {(professor ? ["public", "class"] : ["private", "class"]).map((v) => <option key={v} value={v}>{t(`mat.vis.${v}`)}</option>)}
        </select>
      </label>
      {error && <div className="ai-notice bad"><span>{error}</span></div>}
      <button className="btn btn-primary full" disabled={!file || busy}><Icon name="upload" size={15} /> {busy ? t("mat.uploading") : t("mat.send")}</button>
    </form>
  );
}

function MaterialRow({ m, user, concepts, onChange }) {
  const { t, lang } = useI18n();
  const professor = user.role === "professor";
  const canRemove = professor || m.mine;
  const l = lang === "ar" ? "ar" : lang;
  const conceptTitle = (id) => concepts?.[id]?.title?.[l] || id;
  const status = m.status === "indexed" ? t("mat.status.indexed", { n: m.chunks.length }) : t(`mat.status.${m.status}`);
  return (
    <li className="m-row">
      <span className="d-file">{EXT[m.mime] || "DOC"}</span>
      <div className="m-main">
        <div className="m-title">
          <b dir="auto">{m.title}</b>
          {m.source === "professor"
            ? <span className="d-badge official"><Icon name="shield" size={13} /> {t("materials.official")}</span>
            : <span className="d-badge supp">{t(`mat.vis.${m.visibility}`)}</span>}
        </div>
        <small>{m.owner_name} · {sizeLabel(m.size)} · <span className={`m-status ${m.status}`}>{m.status === "processing" && <i className="d-spin" />}{status}</span></small>
        {m.detail && m.status !== "indexed" && <small className="m-detail">{m.detail}</small>}
        {m.chunks.length > 0 && (
          <div className="m-tags" aria-label={t("mat.tags")}>
            {m.chunks.slice(0, 12).map((c) => (
              professor && concepts
                ? (
                  <select className="input"
                    key={c.id}
                    className={"m-tag" + (c.confirmed ? " ok" : c.confidence < 0.7 ? " low" : "")}
                    value={c.concept || ""}
                    title={c.title}
                    onChange={(e) => api.confirmTag(m.id, c.id, e.target.value || null).then(onChange).catch(() => {})}
                  >
                    <option value="">{t("mat.noConcept")}</option>
                    {Object.keys(concepts).map((id) => <option key={id} value={id}>{conceptTitle(id)}{!c.confirmed && c.concept === id && c.confidence < 0.7 ? ` · ${t("mat.check")}` : ""}</option>)}
                  </select>
                )
                : c.concept && <span key={c.id} className="m-tag static" title={c.title}>{conceptTitle(c.concept)}</span>
            ))}
          </div>
        )}
      </div>
      <div className="m-actions">
        <a className="btn btn-secondary small" href={api.fileUrl(m, user)} target="_blank" rel="noreferrer">{t("mat.open")}</a>
        {canRemove && <button className="d-icon-btn" title={t("mat.delete")} aria-label={t("mat.delete")} onClick={() => api.remove(m.id, user).then(onChange).catch(() => {})}><Icon name="trash" size={16} /></button>}
      </div>
    </li>
  );
}

export default function Materials({ user }) {
  const { t } = useI18n();
  const [list, setList] = useState(null);
  const [error, setError] = useState(null);
  const [concepts, setConcepts] = useState(null);
  const load = () => api.list(user).then((l) => { setList(l); setError(null); }).catch((e) => setError(errorText(t, e)));

  useEffect(() => {
    load();
    ai.course().then((c) => setConcepts(c.concepts)).catch(() => {});
  }, [user.role]); // eslint-disable-line react-hooks/exhaustive-deps

  // Poll while something is being indexed.
  const processing = list?.some((m) => m.status === "processing");
  useEffect(() => {
    if (!processing) return;
    const id = setInterval(load, 1500);
    return () => clearInterval(id);
  }, [processing]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="d-grid">
      <Card icon="book" title={t("materials.title")} sub={t("materials.sub")} className="span-8 d-card-top">
        {error && <div className="ai-notice bad"><span>{error}</span></div>}
        {list && list.length === 0 && <p className="d-empty">{t("mat.empty")}</p>}
        {list && list.length > 0 && (
          <ul className="m-list">
            {list.map((m) => <MaterialRow key={m.id} m={m} user={user} concepts={concepts} onChange={load} />)}
          </ul>
        )}
      </Card>
      <Card icon="upload" title={user.role === "professor" ? t("prof.upload") : t("materials.upload")} className="span-4 d-card-top">
        <UploadForm user={user} onUploaded={load} />
      </Card>
      <Card icon="book" title={t("mat.examples")} example className="span-8 d-card-top">
        <MaterialList items={MATERIALS} />
      </Card>
    </div>
  );
}
