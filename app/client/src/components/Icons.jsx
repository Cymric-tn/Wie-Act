// Small stroke icons, drawn at 24×24 and coloured by currentColor.
const PATHS = {
  select: "M5 3l13 7-6 1.5L9.5 18z",
  hand: "M8 12V5.5a1.5 1.5 0 013 0V11M11 10V4.5a1.5 1.5 0 013 0V11M14 10.5V6a1.5 1.5 0 013 0v7c0 4-2.5 7-6 7-2.6 0-4.2-1.3-5.5-3.5L4 13.2c-.6-1 .6-2.2 1.6-1.5L8 13.5",
  pen: "M4 20l4-1 11-11-3-3L5 16l-1 4zM14 6l3 3",
  highlighter: "M9 11l-5 5v4h4l5-5M9 11l6-6 4 4-6 6M9 11l4 4M4 20h16",
  eraser: "M7 20h12M5 14l8-8 6 6-6 6H9l-4-4zM10 9l6 6",
  laser: "M12 12l8 8M12 12a3 3 0 100-6 3 3 0 000 6zM12 2v1.5M12 14.5V16M4 9h1.5M18.5 9H20M6.3 3.3l1 1M16.7 3.3l-1 1",
  line: "M5 19L19 5",
  arrow: "M5 19L19 5M10 5h9v9",
  rect: "M4 6h16v12H4z",
  ellipse: "M12 5c4.4 0 8 3.1 8 7s-3.6 7-8 7-8-3.1-8-7 3.6-7 8-7z",
  text: "M5 6V5h14v1M12 5v14M9 19h6",
  note: "M5 4h14v11l-5 5H5zM14 20v-5h5",
  equation: "M18 5H7l5 7-5 7h11",
  image: "M4 5h16v14H4zM4 16l5-5 4 4 2-2 5 5M15.5 9.5a1 1 0 100-2 1 1 0 000 2z",
  undo: "M9 14L4 9l5-5M4 9h10a6 6 0 010 12h-3",
  redo: "M15 14l5-5-5-5M20 9H10a6 6 0 000 12h3",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  download: "M12 4v11M7 10l5 5 5-5M5 20h14",
  link: "M10 14a4 4 0 006 0l3-3a4 4 0 00-6-6l-1 1M14 10a4 4 0 00-6 0l-3 3a4 4 0 006 6l1-1",
  users: "M16 19v-1a4 4 0 00-4-4H7a4 4 0 00-4 4v1M9.5 10a3 3 0 100-6 3 3 0 000 6zM21 19v-1a4 4 0 00-3-3.8M15 4.2a3 3 0 010 5.6",
  exit: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10",
  fill: "M4 6h16v12H4z",
  home: "M4 11l8-7 8 7M6 10v10h12V10",
  orbit: "M12 15a3 3 0 100-6 3 3 0 000 6zM12 21a9 9 0 100-18 9 9 0 000 18z",
  atlas: "M12 3l2.2 6.8H21l-5.5 4 2.1 6.7L12 16.4l-5.6 4.1 2.1-6.7L3 9.8h6.8z",
  board: "M4 5h16v11H4zM8 20l4-4 4 4",
  lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 017 0v3",
  unlock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 016.8-1.2",
  present: "M3 5h18v11H3zM8 20h8M12 16v4M10 8.5l4 2.5-4 2.5z",
  spotlight: "M12 3l2.2 6.8H21l-5.5 4 2.1 6.7L12 16.4l-5.6 4.1 2.1-6.7L3 9.8h6.8z",
  crown: "M4 18h16M5 15l-1-9 5 4 3-6 3 6 5-4-1 9z",
  timer: "M12 21a8 8 0 100-16 8 8 0 000 16zM12 9v4l2.5 1.5M10 2h4",
  history: "M4 12a8 8 0 102.3-5.6M4 4v3.5h3.5M12 8v4l3 2",
  play: "M8 5l11 7-11 7z",
  pause: "M8 5v14M16 5v14",
  help: "M12 21a9 9 0 100-18 9 9 0 000 18zM9.5 9.5a2.5 2.5 0 114 2c-1 .6-1.5 1.2-1.5 2.5M12 17h.01",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  left: "M15 5l-7 7 7 7",
  right: "M9 5l7 7-7 7",
  copy: "M8 8h11v11H8zM5 15V5h10",
  duplicate: "M8 8h11v11H8zM5 15V5h10M13.5 11v5M11 13.5h5",
  front: "M8 8h11v11H8zM5 5h8v3M5 5v8h3",
  back: "M5 5h11v11H5zM16 9h3v10H9v-3",
  fit: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  magic: "M5 19L15 9M14 4v3M19 9h-3M17.5 5.5l-2 2M9 4l.6 1.4L11 6l-1.4.6L9 8l-.6-1.4L7 6l1.4-.6zM19 14l.6 1.4L21 16l-1.4.6L19 18l-.6-1.4L17 16l1.4-.6z",
  close: "M6 6l12 12M18 6L6 18",
  edit: "M4 20h4L19 9l-4-4L4 16zM13 7l4 4",
  layout: "M4 4h16v16H4zM4 10h16M10 10v10",
  vote: "M12 5l7 9H5z",
  map: "M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2zM9 4v14M15 6v14",
  page: "M6 3h9l4 4v14H6zM14 3v5h5",
  sparkle: "M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
  key: "M14 10a4 4 0 10-4 4M10 14l-6 6M7 17l2 2M5 19l1.5 1.5",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  mic: "M12 15a3 3 0 003-3V6a3 3 0 00-6 0v6a3 3 0 003 3zM6 11a6 6 0 0012 0M12 17v4M9 21h6",
  micOff: "M15 10V6a3 3 0 00-5.7-1.3M9 9v3a3 3 0 004.8 2.4M6 11a6 6 0 009.6 4.8M18 11a6 6 0 01-.5 2.4M12 17v4M9 21h6M4 4l16 16",
  exam: "M8 4h8v3H8zM6 6H5v15h14V6h-1M9 12l2 2 4-4M9 17h6",
  book: "M4 5a2 2 0 012-2h13v15H6a2 2 0 00-2 2zM4 20a2 2 0 002 2h13v-4M9 7h6",
  globe: "M12 21a9 9 0 100-18 9 9 0 000 18zM3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3z",
  bell: "M6 16V11a6 6 0 0112 0v5l2 2H4zM10 20a2 2 0 004 0",
  search: "M11 18a7 7 0 100-14 7 7 0 000 14zM20 20l-4-4",
  settings: "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z",
  upload: "M12 16V4M7 9l5-5 5 5M5 20h14",
  chart: "M4 20V10M10 20V4M16 20v-7M22 20H2",
  calendar: "M4 6h16v15H4zM4 10h16M9 3v4M15 3v4",
  check: "M5 12l5 5L20 7",
  send: "M4 12l16-8-6 16-2-7z",
  menu: "M4 6h16M4 12h16M4 18h16",
  diagram: "M4 4h6v5H4zM14 15h6v5h-6zM14 4h6v5h-6zM7 9v3h10v3M17 9v0",
  shield: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6zM9 12l2 2 4-4",
  door: "M5 21V4h10l4 2v15M5 21h14M12 12h.01",
  planet: "M12 17a5 5 0 100-10 5 5 0 000 10zM3 15c-2-2 2-5 9-6.5S22 6 21 9c-.6 1.9-3.6 4.1-8.7 5.6C7.5 16 4 16 3 15z",
  wave: "M3 12h2M7 8v8M11 5v14M15 9v6M19 7v10M21 12h0",
  bolt: "M13 3L5 14h6l-1 7 8-11h-6z",
  chevronDown: "M6 9l6 6 6-6",
  sun: "M12 16a4 4 0 100-8 4 4 0 000 8zM12 2.5v2M12 19.5v2M4.6 4.6l1.4 1.4M18 18l1.4 1.4M2.5 12h2M19.5 12h2M4.6 19.4L6 18M18 6l1.4-1.4",
  moon: "M20 14.6A8.2 8.2 0 019.4 4 8.2 8.2 0 1020 14.6z"
};

export default function Icon({ name, size = 20, filled = false, strokeWidth = 1.8 }) {
  // Directional arrows mirror in right-to-left layouts.
  const flip = name === "left" || name === "right" ? "flip-rtl" : undefined;
  return (
    <svg
      className={flip}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      fillOpacity={filled ? 0.25 : undefined}
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={PATHS[name] || PATHS.more} />
    </svg>
  );
}
