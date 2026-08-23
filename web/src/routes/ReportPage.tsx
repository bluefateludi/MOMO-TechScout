import { useEffect } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { techScoutApi } from "../api";
import type { TechScoutEvidence } from "../api/contracts";
import { syntheticNotice } from "../api/techscoutFixtures";
import { useResource } from "./useResource";
import { useI18n } from "../i18n";

function CitationLink({ runId, marker, evidenceId, item, returnTo }: { runId: string; marker: string; evidenceId: string; item?: TechScoutEvidence; returnTo: string }) {
  const { t } = useI18n();
  const kind = item?.kind === "retrieved_fact" ? t("retrievedFact") : item?.kind === "local_measurement" ? t("localMeasurement") : item?.kind === "model_inference" ? t("modelInference") : t("unresolvedEvidence");
  const authority = item?.acquisition_state === "live" ? t("live") : item?.acquisition_state === "cache" ? t("cached") : item?.acquisition_state === "synthetic" ? t("synthetic") : item?.acquisition_state === "unavailable" ? t("unavailable") : "—";
  const description = item?.claim ?? evidenceId;
  return <Link className="citation-ticket" to={`/runs/${runId}/evidence/${encodeURIComponent(evidenceId)}?citation=${marker}&return=${returnTo}`} aria-label={`${t("openEvidence")} ${marker} (${authority}, ${kind}): ${description}`}><b>{marker}</b><span>{authority} · {kind}</span></Link>;
}

export function ReportPage() {
  const { locale, t } = useI18n();
  const { id = "" } = useParams(); const location = useLocation(); const report = useResource(() => techScoutApi.getReport(id).then((response) => response.data), [id]); const evidence = useResource(() => techScoutApi.getEvidence(id).then((response) => response.data.items), [id]);
  useEffect(() => { if (!report.data) return; const targetId = location.hash.slice(1); if (!/^(constraint-\d+|evidence-ledger)$/.test(targetId)) return; document.getElementById(targetId)?.scrollIntoView?.({ block: "center" }); }, [location.hash, report.data]);
  if (report.error) return <div className="page-state" role="alert">{report.error.message}</div>; if (!report.data) return <div className="page-state">{t("loadingReport")}</div>; const data = report.data;
  const evidenceById = new Map(evidence.data?.map((item) => [item.evidence_id, item]));
  const markerFor = (evidenceId: string) => { const index = data.evidence_ids.indexOf(evidenceId); return index < 0 ? "E??" : `E${String(index + 1).padStart(2, "0")}`; };
  return <article className="decision-report">{data.synthetic && <div className="synthetic-ribbon" role="note">{locale === "en" ? syntheticNotice : t("syntheticBoundary")}</div>}<header><div><p className="eyebrow">{t("decisionReport")} · {data.verdict.replaceAll("_", " ")}</p><h1>{data.recommendation ? <>{t("recommend")} <em>{data.recommendation}</em></> : t("noWinner")}</h1><p>{data.summary}</p></div><Link to={`/runs/${id}`}>{t("timeline")}</Link></header>
    <section className="constraint-table"><div className="section-number">01</div><div><p className="eyebrow">{t("hardConstraints")}</p><h2>{t("gateRecord")}</h2>{data.constraints.map((item, index) => { const returnTo = `constraint-${index + 1}`; return <article id={returnTo} key={`${item.candidate_id}-${item.constraint}`}><span data-status={item.status}>{item.status.replaceAll("_", " ")}</span><strong>{item.constraint}</strong><small>{item.candidate_id}</small><div>{item.evidence_ids.map((evidenceId) => <CitationLink key={evidenceId} runId={id} marker={markerFor(evidenceId)} evidenceId={evidenceId} item={evidenceById.get(evidenceId)} returnTo={returnTo}/>)}</div></article>; })}</div></section>
    <section className="poc-grid"><header><span className="section-number">02</span><div><p className="eyebrow">{t("poc")}</p><h2>{t("allowlisted")}</h2></div></header><div>{data.poc_results.map((poc) => <article key={poc.candidate_id}><span>{poc.verified ? t("pocVerified") : poc.status.replaceAll("_", " ")}</span><h3>{poc.candidate_id}</h3><p>{poc.recipe_id ?? t("noRecipe")}</p><ul>{poc.checks.map((check) => <li key={check}>{check}</li>)}</ul><small>{poc.synthetic ? t("syntheticFixture") : poc.status === "research_only" ? t("researchOnly") : t("reviewedDocker")} · {poc.duration_ms} ms</small></article>)}</div></section>
    <section className="evidence-index" id="evidence-ledger"><header><span className="section-number">03</span><div><p className="eyebrow">{t("evidence")}</p><h2>{t("claimLedger")}</h2></div></header>{evidence.data?.map((item) => { const marker = markerFor(item.evidence_id); const kind = item.kind === "retrieved_fact" ? t("retrievedFact") : item.kind === "local_measurement" ? t("localMeasurement") : t("modelInference"); const authority = item.acquisition_state === "live" ? t("live") : item.acquisition_state === "cache" ? t("cached") : item.acquisition_state === "synthetic" ? t("synthetic") : t("unavailable"); return <Link key={item.evidence_id} to={`/runs/${id}/evidence/${encodeURIComponent(item.evidence_id)}?citation=${marker}&return=evidence-ledger`} aria-label={`${t("openEvidence")} ${marker}: ${item.claim}`}><span className="ledger-marker">{marker}<b data-authority={item.acquisition_state}>{authority}</b></span><strong>{item.claim}</strong><small><span>{item.source_title}</span><em>{kind}</em></small></Link>; })}</section>
    <section className="limitations"><span className="section-number">04</span><div><p className="eyebrow">{t("limits")}</p><h2>{t("doesNotProve")}</h2><ul>{data.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul></div></section>
  </article>;
}
