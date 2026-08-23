import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { techScoutApi } from "../api";
import type { TechScoutCreateRunRequest, TechScoutRunSummary } from "../api/contracts";
import { ApiError } from "../api/client";
import { TECHSCOUT_FIXTURE_ID } from "../api/techscoutFixtures";
import { useI18n } from "../i18n";

const initial: TechScoutCreateRunRequest = {
  question: "", project_context: "Local RAG service choosing a Python vector store.",
  environment: { python_version: "3.11", operating_system: "Linux", deployment: "single-node local" },
  hard_constraints: ["local persistence", "metadata equality filtering"], candidates: [], mode: "fast",
};

export function HomePage() {
  const { t } = useI18n();
  const navigate = useNavigate(); const [form, setForm] = useState(initial); const [constraints, setConstraints] = useState(initial.hard_constraints.join("\n")); const [candidates, setCandidates] = useState("Chroma, Qdrant Local, pgvector");
  const [runs, setRuns] = useState<TechScoutRunSummary[]>([]); const [pending, setPending] = useState(false); const [error, setError] = useState<ApiError | null>(null);
  useEffect(() => { void techScoutApi.listRuns().then((response) => setRuns(response.data.items)).catch(() => setRuns([])); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setError(null);
    try {
      const body: TechScoutCreateRunRequest = { ...form, hard_constraints: constraints.split("\n").map((item) => item.trim()).filter(Boolean), candidates: candidates.split(",").map((name) => name.trim()).filter(Boolean).map((name) => ({ name })) };
      const response = await techScoutApi.createRun(body); navigate(`/runs/${response.data.id}`);
    } catch (caught) { setError(caught instanceof ApiError ? caught : new ApiError(0, "connection_lost", "The local API could not be reached.")); } finally { setPending(false); }
  }
  return <>
    <section className="tech-hero"><div><p className="eyebrow">{t("heroEye")}</p><h1>{t("heroTitleA")}<br/><em>{t("heroTitleB")}</em></h1><p className="dek">{t("heroDek")}</p></div><aside className="scope-card"><b>{t("boundary")}</b><strong>{t("vectorStores")}</strong><p>{t("boundaryBody")}</p></aside></section>
    <section className="tech-desk"><form className="tech-form" onSubmit={submit}><header><span>01</span><div><p className="eyebrow">{t("newTask")}</p><h2>{t("frame")}</h2></div></header>
      <label>{t("question")}<textarea aria-label={t("question")} required minLength={3} value={form.question} onChange={(event) => setForm({ ...form, question: event.target.value })} placeholder={t("questionPlaceholder")}/></label>
      <label>{t("project")}<textarea aria-label={t("project")} required value={form.project_context} onChange={(event) => setForm({ ...form, project_context: event.target.value })}/></label>
      <div className="env-grid"><label>{t("python")}<input aria-label={`${t("python")} version`} value={form.environment.python_version} onChange={(event) => setForm({ ...form, environment: { ...form.environment, python_version: event.target.value } })}/></label><label>{t("os")}<input aria-label={t("os")} value={form.environment.operating_system} onChange={(event) => setForm({ ...form, environment: { ...form.environment, operating_system: event.target.value } })}/></label><label>{t("deployment")}<input aria-label={t("deployment")} value={form.environment.deployment} onChange={(event) => setForm({ ...form, environment: { ...form.environment, deployment: event.target.value } })}/></label></div>
      <label>{t("constraints")} <small>{t("constraintsHint")}</small><textarea aria-label={t("constraints")} value={constraints} onChange={(event) => setConstraints(event.target.value)}/></label>
      <label>{t("candidates")} <small>{t("candidatesHint")}</small><input aria-label={t("candidates")} value={candidates} onChange={(event) => setCandidates(event.target.value)}/></label>
      <fieldset className="mode-field"><legend>{t("mode")}</legend><label><input type="radio" name="mode" checked={(form.mode ?? "fast") === "fast"} onChange={() => setForm({ ...form, mode: "fast" })}/> {t("fast")}</label><label><input type="radio" name="mode" checked={form.mode === "verified"} onChange={() => setForm({ ...form, mode: "verified" })}/> {t("verified")}</label></fieldset>
      {error && <div className="api-warning" role="alert"><strong>{error.code}</strong><span>{error.message}</span></div>}
      <button className="primary-action" disabled={pending}>{pending ? t("submitting") : t("start")}</button>
    </form><aside className="recent-runs"><header><span>02</span><div><p className="eyebrow">{t("recent")}</p><h2>{t("ledger")}</h2></div></header><Link className="offline-callout" to={`/runs/${TECHSCOUT_FIXTURE_ID}`}><b>{t("fixtureTitle")}</b><span>{t("fixtureBody")}</span></Link>{runs.map((run) => <Link key={run.id} to={`/runs/${run.id}`}><span className="run-dot" data-status={run.status}/><strong>{run.question}</strong><small>{run.status.replaceAll("_", " ")}</small></Link>)}</aside></section>
  </>;
}
