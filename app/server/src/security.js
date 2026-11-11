// Small, dependency-free hardening for the API server.
//
//  securityHeaders   every response: no MIME sniffing, no framing, no referrer, no caching of API answers, and no
//                    "x-powered-by". The API never serves the web app's HTML, so the policy can be strict.
//  matchesType       an upload must BE what it says it is: a PDF starts with %PDF-, a .docx is a zip, Markdown and
//                    text are UTF-8 without control bytes. The declared type alone is the browser's claim.
//  fileHeaders       an uploaded file is opened in the browser from the API's origin: it is sandboxed (no script, no
//                    form, no top navigation), and anything that is not a PDF or plain text is a download.
const { TextDecoder } = require("node:util");

function securityHeaders(app) {
  app.disable("x-powered-by");
  return (req, res, next) => {
    res.set({
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
      "Referrer-Policy": "no-referrer",
      "Cross-Origin-Opener-Policy": "same-origin",
      "Permissions-Policy": "camera=(), geolocation=(), payment=()",
      "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    });
    if (req.path.startsWith("/api/")) res.set("Cache-Control", "no-store");
    next();
  };
}

const PDF = Buffer.from("%PDF-");
const ZIP = Buffer.from([0x50, 0x4b, 0x03, 0x04]);
const strictUtf8 = new TextDecoder("utf-8", { fatal: true });

function isText(bytes) {
  try {
    const text = strictUtf8.decode(bytes);
    // eslint-disable-next-line no-control-regex
    return !/[\u0000-\u0008\u000e-\u001f]/.test(text);
  } catch {
    return false;
  }
}

/** Do the bytes fit the declared type? (PDF readers accept a little junk before the header: the first 1 KB is searched.) */
function matchesType(mime, bytes) {
  if (mime === "application/pdf") return bytes.subarray(0, 1024).includes(PDF);
  if (mime === "application/vnd.openxmlformats-officedocument.wordprocessingml.document") return bytes.subarray(0, 4).equals(ZIP);
  if (mime === "text/markdown" || mime === "text/plain") return isText(bytes);
  return false;
}

/** Headers for an uploaded file sent back to a browser. Markdown goes out as plain text: it is never rendered as HTML. */
function fileHeaders(mime, filename) {
  const safe = String(filename || "file").replace(/["\\\r\n]/g, "_");
  const pdf = mime === "application/pdf";
  const text = mime === "text/markdown" || mime === "text/plain";
  return {
    "Content-Type": text ? "text/plain; charset=utf-8" : mime,
    "Content-Disposition": `${pdf || text ? "inline" : "attachment"}; filename="${safe}"`,
    // a sandbox stops Chrome's PDF viewer, so a PDF gets the strict policy without it
    "Content-Security-Policy": `${pdf ? "" : "sandbox; "}default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'`,
    "X-Content-Type-Options": "nosniff"
  };
}

module.exports = { securityHeaders, matchesType, fileHeaders };
