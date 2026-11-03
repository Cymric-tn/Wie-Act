// Turning dropped, pasted or uploaded files into board images.
const MAX_SIDE = 1600;
const MAX_BYTES = 2_300_000; // stays under the server's 2.5 MB limit per image

function canvasToDataUrl(canvas, preferPng) {
  if (preferPng) {
    const png = canvas.toDataURL("image/png");
    if (png.length <= MAX_BYTES) return png;
  }
  for (const quality of [0.86, 0.72, 0.58, 0.45]) {
    const jpeg = canvas.toDataURL("image/jpeg", quality);
    if (jpeg.length <= MAX_BYTES) return jpeg;
  }
  return null;
}

/** Downscales until the encoded image fits the size budget. */
function encode(source, width, height, { preferPng = false, white = false } = {}) {
  let scale = Math.min(1, MAX_SIDE / Math.max(width, height));
  for (let attempt = 0; attempt < 4; attempt++) {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width * scale));
    canvas.height = Math.max(1, Math.round(height * scale));
    const ctx = canvas.getContext("2d");
    if (white || !preferPng) {
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    const src = canvasToDataUrl(canvas, preferPng);
    if (src) return { src, w: canvas.width, h: canvas.height };
    scale *= 0.7;
  }
  throw new Error("That image is too large to add to the board.");
}

export async function imageFileToData(file) {
  const bitmap = await createImageBitmap(file);
  try {
    return encode(bitmap, bitmap.width, bitmap.height, { preferPng: /png|gif|webp/.test(file.type) });
  } finally {
    bitmap.close?.();
  }
}

/** Renders each PDF page to an image. Loads pdf.js on first use. */
export async function pdfFileToPages(file, { maxPages = 20, onProgress } = {}) {
  const pdfjs = await import("pdfjs-dist");
  const { default: workerUrl } = await import("pdfjs-dist/build/pdf.worker.min.mjs?url");
  pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

  const task = pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) });
  const doc = await task.promise;
  const total = doc.numPages;
  const count = Math.min(total, maxPages);
  const pages = [];
  for (let n = 1; n <= count; n++) {
    const page = await doc.getPage(n);
    const base = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: MAX_SIDE / Math.max(base.width, base.height) });
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(viewport.width);
    canvas.height = Math.round(viewport.height);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    await page.render({ canvas, viewport }).promise;
    pages.push({ ...encode(canvas, canvas.width, canvas.height, { white: true }), number: n });
    onProgress?.(n, count);
  }
  await task.destroy();
  return { pages, total };
}

export const isPdf = (file) => file.type === "application/pdf" || /\.pdf$/i.test(file.name);
export const isImage = (file) => /^image\/(png|jpeg|webp|gif)$/.test(file.type);
