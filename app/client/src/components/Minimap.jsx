import { memo, useEffect, useRef } from "react";
import { bbox, unionBox, visibleBox } from "../lib/geometry";
import { lumiColors } from "../lib/lumiColors";

const W = 200;
const H = 124;

/** Overview of the page with the visible area; click or drag to navigate. */
function Minimap({ items, view, size, onNavigate, light }) {
  const canvasRef = useRef(null);
  const frameRef = useRef(null);

  const visible = visibleBox(view, size.w, size.h);
  const content = unionBox(items.map(bbox));
  const world = unionBox([visible, ...(content ? [content] : [])]);
  const pad = Math.max(world.w, world.h) * 0.08;
  const area = { x: world.x - pad, y: world.y - pad, w: world.w + pad * 2, h: world.h + pad * 2 };
  const scale = Math.min(W / area.w, H / area.h);
  const ox = (W - area.w * scale) / 2 - area.x * scale;
  const oy = (H - area.h * scale) / 2 - area.y * scale;
  frameRef.current = { scale, ox, oy };

  useEffect(() => {
    const canvas = canvasRef.current;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    items.forEach((item) => {
      const b = bbox(item);
      ctx.globalAlpha = item.type === "note" || item.type === "image" ? 0.75 : 0.55;
      ctx.fillStyle = item.color || lumiColors().iris;
      ctx.fillRect(ox + b.x * scale, oy + b.y * scale, Math.max(1.5, b.w * scale), Math.max(1.5, b.h * scale));
    });
    ctx.globalAlpha = 1;
    ctx.strokeStyle = lumiColors().iris;
    ctx.lineWidth = 1.5;
    ctx.fillStyle = `${lumiColors().iris}1a`;
    const vx = ox + visible.x * scale, vy = oy + visible.y * scale;
    ctx.fillRect(vx, vy, visible.w * scale, visible.h * scale);
    ctx.strokeRect(vx, vy, visible.w * scale, visible.h * scale);
  });

  function navigate(e) {
    const rect = canvasRef.current.getBoundingClientRect();
    const { scale: s, ox: x0, oy: y0 } = frameRef.current;
    onNavigate((e.clientX - rect.left - x0) / s, (e.clientY - rect.top - y0) / s);
  }

  return (
    <div className="minimap" aria-label="Minimap">
      <canvas
        ref={canvasRef}
        style={{ width: W, height: H }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          navigate(e);
        }}
        onPointerMove={(e) => e.buttons && navigate(e)}
      />
    </div>
  );
}

export default memo(Minimap);
