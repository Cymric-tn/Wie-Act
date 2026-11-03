// LaTeX → SVG with MathJax, loaded on first use so the main bundle stays small.
import texSvgUrl from "mathjax/es5/tex-svg-full.js?url";

const EX_TO_WORLD = 9; // world units per TeX "ex"
let loading = null;

function loadMathJax() {
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      window.MathJax = {
        svg: { fontCache: "none" },
        startup: {
          typeset: false,
          ready() {
            window.MathJax.startup.defaultReady();
            window.MathJax.startup.promise.then(() => resolve(window.MathJax));
          }
        }
      };
      const script = document.createElement("script");
      script.src = texSvgUrl;
      script.async = true;
      script.onerror = () => reject(new Error("Could not load the equation renderer."));
      document.head.appendChild(script);
    });
  }
  return loading;
}

/**
 * Renders LaTeX to a standalone SVG data URL.
 * Resolves { src, w, h } in world units; rejects with the TeX error message.
 */
export async function texToSvg(latex, color) {
  const MathJax = await loadMathJax();
  const node = MathJax.tex2svg(latex, { display: true });
  const error = node.querySelector("[data-mjx-error]");
  if (error) throw new Error(error.getAttribute("data-mjx-error"));
  const svg = node.querySelector("svg");
  const w = parseFloat(svg.getAttribute("width")) * EX_TO_WORLD;
  const h = parseFloat(svg.getAttribute("height")) * EX_TO_WORLD;
  svg.setAttribute("width", `${w}px`);
  svg.setAttribute("height", `${h}px`);
  svg.setAttribute("style", `color:${color}`);
  svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const markup = new XMLSerializer().serializeToString(svg);
  return { src: `data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`, w, h };
}
