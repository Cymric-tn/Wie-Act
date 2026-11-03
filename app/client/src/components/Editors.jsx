import { useEffect, useRef, useState } from "react";
import { texToSvg } from "../lib/math";
import { NOTE_INK } from "../lib/theme";
import Icon from "./Icons";

/** Commits at most once, whether by keyboard, blur or unmount. */
function useOnce(fn) {
  const done = useRef(false);
  return (...args) => {
    if (done.current) return;
    done.current = true;
    fn(...args);
  };
}

export function TextEditor({ left, top, fontSize, color, initial = "", onCommit, onCancel }) {
  const [value, setValue] = useState(initial);
  const commit = useOnce(onCommit);
  const cancel = useOnce(onCancel);
  return (
    <textarea
      className="text-draft"
      autoFocus
      style={{ left, top, color, fontSize }}
      value={value}
      placeholder="Type, then Enter"
      maxLength={500}
      onFocus={(e) => e.target.select()}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => commit(value)}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          commit(value);
        }
        if (e.key === "Escape") cancel();
      }}
    />
  );
}

export function NoteEditor({ left, top, width, height, fontSize, padding, background, initial = "", onCommit, onCancel }) {
  const [value, setValue] = useState(initial);
  const commit = useOnce(onCommit);
  const cancel = useOnce(onCancel);
  return (
    <div className="note-draft" style={{ left, top, width, height, background, "--pad": `${padding}px` }}>
      <textarea
        autoFocus
        style={{ fontSize, color: NOTE_INK }}
        value={value}
        maxLength={500}
        placeholder="Write an idea…"
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => commit(value)}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) commit(value);
          if (e.key === "Escape") cancel();
        }}
      />
      <span className="note-draft-hint">Ctrl/⌘ + Enter to save</span>
    </div>
  );
}

const EXAMPLES = [
  ["Fraction", "\\frac{a}{b}"],
  ["Integral", "\\int_0^1 x^2\\,dx"],
  ["Sum", "\\sum_{n=1}^{\\infty} \\frac{1}{n^2} = \\frac{\\pi^2}{6}"],
  ["Root", "x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}"],
  ["Limit", "\\lim_{x \\to 0} \\frac{\\sin x}{x} = 1"],
  ["Matrix", "\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}"]
];

export function EquationEditor({ left, top, color, initial = "", onCommit, onCancel }) {
  const [value, setValue] = useState(initial);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    if (!value.trim()) {
      setPreview(null);
      setError("");
      return;
    }
    let cancelled = false;
    const t = setTimeout(() => {
      texToSvg(value, color)
        .then((r) => !cancelled && (setPreview(r.src), setError("")))
        .catch((e) => !cancelled && (setPreview(null), setError(e.message)));
    }, 180);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [value, color]);

  const submit = () => value.trim() && !error && onCommit(value.trim());

  return (
    <div className="equation-editor" style={{ left, top }} role="dialog" aria-label="Equation">
      <div className="eq-head">
        <span className="eyebrow">EQUATION · LATEX</span>
        <button className="close" onClick={onCancel} aria-label="Cancel"><Icon name="close" size={16} /></button>
      </div>
      <textarea
        ref={inputRef}
        autoFocus
        value={value}
        rows={2}
        spellCheck={false}
        placeholder="e.g. \int_0^1 x^2\,dx"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            submit();
          }
          if (e.key === "Escape") onCancel();
        }}
      />
      <div className={"eq-preview" + (error ? " error" : "")}>
        {error ? <span>{error}</span> : preview ? <img src={preview} alt="Equation preview" /> : <span className="muted">Preview appears here</span>}
      </div>
      <div className="eq-examples">
        {EXAMPLES.map(([label, tex]) => (
          <button
            key={label}
            className="filter"
            onClick={() => {
              setValue(tex);
              inputRef.current?.focus();
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="review-actions">
        <button className="cta secondary" onClick={onCancel}>Cancel</button>
        <button className="cta" onClick={submit} disabled={!value.trim() || Boolean(error)}>
          {initial ? "Update" : "Insert"} equation
        </button>
      </div>
    </div>
  );
}
