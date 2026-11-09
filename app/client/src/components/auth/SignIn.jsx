import { useState } from "react";
import Icon from "../Icons";
import { Link, navigate, useLocation } from "../../lib/router";
import { useI18n, LanguageSwitcher } from "../../lib/i18n";
import { getUser, saveUser } from "../../lib/session";
import "../dashboard/dashboard.css";
import "../landing/landing.css";
import { LumiLogo } from "../LumiSpark";

function nameFromEmail(email) {
  const local = email.split("@")[0] || "";
  return local.split(/[._-]+/).filter(Boolean).map((p) => p[0].toUpperCase() + p.slice(1)).join(" ") || "Student";
}

export default function SignIn() {
  const { t } = useI18n();
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  const previous = getUser();
  const [mode, setMode] = useState(params.get("mode") === "signup" ? "signup" : "signin");
  const [role, setRole] = useState(params.get("role") === "professor" ? "professor" : previous?.role || "student");
  const [name, setName] = useState("");
  const [email, setEmail] = useState(previous?.email || "");
  const [password, setPassword] = useState("");

  const signup = mode === "signup";
  const valid = email.includes("@") && password.length >= 4 && (!signup || name.trim());

  function submit(e) {
    e.preventDefault();
    if (!valid) return;
    const sameAccount = previous && previous.email === email.trim();
    const displayName = signup ? name.trim() : sameAccount ? previous.name : nameFromEmail(email);
    saveUser({ name: displayName, email: email.trim(), role });
    navigate("/app");
  }

  return (
    <div className="auth">
      <div className="lp-sky" aria-hidden="true" />
      <aside className="auth-art" aria-hidden="true">
        <div className="auth-orbits">
          <span className="auth-sun" />
          {[0, 1, 2].map((i) => <span key={i} className={`auth-orbit o${i}`}><i /></span>)}
        </div>
        <p className="auth-quote">{t("hero.title1")} <em>{t("hero.title2")}</em></p>
      </aside>

      <main className="auth-main">
        <div className="auth-top">
          <Link to="/" className="auth-back"><Icon name="left" size={16} /> {t("auth.back")}</Link>
          <LanguageSwitcher />
        </div>

        <form className="auth-card" onSubmit={submit}>
          <Link to="/" className="d-brand"><LumiLogo height={26} /></Link>
          <h1>{signup ? t("auth.create") : t("auth.welcome")}</h1>
          <p className="auth-sub">{t("auth.sub")}</p>

          <div className="auth-tabs" role="tablist">
            {["signin", "signup"].map((m) => (
              <button key={m} type="button" role="tab" aria-selected={mode === m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>
                {t(`auth.${m}`)}
              </button>
            ))}
          </div>

          <fieldset className="auth-roles">
            <legend>{t("auth.role")}</legend>
            {[
              ["student", "users"],
              ["professor", "chart"]
            ].map(([r, icon]) => (
              <label key={r} className={"auth-role" + (role === r ? " active" : "")}>
                <input type="radio" name="role" value={r} checked={role === r} onChange={() => setRole(r)} />
                <Icon name={icon} size={20} />
                <b>{t(`role.${r}`)}</b>
              </label>
            ))}
          </fieldset>

          {signup && (
            <label className="auth-field">
              <span>{t("auth.name")}</span>
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={40} required />
            </label>
          )}
          <label className="auth-field">
            <span>{t("auth.email")}</span>
            <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" placeholder="name@university.tn" dir="ltr" required />
          </label>
          <label className="auth-field">
            <span>{t("auth.password")}</span>
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={signup ? "new-password" : "current-password"} minLength={4} required dir="ltr" />
          </label>
          {!signup && <button type="button" className="link-btn auth-forgot" onClick={() => setMode("signup")}>{t("auth.forgot")}</button>}

          <button className="btn btn-primary full lp-big" type="submit" disabled={!valid}>
            {signup ? t("auth.signup") : t("auth.signin")} <Icon name="right" size={17} />
          </button>
          <button
            type="button"
            className="link-btn auth-guest"
            onClick={() => {
              saveUser({ name: role === "professor" ? "Dr. Guest" : "Guest", email: "", role });
              navigate("/app");
            }}
          >
            {t("auth.or")}
          </button>
          <p className="auth-note"><Icon name="lock" size={14} /> {t("auth.preview")}</p>
        </form>
      </main>
    </div>
  );
}
