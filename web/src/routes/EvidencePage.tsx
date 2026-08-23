import { Link, useParams } from "react-router-dom";
import { techScoutApi } from "../api";
import { syntheticNotice } from "../api/techscoutFixtures";
import { useResource } from "./useResource";
import { useI18n } from "../i18n";

export function EvidencePage() {
  const { locale, t } = useI18n();
  const { id = "", evidenceId = "" } = useParams(); const evidence = useResource(() => techScoutApi.getEvidenceItem(id, evidenceId).then((response) => response.data), [id, evidenceId]);
  if (evidence.error) return <div className="page-state" role="alert">{evidence.error.message}</div>; if (!evidence.data) return <div className="page-state">{t("loadingEvidence")}</div>; const item = evidence.data;
  const authority = item.acquisition_state === "live" ? t("live") : item.acquisition_state === "cache" ? t("cached") : item.acquisition_state === "synthetic" ? t("synthetic") : t("unavailable");
  const authorityNotice = item.acquisition_state === "live" ? t("liveEvidence") : item.acquisition_state === "cache" ? t("cachedEvidence") : t("unavailableEvidence");
  return <article className="tech-evidence"><div className="synthetic-ribbon" role="note">{item.acquisition_state === "synthetic" ? (locale === "en" ? syntheticNotice : t("syntheticBoundary")) : authorityNotice}</div><header><div><p className="eyebrow">{item.kind.replaceAll("_", " ")} · {item.candidate_id}</p><h1>{t("evidence")}<br/><em>{t("evidenceRecord")}</em></h1></div><Link to={`/runs/${id}/report`}>{t("timeline")}</Link></header><blockquote>{item.claim}</blockquote><dl><div><dt>{t("authority")}</dt><dd>{authority}</dd></div><div><dt>{t("evidenceId")}</dt><dd>{item.evidence_id}</dd></div><div><dt>{t("sourceType")}</dt><dd>{item.source_type.replaceAll("_", " ")}</dd></div><div><dt>{t("sourceTitle")}</dt><dd>{item.source_title}</dd></div><div><dt>{t("sourceHash")}</dt><dd><code>{item.snapshot_sha256}</code></dd></div><div><dt>{t("snapshot")}</dt><dd>{new Date(item.as_of).toLocaleString()}</dd></div></dl>{item.source_url ? <a className="primary-action inline" href={item.source_url} target="_blank" rel="noopener noreferrer">{t("openSource")}</a> : item.acquisition_state === "synthetic" ? <p className="offline-source">{t("frozenSource")}</p> : null}</article>;
}
