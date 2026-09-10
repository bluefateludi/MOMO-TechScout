import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { techScoutApi } from "../api";
import { ApiError } from "../api/client";
import type { DecisionWorkflow, TechScoutRunSummary } from "../api/contracts";
import { TECHSCOUT_FIXTURE_ID } from "../api/techscoutFixtures";
import { createDecisionWorkspaceAdapter } from "../decision-workspace/adapter";
import { compactLines, initialDecisionDraft, validateDecisionDraft, type DecisionWorkspaceDraft } from "../decision-workspace/model";
import { useI18n } from "../i18n";

const workspace = createDecisionWorkspaceAdapter(techScoutApi);
const copy = {
  en: {
    workspace: "Decision workspace", title: "Frame the choice. Then test it.", contextKicker: "01 / CONTEXT", thesis: "Keep user facts, criteria, unknowns, research, and verification separate. A planner may propose questions, but cannot invent a Must-have.", frozenExample: "Open frozen example", progress: "Decision workspace progress", stepContext: "Decision Context", stepContextNote: "user-owned facts", stepReviewNote: "trace every item", stepLaunch: "Launch", stepLaunchNote: "existing run contract",
    legacyTitle: "Legacy run projection", legacyBody: "Only the older run’s core question, environment, Must-haves, and candidates were recovered. Review blank fields before continuing.", restoredTitle: "Decision Workflow restored", restoredBody: "Decision Context, saved confirmations, run mode, and candidate scope were restored from the public API.", paused: "Workspace paused", dismiss: "Dismiss",
    userInput: "User-owned input", today: "State what is true today.", todayNote: "Be specific enough to investigate, and leave unresolved facts visible.", exactChoice: "The exact technology choice to make", projectSummary: "Project summary", projectSummaryHint: "Application, users, and the boundary of this decision", currentStack: "Current stack", primaryUses: "Primary use cases", onePerLine: "one item per line", runtime: "Runtime & deployment", pythonVersion: "Python version", operatingSystem: "Operating system", topology: "Topology", deploymentTopology: "Deployment topology", requirementsTitle: "Review User Requirements", requirementsBody: "These atomic requirements are now saved with the run. Confirm only after checking their category and wording.", confirmRequirements: "Confirm Requirements Review →", criteriaTitle: "Confirm Selection Criteria", criteriaBody: "Review the derived criteria and Research Plan. Research remains blocked until this confirmation is saved.", confirmCriteria: "Confirm criteria and start research →", researchQuestions: "Research Questions", pocChecks: "PoC Checks", researchPlan: "Research Plan", confirmedLabel: "I confirm this saved review", startOver: "Start a new Decision Context", loadingWorkflow: "Restoring saved workflow…",
    rulesKicker: "Decision rules", rulesTitle: "Classify what matters.", mustTitle: "Must-have", mustBody: "Non-negotiable. An unmet item makes a candidate ineligible.", preferenceTitle: "Preference", preferenceBody: "Ranks eligible candidates, but never overrides a failed Must-have.", unknownTitle: "Unknown", unknownBody: "Unresolved on purpose. Research may clarify it; silence may not.", userConfirmed: "user-confirmed only", optionalDetails: "Quality, team & policy details", optional: "optional but decision-relevant", team: "Team capabilities", performance: "Performance requirements", budget: "Budget constraints", security: "Security requirements", license: "License requirements", shortlist: "Candidate shortlist", shortlistHint: "one per line · maximum three", runMode: "Run mode", fastMode: "Fast Demo / frozen Evidence", verifiedMode: "Verified / live authorities", next: "Next: review the classified requirements and proposed investigation work.", preparing: "Preparing review…", reviewRules: "Review decision rules →",
    recent: "RECENT", ledger: "Decision records", frozenFixture: "Frozen fixture", localVector: "Local RAG vector store", openCompleted: "Open completed run →", useStartingPoint: "Use as starting point", loadingRun: "Loading…", userPreserved: "User statement preserved", linkedTo: "Linked to", creating: "Saving…", contextIssues: "context issues", workflowError: "The run or Requirements Review could not be saved. Your Decision Context remains here; try again.", apiError: "The local API could not be reached. Your confirmed review remains on this page.", loadError: "That Decision Workflow is unavailable. The current draft was not changed.",
  },
  "zh-CN": {
    workspace: "决策工作台", title: "先定义选择，再验证结论。", contextKicker: "01 / 决策背景", thesis: "把用户事实、评估标准、未知项、调研与验证分开记录。系统可以提出问题，但不能替用户发明 Must-have。", frozenExample: "查看冻结示例", progress: "决策工作台进度", stepContext: "决策背景", stepContextNote: "用户提供的事实", stepReviewNote: "逐项检查来源", stepLaunch: "启动任务", stepLaunchNote: "使用现有运行契约",
    legacyTitle: "旧版任务投影", legacyBody: "仅恢复旧任务的问题、环境、Must-have 与候选项。继续前请检查所有空白字段。", restoredTitle: "已恢复 Decision Workflow", restoredBody: "已从公开 API 恢复 Decision Context、确认状态、运行模式与候选范围。", paused: "工作台已暂停", dismiss: "关闭",
    userInput: "用户输入", today: "记录当前已知事实", todayNote: "信息应足以调研；尚未确认的内容保持 Unknown。", exactChoice: "本次需要回答的技术选择", projectSummary: "项目概况", projectSummaryHint: "应用、用户与本次决策边界", currentStack: "当前技术栈", primaryUses: "主要使用场景", onePerLine: "每行一项", runtime: "运行与部署", pythonVersion: "Python 版本", operatingSystem: "操作系统", topology: "部署拓扑", deploymentTopology: "部署拓扑", requirementsTitle: "审阅 User Requirements", requirementsBody: "这些原子化需求已随任务持久化。请核对分类和表述后再确认。", confirmRequirements: "确认 Requirements Review →", criteriaTitle: "确认 Selection Criteria", criteriaBody: "请检查派生标准与 Research Plan。保存本次确认前，系统不会开始调研。", confirmCriteria: "确认标准并开始调研 →", researchQuestions: "调研问题（Research Questions）", pocChecks: "验证项（PoC Checks）", researchPlan: "调研计划（Research Plan）", confirmedLabel: "我确认这份已保存的审阅记录", startOver: "创建新的 Decision Context", loadingWorkflow: "正在恢复已保存的工作流…",
    rulesKicker: "决策规则", rulesTitle: "区分约束、偏好与未知项", mustTitle: "硬约束（Must-have）", mustBody: "不可妥协；任一项不满足，候选即失去资格。", preferenceTitle: "偏好（Preference）", preferenceBody: "只对合格候选排序，不能覆盖失败的 Must-have。", unknownTitle: "未知项（Unknown）", unknownBody: "明确保留未解决问题；调研可以澄清，沉默不能当作答案。", userConfirmed: "仅限用户确认", optionalDetails: "质量、团队与策略", optional: "可选，但可能影响决策", team: "团队能力", performance: "性能要求", budget: "预算约束", security: "安全要求", license: "许可证要求", shortlist: "候选清单", shortlistHint: "每行一项，最多三个", runMode: "运行模式", fastMode: "Fast Demo（冻结 Evidence）", verifiedMode: "Verified（实时 authority）", next: "下一步：检查分类结果和拟议的调研工作。", preparing: "正在准备…", reviewRules: "检查决策规则 →",
    recent: "最近任务", ledger: "决策记录", frozenFixture: "冻结夹具", localVector: "本地 RAG 向量存储", openCompleted: "查看已完成任务 →", useStartingPoint: "作为新任务起点", loadingRun: "载入中…", userPreserved: "保留用户原始陈述", linkedTo: "关联", creating: "正在保存…", contextIssues: "项输入需要检查", workflowError: "无法保存任务或 Requirements Review。Decision Context 仍保留在本页，请重试。", apiError: "无法连接本地 API。已确认内容仍保留在本页。", loadError: "无法读取该 Decision Workflow，当前草稿未改变。",
  },
} as const;

function LinesField({ label, hint, value, onChange, rows = 2, className }: { label: string; hint?: string; value: string[]; onChange: (value: string[]) => void; rows?: number; className?: string }) {
  return <label className={className}>{label}{hint && <small>{hint}</small>}<textarea rows={rows} aria-label={label} value={value.join("\n")} onChange={(event) => onChange(compactLines(event.target.value))}/></label>;
}

export function DecisionWorkspacePage() {
  const { locale, t } = useI18n();
  const c = copy[locale];
  const navigate = useNavigate();
  const { id: workflowRunId } = useParams();
  const [draft, setDraft] = useState(initialDecisionDraft);
  const [workflow, setWorkflow] = useState<DecisionWorkflow | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [runs, setRuns] = useState<TechScoutRunSummary[]>([]);
  const [status, setStatus] = useState<"idle" | "reviewing" | "confirming" | "loading_run" | "loading_workflow">(workflowRunId ? "loading_workflow" : "idle");
  const [error, setError] = useState<string | null>(null);
  const [compatibility, setCompatibility] = useState<"native" | "legacy_projection" | null>(null);
  const [issues, setIssues] = useState<ReturnType<typeof validateDecisionDraft>>([]);
  const phaseHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => { void techScoutApi.listRuns().then((response) => setRuns(response.data.items)).catch(() => setRuns([])); }, []);
  useEffect(() => {
    if (!workflowRunId) return;
    setStatus("loading_workflow"); setError(null);
    void workspace.load(workflowRunId).then((loaded) => {
      setDraft(loaded.draft); setCompatibility(loaded.compatibility); setWorkflow(loaded.workflow ?? null); setConfirmed(false);
      if (loaded.workflow?.state === "research_ready") navigate(`/runs/${workflowRunId}`, { replace: true });
    }).catch(() => setError(c.loadError)).finally(() => setStatus("idle"));
  }, [workflowRunId, navigate, c.loadError]);
  useEffect(() => { if (workflow) phaseHeading.current?.focus(); }, [workflow]);

  function update(next: Partial<DecisionWorkspaceDraft>) { setDraft((current) => ({ ...current, ...next })); setConfirmed(false); setCompatibility(null); }

  async function prepareReview() {
    const nextIssues = validateDecisionDraft(draft); setIssues(nextIssues); setError(null);
    if (nextIssues.length) { document.querySelector<HTMLElement>(`[data-field="${nextIssues[0].field}"] textarea, [data-field="${nextIssues[0].field}"] input`)?.focus(); return; }
    setStatus("reviewing");
    try { const saved = await workspace.startReview(draft); setWorkflow(saved); setConfirmed(false); navigate(`/runs/${saved.run_id}/workflow`); }
    catch { setError(c.workflowError); }
    finally { setStatus("idle"); }
  }

  async function advanceWorkflow() {
    if (!confirmed || !workflow) return;
    setStatus("confirming"); setError(null);
    try {
      const saved = workflow.state === "requirements_review" ? await workspace.confirmRequirements(workflow) : await workspace.confirmCriteria(workflow);
      setWorkflow(saved); setConfirmed(false);
      if (saved.state === "research_ready") navigate(`/runs/${saved.run_id}`);
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : c.apiError); }
    finally { setStatus("idle"); }
  }

  async function loadRun(runId: string) {
    setStatus("loading_run"); setError(null);
    try {
      const loaded = await workspace.load(runId);
      if (loaded.workflow && loaded.workflow.state !== "research_ready") { navigate(`/runs/${runId}/workflow`); return; }
      setDraft(loaded.draft); setCompatibility(loaded.compatibility); setWorkflow(null); setConfirmed(false); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('[data-field="question"] textarea')?.focus());
    }
    catch { setError(c.loadError); }
    finally { setStatus("idle"); }
  }

  function handleShortcut(event: KeyboardEvent<HTMLElement>) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !workflow) { event.preventDefault(); void prepareReview(); }
  }

  if (status === "loading_workflow" && !workflow) return <div className="page-state">{c.loadingWorkflow}</div>;

  return <div className="decision-workspace" onKeyDown={handleShortcut}>
    <section className="workspace-intro" aria-labelledby="workspace-title">
      <div><p className="eyebrow">{c.workspace}</p><h1 id="workspace-title">{c.title}</h1></div>
      <div className="workspace-thesis"><span>{c.contextKicker}</span><p>{c.thesis}</p><Link to={`/runs/${TECHSCOUT_FIXTURE_ID}`}>{c.frozenExample} <span aria-hidden="true">→</span></Link></div>
    </section>

    <nav className="workspace-steps" aria-label={c.progress}>
      <span data-active={!workflow}>1 <b>{c.stepContext}</b><small>{c.stepContextNote}</small></span>
      <span data-active={workflow?.state === "requirements_review"}>2 <b>Requirements Review</b><small>{c.stepReviewNote}</small></span>
      <span data-active={workflow?.state === "criteria_confirmation"}>3 <b>Criteria Confirmation</b><small>Research Ready</small></span>
      <span data-active={workflow?.state === "research_ready"}>4 <b>{c.stepLaunch}</b><small>{c.stepLaunchNote}</small></span>
    </nav>

    {compatibility === "legacy_projection" && <div className="compatibility-note" role="status"><b>{c.legacyTitle}</b><span>{c.legacyBody}</span></div>}
    {compatibility === "native" && <div className="compatibility-note" role="status"><b>{c.restoredTitle}</b><span>{c.restoredBody}</span></div>}
    {error && <div className="workspace-error" role="alert"><b>{c.paused}</b><span>{error}</span><button type="button" onClick={() => setError(null)}>{c.dismiss}</button></div>}

    {!workflow ? <section className="context-stage" aria-labelledby="context-heading">
      <div className="stage-rail"><span>01</span><p>DECISION<br/>CONTEXT</p><small>Ctrl/⌘ + Enter</small></div>
      <form className="context-sheet" onSubmit={(event) => { event.preventDefault(); void prepareReview(); }} noValidate>
        <header><div><p className="eyebrow">{c.userInput}</p><h2 id="context-heading">{c.today}</h2></div><p>{c.todayNote}</p></header>
        {issues.length > 0 && <div className="issue-summary" role="alert"><b>{issues.length} {c.contextIssues}</b><ul>{issues.map((issue) => <li key={`${issue.field}-${issue.message}`}>{issue.message}</li>)}</ul></div>}
        <div className="field-grid">
          <label className="field-wide" data-field="question">{t("question")}<small>{c.exactChoice}</small><textarea rows={2} aria-label={t("question")} value={draft.question} onChange={(event) => update({ question: event.target.value })}/></label>
          <label className="field-wide" data-field="projectSummary">{c.projectSummary}<small>{c.projectSummaryHint}</small><textarea rows={3} aria-label={c.projectSummary} value={draft.projectSummary} onChange={(event) => update({ projectSummary: event.target.value })}/></label>
          <LinesField label={c.currentStack} hint={c.onePerLine} value={draft.currentStack} onChange={(currentStack) => update({ currentStack })}/>
          <LinesField label={c.primaryUses} hint={c.onePerLine} value={draft.useCases} onChange={(useCases) => update({ useCases })}/>
        </div>
        <fieldset className="deployment-strip" data-field="deployment"><legend>{c.runtime}</legend><label>Python<input aria-label={c.pythonVersion} value={draft.deployment.pythonVersion} onChange={(event) => update({ deployment: { ...draft.deployment, pythonVersion: event.target.value } })}/></label><label>{c.operatingSystem}<input aria-label={c.operatingSystem} value={draft.deployment.operatingSystem} onChange={(event) => update({ deployment: { ...draft.deployment, operatingSystem: event.target.value } })}/></label><label>{c.topology}<input aria-label={c.deploymentTopology} value={draft.deployment.deployment} onChange={(event) => update({ deployment: { ...draft.deployment, deployment: event.target.value } })}/></label></fieldset>

        <section className="requirement-lanes" aria-labelledby="requirements-heading"><header><span>02</span><div><p className="eyebrow">{c.rulesKicker}</p><h3 id="requirements-heading">{c.rulesTitle}</h3></div></header>
          <div className="lane must-lane" data-field="mustHaves"><div><b>M</b><h4>{c.mustTitle}</h4><p>{c.mustBody}</p></div><textarea rows={5} aria-label={c.mustTitle} value={draft.mustHaves.join("\n")} onChange={(event) => update({ mustHaves: compactLines(event.target.value) })}/><small>{draft.mustHaves.length}/5 · {c.userConfirmed}</small></div>
          <div className="lane preference-lane" data-field="preferences"><div><b>P</b><h4>{c.preferenceTitle}</h4><p>{c.preferenceBody}</p></div><textarea rows={5} aria-label={c.preferenceTitle} value={draft.preferences.join("\n")} onChange={(event) => update({ preferences: compactLines(event.target.value) })}/><small>{draft.preferences.length}/12 · {c.onePerLine}</small></div>
          <div className="lane unknown-lane" data-field="unknowns"><div><b>?</b><h4>{c.unknownTitle}</h4><p>{c.unknownBody}</p></div><textarea rows={5} aria-label={c.unknownTitle} value={draft.unknowns.join("\n")} onChange={(event) => update({ unknowns: compactLines(event.target.value) })}/><small>{draft.unknowns.length}/12 · {c.onePerLine}</small></div>
        </section>

        <details className="context-details"><summary><span>03</span><b>{c.optionalDetails}</b><small>{c.optional}</small></summary><div className="detail-grid">
          <LinesField label={c.team} value={draft.teamCapabilities} onChange={(teamCapabilities) => update({ teamCapabilities })}/><LinesField label={c.performance} value={draft.performanceRequirements} onChange={(performanceRequirements) => update({ performanceRequirements })}/><LinesField label={c.budget} value={draft.budgetConstraints} onChange={(budgetConstraints) => update({ budgetConstraints })}/><LinesField label={c.security} value={draft.securityRequirements} onChange={(securityRequirements) => update({ securityRequirements })}/><LinesField label={c.license} value={draft.licenseRequirements} onChange={(licenseRequirements) => update({ licenseRequirements })}/>
        </div></details>

        <div className="launch-inputs" data-field="candidates"><LinesField label={c.shortlist} hint={c.shortlistHint} value={draft.candidates} onChange={(candidates) => update({ candidates })} rows={3}/><fieldset><legend>{c.runMode}</legend><label><input type="radio" name="workspace-mode" checked={draft.mode === "fast"} onChange={() => update({ mode: "fast" })}/> {c.fastMode}</label><label><input type="radio" name="workspace-mode" checked={draft.mode === "verified"} onChange={() => update({ mode: "verified" })}/> {c.verifiedMode}</label></fieldset></div>
        <div className="sheet-actions"><span>{c.next}</span><button className="primary-action" disabled={status === "reviewing"}>{status === "reviewing" ? c.preparing : c.reviewRules}</button></div>
      </form>
      <aside className="decision-ledger"><header><span>{c.recent}</span><b>{c.ledger}</b></header><Link to={`/runs/${TECHSCOUT_FIXTURE_ID}`}><small>{c.frozenFixture}</small><strong>{c.localVector}</strong><span>{c.openCompleted}</span></Link>{runs.slice(0, 4).map((run) => <article key={run.id}><small>{run.status.replaceAll("_", " ")}</small><strong>{run.question}</strong><button type="button" onClick={() => void loadRun(run.id)} disabled={status === "loading_run"}>{status === "loading_run" ? c.loadingRun : c.useStartingPoint}</button></article>)}</aside>
    </section> : <section className="review-stage" aria-labelledby="review-heading">
      <header className="review-header"><div><p className="eyebrow">{workflow.state.replaceAll("_", " ")} · v{workflow.version}</p><h2 id="review-heading" ref={phaseHeading} tabIndex={-1}>{workflow.state === "requirements_review" ? c.requirementsTitle : c.criteriaTitle}</h2></div><Link to="/">{c.startOver}</Link></header>
      <div className="workflow-boundary" role="note"><b>{workflow.state === "requirements_review" ? "Requirements Review" : "Criteria Confirmation"}</b><p>{workflow.state === "requirements_review" ? c.requirementsBody : c.criteriaBody}</p></div>
      {workflow.state === "requirements_review" ? <div className="review-grid">{(["hard_constraint", "evaluation_criterion", "unknown"] as const).map((kind) => {
        const items = workflow.requirements.filter((item) => item.kind === kind); if (!items.length) return null;
        const title = kind === "hard_constraint" ? "Must-have" : kind === "evaluation_criterion" ? "Preference" : "Unknown";
        return <section className={`review-column review-${kind}`} key={kind}><header><span>{kind === "hard_constraint" ? "M" : kind === "evaluation_criterion" ? "P" : "?"}</span><div><h3>{title}</h3><small>{c.userPreserved}</small></div></header><ol>{items.map((item) => <li key={item.requirement_id}><span>{item.statement}</span><small>{item.requirement_id}</small></li>)}</ol></section>;
      })}</div> : workflow.selection_criteria && workflow.research_plan ? <div className="review-grid criteria-grid">
        <CriteriaColumn title="Must-have" index="M" items={workflow.selection_criteria.hard_constraints.map((item) => ({ id: item.item_id, text: item.statement, links: item.requirement_ids }))} linkedTo={c.linkedTo}/>
        <CriteriaColumn title="Preference" index="P" items={workflow.selection_criteria.evaluation_criteria.map((item) => ({ id: item.item_id, text: item.statement, links: item.requirement_ids }))} linkedTo={c.linkedTo}/>
        <CriteriaColumn title="Unknown" index="?" items={workflow.selection_criteria.unknowns.map((item) => ({ id: item.item_id, text: item.statement, links: item.requirement_ids }))} linkedTo={c.linkedTo}/>
        <CriteriaColumn title={c.researchQuestions} index="RQ" items={workflow.selection_criteria.research_questions.map((item) => ({ id: item.item_id, text: item.question, links: item.requirement_ids }))} linkedTo={c.linkedTo}/>
        <CriteriaColumn title={c.pocChecks} index="PC" items={workflow.selection_criteria.poc_checks.map((item) => ({ id: item.item_id, text: item.check, links: item.requirement_ids }))} linkedTo={c.linkedTo}/>
        <section className="research-plan-card"><p className="eyebrow">{c.researchPlan}</p><strong>{workflow.research_plan.plan_id}</strong><p>{workflow.research_plan.poc_intent}</p><small>{workflow.research_plan.planned_evidence.length} Evidence queries · {workflow.research_plan.required_capabilities.length} required capabilities</small></section>
      </div> : null}
      <footer className="review-actions"><label className="confirm-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}/><span>{c.confirmedLabel}</span></label><button className="primary-action" disabled={!confirmed || status === "confirming"} onClick={() => void advanceWorkflow()}>{status === "confirming" ? c.creating : workflow.state === "requirements_review" ? c.confirmRequirements : c.confirmCriteria}</button></footer>
    </section>}
  </div>;
}

function CriteriaColumn({ title, index, items, linkedTo }: { title: string; index: string; items: { id: string; text: string; links: string[] }[]; linkedTo: string }) {
  if (!items.length) return null;
  return <section className="review-column"><header><span>{index}</span><div><h3>{title}</h3><small>{items.length} items</small></div></header><ol>{items.map((item) => <li key={item.id}><span>{item.text}</span><small>{linkedTo} {item.links.join(", ")}</small></li>)}</ol></section>;
}
