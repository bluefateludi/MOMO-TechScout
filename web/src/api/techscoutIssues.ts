import type { Locale } from "../i18n";

const issueMessages: Record<Locale, Record<string, string>> = { en: {
  poc_timeout: "The allowlisted verification timed out within its configured bound.",
  research_only_candidate: "One or more candidates had no trusted verification recipe.",
  dependency_version_conflict: "The verification environment found a dependency version conflict.",
  insufficient_evidence: "The available evidence did not cover every hard constraint.",
  approval_denied: "The requested operation was not approved.",
  dependency_conflict: "The deterministic local PoC found a dependency conflict and preserved the bounded recovery trace.",
  tool_unavailable: "Live provider or real Docker verification is unavailable; the run published an explicit limited result.",
  execution_initialization_failed: "The local TechScout executor failed before it could publish a report.",
  retry_exhausted: "The bounded infrastructure retry was exhausted; the run failed without publishing a report.",
  unexpected_execution_failure: "An unexpected execution failure was classified and terminated safely.",
  artifact_publish_failed: "Failure artifacts could not be published; the task state still terminated safely.",
  deadline_exceeded: "The run exceeded its absolute deadline and terminated without a report.",
  poc_recipe_unsupported: "No reviewed local PoC recipe exists for this candidate, so it remains research-only.",
}, "zh-CN": {
  poc_timeout: "白名单验证在配置的时间上限内超时。",
  research_only_candidate: "一个或多个候选项没有可信验证配方。",
  dependency_version_conflict: "验证环境发现依赖版本冲突。",
  insufficient_evidence: "现有证据未覆盖所有硬约束。",
  approval_denied: "请求的操作未获批准。",
  dependency_conflict: "确定性本地 PoC 发现依赖冲突，并保留了有界恢复 Trace。",
  tool_unavailable: "实时 provider 或真实 Docker 验证不可用；运行发布了明确的受限结果。",
  execution_initialization_failed: "本地 TechScout 执行器在发布报告前失败。",
  retry_exhausted: "有界基础设施重试已耗尽；运行以失败结束且未发布报告。",
  unexpected_execution_failure: "未预期执行异常已完成分类，并安全进入失败终态。",
  artifact_publish_failed: "失败产物无法发布，但任务状态仍已安全终态化。",
  deadline_exceeded: "运行超过绝对截止时间，且未发布报告。",
  poc_recipe_unsupported: "此候选项没有经审查的本地 PoC 配方，因此仅限调研。",
} };

export function messageForTechScoutIssue(code: string, locale: Locale = "en"): string {
  return issueMessages[locale][code] ?? (locale === "zh-CN" ? "运行遇到由此稳定代码记录的有界条件。" : "The run reached a bounded condition recorded by this stable code.");
}
