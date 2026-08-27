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
const copy = {
  en: {
    workspace: "Decision workspace", title: "Frame the choice. Then test it.", contextKicker: "01 / CONTEXT", thesis: "Keep user facts, criteria, unknowns, research, and verification separate. A planner may propose questions, but cannot invent a Must-have.", frozenExample: "Open frozen example", progress: "Decision workspace progress", stepContext: "Decision Context", stepContextNote: "user-owned facts", stepReview: "Review criteria", stepReviewNote: "trace every item", stepLaunch: "Launch", stepLaunchNote: "existing run contract",
    legacyTitle: "Legacy run projection", legacyBody: "Only the older run’s core question, environment, Must-haves, and candidates were recovered. Review blank fields before continuing.", restoredTitle: "Decision Context restored", restoredBody: "The canonical v2 context was loaded. Unknowns and planner proposals remain blank because that endpoint does not include them.", paused: "Workspace paused", dismiss: "Dismiss",
    userInput: "User-owned input", today: "State what is true today.", todayNote: "Be specific enough to investigate, and leave unresolved facts visible.", exactChoice: "The exact technology choice to make", projectSummary: "Project summary", projectSummaryHint: "Application, users, and the boundary of this decision", currentStack: "Current stack", primaryUses: "Primary use cases", onePerLine: "one item per line", runtime: "Runtime & deployment", pythonVersion: "Python version", operatingSystem: "Operating system", topology: "Topology", deploymentTopology: "Deployment topology",
    rulesKicker: "Decision rules", rulesTitle: "Classify what matters.", mustTitle: "Must-have", mustBody: "Non-negotiable. An unmet item makes a candidate ineligible.", preferenceTitle: "Preference", preferenceBody: "Ranks eligible candidates, but never overrides a failed Must-have.", unknownTitle: "Unknown", unknownBody: "Unresolved on purpose. Research may clarify it; silence may not.", userConfirmed: "user-confirmed only", optionalDetails: "Quality, team & policy details", optional: "optional but decision-relevant", team: "Team capabilities", performance: "Performance requirements", budget: "Budget constraints", security: "Security requirements", license: "License requirements", shortlist: "Candidate shortlist", shortlistHint: "one per line · maximum three", runMode: "Run mode", fastMode: "Fast Demo / frozen Evidence", verifiedMode: "Verified / live authorities", next: "Next: review the classified requirements and proposed investigation work.", preparing: "Preparing review…", reviewRules: "Review decision rules →",
    recent: "RECENT", ledger: "Decision records", frozenFixture: "Frozen fixture", localVector: "Local RAG vector store", openCompleted: "Open completed run →", useStartingPoint: "Use as starting point", loadingRun: "Loading…", reviewKicker: "Traceability review", reviewTitle: "Confirm the work before it begins.", edit: "← Edit Decision Context", previewTitle: "Preview boundary", previewBody: "The classification below is deterministic fixture output. Decision Context uses the existing v2 run contract; Unknowns, Research Questions, PoC Checks, and these confirmations are not yet persisted.", userPreserved: "User statement preserved", linkedTo: "Linked to", reviewed: "I reviewed this lane", lanesConfirmed: "lanes confirmed", ready: "Ready to create the run envelope.", confirmAll: "Confirm each populated lane to continue.", creating: "Creating run…", contextIssues: "context issues", previewError: "The criteria preview could not be prepared. Your Decision Context remains here; try again.", apiError: "The local API could not be reached. Your confirmed review remains on this page.", loadError: "That Decision Context is unavailable. The current draft was not changed.",
  },
  "zh-CN": {
    workspace: "决策工作台", title: "先定义选择，再验证结论。", contextKicker: "01 / 决策背景", thesis: "把用户事实、评估标准、未知项、调研与验证分开记录。系统可以提出问题，但不能替用户发明 Must-have。", frozenExample: "查看冻结示例", progress: "决策工作台进度", stepContext: "决策背景", stepContextNote: "用户提供的事实", stepReview: "确认标准", stepReviewNote: "逐项检查来源", stepLaunch: "启动任务", stepLaunchNote: "使用现有运行契约",
    legacyTitle: "旧版任务投影", legacyBody: "仅恢复旧任务的问题、环境、Must-have 与候选项。继续前请检查所有空白字段。", restoredTitle: "已恢复 Decision Context", restoredBody: "已载入 v2 决策背景。该接口不包含 Unknown 与规划建议，因此相关字段保持为空。", paused: "工作台已暂停", dismiss: "关闭",
    userInput: "用户输入", today: "记录当前已知事实", todayNote: "信息应足以调研；尚未确认的内容保持 Unknown。", exactChoice: "本次需要回答的技术选择", projectSummary: "项目概况", projectSummaryHint: "应用、用户与本次决策边界", currentStack: "当前技术栈", primaryUses: "主要使用场景", onePerLine: "每行一项", runtime: "运行与部署", pythonVersion: "Python 版本", operatingSystem: "操作系统", topology: "部署拓扑", deploymentTopology: "部署拓扑",
    rulesKicker: "决策规则", rulesTitle: "区分约束、偏好与未知项", mustTitle: "硬约束（Must-have）", mustBody: "不可妥协；任一项不满足，候选即失去资格。", preferenceTitle: "偏好（Preference）", preferenceBody: "只对合格候选排序，不能覆盖失败的 Must-have。", unknownTitle: "未知项（Unknown）", unknownBody: "明确保留未解决问题；调研可以澄清，沉默不能当作答案。", userConfirmed: "仅限用户确认", optionalDetails: "质量、团队与策略", optional: "可选，但可能影响决策", team: "团队能力", performance: "性能要求", budget: "预算约束", security: "安全要求", license: "许可证要求", shortlist: "候选清单", shortlistHint: "每行一项，最多三个", runMode: "运行模式", fastMode: "Fast Demo（冻结 Evidence）", verifiedMode: "Verified（实时 authority）", next: "下一步：检查分类结果和拟议的调研工作。", preparing: "正在准备…", reviewRules: "检查决策规则 →",
    recent: "最近任务", ledger: "决策记录", frozenFixture: "冻结夹具", localVector: "本地 RAG 向量存储", openCompleted: "查看已完成任务 →", useStartingPoint: "作为新任务起点", loadingRun: "载入中…", reviewKicker: "可追溯性确认", reviewTitle: "执行前确认每一项工作", edit: "← 返回编辑 Decision Context", previewTitle: "预览边界", previewBody: "以下分类是确定性的 fixture 预览。Decision Context 使用现有 v2 运行契约；Unknown、Research Question、PoC Check 与本页确认尚未持久化。", userPreserved: "保留用户原始陈述", linkedTo: "关联", reviewed: "我已检查此栏", lanesConfirmed: "栏已确认", ready: "可以创建任务。", confirmAll: "请确认每个非空栏。", creating: "正在创建任务…", contextIssues: "项输入需要检查", previewError: "无法生成标准预览。Decision Context 已保留，请重试。", apiError: "无法连接本地 API。已确认内容仍保留在本页。", loadError: "无法读取该 Decision Context，当前草稿未改变。",
  },
} as const;

const reviewLabels = {
  en: {
    must_have: { index: "M", title: "Must-have", note: "User-owned · disqualifies when unmet" }, preference: { index: "P", title: "Preference", note: "User-owned · ranks eligible candidates" }, unknown: { index: "U", title: "Unknown", note: "Unresolved · never treated as a negative fact" }, research_question: { index: "RQ", title: "Research Question", note: "Fixture proposal · seeks authoritative Evidence" }, poc_check: { index: "PC", title: "PoC Check", note: "Fixture proposal · bounded local verification" },
  },
  "zh-CN": {
    must_have: { index: "M", title: "硬约束（Must-have）", note: "用户确认 · 不满足即失去资格" }, preference: { index: "P", title: "偏好（Preference）", note: "用户确认 · 仅对合格候选排序" }, unknown: { index: "U", title: "未知项（Unknown）", note: "保持未解决 · 不自动视为负面事实" }, research_question: { index: "RQ", title: "调研问题（Research Question）", note: "Fixture 建议 · 寻找权威 Evidence" }, poc_check: { index: "PC", title: "验证项（PoC Check）", note: "Fixture 建议 · 有界本地验证" },
  },
};

function LinesField({ label, hint, value, onChange, rows = 2, className }: { label: string; hint?: string; value: string[]; onChange: (value: string[]) => void; rows?: number; className?: string }) {
  return <label className={className}>{label}{hint && <small>{hint}</small>}<textarea rows={rows} aria-label={label} value={value.join("\n")} onChange={(event) => onChange(compactLines(event.target.value))}/></label>;
}

export function DecisionWorkspacePage() {
  const { locale, t } = useI18n();
  const c = copy[locale];
  const labels = reviewLabels[locale];
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
    catch { setError(c.previewError); }
    finally { setStatus("idle"); }
  }

  async function launch() {
    if (!allConfirmed) return;
    setStatus("launching"); setError(null);
    try { navigate(`/runs/${(await workspace.launch(draft)).id}`); }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : c.apiError); setStatus("idle"); }
  }

  async function loadRun(runId: string) {
    setStatus("loading_run"); setError(null);
    try { const loaded = await workspace.load(runId); setDraft(loaded.draft); setCompatibility(loaded.compatibility); setReview(null); setConfirmed({}); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('[data-field="question"] textarea')?.focus()); }
    catch { setError(c.loadError); }
    finally { setStatus("idle"); }
  }

  function handleShortcut(event: KeyboardEvent<HTMLElement>) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !review) { event.preventDefault(); void prepareReview(); }
  }

  return <div className="decision-workspace" onKeyDown={handleShortcut}>
    <section className="workspace-intro" aria-labelledby="workspace-title">
      <div><p className="eyebrow">{c.workspace}</p><h1 id="workspace-title">{c.title}</h1></div>
      <div className="workspace-thesis"><span>{c.contextKicker}</span><p>{c.thesis}</p><Link to={`/runs/${TECHSCOUT_FIXTURE_ID}`}>{c.frozenExample} <span aria-hidden="true">→</span></Link></div>
    </section>

    <nav className="workspace-steps" aria-label={c.progress}>
      <span data-active={!review}>1 <b>{c.stepContext}</b><small>{c.stepContextNote}</small></span>
      <span data-active={Boolean(review)}>2 <b>{c.stepReview}</b><small>{c.stepReviewNote}</small></span>
      <span data-active={allConfirmed}>3 <b>{c.stepLaunch}</b><small>{c.stepLaunchNote}</small></span>
    </nav>

    {compatibility === "legacy_projection" && <div className="compatibility-note" role="status"><b>{c.legacyTitle}</b><span>{c.legacyBody}</span></div>}
    {compatibility === "native" && <div className="compatibility-note" role="status"><b>{c.restoredTitle}</b><span>{c.restoredBody}</span></div>}
    {error && <div className="workspace-error" role="alert"><b>{c.paused}</b><span>{error}</span><button type="button" onClick={() => setError(null)}>{c.dismiss}</button></div>}

    {!review ? <section className="context-stage" aria-labelledby="context-heading">
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
      <header className="review-header"><div><p className="eyebrow">{c.reviewKicker}</p><h2 id="review-heading" ref={phaseHeading} tabIndex={-1}>{c.reviewTitle}</h2></div><button type="button" onClick={() => { setReview(null); setConfirmed({}); }}>{c.edit}</button></header>
      <div className="fixture-boundary" role="note"><b>{c.previewTitle}</b><p>{c.previewBody}</p></div>
      <div className="review-grid">{reviewKinds.map((kind) => { const meta = labels[kind]; return <section className={`review-column review-${kind}`} key={kind} aria-labelledby={`review-${kind}`}><header><span>{meta.index}</span><div><h3 id={`review-${kind}`}>{meta.title}</h3><small>{meta.note}</small></div></header><ol>{grouped[kind].map((item) => <li key={item.itemId}><span>{item.statement}</span><small>{item.authority === "user" ? c.userPreserved : `${c.linkedTo} ${item.requirementIds.join(", ")}`}</small></li>)}</ol><label className="confirm-check"><input type="checkbox" checked={Boolean(confirmed[kind])} onChange={(event) => setConfirmed({ ...confirmed, [kind]: event.target.checked })}/><span>{c.reviewed}</span></label></section>; })}</div>
      <footer className="review-actions"><div><b>{reviewKinds.filter((kind) => confirmed[kind]).length}/{reviewKinds.length} {c.lanesConfirmed}</b><span>{allConfirmed ? c.ready : c.confirmAll}</span></div><button className="primary-action" disabled={!allConfirmed || status === "launching"} onClick={() => void launch()}>{status === "launching" ? c.creating : t("start")}</button></footer>
    </section>}
  </div>;
}
