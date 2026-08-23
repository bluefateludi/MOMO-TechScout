import { ErrorPanel } from "../components/Feedback";
import { useI18n } from "../i18n";
export function NotFoundPage() { const { t } = useI18n(); return <ErrorPanel code="not_found" message={t("notFound")}/>; }
