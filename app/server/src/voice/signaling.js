// Group voice for a board room: WebRTC audio in a full mesh between the members, with Socket.IO as the signaling channel.
// No media goes through the server. Events are namespaced voice:* and scoped to the Socket.IO room `voice:<roomId>`.
//
// Membership: a socket may join the voice channel of the board room it has joined (board:join checked its access code);
// the server keeps the map below and relays offers / answers / ICE only between two sockets of the same voice channel.
// Glare is avoided by convention: the newcomer never offers; every member already there offers to it.

const MAX_SDP = 20_000;
const MAX_CANDIDATE = 2_000;
const DEFAULT_ICE = [{ urls: "stun:stun.l.google.com:19302" }];

function iceServersFromEnv(raw = process.env.ICE_SERVERS) {
  if (!raw) return DEFAULT_ICE;
  try {
    const list = JSON.parse(raw);
    return Array.isArray(list) && list.length ? list : DEFAULT_ICE;
  } catch {
    return DEFAULT_ICE;
  }
}

/** Only the fields an RTCSessionDescriptionInit / RTCIceCandidateInit has; anything else is dropped. */
function cleanSignal(event, data) {
  if (!data || typeof data !== "object") return null;
  if (event === "voice:ice") {
    if (typeof data.candidate !== "string" || data.candidate.length > MAX_CANDIDATE) return null;
    return {
      candidate: data.candidate,
      sdpMid: typeof data.sdpMid === "string" ? data.sdpMid.slice(0, 64) : null,
      sdpMLineIndex: Number.isInteger(data.sdpMLineIndex) ? data.sdpMLineIndex : null,
      ...(typeof data.usernameFragment === "string" ? { usernameFragment: data.usernameFragment.slice(0, 256) } : {})
    };
  }
  const type = event === "voice:offer" ? "offer" : "answer";
  if (data.type !== type || typeof data.sdp !== "string" || data.sdp.length > MAX_SDP) return null;
  return { type, sdp: data.sdp };
}

function createVoiceRooms({ io, maxPeers = Number(process.env.VOICE_MAX_PEERS) || 6, iceServers = iceServersFromEnv() } = {}) {
  /** roomId -> Map<socketId, { socketId, userId, name, color, muted }> */
  const voiceRooms = new Map();
  const channel = (roomId) => `voice:${roomId}`;
  const peersOf = (roomId, except) => [...(voiceRooms.get(roomId)?.values() || [])].filter((p) => p.socketId !== except);

  /** Removes the socket from its voice channel and tells the others. Safe to call when it is in none. */
  function leave(socket) {
    const roomId = socket.data.voiceRoom;
    if (!roomId) return;
    socket.data.voiceRoom = null;
    const peers = voiceRooms.get(roomId);
    peers?.delete(socket.id);
    if (peers && peers.size === 0) voiceRooms.delete(roomId);
    socket.leave(channel(roomId));
    io.to(channel(roomId)).emit("voice:peer-left", { socketId: socket.id });
  }

  /**
   * @param on  the connection's rate-limited handler registrar from app.js: on(event, handler, cost)
   * @param lumi  optional { canUse(user), history(roomId) } for the join ack
   */
  function register(socket, on, lumi) {
    on("voice:join", (payload, ack) => {
      const reply = typeof ack === "function" ? ack : () => {};
      const roomId = payload?.roomId;
      const user = socket.data.user;
      if (!user || typeof roomId !== "string" || socket.data.room !== roomId) {
        return reply({ ok: false, error: "Join the room's board before its voice channel." });
      }
      const peers = voiceRooms.get(roomId) || new Map();
      if (socket.data.voiceRoom !== roomId) {
        if (peers.size >= maxPeers) return reply({ ok: false, error: "Room full" });
        leave(socket);
        voiceRooms.set(roomId, peers);
        const me = { socketId: socket.id, userId: user.clientId, name: user.name, color: user.color, muted: Boolean(payload.muted) };
        peers.set(socket.id, me);
        socket.data.voiceRoom = roomId;
        socket.join(channel(roomId));
        socket.to(channel(roomId)).emit("voice:peer-joined", me);
      }
      reply({
        ok: true,
        peers: peersOf(roomId, socket.id),
        iceServers,
        maxPeers,
        lumi: lumi ? Boolean(lumi.canUse(user)) : false,
        history: lumi ? lumi.history(roomId) : []
      });
    }, 5);

    on("voice:leave", () => leave(socket));

    for (const event of ["voice:offer", "voice:answer", "voice:ice"]) {
      on(event, (payload) => {
        const roomId = socket.data.voiceRoom;
        const to = payload?.to;
        if (!roomId || typeof to !== "string" || to === socket.id) return;
        if (!voiceRooms.get(roomId)?.has(to)) return; // never relay outside the sender's own voice channel
        const data = cleanSignal(event, payload.data);
        if (data) io.to(to).emit(event, { from: socket.id, data });
      }, event === "voice:ice" ? 1 : 3);
    }

    on("voice:mute", (payload) => {
      const roomId = socket.data.voiceRoom;
      const me = roomId && voiceRooms.get(roomId)?.get(socket.id);
      if (!me || payload?.roomId !== roomId) return;
      me.muted = Boolean(payload.muted);
      socket.to(channel(roomId)).emit("voice:peer-muted", { socketId: socket.id, muted: me.muted });
    });
  }

  return { voiceRooms, channel, peersOf, leave, register, maxPeers, iceServers };
}

module.exports = { createVoiceRooms, cleanSignal, iceServersFromEnv, DEFAULT_ICE };
