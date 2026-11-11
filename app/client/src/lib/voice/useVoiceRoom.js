// Group voice for one board room: the microphone, one RTCPeerConnection per other member (full mesh, ≤ 6 people) and
// the voice:* signaling over the board's Socket.IO connection. The server relays signals only inside one voice channel.
//
// Glare is avoided by convention: the newcomer never offers. Everyone already in the channel offers to it, and it
// answers each. ICE candidates that arrive before the remote description are queued.

import { useCallback, useEffect, useRef, useState } from "react";

const DEFAULT_ICE = [{ urls: "stun:stun.l.google.com:19302" }];
const SPEAKING_RMS = 0.025; // time-domain RMS above which someone counts as speaking
const SPEAKING_HOLD_MS = 350; // keeps the ring on between words
const POLL_MS = 100;

function micError(error) {
  if (typeof window !== "undefined" && !window.isSecureContext) return "Voice needs HTTPS (or localhost) to use the microphone.";
  switch (error?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Microphone permission was denied. Allow it in the browser's site settings, then join again.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "No microphone was found on this device.";
    case "NotReadableError":
    case "AbortError":
      return "The microphone is busy in another app.";
    default:
      return "The microphone could not start.";
  }
}

export function useVoiceRoom(socket, roomId) {
  const [joined, setJoined] = useState(false);
  const [joining, setJoining] = useState(false);
  const [peers, setPeers] = useState([]); // { socketId, userId, name, color, muted, speaking, state }
  const [muted, setMuted] = useState(false);
  const [selfSpeaking, setSelfSpeaking] = useState(false);
  const [error, setError] = useState(null);
  const [welcome, setWelcome] = useState({ lumi: false, history: [] }); // from the join ack

  const r = useRef({
    joined: false, stream: null, iceServers: DEFAULT_ICE, pcs: new Map(), offerer: new Set(), restarted: new Set(),
    pending: new Map(), audios: new Map(), ctx: null, meters: new Map(), lastLoud: new Map(), speaking: new Map(),
    timer: null, muted: false, suppressed: false
  });

  const patchPeer = useCallback((socketId, patch) => {
    setPeers((list) => list.map((p) => (p.socketId === socketId ? { ...p, ...patch } : p)));
  }, []);

  // ---------- local track ----------
  const applyTrack = useCallback(() => {
    const s = r.current;
    s.stream?.getAudioTracks().forEach((t) => { t.enabled = !s.muted && !s.suppressed; });
  }, []);

  /** Lumi's text-to-speech turns this on while it reads, so the group does not hear Lumi twice through this mic. */
  const setLocalMicSuppressed = useCallback((on) => {
    r.current.suppressed = Boolean(on);
    applyTrack();
  }, [applyTrack]);

  // ---------- level meters (speaking rings) ----------
  const meter = useCallback((key, stream) => {
    const s = r.current;
    if (!s.ctx || !stream?.getAudioTracks().length) return;
    try {
      const source = s.ctx.createMediaStreamSource(stream);
      const analyser = s.ctx.createAnalyser();
      analyser.fftSize = 512;
      source.connect(analyser); // not to the destination: the <audio> element plays it
      s.meters.get(key)?.source.disconnect();
      s.meters.set(key, { source, analyser, buf: new Float32Array(analyser.fftSize) });
    } catch {
      // Some browsers refuse a source for a remote stream before it has data; the ring just stays off.
    }
  }, []);

  const startMeters = useCallback(() => {
    const s = r.current;
    clearInterval(s.timer);
    s.timer = setInterval(() => {
      const now = performance.now();
      s.meters.forEach((m, key) => {
        m.analyser.getFloatTimeDomainData(m.buf);
        let sum = 0;
        for (let i = 0; i < m.buf.length; i++) sum += m.buf[i] * m.buf[i];
        if (Math.sqrt(sum / m.buf.length) > SPEAKING_RMS) s.lastLoud.set(key, now);
        const on = now - (s.lastLoud.get(key) || 0) < SPEAKING_HOLD_MS;
        if (s.speaking.get(key) === on) return;
        s.speaking.set(key, on);
        if (key === "self") setSelfSpeaking(on);
        else patchPeer(key, { speaking: on });
      });
    }, POLL_MS);
  }, [patchPeer]);

  // ---------- peers ----------
  const closePeer = useCallback((id) => {
    const s = r.current;
    const pc = s.pcs.get(id);
    if (pc) {
      pc.onicecandidate = pc.ontrack = pc.onconnectionstatechange = null;
      pc.close();
    }
    s.pcs.delete(id);
    s.offerer.delete(id);
    s.restarted.delete(id);
    s.pending.delete(id);
    const audio = s.audios.get(id);
    if (audio) {
      audio.pause();
      audio.srcObject = null;
    }
    s.audios.delete(id);
    s.meters.get(id)?.source.disconnect();
    s.meters.delete(id);
    s.speaking.delete(id);
  }, []);

  const sendOffer = useCallback(async (id, options) => {
    const pc = r.current.pcs.get(id);
    if (!pc) return;
    const offer = await pc.createOffer(options);
    await pc.setLocalDescription(offer);
    socket.emit("voice:offer", { to: id, data: { type: pc.localDescription.type, sdp: pc.localDescription.sdp } });
  }, [socket]);

  const createPeer = useCallback((id) => {
    const s = r.current;
    if (s.pcs.has(id)) return s.pcs.get(id);
    const pc = new RTCPeerConnection({ iceServers: s.iceServers });
    s.stream.getTracks().forEach((t) => pc.addTrack(t, s.stream));
    pc.onicecandidate = (e) => {
      if (e.candidate) socket.emit("voice:ice", { to: id, data: e.candidate.toJSON() });
    };
    pc.ontrack = (e) => {
      const stream = e.streams[0] || new MediaStream([e.track]);
      let audio = s.audios.get(id);
      if (!audio) {
        audio = new Audio();
        audio.autoplay = true;
        audio.playsInline = true;
        s.audios.set(id, audio);
      }
      if (audio.srcObject !== stream) {
        audio.srcObject = stream;
        audio.play().catch(() => {}); // join was a click, so autoplay is allowed; a refusal only means no sound yet
        meter(id, stream);
      }
    };
    pc.onconnectionstatechange = () => {
      const state = pc.connectionState;
      patchPeer(id, { state });
      if (state === "failed" && s.offerer.has(id) && !s.restarted.has(id)) {
        // One ICE restart, started by the side that made the original offer (the other side answers it); after
        // that the pair stays "failed" and the participant row says so.
        s.restarted.add(id);
        pc.restartIce?.();
        sendOffer(id, { iceRestart: true }).catch(() => {});
      } else if (state === "closed") {
        closePeer(id);
      }
    };
    s.pcs.set(id, pc);
    return pc;
  }, [socket, meter, patchPeer, sendOffer, closePeer]);

  const flushIce = useCallback(async (id) => {
    const s = r.current;
    const pc = s.pcs.get(id);
    const queued = s.pending.get(id) || [];
    s.pending.delete(id);
    for (const c of queued) await pc?.addIceCandidate(c).catch(() => {});
  }, []);

  // ---------- teardown ----------
  const teardown = useCallback(() => {
    const s = r.current;
    [...s.pcs.keys()].forEach(closePeer);
    s.stream?.getTracks().forEach((t) => t.stop());
    s.stream = null;
    clearInterval(s.timer);
    s.timer = null;
    s.meters.forEach((m) => m.source.disconnect());
    s.meters.clear();
    s.speaking.clear();
    s.ctx?.close().catch(() => {});
    s.ctx = null;
    s.joined = false;
    s.suppressed = false;
    setJoined(false);
    setJoining(false);
    setPeers([]);
    setSelfSpeaking(false);
  }, [closePeer]);

  // ---------- signaling listeners (for the life of the board) ----------
  useEffect(() => {
    const s = r.current;
    const onPeerJoined = async (p) => {
      if (!s.joined || !p?.socketId) return;
      setPeers((list) => [...list.filter((x) => x.socketId !== p.socketId), { ...p, speaking: false, state: "new" }]);
      createPeer(p.socketId);
      s.offerer.add(p.socketId);
      sendOffer(p.socketId).catch(() => patchPeer(p.socketId, { state: "failed" }));
    };
    const onOffer = async ({ from, data } = {}) => {
      if (!s.joined || !from) return;
      try {
        const pc = createPeer(from);
        await pc.setRemoteDescription(data);
        await flushIce(from);
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        socket.emit("voice:answer", { to: from, data: { type: pc.localDescription.type, sdp: pc.localDescription.sdp } });
      } catch {
        patchPeer(from, { state: "failed" });
      }
    };
    const onAnswer = async ({ from, data } = {}) => {
      const pc = s.pcs.get(from);
      if (!pc) return;
      try {
        await pc.setRemoteDescription(data);
        await flushIce(from);
      } catch {
        patchPeer(from, { state: "failed" });
      }
    };
    const onIce = ({ from, data } = {}) => {
      if (!s.joined || !from || !data) return;
      const pc = s.pcs.get(from);
      if (!pc || !pc.remoteDescription) {
        const list = s.pending.get(from) || [];
        if (list.length < 100) list.push(data);
        s.pending.set(from, list);
        return;
      }
      pc.addIceCandidate(data).catch(() => {});
    };
    const onPeerLeft = ({ socketId } = {}) => {
      closePeer(socketId);
      setPeers((list) => list.filter((p) => p.socketId !== socketId));
    };
    const onPeerMuted = ({ socketId, muted: m } = {}) => patchPeer(socketId, { muted: Boolean(m) });
    const onDisconnect = () => {
      if (!s.joined) return;
      teardown(); // the server dropped us from the channel with the old socket
      setError("Voice disconnected. Join again once you are back online.");
    };

    const bound = [
      ["voice:peer-joined", onPeerJoined], ["voice:offer", onOffer], ["voice:answer", onAnswer], ["voice:ice", onIce],
      ["voice:peer-left", onPeerLeft], ["voice:peer-muted", onPeerMuted], ["disconnect", onDisconnect]
    ];
    bound.forEach(([name, fn]) => socket.on(name, fn));
    return () => {
      bound.forEach(([name, fn]) => socket.off(name, fn));
      if (s.joined) socket.emit("voice:leave", { roomId });
      teardown();
    };
  }, [socket, roomId, createPeer, sendOffer, flushIce, closePeer, patchPeer, teardown]);

  // ---------- actions ----------
  /** Must run from a click: the microphone prompt and audio playback both need a user gesture. */
  const join = useCallback(async () => {
    const s = r.current;
    if (s.joined || joining) return;
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof RTCPeerConnection === "undefined") {
      setError(window.isSecureContext ? "This browser cannot do voice calls." : "Voice needs HTTPS (or localhost) to use the microphone.");
      return;
    }
    setJoining(true);
    try {
      s.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false
      });
    } catch (e) {
      setJoining(false);
      setError(micError(e));
      return;
    }
    applyTrack();
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      s.ctx = Ctx ? new Ctx() : null;
      s.ctx?.resume?.().catch(() => {});
    } catch {
      s.ctx = null;
    }
    socket.timeout(8000).emit("voice:join", { roomId, muted: s.muted }, (err, ack) => {
      if (err || !ack?.ok) {
        teardown();
        setError(err ? "The server did not answer. Try again." : ack?.error === "Room full" ? "The voice channel is full (6 people)." : ack?.error || "Could not join voice.");
        return;
      }
      s.iceServers = Array.isArray(ack.iceServers) && ack.iceServers.length ? ack.iceServers : DEFAULT_ICE;
      s.joined = true;
      setPeers((ack.peers || []).map((p) => ({ ...p, speaking: false, state: "new" })));
      setWelcome({ lumi: Boolean(ack.lumi), history: Array.isArray(ack.history) ? ack.history : [] });
      meter("self", s.stream);
      startMeters();
      setJoined(true);
      setJoining(false);
    });
  }, [socket, roomId, joining, applyTrack, teardown, meter, startMeters]);

  const leave = useCallback(() => {
    if (r.current.joined) socket.emit("voice:leave", { roomId });
    teardown();
  }, [socket, roomId, teardown]);

  const toggleMute = useCallback(() => {
    const s = r.current;
    s.muted = !s.muted;
    setMuted(s.muted);
    applyTrack();
    if (s.joined) socket.emit("voice:mute", { roomId, muted: s.muted });
  }, [socket, roomId, applyTrack]);

  const getLocalStream = useCallback(() => r.current.stream, []);

  return {
    joined, joining, peers, muted, selfSpeaking, error, setError, welcome,
    join, leave, toggleMute, setLocalMicSuppressed, getLocalStream
  };
}
