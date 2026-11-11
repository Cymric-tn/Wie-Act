// Appearance controls. ThemeToggle: one icon button that flips light <-> dark (top bars). ThemeChoice: dark / light /
// system as a segmented control (Settings). Both write the same preference (lib/themeMode.js).
import Icon from "./Icons";
import { useI18n } from "../lib/i18n";
import { THEMES, useTheme } from "../lib/themeMode";

export function ThemeToggle({ className = "d-icon-btn" }) {
  const { t } = useI18n();
  const { theme, setPref } = useTheme();
  const toLight = theme !== "light";
  const label = t(toLight ? "theme.toLight" : "theme.toDark");
  return (
    <button className={`theme-toggle ${className}`} onClick={() => setPref(toLight ? "light" : "dark")} aria-label={label} title={label}>
      <Icon name={toLight ? "sun" : "moon"} size={19} />
    </button>
  );
}

export function ThemeChoice() {
  const { t } = useI18n();
  const { pref, setPref } = useTheme();
  const icon = { dark: "moon", light: "sun", system: "settings" };
  return (
    <div className="theme-choice" role="radiogroup" aria-label={t("theme.label")}>
      {THEMES.map((id) => (
        <button key={id} role="radio" aria-checked={pref === id} className={pref === id ? "active" : ""} onClick={() => setPref(id)}>
          <Icon name={icon[id]} size={16} /> {t(`theme.${id}`)}
        </button>
      ))}
    </div>
  );
}
