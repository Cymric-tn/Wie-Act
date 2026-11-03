import { useEffect, useRef, useState } from "react";
import { socket } from "../socket";
import Lobby from "./Lobby";
import Board from "./Board";
import { clientId, readStored, store } from "../lib/identity";
import { getUser } from "../lib/session";

function roomFromUrl() {
  const params = new URLSearchParams(window.location.search);
  return params.get("room") || params.get("course") || "";
}

export default function BoardApp() {
  const [status, setStatus] = useState(socket.connected ? "Connected" : "Connecting…");
  const [session, setSession] = useState(null); // { room, me, users, items, active, meta }
  const [error, setError] = useState(null);
  const wantedRef = useRef(null); // last join request, replayed after reconnects

  // The board's interface is English and laid out left-to-right in every language.
  useEffect(() => {
    const html = document.documentElement;
    const previous = html.dir;
    html.dir = "ltr";
    return () => {
      html.dir = previous;
    };
  }, []);

  useEffect(() => {
    const onConnect = () => {
      setStatus("Connected");
      // After a reconnect, rejoin the same room; the server sends a fresh snapshot.
      if (wantedRef.current) socket.emit("board:join", wantedRef.current);
    };
    const onDisconnect = () => setStatus("Reconnecting…");
    const onConnectError = () => setStatus("Server unreachable");
    const onJoined = (data) => {
      setSession(data);
      setError(null);
      const url = new URL(window.location.href);
      url.search = "";
      url.searchParams.set("room", data.room);
      window.history.replaceState(null, "", url);
    };
    const onError = (err) => {
      setError(typeof err === "string" ? { message: err } : err);
      if (err?.code === "code-required" || err?.code === "code-wrong") {
        wantedRef.current = null;
        setSession(null);
      }
    };

    // The socket may have connected before this effect subscribed.
    if (socket.connected) setStatus("Connected");
    socket.on("connect", onConnect);
    socket.on("disconnect", onDisconnect);
    socket.on("connect_error", onConnectError);
    socket.on("board:joined", onJoined);
    socket.on("board:error", onError);
    return () => {
      socket.off("connect", onConnect);
      socket.off("disconnect", onDisconnect);
      socket.off("connect_error", onConnectError);
      socket.off("board:joined", onJoined);
      socket.off("board:error", onError);
    };
  }, []);

  // Signed-in users arriving with a room (from the dashboard) skip the lobby.
  const autoJoinedRef = useRef(false);
  useEffect(() => {
    if (status !== "Connected" || session || autoJoinedRef.current) return;
    const user = getUser();
    const room = roomFromUrl();
    if (!user || !room) return;
    autoJoinedRef.current = true;
    join({ name: user.name, room, code: "" });
  });

  function join({ name, room, code }) {
    store("ednova-board-name", name);
    wantedRef.current = { name, room, code, clientId: clientId() };
    setError(null);
    socket.emit("board:join", wantedRef.current);
  }

  function leave() {
    socket.emit("board:leave");
    wantedRef.current = null;
    setSession(null);
    const url = new URL(window.location.href);
    url.search = "";
    window.history.replaceState(null, "", url);
  }

  if (!session) {
    return (
      <Lobby
        status={status}
        initialName={readStored("ednova-board-name", "")}
        initialRoom={roomFromUrl() || "classroom"}
        error={error}
        onJoin={join}
      />
    );
  }

  return <Board key={session.room} session={session} status={status} onLeave={leave} />;
}
