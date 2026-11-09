import { memo, useEffect, useRef, useState } from "react";
import Icon from "./Icons";

export const TOOLS = [
  { id: "select", label: "Select", key: "V", group: 0 },
  { id: "hand", label: "Pan", key: "H", group: 0 },
  { id: "pen", label: "Pen", key: "P", group: 1 },
  { id: "highlighter", label: "Highlighter", key: "I", group: 1 },
  { id: "eraser", label: "Eraser", key: "E", group: 1 },
  { id: "laser", label: "Laser pointer", key: "X", group: 1 },
  { id: "shape", label: "Shapes", group: 2 },
  { id: "text", label: "Text", key: "T", group: 3 },
  { id: "note", label: "Sticky note", key: "N", group: 3 },
  { id: "equation", label: "Equation", key: "K", group: 3 }
];

export const SHAPES = [
  { id: "line", label: "Line", key: "L" },
  { id: "arrow", label: "Arrow", key: "A" },
  { id: "rect", label: "Rectangle", key: "R" },
  { id: "ellipse", label: "Ellipse", key: "O" }
];

/** Tools that change the board; disabled while it is locked or in history. */
export const EDIT_TOOLS = new Set(["pen", "highlighter", "eraser", "laser", "shape", "text", "note", "equation"]);

function ToolDock({ tool, shapeKind, onTool, onImage, onUndo, onRedo, canUndo, canRedo, readOnly }) {
  const [flyout, setFlyout] = useState(false);
  const fileRef = useRef(null);
  const shapeLabel = SHAPES.find((s) => s.id === shapeKind)?.label;

  useEffect(() => {
    if (!flyout) return;
    const close = (e) => !e.target.closest?.(".shape-flyout, .shape-trigger") && setFlyout(false);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [flyout]);

  return (
    <nav className="tool-dock glass" aria-label="Drawing tools">
      {TOOLS.map((t, i) => {
        const disabled = readOnly && EDIT_TOOLS.has(t.id);
        const sep = i > 0 && TOOLS[i - 1].group !== t.group;
        if (t.id === "shape") {
          return (
            <span key={t.id} className="tool-wrap">
              {sep && <span className="dock-sep" />}
              <span className="shape-group">
                <button
                  className={"tool shape-trigger" + (tool === "shape" ? " active" : "")}
                  disabled={disabled}
                  aria-pressed={tool === "shape"}
                  aria-haspopup="true"
                  aria-expanded={flyout}
                  aria-label={`${shapeLabel} — click again for more shapes`} title={`${shapeLabel} — click again for more shapes`}
                  onClick={() => {
                    if (tool === "shape") setFlyout((f) => !f);
                    else onTool("shape", shapeKind);
                  }}
                >
                  <Icon name={shapeKind} />
                  <i className="flyout-caret" />
                </button>
                {flyout && (
                  <span className="shape-flyout" role="menu">
                    {SHAPES.map((s) => (
                      <button
                        key={s.id}
                        role="menuitemradio"
                        aria-checked={shapeKind === s.id}
                        className={"tool" + (shapeKind === s.id ? " active" : "")}
                        aria-label={`${s.label} (${s.key})`} title={`${s.label} (${s.key})`}
                        onClick={() => {
                          onTool("shape", s.id);
                          setFlyout(false);
                        }}
                      >
                        <Icon name={s.id} />
                      </button>
                    ))}
                  </span>
                )}
              </span>
            </span>
          );
        }
        return (
          <span key={t.id} className="tool-wrap">
            {sep && <span className="dock-sep" />}
            <button
              className={"tool" + (tool === t.id ? " active" : "")}
              onClick={() => onTool(t.id)}
              disabled={disabled}
              aria-pressed={tool === t.id}
              aria-label={`${t.label} (${t.key})`} title={`${t.label} (${t.key})`}
            >
              <Icon name={t.id} />
            </button>
          </span>
        );
      })}
      <button className="tool" onClick={() => fileRef.current?.click()} disabled={readOnly} aria-label="Insert image or PDF" title="Insert image or PDF">
        <Icon name="image" />
      </button>
      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif,application/pdf"
        multiple
        hidden
        onChange={(e) => {
          onImage([...e.target.files]);
          e.target.value = "";
        }}
      />
      <span className="dock-sep" />
      <button className="tool" onClick={onUndo} disabled={!canUndo || readOnly} aria-label="Undo (Ctrl+Z)" title="Undo (Ctrl+Z)">
        <Icon name="undo" />
      </button>
      <button className="tool" onClick={onRedo} disabled={!canRedo || readOnly} aria-label="Redo (Ctrl+Shift+Z)" title="Redo (Ctrl+Shift+Z)">
        <Icon name="redo" />
      </button>
    </nav>
  );
}

export default memo(ToolDock);
