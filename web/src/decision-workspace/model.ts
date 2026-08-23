import type { TechScoutCreateRunRequest } from "../api/contracts";

export type RequirementKind = "must_have" | "preference" | "unknown";
export type ReviewItemKind = RequirementKind | "research_question" | "poc_check";

export interface DecisionWorkspaceDraft {
  question: string;
  projectSummary: string;
  currentStack: string[];
  useCases: string[];
  deployment: { pythonVersion: string; operatingSystem: string; deployment: string };
  teamCapabilities: string[];
  performanceRequirements: string[];
  budgetConstraints: string[];
  securityRequirements: string[];
  licenseRequirements: string[];
  mustHaves: string[];
  preferences: string[];
  unknowns: string[];
  candidates: string[];
  mode: "fast" | "verified";
}

export interface WorkspaceReviewItem {
  itemId: string;
  kind: ReviewItemKind;
  statement: string;
  requirementIds: string[];
  authority: "user" | "planner_fixture";
}

export interface WorkspaceReview {
  reviewId: string;
  items: WorkspaceReviewItem[];
  generatedAt: string;
  integration: "fixture_preview";
}

export interface DraftIssue {
  field: keyof DecisionWorkspaceDraft | "deployment";
  message: string;
}

export const initialDecisionDraft: DecisionWorkspaceDraft = {
  question: "Which local vector store should this RAG service adopt?",
  projectSummary: "A Python RAG service needs a small, maintainable vector store for local-first deployment.",
  currentStack: ["Python 3.11", "FastAPI"],
  useCases: ["semantic retrieval", "metadata-scoped lookup"],
  deployment: { pythonVersion: "3.11", operatingSystem: "Linux", deployment: "single-node local" },
  teamCapabilities: ["Python application development", "no dedicated database operations"],
  performanceRequirements: [],
  budgetConstraints: ["no managed service required for the first release"],
  securityRequirements: ["data remains on the local host"],
  licenseRequirements: ["open-source license suitable for commercial use"],
  mustHaves: ["local persistence", "metadata equality filtering"],
  preferences: ["low operational overhead"],
  unknowns: ["expected corpus size after twelve months"],
  candidates: ["Chroma", "Qdrant Local", "pgvector"],
  mode: "fast",
};

export function compactLines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

export function validateDecisionDraft(draft: DecisionWorkspaceDraft): DraftIssue[] {
  const issues: DraftIssue[] = [];
  const checkList = (field: keyof DecisionWorkspaceDraft, values: string[], max: number) => {
    if (values.length > max) issues.push({ field, message: `${field} supports at most ${max} items.` });
    const normalized = values.map((item) => item.toLocaleLowerCase());
    if (new Set(normalized).size !== normalized.length) issues.push({ field, message: `${field} contains a duplicate item.` });
  };
  if (draft.question.trim().length < 3) issues.push({ field: "question", message: "Add a specific decision question." });
  if (draft.projectSummary.trim().length < 3) issues.push({ field: "projectSummary", message: "Describe the software project and decision boundary." });
  if (!draft.deployment.pythonVersion.trim() || !draft.deployment.operatingSystem.trim() || !draft.deployment.deployment.trim()) issues.push({ field: "deployment", message: "Complete the Python, operating system, and deployment fields." });
  if (draft.mustHaves.length < 1) issues.push({ field: "mustHaves", message: "Provide at least one must-have." });
  checkList("currentStack", draft.currentStack, 20); checkList("useCases", draft.useCases, 12);
  checkList("teamCapabilities", draft.teamCapabilities, 12); checkList("performanceRequirements", draft.performanceRequirements, 12);
  checkList("budgetConstraints", draft.budgetConstraints, 12); checkList("securityRequirements", draft.securityRequirements, 12);
  checkList("licenseRequirements", draft.licenseRequirements, 12); checkList("mustHaves", draft.mustHaves, 5);
  checkList("preferences", draft.preferences, 12); checkList("unknowns", draft.unknowns, 12); checkList("candidates", draft.candidates, 3);
  const mustHaves = new Set(draft.mustHaves.map((item) => item.toLocaleLowerCase()));
  const overlap = draft.preferences.find((item) => mustHaves.has(item.toLocaleLowerCase()));
  if (overlap) issues.push({ field: "preferences", message: `“${overlap}” cannot be both a must-have and a preference.` });
  return issues;
}

export function toCreateRunRequest(draft: DecisionWorkspaceDraft): TechScoutCreateRunRequest {
  return {
    question: draft.question.trim(), project_context: draft.projectSummary.trim(),
    current_stack: draft.currentStack, use_cases: draft.useCases,
    environment: { python_version: draft.deployment.pythonVersion.trim(), operating_system: draft.deployment.operatingSystem.trim(), deployment: draft.deployment.deployment.trim() },
    team_capabilities: draft.teamCapabilities, performance_requirements: draft.performanceRequirements,
    budget_constraints: draft.budgetConstraints, security_requirements: draft.securityRequirements,
    license_requirements: draft.licenseRequirements, hard_constraints: draft.mustHaves,
    preferences: draft.preferences, candidates: draft.candidates.map((name) => ({ name })), mode: draft.mode,
  };
}
