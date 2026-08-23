import { Link, useParams } from "react-router-dom";
import { techScoutApi } from "../api";
import { syntheticNotice } from "../api/techscoutFixtures";
import { useResource } from "./useResource";
import { useI18n } from "../i18n";

export function ReportPage() {
  const { locale, t } = useI18n();
  const { id = "" } = useParams(); const report = useResource(() => techScoutApi.getReport(id).then((response) => response.data), [id]); const evidence = useResource(() => techScoutApi.getEvidence(id).then((response) => response.data.items), [id]);
  if (report.error) return <div className="page-state" role="alert">{report.error.message}</div>; if (!report.data) return <div className="page-state">{t("loadingReport")}</div>; const data = report.data;
  return <article className="decision-report">{data.synthetic && <div className="synthetic-ribbon" role="note">{locale === "en" ? syntheticNotice : t("syntheticBoundary")}</div>}<header><div><p className="eyebrow">{t("decisionReport")} · {data.verdict.replaceAll("_", " ")}</p><h1>{data.recommendation ? <>{t("recommend")} <em>{data.recommendation}</em></> : t("noWinner")}</h1><p>{data.summary}</p></div><Link to={`/runs/${id}`}>{t("timeline")}</Link></header>
    <section className="constraint-table"><div className="section-number">01</div><div><p className="eyebrow">{t("hardConstraints")}</p><h2>{t("gateRecord")}</h2>{data.constraints.map((item) => <article key={`${item.candidate_id}-${item.constraint}`}><span data-status={item.status}>{item.status.replaceAll("_", " ")}</span><strong>{item.constraint}</strong><small>{item.candidate_id}</small><div>{item.evidence_ids.map((evidenceId) => <Link key={evidenceId} to={`/runs/${id}/evidence/${encodeURIComponent(evidenceId)}`}>{evidenceId}</Link>)}</div></article>)}</div></section>
    <section className="poc-grid"><header><span className="section-number">02</span><div><p className="eyebrow">{t("poc")}</p><h2>{t("allowlisted")}</h2></div></header><div>{data.poc_results.map((poc) => <article key={poc.candidate_id}><span>{poc.verified ? t("pocVerified") : poc.status.replaceAll("_", " ")}</span><h3>{poc.candidate_id}</h3><p>{poc.recipe_id ?? t("noRecipe")}</p><ul>{poc.checks.map((check) => <li key={check}>{check}</li>)}</ul><small>{poc.synthetic ? t("syntheticFixture") : poc.status === "research_only" ? t("researchOnly") : t("reviewedDocker")} · {poc.duration_ms} ms</small></article>)}</div></section>
    <section className="evidence-index"><header><span className="section-number">03</span><div><p className="eyebrow">{t("evidence")}</p><h2>{t("claimLedger")}</h2></div></header>{evidence.data?.map((item) => <Link key={item.evidence_id} to={`/runs/${id}/evidence/${encodeURIComponent(item.evidence_id)}`}><span>{item.kind.replaceAll("_", " ")}</span><strong>{item.claim}</strong><small>{item.source_title}</small></Link>)}</section>
    <section className="limitations"><span className="section-number">04</span><div><p className="eyebrow">{t("limits")}</p><h2>{t("doesNotProve")}</h2><ul>{data.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul></div></section>
  </article>;
}
