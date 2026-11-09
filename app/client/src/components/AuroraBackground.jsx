// The app-wide light behind the glass. Rendered once by App, outside the routes.
//   <AuroraBackground intensity="calm" />   dashboards, study rooms
//   <AuroraBackground intensity="full" />   the Universe, sign-in, landing
import { useEffect } from "react";

// A fixed star field: the same sky on every visit. Offsets are viewport units, so it needs one element and no images.
const STARS = (() => {
  let seed = 23;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  return Array.from({ length: 56 }, () => {
    const big = rand() > 0.8;
    return `${(rand() * 100).toFixed(2)}vw ${(rand() * 100).toFixed(2)}vh 0 ${big ? "0.5px" : "0"} rgb(255 255 255 / ${(0.12 + rand() * 0.28).toFixed(2)})`;
  }).join(",");
})();

export default function AuroraBackground({ intensity = "calm" }) {
  // On the root element too: glass reads it to stay legible over the brighter sky.
  useEffect(() => {
    document.documentElement.dataset.aurora = intensity;
  }, [intensity]);

  return (
    <div className="aurora" data-intensity={intensity} aria-hidden="true">
      <div className="aurora-stars" style={{ boxShadow: STARS }} />
      <i className="aurora-blob aurora-iris" />
      <i className="aurora-blob aurora-glow" />
      <i className="aurora-blob aurora-violet" />
    </div>
  );
}
