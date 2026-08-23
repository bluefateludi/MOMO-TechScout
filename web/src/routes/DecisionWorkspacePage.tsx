import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { techScoutApi } from "../api";
import { ApiError } from "../api/client";
import type { TechScoutRunSummary } from "../api/contracts";
import { TECHSCOUT_FIXTURE_ID } from "../api/techscoutFixtures";
import { createDecisionWorkspaceAdapter } from "../decision-workspace/adapter";
import { compactLines, initialDecisionDraft, validateDecisionDraft, type DecisionWorkspaceDraft, type ReviewItemKind, type WorkspaceReview } from "../decision-workspace/model";
import { useI18n } from "../i18n";

const workspace = createDecisionWorkspaceAdapter(techScoutApi);
const reviewOrder: ReviewItemKind[] = ["must_have", "preference", "unknown", "research_question", "poc_check"];
const reviewLabels: Record<ReviewItemKind, { index: string; title: string; note: string }> = {
  must_have: { index: "M", title: "Must-have", note: "User-owned · disqualifies when unmet" },
  preference: { index: "P", title: "Preference", note: "User-owned · ranks eligible candidates" },
  unknown: { index: "U", title: "Unknown", note: "Unresolved · never treated as a negative fact" },
  research_question: { index: "RQ", title: "Research question", note: "Fixture proposal · seeks authoritative evidence" },
  poc_check: { index: "PC", title: "PoC check", note: "Fixture proposal · bounded local verification" },
};

function LinesField({ label, hint, value, onChange, rows = 2, className }: { label: string; hint?: string; value: string[]; onChange: (value: string[]) => void; rows?: number; className?: string }) {
  return <label className={className}>{label}{hint && <small>{hint}</small>}<textarea rows={rows} aria-label={label} value={value.join("\n")} onChange={(event) => onChange(compactLines(event.target.value))}/></label>;
}

export function DecisionWorkspacePage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const [draft, setDraft] = useState(initialDecisionDraft);
  const [review, setReview] = useState<WorkspaceReview | null>(null);
  const [confirmed, setConfirmed] = useState<Partial<Record<ReviewItemKind, boolean>>>({});
  const [runs, setRuns] = useState<TechScoutRunSummary[]>([]);
  const [status, setStatus] = useState<"idle" | "reviewing" | "launching" | "loading_run">("idle");
  const [error, setError] = useState<string | null>(null);
  const [compatibility, setCompatibility] = useState<"native" | "legacy_projection" | null>(null);
  const [issues, setIssues] = useState<ReturnType<typeof validateDecisionDraft>>([]);
  const phaseHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => { void techScoutApi.listRuns().then((response) => setRuns(response.data.items)).catch(() => setRuns([])); }, []);
  useEffect(() => { if (review) phaseHeading.current?.focus(); }, [review]);

  const grouped = useMemo(() => Object.fromEntries(reviewOrder.map((kind) => [kind, review?.items.filter((item) => item.kind === kind) ?? []])) as Record<ReviewItemKind, WorkspaceReview["items"]>, [review]);
  const reviewKinds = reviewOrder.filter((kind) => grouped[kind].length > 0);
  const allConfirmed = reviewKinds.length > 0 && reviewKinds.every((kind) => confirmed[kind]);

  function update(next: Partial<DecisionWorkspaceDraft>) { setDraft((current) => ({ ...current, ...next })); setReview(null); setConfirmed({}); setCompatibility(null); }

  async function prepareReview() {
    const nextIssues = validateDecisionDraft(draft); setIssues(nextIssues); setError(null);
    if (nextIssues.length) { document.querySelector<HTMLElement>(`[data-field="${nextIssues[0].field}"] textarea, [data-field="${nextIssues[0].field}"] input`)?.focus(); return; }
    setStatus("reviewing");
    try { setReview(await workspace.preview(draft)); setConfirmed({}); }
    catch { setError("The criteria preview could not be prepared. Your Decision Context is still here; try again."); }
    finally { setStatus("idle"); }
  }

  async function launch() {
    if (!allConfirmed) return;
    setStatus("launching"); setError(null);
    try { navigate(`/runs/${(await workspace.launch(draft)).id}`); }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : "The local API could not be reached. Your confirmed review remains on this page."); setStatus("idle"); }
  }

  async function loadRun(runId: string) {
    setStatus("loading_run"); setError(null);
    try { const loaded = await workspace.load(runId); setDraft(loaded.draft); setCompatibility(loaded.compatibility); setReview(null); setConfirmed({}); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('[data-field="question"] textarea')?.focus()); }
    catch { setError("That Decision Context is unavailable. The current draft was not changed."); }
    finally { setStatus("idle"); }
  }

  function handleShortcut(event: KeyboardEvent<HTMLElement>) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !review) { event.preventDefault(); void prepareReview(); }
  }

  return <div className="decision-workspace" onKeyDown={handleShortcut}>
    <section className="workspace-intro" aria-labelledby="workspace-title">
      <div><p className="eyebrow">Decision workspace · round 04</p><h1 id="workspace-title">Frame the choice.<br/><em>Then test it.</em></h1></div>
      <div className="workspace-thesis"><span>01 / CONTEXT</span><p>Keep user facts, criteria, unknowns, research, and verification in separate lanes. A planner may propose questions. It may not invent a must-have.</p><Link to={`/runs/${TECHSCOUT_FIXTURE_ID}`}>Open frozen example <span aria-hidden="true">↗</span></Link></div>
    </section>

    <nav className="workspace-steps" aria-label="Decision workspace progress">
      <span data-active={!review}>1 <b>Decision Context</b><small>user-owned facts</small></span>
      <span data-active={Boolean(review)}>2 <b>Review criteria</b><small>trace every item</small></span>
      <span data-active={allConfirmed}>3 <b>Launch</b><small>existing run contract</small></span>
    </nav>

    {compatibility === "legacy_projection" && <div className="compatibility-note" role="status"><b>Legacy run projection</b><span>Only the older run’s core question, environment, must-haves, and candidates were recoverable. Review every blank lane before continuing.</span></div>}
    {compatibility === "native" && <div className="compatibility-note" role="status"><b>Decision Context restored</b><span>The canonical v2 context was loaded. Unknowns and planner proposals are intentionally blank because they are not part of that endpoint yet.</span></div>}
    {error && <div className="workspace-error" role="alert"><b>Workspace paused</b><span>{error}</span><button type="button" onClick={() => setError(null)}>Dismiss</button></div>}

    {!review ? <section className="context-stage" aria-labelledby="context-heading">
      <div className="stage-rail"><span>01</span><p>DECISION<br/>CONTEXT</p><small>Ctrl/⌘ + Enter<br/>to review</small></div>
      <form className="context-sheet" onSubmit={(event) => { event.preventDefault(); void prepareReview(); }} noValidate>
        <header><div><p className="eyebrow">User-owned input</p><h2 id="context-heading">State what is true today.</h2></div><p>Specific enough to investigate. Honest enough to leave blanks and unknowns visible.</p></header>
        {issues.length > 0 && <div className="issue-summary" role="alert"><b>{issues.length} context {issues.length === 1 ? "issue" : "issues"}</b><ul>{issues.map((issue) => <li key={`${issue.field}-${issue.message}`}>{issue.message}</li>)}</ul></div>}
        <div className="field-grid">
          <label className="field-wide" data-field="question">{t("question")}<small>The exact technology choice to make</small><textarea rows={2} aria-label={t("question")} value={draft.question} onChange={(event) => update({ question: event.target.value })}/></label>
          <label className="field-wide" data-field="projectSummary">Project summary<small>Application, users, and the boundary of this decision</small><textarea rows={3} aria-label="Project summary" value={draft.projectSummary} onChange={(event) => update({ projectSummary: event.target.value })}/></label>
          <LinesField label="Current stack" hint="one item per line" value={draft.currentStack} onChange={(currentStack) => update({ currentStack })}/>
          <LinesField label="Primary use cases" hint="one outcome per line" value={draft.useCases} onChange={(useCases) => update({ useCases })}/>
        </div>
        <fieldset className="deployment-strip" data-field="deployment"><legend>Runtime & deployment</legend><label>Python<input aria-label="Python version" value={draft.deployment.pythonVersion} onChange={(event) => update({ deployment: { ...draft.deployment, pythonVersion: event.target.value } })}/></label><label>Operating system<input aria-label="Operating system" value={draft.deployment.operatingSystem} onChange={(event) => update({ deployment: { ...draft.deployment, operatingSystem: event.target.value } })}/></label><label>Topology<input aria-label="Deployment topology" value={draft.deployment.deployment} onChange={(event) => update({ deployment: { ...draft.deployment, deployment: event.target.value } })}/></label></fieldset>

        <section className="requirement-lanes" aria-labelledby="requirements-heading"><header><span>02</span><div><p className="eyebrow">Classification matters</p><h3 id="requirements-heading">Name the decision rules.</h3></div></header>
          <div className="lane must-lane" data-field="mustHaves"><div><b>M</b><h4>Must-have</h4><p>Non-negotiable. An unmet item makes a candidate ineligible.</p></div><textarea rows={5} aria-label="Must-haves" value={draft.mustHaves.join("\n")} onChange={(event) => update({ mustHaves: compactLines(event.target.value) })}/><small>{draft.mustHaves.length}/5 · user-confirmed only</small></div>
          <div className="lane preference-lane" data-field="preferences"><div><b>P</b><h4>Preference</h4><p>Ranks viable candidates. It never overrides a failed must-have.</p></div><textarea rows={5} aria-label="Preferences" value={draft.preferences.join("\n")} onChange={(event) => update({ preferences: compactLines(event.target.value) })}/><small>{draft.preferences.length}/12 · one per line</small></div>
          <div className="lane unknown-lane" data-field="unknowns"><div><b>?</b><h4>Unknown</h4><p>Unresolved on purpose. Research may clarify it; silence may not.</p></div><textarea rows={5} aria-label="Unknowns" value={draft.unknowns.join("\n")} onChange={(event) => update({ unknowns: compactLines(event.target.value) })}/><small>{draft.unknowns.length}/12 · one per line</small></div>
        </section>

        <details className="context-details"><summary><span>03</span><b>Quality, team & policy details</b><small>optional but decision-relevant</small></summary><div className="detail-grid">
          <LinesField label="Team capabilities" value={draft.teamCapabilities} onChange={(teamCapabilities) => update({ teamCapabilities })}/><LinesField label="Performance requirements" value={draft.performanceRequirements} onChange={(performanceRequirements) => update({ performanceRequirements })}/><LinesField label="Budget constraints" value={draft.budgetConstraints} onChange={(budgetConstraints) => update({ budgetConstraints })}/><LinesField label="Security requirements" value={draft.securityRequirements} onChange={(securityRequirements) => update({ securityRequirements })}/><LinesField label="License requirements" value={draft.licenseRequirements} onChange={(licenseRequirements) => update({ licenseRequirements })}/>
        </div></details>

        <div className="launch-inputs" data-field="candidates"><LinesField label="Candidate shortlist" hint="one per line · maximum three" value={draft.candidates} onChange={(candidates) => update({ candidates })} rows={3}/><fieldset><legend>Run mode</legend><label><input type="radio" name="workspace-mode" checked={draft.mode === "fast"} onChange={() => update({ mode: "fast" })}/> Fast / frozen evidence</label><label><input type="radio" name="workspace-mode" checked={draft.mode === "verified"} onChange={() => update({ mode: "verified" })}/> Verified / live authorities</label></fieldset></div>
        <div className="sheet-actions"><span>Next: classify the requirements and inspect proposed investigation work.</span><button className="primary-action" disabled={status === "reviewing"}>{status === "reviewing" ? "Preparing review…" : "Review decision rules →"}</button></div>
      </form>
      <aside className="decision-ledger"><header><span>RECENT</span><b>Context ledger</b></header><Link to={`/runs/${TECHSCOUT_FIXTURE_ID}`}><small>Frozen fixture</small><strong>Local RAG vector store</strong><span>Open completed run ↗</span></Link>{runs.slice(0, 4).map((run) => <article key={run.id}><small>{run.status.replaceAll("_", " ")}</small><strong>{run.question}</strong><button type="button" onClick={() => void loadRun(run.id)} disabled={status === "loading_run"}>Use as starting point</button></article>)}</aside>
    </section> : <section className="review-stage" aria-labelledby="review-heading">
      <header className="review-header"><div><p className="eyebrow">Traceability review</p><h2 id="review-heading" ref={phaseHeading} tabIndex={-1}>Confirm the work before the work begins.</h2></div><button type="button" onClick={() => { setReview(null); setConfirmed({}); }}>← Edit Decision Context</button></header>
      <div className="fixture-boundary" role="note"><b>Preview boundary</b><p>Classification below is deterministic fixture output. Decision Context will use the existing v2 run contract; Unknowns, Research Questions, PoC Checks, and this confirmation await a backend orchestration endpoint and are not yet persisted.</p></div>
      <div className="review-grid">{reviewKinds.map((kind) => { const meta = reviewLabels[kind]; return <section className={`review-column review-${kind}`} key={kind} aria-labelledby={`review-${kind}`}><header><span>{meta.index}</span><div><h3 id={`review-${kind}`}>{meta.title}</h3><small>{meta.note}</small></div></header><ol>{grouped[kind].map((item) => <li key={item.itemId}><span>{item.statement}</span><small>{item.authority === "user" ? "User statement preserved" : `Linked to ${item.requirementIds.join(", ")}`}</small></li>)}</ol><label className="confirm-check"><input type="checkbox" checked={Boolean(confirmed[kind])} onChange={(event) => setConfirmed({ ...confirmed, [kind]: event.target.checked })}/><span>I reviewed this lane</span></label></section>; })}</div>
      <footer className="review-actions"><div><b>{reviewKinds.filter((kind) => confirmed[kind]).length}/{reviewKinds.length} lanes confirmed</b><span>{allConfirmed ? "Ready to create the run envelope." : "Confirm each populated lane to continue."}</span></div><button className="primary-action" disabled={!allConfirmed || status === "launching"} onClick={() => void launch()}>{status === "launching" ? "Creating run…" : t("start")}</button></footer>
    </section>}
  </div>;
}
