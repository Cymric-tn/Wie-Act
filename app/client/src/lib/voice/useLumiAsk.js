// "Ask Lumi" in the voice channel: hold to record a question, release to send it. The server transcribes it, answers
// through the engine and sends the question and the answer to everyone in the channel (lumi:thinking, lumi:reply,
// lumi:error); each browser shows them in the transcript strip and reads the answer aloud.
//
// Recording uses a clone of the call's microphone track, forced on: a member muted to the group can still ask Lumi
// (the question audio goes only to the server; the answer is shared with the room).

import { useCallback, useEffect, useRef, useState } from "react";
import { clientId, readStored, store } from "../identity";
import { speak, stopSpeaking, canSpeak } from "./speak";

const MIN_MS = 400;
const MAX_MS = 20_000;
const KEEP = 20;
const READ_KEY = "lumi-voice-read-aloud";
const LANG_NAME = { fr: "French", en: "English", ar: "Arabic" };

function recorderType() {
  if (typeof MediaRecorder === "undefined") return null;
  return ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4"].find((t) => MediaRecorder.isTypeSupported?.(t)) || "";
}

export function useLumiAsk(socket, roomId, { joined, canUseLumi, getLocalStream, setLocalMicSuppressed, initialHistory }) {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [sending, setSending] = useState(false);
  const [thinking, setThinking] = useState(null); // { askedBy, stage, transcript }
  const [speaking, setSpeaking] = useState(false);
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const [readAloud, setReadAloudState] = useState(() => readStored(READ_KEY, "on") !== "off");

  const rec = useRef(null); // { recorder, track, chunks, startedAt, tick, cap, discard }
  const readRef = useRef(readAloud);
  const suppressRef = useRef(setLocalMicSuppressed);
  useEffect(() => {
    suppressRef.current = setLocalMicSuppressed;
  }, [setLocalMicSuppressed]);

  const setReadAloud = useCallback((on) => {
    readRef.current = on;
    setReadAloudState(on);
    store(READ_KEY, on ? "on" : "off");
    if (!on) stopSpeaking();
  }, []);

  const read = useCallback((entry) => {
    const text = entry.spoken || entry.reply;
    if (!readRef.current || !text) return;
    const { hasVoice, supported } = speak(text, entry.lang, {
      onStart: () => {
        setSpeaking(true);
        suppressRef.current?.(true);
      },
      onEnd: () => {
        setSpeaking(false);
        suppressRef.current?.(false);
      }
    });
    if (!supported) setNote("This browser cannot read aloud; Lumi's answers are shown as text.");
    else if (!hasVoice) setNote(`No ${LANG_NAME[entry.lang] || entry.lang} voice on this device, reading as text.`);
    else setNote(null);
  }, []);

  // The strip a latecomer sees: the room's last exchanges from the join ack.
  useEffect(() => {
    if (joined) setMessages((initialHistory || []).slice(-KEEP));
  }, [joined, initialHistory]);

  useEffect(() => {
    const me = clientId();
    const onThinking = (t) => {
      setThinking(t || null);
      if (t?.askedBy?.userId === me) setError(null);
    };
    const onReply = (entry) => {
      if (!entry?.id) return;
      setThinking(null);
      setMessages((list) => [...list.filter((m) => m.id !== entry.id), entry].slice(-KEEP));
      read(entry);
    };
    const onError = (e) => {
      setThinking(null);
      if (e?.askedBy?.userId === me) setError(e.message || "Lumi could not answer.");
    };
    socket.on("lumi:thinking", onThinking);
    socket.on("lumi:reply", onReply);
    socket.on("lumi:error", onError);
    return () => {
      socket.off("lumi:thinking", onThinking);
      socket.off("lumi:reply", onReply);
      socket.off("lumi:error", onError);
    };
  }, [socket, read]);

  // Leaving the call drops a recording in progress and Lumi's voice; unmounting too.
  useEffect(() => {
    if (joined) return;
    setThinking(null);
    const r = rec.current;
    if (r) {
      r.discard = true;
      clearInterval(r.tick);
      clearTimeout(r.cap);
      if (r.recorder.state !== "inactive") r.recorder.stop();
      setRecording(false);
    }
    stopSpeaking();
  }, [joined]);
  useEffect(() => () => {
    const r = rec.current;
    if (r) {
      r.discard = true;
      clearInterval(r.tick);
      clearTimeout(r.cap);
      if (r.recorder.state !== "inactive") r.recorder.stop();
      r.track.stop();
    }
    stopSpeaking();
  }, []);

  const send = useCallback((blob, mimeType) => {
    setSending(true);
    blob.arrayBuffer().then((audio) => {
      socket.timeout(75_000).emit("lumi:ask", { roomId, audio, mimeType }, (err, ack) => {
        setSending(false);
        if (err) setError("Lumi did not answer in time. Try again.");
        else if (!ack?.ok) setError(ack?.error || "Lumi could not answer.");
      });
    }).catch(() => {
      setSending(false);
      setError("The recording could not be read.");
    });
  }, [socket, roomId]);

  const stopAsk = useCallback(() => {
    const r = rec.current;
    if (!r || r.stopping) return;
    r.stopping = true;
    clearInterval(r.tick);
    clearTimeout(r.cap);
    if (performance.now() - r.startedAt < MIN_MS) {
      r.discard = true;
      setNote("Hold the button while you speak, then release.");
    }
    if (r.recorder.state !== "inactive") r.recorder.stop();
    else r.track.stop();
    setRecording(false);
  }, []);

  const startAsk = useCallback(() => {
    if (!joined || !canUseLumi || rec.current) return;
    if (thinking || sending) {
      setError("Lumi is answering, wait a moment");
      return;
    }
    const base = getLocalStream?.();
    const source = base?.getAudioTracks()[0];
    const type = recorderType();
    if (!source || type === null) {
      setError("This browser cannot record audio.");
      return;
    }
    stopSpeaking(); // do not record Lumi's own voice
    setError(null);
    setNote(null);
    const track = source.clone();
    track.enabled = true;
    let recorder;
    try {
      recorder = new MediaRecorder(new MediaStream([track]), { ...(type ? { mimeType: type } : {}), audioBitsPerSecond: 32_000 });
    } catch {
      track.stop();
      setError("This browser cannot record audio.");
      return;
    }
    const r = { recorder, track, chunks: [], startedAt: performance.now(), discard: false, stopping: false };
    recorder.ondataavailable = (e) => {
      if (e.data?.size) r.chunks.push(e.data);
    };
    recorder.onstop = () => {
      track.stop();
      rec.current = null;
      if (r.discard || !r.chunks.length) return;
      const mimeType = (recorder.mimeType || type || r.chunks[0].type || "audio/webm");
      send(new Blob(r.chunks, { type: mimeType }), mimeType);
    };
    r.tick = setInterval(() => setElapsed(performance.now() - r.startedAt), 100);
    r.cap = setTimeout(stopAsk, MAX_MS); // hard cap: sends what was said
    rec.current = r;
    recorder.start();
    setElapsed(0);
    setRecording(true);
  }, [joined, canUseLumi, thinking, sending, getLocalStream, send, stopAsk]);

  return {
    recording, elapsed, maxMs: MAX_MS, sending, thinking, speaking, messages, error, note, readAloud,
    canRead: canSpeak(), setReadAloud, startAsk, stopAsk, clearError: () => setError(null), clearNote: () => setNote(null)
  };
}
