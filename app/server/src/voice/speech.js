// Lumi voice: turning an engine answer into something a speech synthesizer can read, and the few fixed lines Lumi says
// without spending a model call (off-topic, busy, quota, not heard). No dependency; tested in test/voice.test.js.

const SPOKEN_MAX = 600; // ~40 s of speech; the full answer stays readable in the transcript strip
const DISPLAY_MAX = 1500;

// LaTeX the explain task can emit -> a symbol the browser voices read (√ is read "square root" / "racine carrée").
const TEX = {
  sqrt: "√", times: "×", cdot: "·", div: "÷", pm: "±", infty: "∞", to: "→", rightarrow: "→", Rightarrow: "⇒",
  leq: "≤", le: "≤", geq: "≥", ge: "≥", neq: "≠", ne: "≠", approx: "≈", pi: "π", alpha: "α", beta: "β", Delta: "Δ",
  lim: "lim", ln: "ln", log: "log", sin: "sin", cos: "cos", tan: "tan", exp: "exp", in: "∈", mathbb: "", left: "", right: "",
  quad: " ", qquad: " ", text: "", mathrm: "", displaystyle: ""
};

function stripMarkup(text) {
  return String(text || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/https?:\/\/\S+|www\.\S+/g, "")
    .replace(/^\s{0,3}#{1,6}\s*/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^\s*[-*+•]\s+/gm, "")
    .replace(/^\s*\|.*\|\s*$/gm, (row) => row.replace(/\|/g, " ").replace(/-{3,}/g, " "))
    .replace(/\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, "($1)/($2)")
    .replace(/\\([A-Za-z]+)/g, (_, name) => (name in TEX ? TEX[name] : name))
    .replace(/\\[()[\]]/g, "")
    .replace(/\$+/g, "")
    .replace(/[{}]/g, "")
    .replace(/\*\*|__|[*`~]/g, "")
    .replace(/\p{Extended_Pictographic}️?/gu, "")
    .replace(/[ \t]+/g, " ");
}

/** Cuts at the last sentence end before max (., !, ?, Arabic ؟ and ۔, …); else at a word, with an ellipsis. */
function cutAtSentence(text, max) {
  if (text.length <= max) return text;
  const head = text.slice(0, max + 1);
  let end = -1;
  for (const m of head.matchAll(/[.!?؟۔…](?=\s|$)/g)) end = m.index + 1;
  if (end >= max * 0.4) return head.slice(0, end).trim();
  const space = head.lastIndexOf(" ");
  return `${head.slice(0, space > 0 ? space : max).trim()}…`;
}

/** -> { display, spoken }: the answer for the transcript strip (paragraphs kept) and for text-to-speech (one flow). */
function forSpeech(text, { spokenMax = SPOKEN_MAX, displayMax = DISPLAY_MAX } = {}) {
  const clean = stripMarkup(text);
  const paragraphs = clean.split(/\n+/).map((l) => l.trim()).filter(Boolean);
  const display = cutAtSentence(paragraphs.join("\n"), displayMax);
  // A line without its own full stop (a heading, an exercise) still gets a pause when read aloud.
  const spoken = cutAtSentence(paragraphs.map((l) => (/[.!?؟۔…:;,]$/.test(l) ? l : `${l}.`)).join(" "), spokenMax);
  return { display, spoken };
}

/**
 * fr | en | ar for the reader voice, from the text's own script: Arabic letters -> ar. Latin text in an Arabic-language
 * turn is French (the engine's Darija answers often have a French body); otherwise the question's language stands.
 */
function guessLang(text, fallback = "en") {
  const arabic = (String(text).match(/[\u0600-\u06FF]/g) || []).length;
  const latin = (String(text).match(/[A-Za-zÀ-ÿ]/g) || []).length;
  if (arabic > latin) return "ar";
  if (fallback === "ar") return latin ? "fr" : "ar";
  return fallback === "fr" || fallback === "en" ? fallback : "en";
}

// Fixed lines: Lumi's own words when no model answer is spoken. Keyed by the app's fr | en | ar.
const LINES = {
  offTopic: {
    en: "Let's keep our focus on the session. Ask me about the course or about what is on the board.",
    fr: "Restons concentrés sur la séance. Pose-moi une question sur le cours ou sur ce qui est au tableau.",
    ar: "لنركز على جلسة المراجعة. اسألني عن الدرس أو عما هو مكتوب على السبورة."
  },
  busy: {
    en: "I am busy with another question from your room. Ask me again in {s} seconds.",
    fr: "Je réponds déjà à une autre question de votre salle. Redemande-moi dans {s} secondes.",
    ar: "أنا أجيب على سؤال آخر من غرفتكم. أعد السؤال بعد {s} ثانية."
  },
  quota: {
    en: "The AI service has reached its limit for the moment. Try again in {s} seconds.",
    fr: "Le service d'IA a atteint sa limite pour le moment. Réessaie dans {s} secondes.",
    ar: "بلغت خدمة الذكاء الاصطناعي حدها حاليا. حاول مرة أخرى بعد {s} ثانية."
  },
  unavailable: {
    en: "I can't reach the AI service right now. Try again in a moment.",
    fr: "Je n'arrive pas à joindre le service d'IA pour l'instant. Réessaie dans un instant.",
    ar: "لا أستطيع الوصول إلى خدمة الذكاء الاصطناعي الآن. حاول بعد قليل."
  }
};

function line(name, lang, vars = {}) {
  const set = LINES[name] || LINES.unavailable;
  const text = set[lang] || set.en;
  return text.replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));
}

module.exports = { forSpeech, stripMarkup, cutAtSentence, guessLang, line, LINES, SPOKEN_MAX };
