import Icon from "../Icons";
import ProgressMap from "../dashboard/ProgressMap";
import { Link } from "../../lib/router";
import { useI18n, LanguageSwitcher } from "../../lib/i18n";
import { getUser } from "../../lib/session";
import "../dashboard/dashboard.css";
import "./landing.css";
import { LumiLogo, LumiSpark } from "../LumiSpark";

const FEATURES = [
  { id: "map", icon: "planet", color: "var(--lumi-iris)" },
  { id: "rooms", icon: "users", color: "var(--lumi-iris)" },
  { id: "board", icon: "board", color: "var(--lumi-iris)" },
  { id: "voice", icon: "mic", color: "var(--lumi-iris)" },
  { id: "prep", icon: "exam", color: "var(--lumi-iris)" },
  { id: "materials", icon: "book", color: "var(--lumi-iris)" },
  { id: "companion", icon: "sparkle", color: "var(--lumi-iris)" },
  { id: "language", icon: "globe", color: "var(--lumi-iris)" }
];

function Nav() {
  const { t } = useI18n();
  const user = getUser();
  return (
    <header className="lp-nav glass">
      <Link to="/" className="d-brand"><LumiLogo height={26} /></Link>
      <nav className="lp-links">
        <a href="#features">{t("nav.features")}</a>
        <a href="#how">{t("nav.how")}</a>
        <a href="#roles">{t("nav.roles")}</a>
      </nav>
      <div className="lp-nav-right">
        <LanguageSwitcher />
        {user ? (
          <Link to="/app" className="btn btn-primary small">{t("nav.home")} <Icon name="right" size={15} /></Link>
        ) : (
          <>
            <Link to="/login" className="lp-login">{t("nav.login")}</Link>
            <Link to="/login?mode=signup" className="btn btn-primary small">{t("nav.start")}</Link>
          </>
        )}
      </div>
    </header>
  );
}

function HeroVisual() {
  const { t } = useI18n();
  return (
    <div className="lp-visual">
      <div className="lp-window glass-strong">
        <div className="lp-window-bar">
          <i /><i /><i />
          <span>Lumi · {t("map.title")}</span>
        </div>
        <ProgressMap compact />
      </div>
      <div className="lp-float lp-float-chat glass" dir="auto">
        <span className="d-ai-avatar small"><LumiSpark size={18} /></span>
        <p>{t("chat.ai2")}</p>
      </div>
      <div className="lp-float lp-float-fact glass">
        <Icon name="shield" size={16} />
        <span>{t("board.fact")}</span>
        <Icon name="check" size={16} />
      </div>
      <div className="lp-float lp-float-room glass">
        <span className="d-live on" />
        <b>Analyse · Dérivées</b>
        <span className="d-avatars">
          {["var(--lumi-iris)", "var(--lumi-iris-strong)", "var(--lumi-slate)"].map((c, i) => <span key={c} className="d-avatar" style={{ "--c": c }}>{["AK", "YB", "ST"][i]}</span>)}
        </span>
      </div>
    </div>
  );
}

export default function Landing() {
  const { t } = useI18n();
  return (
    <div className="lp">
      <Nav />

      <section className="lp-hero">
        <div className="lp-hero-copy">
          <span className="eyebrow">{t("hero.eyebrow")}</span>
          <h1>
            {t("hero.title1")}
            <em>{t("hero.title2")}</em>
          </h1>
          <p className="lp-lead">{t("hero.sub")}</p>
          <div className="lp-ctas">
            <Link to="/login?mode=signup" className="btn btn-primary lp-big">{t("hero.ctaStudent")} <Icon name="right" size={17} /></Link>
            <Link to="/login?mode=signup&role=professor" className="btn btn-secondary lp-big">{t("hero.ctaProf")}</Link>
          </div>
          <p className="lp-langs"><Icon name="globe" size={16} /> {t("hero.langs")}</p>
          <dl className="lp-stats">
            <div><dt>3</dt><dd>{t("hero.stat1")}</dd></div>
            <div><dt>{t("hero.live")}</dt><dd>{t("hero.stat2")}</dd></div>
            <div><dt><Icon name="shield" size={22} /></dt><dd>{t("hero.stat3")}</dd></div>
          </dl>
        </div>
        <HeroVisual />
      </section>

      <section className="lp-section" id="features">
        <div className="lp-head">
          <span className="eyebrow">{t("features.eyebrow")}</span>
          <h2>{t("features.title")}</h2>
        </div>
        <div className="lp-features">
          {FEATURES.map((f) => (
            <article key={f.id} className="lp-feature glass glass-hover" style={{ "--c": f.color }}>
              <span className="lp-feature-icon"><Icon name={f.icon} size={22} /></span>
              <h3>{t(`feature.${f.id}.title`)}</h3>
              <p>{t(`feature.${f.id}.desc`)}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="lp-section" id="how">
        <div className="lp-head">
          <span className="eyebrow">{t("how.eyebrow")}</span>
          <h2>{t("how.title")}</h2>
        </div>
        <ol className="lp-steps">
          {[1, 2, 3].map((n) => (
            <li key={n}>
              <span className="lp-step-num">{n}</span>
              <h3>{t(`how.${n}.title`)}</h3>
              <p>{t(`how.${n}.desc`)}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="lp-section lp-chat-section">
        <div className="lp-chat-copy">
          <span className="eyebrow">{t("chat.eyebrow")}</span>
          <h2>{t("chat.title")}</h2>
          <p className="lp-lead">{t("chat.sub")}</p>
        </div>
        <div className="lp-chat glass-ai">
          <div className="lp-bubble me" dir="ltr" lang="aeb-Latn">
            {t("chat.user")}
            <small>Darija</small>
          </div>
          <div className="lp-bubble ai">
            <span className="d-ai-avatar small"><LumiSpark size={18} /></span>
            <div>
              <p>{t("chat.ai")}</p>
              <span className="lp-verified"><Icon name="shield" size={13} /> {t("chat.badge")}</span>
            </div>
          </div>
          <div className="lp-bubble ai">
            <span className="d-ai-avatar small"><LumiSpark size={18} /></span>
            <p>{t("chat.ai2")}</p>
          </div>
        </div>
      </section>

      <section className="lp-section" id="roles">
        <div className="lp-head">
          <span className="eyebrow">{t("roles.eyebrow")}</span>
        </div>
        <div className="lp-roles">
          {[
            ["student", "users", "var(--lumi-iris)", "/login?mode=signup"],
            ["prof", "chart", "var(--lumi-iris-strong)", "/login?mode=signup&role=professor"]
          ].map(([role, icon, color, to]) => (
            <article key={role} className="lp-role glass" style={{ "--c": color }}>
              <span className="lp-feature-icon"><Icon name={icon} size={22} /></span>
              <h3>{t(`roles.${role}.title`)}</h3>
              <ul>
                {[1, 2, 3, 4].map((n) => <li key={n}><Icon name="check" size={16} /> {t(`roles.${role}.${n}`)}</li>)}
              </ul>
              <Link to={to} className="btn btn-secondary">{role === "prof" ? t("hero.ctaProf") : t("hero.ctaStudent")} <Icon name="right" size={16} /></Link>
            </article>
          ))}
        </div>
      </section>

      <section className="lp-cta glass-strong">
        <h2>{t("cta.title")}</h2>
        <p>{t("cta.sub")}</p>
        <Link to="/login?mode=signup" className="btn btn-primary lp-big">{t("cta.button")} <Icon name="right" size={17} /></Link>
      </section>

      <footer className="lp-footer">
        <span className="d-brand small"><LumiLogo height={20} /></span>
        <span>© 2026 · {t("footer.made")}</span>
        <span className="d-badge soon">{t("footer.preview")}</span>
        <LanguageSwitcher />
      </footer>
    </div>
  );
}
