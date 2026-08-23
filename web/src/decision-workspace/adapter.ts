import type { DecisionContext, TechScoutApi, TechScoutRunSummary } from "../api/contracts";
import { ApiError } from "../api/client";
import { buildWorkspaceReviewFixture } from "./fixtures";
import { initialDecisionDraft, toCreateRunRequest, type DecisionWorkspaceDraft, type WorkspaceReview } from "./model";

export interface LoadedDecisionDraft { draft: DecisionWorkspaceDraft; compatibility: "native" | "legacy_projection" }
export interface DecisionWorkspaceAdapter {
  preview(draft: DecisionWorkspaceDraft): Promise<WorkspaceReview>;
  launch(draft: DecisionWorkspaceDraft): Promise<TechScoutRunSummary>;
  load(runId: string): Promise<LoadedDecisionDraft>;
}
export interface CriteriaPreviewPort { preview(draft: DecisionWorkspaceDraft): Promise<WorkspaceReview> }

export const fixtureCriteriaPreview: CriteriaPreviewPort = {
  async preview(draft) { await new Promise((resolve) => setTimeout(resolve, 180)); return buildWorkspaceReviewFixture(draft); },
};

function contextToDraft(context: DecisionContext): DecisionWorkspaceDraft {
  return {
    ...initialDecisionDraft, question: context.question, projectSummary: context.project_summary,
    currentStack: context.current_stack, useCases: context.use_cases,
    deployment: { pythonVersion: context.deployment.python_version, operatingSystem: context.deployment.operating_system, deployment: context.deployment.deployment },
    teamCapabilities: context.team_capabilities, performanceRequirements: context.performance_requirements,
    budgetConstraints: context.budget_constraints, securityRequirements: context.security_requirements,
    licenseRequirements: context.license_requirements, mustHaves: context.must_haves,
    preferences: context.preferences, unknowns: [], candidates: [],
  };
}

export function createDecisionWorkspaceAdapter(api: TechScoutApi, criteria: CriteriaPreviewPort = fixtureCriteriaPreview): DecisionWorkspaceAdapter {
  return {
    preview: (draft) => criteria.preview(draft),
    async launch(draft) { return (await api.createRun(toCreateRunRequest(draft))).data; },
    async load(runId) {
      try { return { draft: contextToDraft((await api.getDecisionContext(runId)).data), compatibility: "native" }; }
      catch (error) {
        if (!(error instanceof ApiError) || error.status !== 404) throw error;
        const run = (await api.getRun(runId)).data;
        return { compatibility: "legacy_projection", draft: {
          ...initialDecisionDraft, question: run.question, projectSummary: run.project_context,
          deployment: { pythonVersion: run.environment.python_version, operatingSystem: run.environment.operating_system, deployment: run.environment.deployment },
          mustHaves: run.hard_constraints, preferences: [], unknowns: [], candidates: run.candidates.map((candidate) => candidate.name),
        } };
      }
    },
  };
}
