import { memo } from "react";
import Icon from "./Icons";

/** Bottom-left: page switcher and zoom controls. */
function PageBar({ pages, pageId, onPage, onAddPage, canAdd, zoom, onZoomIn, onZoomOut, onFit, onResetZoom }) {
  const index = Math.max(0, pages.findIndex((p) => p.id === pageId));
  const go = (delta) => {
    const next = pages[index + delta];
    if (next) onPage(next.id);
  };
  return (
    <div className="page-bar">
      <div className="pager" role="group" aria-label="Pages">
        <button className="icon-chip" onClick={() => go(-1)} disabled={index === 0} title="Previous page (Page Up)">
          <Icon name="left" size={16} />
        </button>
        <select value={pageId} onChange={(e) => onPage(e.target.value)} aria-label="Current page">
          {pages.map((p, i) => (
            <option key={p.id} value={p.id}>{i + 1}. {p.name}</option>
          ))}
        </select>
        <button className="icon-chip" onClick={() => go(1)} disabled={index === pages.length - 1} title="Next page (Page Down)">
          <Icon name="right" size={16} />
        </button>
        <button className="icon-chip" onClick={onAddPage} disabled={!canAdd} title="Add page">
          <Icon name="plus" size={16} />
        </button>
      </div>
      <div className="zoomer" role="group" aria-label="Zoom">
        <button className="icon-chip" onClick={onZoomOut} title="Zoom out (−)"><Icon name="minus" size={16} /></button>
        <button className="zoom-readout" onClick={onResetZoom} title="Reset to 100% (Shift+0)">{Math.round(zoom * 100)}%</button>
        <button className="icon-chip" onClick={onZoomIn} title="Zoom in (+)"><Icon name="plus" size={16} /></button>
        <button className="icon-chip" onClick={onFit} title="Zoom to fit (Shift+1)"><Icon name="fit" size={16} /></button>
      </div>
    </div>
  );
}

export default memo(PageBar);
