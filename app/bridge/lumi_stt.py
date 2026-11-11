"""Speech to text for Lumi voice (push-to-talk questions), with faster-whisper. No LLM, no FelCloud quota.

The model loads lazily (warm() at bridge start so the first question of a demo is not slow) and runs one transcription
at a time: it is CPU work, and two at once would only make both slow. faster-whisper decodes the browser's webm/opus
through PyAV, which ships its own ffmpeg libraries, so no system ffmpeg is needed.

Env:
    WHISPER_MODEL      tiny | base | small (default) | medium | large-v3 | a local path; "off" disables speech input
    WHISPER_DEVICE     auto (default) | cpu | cuda
    WHISPER_COMPUTE    int8 (default) | int8_float16 | float16 | float32
    WHISPER_LANGUAGES  fr,en,ar (default): Whisper detects the language, then the best of these is used for decoding.
                       Tunisian Darija is usually heard as Arabic; restricting the choice keeps a short or accented
                       question from being decoded as, say, Spanish or Persian. Empty = any language Whisper knows.
"""

import os
import sys
import threading

# Whisper's codes -> the app's three languages. Anything else is reported as detected but mapped to en.
LANG_MAP = {"fr": "fr", "en": "en", "ar": "ar"}


class SttUnavailable(Exception):
    """faster-whisper is not installed, the model could not load, or speech input is switched off."""


class SpeechToText:
    def __init__(self, model=None, device=None, compute=None, languages=None):
        self.model_name = model or os.environ.get("WHISPER_MODEL", "small")
        self.device = device or os.environ.get("WHISPER_DEVICE", "auto")
        self.compute = compute or os.environ.get("WHISPER_COMPUTE", "int8")
        raw = languages if languages is not None else os.environ.get("WHISPER_LANGUAGES", "fr,en,ar")
        self.languages = [x.strip() for x in raw.split(",") if x.strip()]
        self._model = None
        self._error = None
        self._load_lock = threading.Lock()
        self._run_lock = threading.Lock()

    @property
    def enabled(self):
        return self.model_name.lower() not in ("off", "none", "0", "")

    def status(self):
        return {"enabled": self.enabled, "model": self.model_name if self.enabled else None,
                "loaded": self._model is not None, "error": self._error}

    def _get(self):
        if not self.enabled:
            raise SttUnavailable("speech input is off (WHISPER_MODEL=off)")
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError:
                    self._error = "faster-whisper is not installed (pip install faster-whisper)"
                    raise SttUnavailable(self._error) from None
                try:
                    self._model = WhisperModel(self.model_name, device=self.device, compute_type=self.compute)
                    self._error = None
                except Exception as error:   # download failed, bad path, no CUDA...
                    self._error = f"{type(error).__name__}: {error}"
                    raise SttUnavailable(self._error) from error
        return self._model

    def warm(self):
        """Loads the model in the background (downloads it on the very first run)."""
        if not self.enabled:
            return

        def go():
            try:
                self._get()
            except SttUnavailable as error:
                print(f"speech input unavailable: {error}", file=sys.stderr, flush=True)
        threading.Thread(target=go, name="stt-warm", daemon=True).start()

    def transcribe(self, path):
        """-> {"text", "lang" (fr|en|ar), "detected" (Whisper's code), "probability", "duration_s"}."""
        model = self._get()
        with self._run_lock:
            from faster_whisper import decode_audio
            audio = decode_audio(path)
            language = None
            probability = None
            detected = None
            if self.languages and hasattr(model, "detect_language"):
                try:
                    detected, probability, ranked = model.detect_language(audio, vad_filter=True)
                except Exception:          # nothing left after VAD (silence): let transcribe() say so
                    ranked = []
                allowed = [(code, p) for code, p in ranked if code in self.languages]
                if allowed:
                    language, probability = max(allowed, key=lambda x: x[1])
            segments, info = model.transcribe(audio, language=language, vad_filter=True, beam_size=1,
                                              condition_on_previous_text=False)
            text = " ".join(s.text.strip() for s in segments).strip()
        code = language or info.language
        return {"text": text, "lang": LANG_MAP.get(code, "en"), "detected": detected or info.language,
                "probability": round(float(probability if probability is not None else info.language_probability), 3),
                "duration_s": round(float(info.duration), 2)}
