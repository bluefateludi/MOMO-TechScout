import { NavLink, Outlet } from "react-router-dom";
import { isMockMode } from "../api";
import { useI18n } from "../i18n";

export function Layout() {
  const { locale, setLocale, t } = useI18n();
  return <div className="app-shell">
    <a className="skip-link" href="#main">{t("skip")}</a>
    {isMockMode && <div className="fixture-banner" role="status">{t("fixture")}</div>}
    <header className="masthead">
      <NavLink className="wordmark" to="/" aria-label={t("home")}><span>MOMO</span><strong>TechScout</strong></NavLink>
      <div className="masthead-tools"><div className="masthead-note">{t("desk")} <i aria-hidden="true">W2</i></div><div className="locale-switch" role="group" aria-label={t("language")}><button type="button" aria-pressed={locale === "en"} onClick={() => setLocale("en")}>{t("english")}</button><button type="button" aria-pressed={locale === "zh-CN"} onClick={() => setLocale("zh-CN")}>{t("chinese")}</button></div></div>
    </header>
    <main id="main"><Outlet /></main>
    <footer><span>{t("footerWave")}</span><span>{t("footerRule")}</span></footer>
  </div>;
}
