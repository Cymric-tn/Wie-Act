import { useEffect } from "react";
import { LanguageProvider } from "./lib/i18n";
import { navigate, useLocation } from "./lib/router";
import { getUser } from "./lib/session";
import Landing from "./components/landing/Landing";
import SignIn from "./components/auth/SignIn";
import Dashboard from "./components/dashboard/Dashboard";
import BoardApp from "./components/BoardApp";
import AuroraBackground from "./components/AuroraBackground";
import "./App.css";
import "./styles/aurora.css";
import "./styles/voice.css";
import "./styles/glass.css"; // last: glass and controls win over the older per-screen rules

function Redirect({ to }) {
  useEffect(() => navigate(to, { replace: true }), [to]);
  return null;
}

function Routes() {
  const { path, search } = useLocation();

  if (path === "/board") return <BoardApp />;
  if (path === "/login") return <SignIn />;
  if (path === "/app" || path.startsWith("/app/")) {
    const user = getUser();
    if (!user) return <Redirect to="/login" />;
    return <Dashboard section={path.split("/")[2] || "home"} />;
  }
  // Old invite links (/?room=…) now open the whiteboard.
  if (path === "/" && new URLSearchParams(search).has("room")) return <Redirect to={`/board${search}`} />;
  if (path !== "/") return <Redirect to="/" />;
  return <Landing />;
}

// Full aurora where there is room to see it, calm where people read and draw.
const auroraFor = (path) => (path === "/" || path === "/login" || path === "/app/map" ? "full" : "calm");

export default function App() {
  const { path } = useLocation();
  return (
    <LanguageProvider>
      {/* Outside the routes, so it never re-mounts on navigation. */}
      <AuroraBackground intensity={auroraFor(path)} />
      <Routes />
    </LanguageProvider>
  );
}
