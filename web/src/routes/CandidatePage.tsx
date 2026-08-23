import { Link, useParams } from "react-router-dom";
import { techScoutApi } from "../api";
import { syntheticNotice } from "../api/techscoutFixtures";
import { useResource } from "./useResource";
import { useI18n } from "../i18n";

export function CandidatePage() {
  const { locale, t } = useI18n();
  const { id = "", candidateId = "" } = useParams(); const candidate = useResource(() => techScoutApi.getCandidate(id, candidateId).then((response) => response.data), [id, candidateId]); const evidence = useResource(() => techScoutApi.getEvidence(id).then((response) => response.data.items.filter((item) => item.candidate_id === candidateId)), [id, candidateId]);
  if (candidate.error) return <div className="page-state" role="alert">{candidate.error.message}</div>; if (!candidate.data) return <div className="page-state">{t("loadingCandidate")}</div>; const item = candidate.data;
  const synthetic = Boolean(evidence.data?.length) && evidence.data?.every((entry) => entry.acquisition_state === "synthetic");
  return <article className="candidate-page">{synthetic && <div className="synthetic-ribbon" role="note">{locale === "en" ? syntheticNotice : t("syntheticBoundary")}</div>}<header><div><p className="eyebrow">{t("candidate")} · {item.support_level.replaceAll("_", " ")}</p><h1>{item.name}</h1></div><Link to={`/runs/${id}`}>{t("backMatrix")}</Link></header><dl><div><dt>{t("verdict")}</dt><dd>{item.verdict.replaceAll("_", " ")}</dd></div><div><dt>{t("compatibility")}</dt><dd>{item.compatibility}</dd></div><div><dt>{t("requestedVersion")}</dt><dd>{item.requested_version ?? t("notPinned")}</dd></div><div><dt>{t("resolvedVersion")}</dt><dd>{item.resolved_version ?? t("notVerified")}</dd></div></dl><section><p className="eyebrow">{t("record")}</p><h2>{evidence.data?.length ?? 0} {t("linked")}</h2>{evidence.data?.map((entry) => <Link key={entry.evidence_id} to={`/runs/${id}/evidence/${encodeURIComponent(entry.evidence_id)}`}><strong>{entry.claim}</strong><small>{entry.kind.replaceAll("_", " ")} · {entry.source_title}</small></Link>)}</section></article>;
}
