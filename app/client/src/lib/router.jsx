// A tiny History-API router: enough for a handful of top-level screens.
import { useEffect, useState } from "react";

const EVENT = "ednova:navigate";

function current() {
  return { path: window.location.pathname.replace(/\/+$/, "") || "/", search: window.location.search };
}

export function navigate(to, { replace = false } = {}) {
  const before = window.location.pathname;
  window.history[replace ? "replaceState" : "pushState"](null, "", to);
  window.dispatchEvent(new Event(EVENT));
  if (window.location.pathname !== before) window.scrollTo(0, 0);
}

export function useLocation() {
  const [location, setLocation] = useState(current);
  useEffect(() => {
    const update = () => setLocation(current());
    window.addEventListener("popstate", update);
    window.addEventListener(EVENT, update);
    return () => {
      window.removeEventListener("popstate", update);
      window.removeEventListener(EVENT, update);
    };
  }, []);
  return location;
}

/** An anchor that navigates in-app, while modified clicks still open new tabs. */
export function Link({ to, onClick, children, ...rest }) {
  return (
    <a
      href={to}
      onClick={(e) => {
        onClick?.(e);
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        navigate(to);
      }}
      {...rest}
    >
      {children}
    </a>
  );
}
