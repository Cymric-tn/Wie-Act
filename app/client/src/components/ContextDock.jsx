import { memo } from "react";
import Icon from "./Icons";
import { PALETTE, NOTE_COLORS } from "../lib/theme";

function Swatches({ colors, value, onChange, custom = true, label = "Color" }) {
  return (
    <div className="swatches" role="radiogroup" aria-label={label}>
      {colors.map((p) => (
        <button
          key={p.value}
          className={"swatch" + (value === p.value ? " active" : "")}
          style={{ "--c": p.value }}
          onClick={() => onChange(p.value)}
          role="radio"
          aria-checked={value === p.value}
          aria-label={p.name} title={p.name}
        />
      ))}
      {custom && (
        <label className="swatch custom" title="Custom color">
          <input type="color" value={/^#[0-9a-f]{6}$/i.test(value || "") ? value : "#a279ff"} onChange={(e) => onChange(e.target.value)} aria-label="Custom color" />
        </label>
      )}
    </div>
  );
}

function WidthControl({ value, onChange, color, label = "Stroke width", max = 24 }) {
  return (
    <label className="width-control" title={label}>
      <span className="width-preview" style={{ "--w": `${Math.max(3, Math.min(22, value))}px`, "--c": color }} />
      <input type="range" min="1" max={max} value={value} onChange={(e) => onChange(Number(e.target.value))} aria-label={label} />
      <span className="muted">{value}</span>
    </label>
  );
}

const Sep = () => <span className="dock-sep vertical" />;

/** The bottom dock: tool options, or actions for the current selection. */
function ContextDock(props) {
  const {
    tool, shapeKind, color, setColor, width, setWidth, fill, setFill, noteColor, setNoteColor,
    shapeAssist, setShapeAssist, selection, readOnly, isHost, onClearPage,
    onRecolor, onSelectionWidth, onSelectionFill, onDuplicate, onFront, onBack, onDelete
  } = props;

  if (readOnly) {
    return <div className="context-dock hint-dock"><Icon name="lock" size={16} /> View only — you can still pan, zoom, vote on notes and react.</div>;
  }

  if (tool === "select" && selection.length) {
    const allNotes = selection.every((i) => i.type === "note");
    const stroked = selection.filter((i) => i.type === "path" || i.type === "shape");
    const fillable = selection.filter((i) => i.type === "shape" && (i.shape === "rect" || i.shape === "ellipse"));
    const first = selection.find((i) => i.color);
    return (
      <div className="context-dock" aria-label="Selection">
        <span className="selection-count">{selection.length} selected</span>
        <Sep />
        {selection.some((i) => i.type !== "image") && (
          <Swatches colors={allNotes ? NOTE_COLORS : PALETTE} custom={!allNotes} value={first?.color} onChange={onRecolor} />
        )}
        {stroked.length > 0 && (
          <>
            <Sep />
            <WidthControl value={Math.round(stroked[0].width)} onChange={onSelectionWidth} color={stroked[0].color} />
          </>
        )}
        {fillable.length > 0 && (
          <button className={"chip" + (fillable.every((i) => i.fill) ? " active" : "")} onClick={onSelectionFill}>
            <Icon name="fill" size={16} filled={fillable.every((i) => i.fill)} /> Fill
          </button>
        )}
        <Sep />
        <button className="icon-chip" onClick={onDuplicate} aria-label="Duplicate (Ctrl+D)" title="Duplicate (Ctrl+D)"><Icon name="duplicate" size={18} /></button>
        <button className="icon-chip" onClick={onFront} aria-label="Bring to front" title="Bring to front"><Icon name="front" size={18} /></button>
        <button className="icon-chip" onClick={onBack} aria-label="Send to back" title="Send to back"><Icon name="back" size={18} /></button>
        <button className="icon-chip danger" onClick={onDelete} aria-label="Delete (Del)" title="Delete (Del)"><Icon name="trash" size={18} /></button>
      </div>
    );
  }

  const hints = {
    select: "Click to select · drag to box-select · double-click text to edit",
    hand: "Drag to pan · Ctrl + scroll or pinch to zoom",
    laser: "A fading pointer everyone can see — nothing is saved",
    eraser: "Drag across anything to erase it"
  };

  if (tool === "note") {
    return (
      <div className="context-dock">
        <span className="dock-label">Note</span>
        <Swatches colors={NOTE_COLORS} custom={false} value={noteColor} onChange={setNoteColor} label="Note color" />
        <Sep />
        <span className="muted">Click the board to add · click ▲ to vote</span>
      </div>
    );
  }

  if (hints[tool]) {
    return (
      <div className="context-dock hint-dock">
        <span>{hints[tool]}</span>
        {tool === "eraser" && isHost && (
          <>
            <Sep />
            <button className="chip danger" onClick={onClearPage}><Icon name="trash" size={16} /> Clear page</button>
          </>
        )}
      </div>
    );
  }

  const canFill = tool === "shape" && (shapeKind === "rect" || shapeKind === "ellipse");
  return (
    <div className="context-dock" aria-label="Pen style">
      <Swatches colors={PALETTE} value={color} onChange={setColor} />
      <Sep />
      <WidthControl
        value={width}
        onChange={setWidth}
        color={color}
        label={tool === "text" ? "Text size" : tool === "equation" ? "Equation size" : "Stroke width"}
      />
      {tool === "shape" && (
        <button className={"chip" + (fill && canFill ? " active" : "")} onClick={() => setFill((f) => !f)} disabled={!canFill} aria-pressed={fill}>
          <Icon name="fill" size={16} filled={fill && canFill} /> Fill
        </button>
      )}
      {tool === "pen" && (
        <button
          className={"chip" + (shapeAssist ? " active" : "")}
          onClick={() => setShapeAssist((s) => !s)}
          aria-pressed={shapeAssist}
          aria-label="Turn rough lines, boxes and circles into clean shapes" title="Turn rough lines, boxes and circles into clean shapes"
        >
          <Icon name="magic" size={16} /> Shape assist
        </button>
      )}
    </div>
  );
}

export default memo(ContextDock);
