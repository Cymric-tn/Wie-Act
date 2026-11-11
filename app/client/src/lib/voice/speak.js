// Lumi's answers read aloud with the browser's own speechSynthesis (FelCloud has no TTS). The voices are the device's:
// quality differs between machines and an Arabic voice may be missing; the answer is always shown as text as well.

const LANG_MAP = { fr: "fr-FR", en: "en-US", ar: "ar-SA" };
const CHUNK = 200; // Chrome stops long utterances after ~15 s; sentences are queued instead
const supported = () => typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;

let cachedVoices = [];
function voices() {
  if (!supported()) return [];
  const list = window.speechSynthesis.getVoices();
  if (list.length) cachedVoices = list;
  return cachedVoices;
}
// getVoices() is often empty until the browser has loaded them.
if (supported()) window.speechSynthesis.addEventListener?.("voiceschanged", voices);

export function pickVoice(bcp47) {
  const list = voices();
  const base = bcp47.slice(0, 2).toLowerCase();
  return list.find((v) => v.lang === bcp47)
    || list.find((v) => v.lang?.replace("_", "-").toLowerCase().startsWith(base))
    || null;
}

export const canSpeak = supported;

function chunks(text) {
  const sentences = String(text).match(/[^.!?؟۔…]+[.!?؟۔…]*\s*/g) || [String(text)];
  const out = [];
  for (const s of sentences) {
    const last = out.length - 1;
    if (last >= 0 && out[last].length + s.length <= CHUNK) out[last] += s;
    else out.push(s);
  }
  return out.map((c) => c.trim()).filter(Boolean);
}

export function stopSpeaking() {
  if (supported()) window.speechSynthesis.cancel();
}

/**
 * Reads text in fr | en | ar. onStart when sound begins, onEnd exactly once when it finishes, fails or is cancelled
 * (also after a safety delay, so the caller's microphone is never left suppressed). -> { hasVoice }.
 */
export function speak(text, lang, { onStart, onEnd } = {}) {
  if (!supported() || !text) {
    onEnd?.();
    return { hasVoice: false, supported: supported() };
  }
  const synth = window.speechSynthesis;
  synth.cancel();
  const tag = LANG_MAP[lang] || LANG_MAP.en;
  const voice = pickVoice(tag);
  let finished = false;
  let started = false;
  const finish = () => {
    if (finished) return;
    finished = true;
    clearTimeout(safety);
    onEnd?.();
  };
  const safety = setTimeout(finish, text.length * 90 + 3000);
  const parts = chunks(text);
  parts.forEach((part, i) => {
    const u = new SpeechSynthesisUtterance(part);
    u.lang = tag;
    if (voice) u.voice = voice;
    u.rate = 1;
    u.onstart = () => {
      if (!started) {
        started = true;
        onStart?.();
      }
    };
    u.onerror = finish;
    if (i === parts.length - 1) u.onend = finish;
    synth.speak(u);
  });
  return { hasVoice: Boolean(voice), supported: true };
}
