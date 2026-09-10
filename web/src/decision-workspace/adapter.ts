import type { DecisionContext, DecisionWorkflow, RequirementsReviewRequest, TechScoutApi } from "../api/contracts";
import { ApiError } from "../api/client";
import { initialDecisionDraft, toCreateRunRequest, type DecisionWorkspaceDraft } from "./model";

export interface LoadedDecisionDraft { draft: DecisionWorkspaceDraft; compatibility: "native" | "legacy_projection"; workflow?: DecisionWorkflow }
export interface DecisionWorkspaceAdapter {
  startReview(draft: DecisionWorkspaceDraft): Promise<DecisionWorkflow>;
  confirmRequirements(workflow: DecisionWorkflow): Promise<DecisionWorkflow>;
  confirmCriteria(workflow: DecisionWorkflow): Promise<DecisionWorkflow>;
  load(runId: string): Promise<LoadedDecisionDraft>;
}

function commandId(operation: string, runId: string): string { return `web:${operation}:${runId}`; }

function requirementsFromDraft(draft: DecisionWorkspaceDraft): RequirementsReviewRequest {
  const inputs = [
    ...draft.mustHaves.map((statement) => ({ kind: "hard_constraint" as const, statement })),
    ...draft.preferences.map((statement) => ({ kind: "evaluation_criterion" as const, statement })),
    ...draft.unknowns.map((statement) => ({ kind: "unknown" as const, statement })),
  ];
  return { requirements: inputs.map((item, index) => ({ ...item, requirement_id: `requirement:web-${index + 1}` })) };
}

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

export function createDecisionWorkspaceAdapter(api: TechScoutApi): DecisionWorkspaceAdapter {
  return {
    async startReview(draft) {
      const run = (await api.createRun(toCreateRunRequest(draft))).data;
      return (await api.reviewRequirements(run.id, requirementsFromDraft(draft), commandId("requirements-review", run.id))).data;
    },
    async confirmRequirements(workflow) {
      return (await api.confirmRequirements(workflow.run_id, commandId("confirm-requirements", workflow.run_id))).data;
    },
    async confirmCriteria(workflow) {
      if (!workflow.selection_criteria) throw new Error("Selection Criteria are unavailable.");
      return (await api.confirmCriteria(workflow.run_id, workflow.selection_criteria.contract_id, commandId("confirm-criteria", workflow.run_id))).data;
    },
    async load(runId) {
      try {
        const [context, workflow, run] = await Promise.all([api.getDecisionContext(runId), api.getWorkflow(runId), api.getRun(runId)]);
        return { draft: { ...contextToDraft(context.data), mode: run.data.mode, candidates: run.data.candidates.map((item) => item.name) }, compatibility: "native", workflow: workflow.data };
      }
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
