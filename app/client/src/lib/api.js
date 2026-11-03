// The EdNova server: port 3000 on whatever host served the page, so LAN testing
// works without editing anything. Override with VITE_SERVER_URL.
export const SERVER_URL =
  import.meta.env?.VITE_SERVER_URL ||
  (typeof window !== "undefined" ? `${window.location.protocol}//${window.location.hostname}:3000` : "http://localhost:3000");
