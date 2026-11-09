import { useEffect, useState } from "react";
import Icon from "../Icons";
import ProgressMap from "./ProgressMap";
import AICompanion from "./AICompanion";
import Prep from "./Prep";
import Materials from "./Materials";
import Review, { EngineStatusCard } from "./Review";
import {
  Card, RoomsCard, BoardCard, PrepCard, MaterialsCard, CompanionCard, JoinRoom, RoomList,
  BoardPreview, AiBoardChips, ClassKpis, GroupProgress, Roster, WeakPoints, ProfActions, openBoard
} from "./Widgets";
import { useI18n, LanguageSwitcher, LANGUAGES } from "../../lib/i18n";
import { Link, navigate } from "../../lib/router";
import { getUser, saveUser, signOut } from "../../lib/session";
import { ROOMS, CLASS, courseMastery } from "../../lib/demoData";
import { listExams } from "../../lib/exams";
import { refreshCourseMap, useCourses } from "../../lib/courseMap";
import { initials } from "../../lib/format";
import { ai, AI_LANGUAGES, replyLanguage, setReplyLanguage } from "../../lib/engine";
import Diagrams from "./Diagrams";
import "./dashboard.css";
import { LumiLogo, LumiSpark } from "../LumiSpark";

const NAV = {
  student: [
    { group: "nav.learn", items: [
      { id: "home", icon: "home", key: "nav.home" },
      { id: "map", icon: "planet", key: "nav.map" },
      { id: "prep", icon: "exam", key: "nav.prep" },
      { id: "materials", icon: "book", key: "nav.materials" },
      { id: "diagrams", icon: "diagram", key: "nav.diagrams" }
    ] },
    { group: "nav.together", items: [
      { id: "rooms", icon: "users", key: "nav.rooms" },
      { id: "board", icon: "board", key: "nav.board", badge: "live" }
    ] }
  ],
  professor: [
    { group: "nav.teach", items: [
      { id: "home", icon: "home", key: "nav.overview" },
      { id: "review", icon: "shield", key: "nav.review", count: "flags" },
      { id: "materials", icon: "book", key: "nav.materials" }
    ] },
    { group: "nav.together", items: [
      { id: "rooms", icon: "door", key: "nav.rooms" },
      { id: "board", icon: "board", key: "nav.board", badge: "live" }
    ] }
  ]
};

const titleKey = (role, section) =>
  NAV[role].flatMap((g) => g.items).find((i) => i.id === section)?.key || (section === "settings" ? "nav.settings" : "nav.home");

function greetingKey() {
  const h = new Date().getHours();
  return h < 12 ? "greet.morning" : h < 18 ? "greet.afternoon" : "greet.evening";
}

/** Open review items, for the professor's sidebar badge. */
function useOpenFlags(enabled) {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const load = () => ai.status().then((s) => alive && setN(s.open_flags || 0)).catch(() => {});
    load();
    const id = setInterval(load, 20000);
    window.addEventListener("lumi:flags", load);
    return () => {
      alive = false;
      clearInterval(id);
      window.removeEventListener("lumi:flags", load);
    };
  }, [enabled]);
  return n;
}

function Sidebar({ user, section, open, onClose, onCompanion, onSignOut }) {
  const { t } = useI18n();
  const flags = useOpenFlags(user.role === "professor");
  return (
    <>
      <div className={"d-scrim" + (open ? " open" : "")} onClick={onClose} aria-hidden="true" />
      <aside className={"d-sidebar" + (open ? " open" : "")} aria-label="Main menu">
        <Link to="/" className="d-brand"><LumiLogo height={26} /></Link>
        <p className="d-tagline">{t("tagline")}</p>
        <nav className="d-nav">
          {NAV[user.role].map((group) => (
            <div key={group.group} className="d-nav-group">
              <span className="d-nav-label">{t(group.group)}</span>
              {group.items.map((item) => (
                <Link
                  key={item.id}
                  to={item.id === "home" ? "/app" : `/app/${item.id}`}
                  className={"d-nav-item" + (section === item.id ? " active" : "")}
                  aria-current={section === item.id ? "page" : undefined}
                  onClick={onClose}
                >
                  <Icon name={item.icon} size={19} />
                  <span>{t(item.key)}</span>
                  {item.badge && <em className={`d-badge ${item.badge}`}>{t(`badge.${item.badge}`)}</em>}
                  {item.count === "flags" && flags > 0 && <em className="d-count">{flags}</em>}
                </Link>
              ))}
            </div>
          ))}
        </nav>
        <div className="d-side-bottom">
          <button className="d-companion-btn" onClick={onCompanion}>
            <span className="d-ai-avatar small"><LumiSpark size={18} onDark /></span>
            <span>{t("nav.companion")}</span>
          </button>
          <Link to="/app/settings" className={"d-nav-item" + (section === "settings" ? " active" : "")} onClick={onClose}>
            <Icon name="settings" size={19} /><span>{t("nav.settings")}</span>
          </Link>
          <div className="d-user">
            <span className="d-avatar" style={{ "--c": user.role === "professor" ? "var(--lumi-iris-strong)" : "var(--lumi-iris)" }}>{initials(user.name)}</span>
            <div>
              <b>{user.name}</b>
              <small>{t(`role.${user.role}`)}</small>
            </div>
            <button className="d-icon-btn" onClick={onSignOut} title={t("nav.signout")} aria-label={t("nav.signout")}><Icon name="exit" size={17} /></button>
          </div>
        </div>
      </aside>
    </>
  );
}

function TopBar({ user, section, onMenu, onRole, onCompanion }) {
  const { t } = useI18n();
  return (
    <header className="d-top">
      <button className="d-icon-btn d-menu" onClick={onMenu} aria-label="Menu"><Icon name="menu" size={20} /></button>
      <h1 className="d-top-title">{t(titleKey(user.role, section))}</h1>
      <div className="d-top-right">
        <div className="d-role" role="radiogroup" aria-label={t("top.viewAs")}>
          <span>{t("top.viewAs")}</span>
          {["student", "professor"].map((role) => (
            <button key={role} role="radio" aria-checked={user.role === role} className={user.role === role ? "active" : ""} onClick={() => onRole(role)}>
              {t(`role.${role}`)}
            </button>
          ))}
        </div>
        <LanguageSwitcher />
        <button className="btn btn-ai small d-top-ai" onClick={onCompanion}><Icon name="sparkle" size={16} /> <span>{t("nav.companion")}</span></button>
      </div>
    </header>
  );
}

function Greeting({ user, onCompanion }) {
  const { t, lang } = useI18n();
  const live = ROOMS.filter((r) => r.live).length;
  const COURSES = useCourses();
  useEffect(() => { refreshCourseMap(); }, []);
  const overall = COURSES.reduce((s, c) => s + courseMastery(c), 0) / COURSES.length;
  const isProf = user.role === "professor";
  const course = COURSES.find((c) => c.id === CLASS.course);
  return (
    <div className="d-greeting span-12">
      <div>
        <span className="eyebrow">{new Date().toLocaleDateString(lang === "ar" ? "ar-TN" : lang, { weekday: "long", day: "numeric", month: "long" }).toUpperCase()}</span>
        <h1>{t(greetingKey(), { name: user.name.split(" ")[0] })}</h1>
        <p>{isProf ? t("home.prof.sub", { course: `${course.id} · ${course.name}` }) : t("home.student.sub", { exams: listExams().length, rooms: live })}</p>
      </div>
      {isProf ? (
        <div className="d-greet-side">
          <label className="d-select">
            <span>{t("prof.course")}</span>
            <select className="input" defaultValue={CLASS.course}>
              {COURSES.map((c) => <option key={c.id} value={c.id}>{c.id} · {c.name}</option>)}
            </select>
          </label>
          <span className="d-pill"><Icon name="users" size={15} /> {t("prof.students", { n: CLASS.students })}</span>
        </div>
      ) : (
        <div className="d-greet-side">
          <div className="d-ring" style={{ "--p": overall }}>
            <b>{Math.round(overall * 100)}%</b>
            <small>{t("mastery.mastered")}</small>
          </div>
          <button className="btn btn-ai" onClick={onCompanion}><Icon name="sparkle" size={16} /> {t("nav.companion")}</button>
        </div>
      )}
    </div>
  );
}

function StudentHome({ user, onSoon, onCompanion }) {
  const { t } = useI18n();
  return (
    <div className="d-grid">
      <Greeting user={user} onCompanion={() => onCompanion()} />
      <Card icon="planet" title={t("map.title")} example className="span-8 map-card">
        <ProgressMap compact onOpenFull={() => navigate("/app/map")} />
      </Card>
      <PrepCard onSoon={onSoon} />
      <RoomsCard onSoon={onSoon} />
      <BoardCard onSoon={onSoon} />
      <MaterialsCard onSoon={onSoon} />
      <CompanionCard onOpen={onCompanion} />
    </div>
  );
}

function ProfessorHome({ user, onSoon, onCompanion }) {
  return (
    <div className="d-grid">
      <Greeting user={user} onCompanion={onCompanion} />
      <ClassKpis />
      <ProfActions onSoon={onSoon} />
      <Roster />
      <GroupProgress />
      <WeakPoints className="span-12" />
    </div>
  );
}

function AiLanguageCard() {
  const { t } = useI18n();
  const [value, setValue] = useState(replyLanguage);
  return (
    <Card icon="sparkle" title={t("settings.ai")} sub={t("settings.aiHint")} className="span-7">
      <div className="d-lang-cards ai-langs" role="radiogroup" aria-label={t("settings.ai")}>
        {AI_LANGUAGES.map((id) => (
          <button key={id} role="radio" aria-checked={value === id} className={"d-lang-card" + (value === id ? " active" : "")}
            onClick={() => { setValue(id); setReplyLanguage(id); }}>
            <b>{id === "auto" ? "✦" : t(`lang.${id}`).split(" · ")[0]}</b>
            <span>{id === "auto" ? t("lang.auto") : t(`lang.${id}`).split(" · ")[1] || t(`lang.${id}`)}</span>
          </button>
        ))}
      </div>
    </Card>
  );
}

function Section({ user, section, onSoon, onToast, onCompanion, onRole }) {
  const { t, lang, setLang } = useI18n();
  const isProf = user.role === "professor";

  switch (section) {
    case "voice": // not built: hidden from the menu, old links land on the home page
    case "groups":
    case "insights":
    case "home":
      return isProf
        ? <ProfessorHome user={user} onSoon={onSoon} onCompanion={onCompanion} />
        : <StudentHome user={user} onSoon={onSoon} onCompanion={onCompanion} />;

    case "map":
      return (
        <div className="d-page">
          <Card icon="planet" title={t("map.title")} sub={t("map.sub")} example className="map-full">
            <ProgressMap />
          </Card>
        </div>
      );

    case "rooms":
      return (
        <div className="d-grid">
          <Card icon="door" title={t("rooms.title")} sub={t("rooms.sub")} className="span-12" example
            actions={<button className="btn btn-primary small" onClick={() => openBoard(`room-${Math.random().toString(36).slice(2, 7)}`)}><Icon name="plus" size={15} /> {t("rooms.new")}</button>}>
            <JoinRoom big />
            <h3 className="d-subhead">{t("rooms.active")}</h3>
            <RoomList />
          </Card>
        </div>
      );

    case "board":
      return (
        <div className="d-grid">
          <Card icon="board" title={t("board.title")} sub={t("board.sub")} className="span-7" actions={<span className="d-badge live">{t("badge.live")}</span>}>
            <BoardPreview />
            <AiBoardChips onSoon={onSoon} />
            <button className="btn btn-primary full" onClick={() => openBoard()}><Icon name="board" size={17} /> {t("board.open")}</button>
          </Card>
          <Card icon="history" title={t("board.recent")} className="span-5" example>
            <RoomList />
          </Card>
        </div>
      );

    case "prep":
      return <Prep />;

    case "materials":
      return <Materials user={user} />;

    case "review":
      return isProf ? <Review onToast={onToast} /> : null;

    case "diagrams":
      return <Diagrams />;

    case "settings":
      return (
        <div className="d-grid">
          <Card icon="globe" title={t("settings.language")} sub={t("settings.languageHint")} className="span-7">
            <div className="d-lang-cards" role="radiogroup" aria-label={t("settings.language")}>
              {LANGUAGES.map((l) => (
                <button key={l.id} role="radio" aria-checked={lang === l.id} className={"d-lang-card" + (lang === l.id ? " active" : "")} onClick={() => setLang(l.id)} lang={l.id} dir={l.dir}>
                  <b>{l.label}</b>
                  <span>{l.name}</span>
                </button>
              ))}
            </div>
          </Card>
          <EngineStatusCard className="span-5" />
          <AiLanguageCard />
          <Card icon="users" title={t("settings.profile")} className="span-5">
            <dl className="d-profile">
              <div><dt>{t("auth.name")}</dt><dd>{user.name}</dd></div>
              {user.email && <div><dt>{t("auth.email")}</dt><dd>{user.email}</dd></div>}
              <div>
                <dt>{t("settings.role")}</dt>
                <dd>
                  <div className="d-role inline">
                    {["student", "professor"].map((role) => (
                      <button key={role} className={user.role === role ? "active" : ""} onClick={() => onRole(role)}>{t(`role.${role}`)}</button>
                    ))}
                  </div>
                </dd>
              </div>
            </dl>
          </Card>
        </div>
      );

    default:
      return null;
  }
}

export default function Dashboard({ section }) {
  const { t } = useI18n();
  const [user, setUser] = useState(getUser);
  const [menuOpen, setMenuOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [aiSeed, setAiSeed] = useState(null);
  const [toast, setToast] = useState(null);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(null), Math.max(2600, toast.text.length * 55));
    return () => clearTimeout(id);
  }, [toast]);

  const onSoon = () => setToast({ text: `${t("common.soon")} — ${t("slot.ready")}`, id: Date.now() });
  const onToast = (text) => setToast({ text, id: Date.now() });
  const openCompanion = (text) => {
    if (typeof text === "string") setAiSeed({ text, id: Date.now() });
    setAiOpen(true);
    setMenuOpen(false);
  };
  const changeRole = (role) => {
    const next = { ...user, role };
    saveUser(next);
    setUser(next);
  };

  return (
    <div className="d-shell">
      <Sidebar
        user={user}
        section={section}
        open={menuOpen}
        onClose={() => setMenuOpen(false)}
        onCompanion={() => openCompanion()}
        onSignOut={() => {
          signOut();
          navigate("/");
        }}
      />
      <div className="d-main">
        <TopBar user={user} section={section} onMenu={() => setMenuOpen(true)} onRole={changeRole} onCompanion={() => openCompanion()} onSoon={onSoon} />
        <main className="d-content" key={`${section}-${user.role}`}>
          <Section user={user} section={section} onSoon={onSoon} onToast={onToast} onCompanion={openCompanion} onRole={changeRole} />
        </main>
      </div>
      {!aiOpen && (
        <button className="d-fab" onClick={() => openCompanion()} aria-label={t("nav.companion")}>
          <LumiSpark size={30} onDark />
        </button>
      )}
      <AICompanion open={aiOpen} onClose={() => setAiOpen(false)} user={user} seed={aiSeed} />
      {toast && <div key={toast.id} className="d-toast" role="status">{toast.text}</div>}
    </div>
  );
}
