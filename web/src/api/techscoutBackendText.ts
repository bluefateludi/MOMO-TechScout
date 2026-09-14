import type { Locale } from "../i18n";

// Backend-issued prose (report summaries, constraint reasons, trace labels) is
// sealed English for audit stability. The UI localizes it with exact-string
// maps; anything unmapped intentionally falls back to the English original so
// no field is ever blanked or invented.

const sentenceMaps: Record<Locale, Record<string, string>> = {
  "en": {},
  "zh-CN": {
    "No safe winner is claimed because the frozen provider cache was used.": "因使用了冻结的 provider 缓存，本次不判定安全胜者。",
    "No safe winner is claimed because neither live nor cached evidence is available.": "实时与缓存证据均不可用，本次不判定安全胜者。",
    "No safe winner is claimed because the candidate has no reviewed local PoC recipe.": "候选项缺少经审查的本地 PoC 配方，本次不判定安全胜者。",
    "No safe winner is claimed because the reviewed Docker PoC is unavailable.": "经审查的 Docker PoC 不可用，本次不判定安全胜者。",
    "No safe winner is claimed because authoritative evidence does not establish every must-have.": "权威证据未覆盖全部必要条件，本次不判定安全胜者。",
    "No safe winner is claimed because the reviewed PoC failed after bounded recovery.": "经审查的 PoC 在有界恢复后仍失败，本次不判定安全胜者。",
    "No safe winner is claimed because authorized exact-revision model authority is unavailable.": "已授权的精确版本模型 authority 不可用，本次不判定安全胜者。",
    "No safe winner is claimed because the authorized model provider did not produce decision/report authority.": "授权的模型 provider 未产生决策/报告 authority，本次不判定安全胜者。",
    "No safe winner is claimed because exact model revision and non-zero provider token usage were not established.": "未能确认精确模型版本与非零 provider token 用量，本次不判定安全胜者。",
    "Live evidence and reviewed Docker PoCs satisfy the Hero Case gates; the first equally qualified item in the user-provided shortlist is the deterministic tie-break.": "实时证据与经审查的 Docker PoC 满足 Hero Case 各项门槛；用户清单中首个同等合格的候选项作为确定性决胜项。",
    "All supported candidates passed frozen evidence and deterministic local PoC validation; the first equally qualified item in the user-provided shortlist is the deterministic tie-break.": "全部受支持候选项通过冻结证据与确定性本地 PoC 验证；用户清单中首个同等合格的候选项作为确定性决胜项。",
    "The first deterministic PoC attempt requires one bounded recovery.": "首次确定性 PoC 尝试需要进行一次有界恢复。",
    "Verified mode is limited because live providers and a real Docker PoC are not connected.": "由于实时 provider 与真实 Docker PoC 未接入，Verified 模式受限。",
    "The synthetic fixture selects Chroma because the frozen evidence and allowlisted PoC cover every hard constraint.": "合成夹具选择 Chroma，因为冻结证据与白名单 PoC 覆盖了全部硬约束。",
    "Frozen cache fallback limits the decision.": "冻结缓存回退限制了本次决策。",
    "Live provider and Docker verification are unavailable.": "实时 provider 与 Docker 验证不可用。",
    "No reviewed local PoC recipe is available.": "没有经审查的本地 PoC 配方。",
    "The reviewed Docker PoC is unavailable.": "经审查的 Docker PoC 不可用。",
    "Live and cached evidence are unavailable.": "实时与缓存证据均不可用。",
    "Authoritative evidence does not establish this must-have.": "权威证据未能确立此必要条件。",
    "The reviewed PoC failed after bounded recovery.": "经审查的 PoC 在有界恢复后失败。",
    "Exact model revision and provider token authority are unavailable.": "精确模型版本与 provider token authority 不可用。",
    "The PoC requires bounded recovery.": "PoC 需要有界恢复。",
    "The reviewed Docker PoC stage failed.": "经审查的 Docker PoC 阶段失败。",
    "The reviewed Docker PoC stage failed after recovery.": "经审查的 Docker PoC 阶段恢复后仍失败。",
    "The deterministic PoC found a dependency conflict.": "确定性 PoC 发现依赖冲突。",
    "Fast Demo requires bounded frozen official evidence per candidate.": "Fast Demo 要求每个候选项具备有界的冻结官方证据。",
    "Fast Demo uses an allowlisted deterministic local PoC.": "Fast Demo 使用白名单内的确定性本地 PoC。",
    "Frozen local MCP research was unavailable.": "冻结的本地 MCP 调研不可用。",
    "Small contract checks do not establish production-scale performance.": "小型契约检查不能证明生产规模性能。",
    "Synthetic Wave 1 contract fixture — not live research or evaluation evidence.": "Wave 1 合成契约夹具——非实时调研或评估证据。",
    "The frozen allowlisted fixture passes persistence and metadata filtering checks.": "冻结的白名单夹具通过持久化与元数据过滤检查。",
    "Chroma documents local persistent storage.": "Chroma 文档记载了本地持久化存储。",
    "Qdrant documents an embedded local mode.": "Qdrant 文档记载了嵌入式本地模式。",
    "A single-node local service with no separately managed database.": "单节点本地服务，无独立管理的数据库。",
    "Harness entered normalize_request.": "Harness 进入需求规范化阶段。",
    "Harness entered plan_research.": "Harness 进入调研规划阶段。",
    "Harness entered research_candidates.": "Harness 进入候选项调研阶段。",
    "Harness entered select_context.": "Harness 进入上下文选择阶段。",
    "Harness entered plan_poc.": "Harness 进入 PoC 规划阶段。",
    "Harness entered execute_poc.": "Harness 进入 PoC 执行阶段。",
    "Harness entered validate.": "Harness 进入校验阶段。",
    "Harness entered review_report.": "Harness 进入报告评审阶段。",
    "Harness entered publish.": "Harness 进入发布阶段。",
    "Harness entered terminal.": "Harness 进入终态阶段。",
  },
};

// Trace labels interpolate slugs (candidate ids, skill names); translate by
// leading phrase so dynamic suffixes survive.
const tracePrefixes: Record<Locale, Array<[string, string]>> = {
  "en": [],
  "zh-CN": [
    ["Routed ", "已按官方文档 skill 路由调研："],
    ["Local MCP returned frozen evidence for ", "本地 MCP 返回冻结证据："],
    ["Routed ", "已按经审查 skill 路由 PoC："],
    ["Local MCP completed ", "本地 MCP 完成 PoC："],
    ["TechScout run accepted", "TechScout 任务已受理"],
    ["TechScout execution started", "TechScout 执行已开始"],
    ["TechScout run reached a terminal state", "TechScout 任务到达终态"],
  ],
};

const enumValueMaps: Record<Locale, Record<string, string>> = {
  "en": {},
  "zh-CN": {
    completed: "已完成",
    completed_with_limitations: "受限完成",
    failed: "已失败",
    queued: "排队中",
    running: "运行中",
    recommended: "推荐",
    not_recommended: "不推荐",
    insufficient_evidence: "证据不足",
    satisfied: "满足",
    not_satisfied: "不满足",
    unknown: "未知",
    v1_supported: "V1 受支持",
    research_only: "仅调研",
    retrieved_fact: "检索事实",
    local_measurement: "本地测量",
    model_inference: "模型推断",
    official_documentation: "官方文档",
    github_repository: "GitHub 仓库",
    github_release: "GitHub 发布",
    github_issue: "GitHub 议题",
    live: "实时",
    cache: "缓存",
    synthetic: "合成",
    unavailable: "不可用",
    passed: "通过",
    not_required: "无需审批",
    required: "需要审批",
    pending: "待审批",
    approved: "已批准",
    denied: "已拒绝",
    none: "无",
    fast: "Fast Demo",
    verified: "Verified",
    compatible: "兼容",
  },
};

export function backendText(text: string | null | undefined, locale: Locale): string {
  if (!text) return text ?? "";
  if (locale !== "zh-CN") return text;
  const exact = sentenceMaps[locale][text];
  if (exact) return exact;
  const prefix = tracePrefixes[locale].find(([lead]) => text.startsWith(lead));
  if (prefix) return `${prefix[1]}${text.slice(prefix[0].length)}`;
  return text;
}

export function backendEnum(value: string | null | undefined, locale: Locale): string {
  if (!value) return value ?? "";
  return enumValueMaps[locale][value] ?? value.replaceAll("_", " ");
}
