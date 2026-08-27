import type { DecisionContext, DecisionWorkflow, TechScoutApi, TechScoutCreateRunRequest, TechScoutRunDetail } from "./contracts";
import { ApiError } from "./client";
import { fixtureTrace, TECHSCOUT_FIXTURE_ID, techScoutCandidates, techScoutEvidence, techScoutReport, techScoutRun } from "./techscoutFixtures";

const runs = new Map<string, TechScoutRunDetail>([[TECHSCOUT_FIXTURE_ID, techScoutRun]]);
const contexts = new Map<string, DecisionContext>();
const workflows = new Map<string, DecisionWorkflow>();
const polls = new Map<string, number>();
const delay = () => new Promise((resolve) => setTimeout(resolve, 15));
function find(id: string) { const run = runs.get(id); if (!run) throw new ApiError(404, "run_not_found", "This run could not be found."); return run; }

export const techScoutMockApi: TechScoutApi = {
  async listRuns() { await delay(); return { data: { items: [...runs.values()], next_cursor: null } }; },
  async createRun(request: TechScoutCreateRunRequest) {
    await delay(); const id = crypto.randomUUID(); const now = new Date().toISOString();
    const fastDemo = (request.mode ?? "fast") === "fast";
    const run: TechScoutRunDetail = { id, status: "queued", synthetic: fastDemo, fixture_name: fastDemo ? "interactive-shell" : null, question: request.question, mode: request.mode ?? "fast", progress: { stage: "plan", completed_stages: [], current_skill: null, current_tool: null, elapsed_seconds: 0 }, created_at: now, finished_at: null, project_context: request.project_context, environment: request.environment, hard_constraints: request.hard_constraints, candidates: [], recovery: { attempted: false, failed_stage: null, action: null, outcome: "not_needed", attempts_used: 0 }, approval: { required: false, status: "not_required", reason: null }, issues: [] };
    runs.set(id, run); contexts.set(id, {
      question: request.question, project_summary: request.project_context,
      current_stack: request.current_stack ?? [], use_cases: request.use_cases ?? [], deployment: request.environment,
      team_capabilities: request.team_capabilities ?? [], performance_requirements: request.performance_requirements ?? [],
      budget_constraints: request.budget_constraints ?? [], security_requirements: request.security_requirements ?? [],
      license_requirements: request.license_requirements ?? [], must_haves: request.hard_constraints,
      preferences: request.preferences ?? [],
    }); workflows.set(id, { run_id: id, state: "draft_context", version: 1, decision_context: contexts.get(id)!, requirements: [], requirements_confirmed: false, selection_criteria: null, research_plan: null, created_at: now, updated_at: now }); polls.set(id, 0); return { data: run, location: `/api/v2/runs/${id}` };
  },
  async getWorkflow(id) { await delay(); find(id); const workflow = workflows.get(id); if (!workflow) throw new ApiError(404, "workflow_not_found", "Workflow not found"); return { data: workflow }; },
  async reviewRequirements(id, request) {
    await delay(); find(id); const current = workflows.get(id)!;
    const updated = { ...current, state: "requirements_review" as const, version: current.version + 1, requirements: request.requirements, updated_at: new Date().toISOString() };
    workflows.set(id, updated); return { data: updated };
  },
  async confirmRequirements(id) {
    await delay(); find(id); const current = workflows.get(id)!; const contractId = `criteria:${id}`;
    const hard = current.requirements.filter((item) => item.kind === "hard_constraint").map((item, index) => ({ item_id: `criterion:hard-${index + 1}`, requirement_ids: [item.requirement_id], statement: item.statement }));
    const evaluation = current.requirements.filter((item) => item.kind === "evaluation_criterion").map((item, index) => ({ item_id: `criterion:evaluation-${index + 1}`, requirement_ids: [item.requirement_id], statement: item.statement }));
    const unknowns = current.requirements.filter((item) => item.kind === "unknown").map((item, index) => ({ item_id: `criterion:unknown-${index + 1}`, requirement_ids: [item.requirement_id], statement: item.statement }));
    const researchQuestions = current.requirements.map((item, index) => ({ item_id: `question:web-${index + 1}`, requirement_ids: [item.requirement_id], question: `What authoritative Evidence establishes: ${item.statement}` }));
    const pocChecks = current.requirements.filter((item) => item.kind === "hard_constraint").map((item, index) => ({ item_id: `poc:web-${index + 1}`, requirement_ids: [item.requirement_id], check: `Verify with a bounded allowlisted PoC: ${item.statement}` }));
    const selection = { contract_id: contractId, run_id: id, requirements: current.requirements, hard_constraints: hard, evaluation_criteria: evaluation, unknowns, research_questions: researchQuestions, poc_checks: pocChecks };
    const updated = { ...current, state: "criteria_confirmation" as const, version: current.version + 1, requirements_confirmed: true, selection_criteria: selection, research_plan: { plan_id: `research-plan:${id}`, criteria_contract_id: contractId, investigation_dimensions: [...hard, ...evaluation, ...unknowns].map((item) => item.statement), required_capabilities: hard.map((item) => item.statement), planned_evidence: researchQuestions.map((item) => item.question), poc_intent: pocChecks.map((item) => item.check).join("; ") || "Research-only." }, updated_at: new Date().toISOString() };
    workflows.set(id, updated); return { data: updated };
  },
  async confirmCriteria(id) { await delay(); find(id); const current = workflows.get(id)!; const updated = { ...current, state: "research_ready" as const, version: current.version + 1, updated_at: new Date().toISOString() }; workflows.set(id, updated); return { data: updated }; },
  async getDecisionContext(id) {
    await delay(); const run = find(id); const context = contexts.get(id);
    if (context) return { data: context };
    return { data: {
      question: run.question, project_summary: run.project_context,
      current_stack: [], use_cases: [], deployment: run.environment,
      team_capabilities: [], performance_requirements: [], budget_constraints: [],
      security_requirements: [], license_requirements: [], must_haves: run.hard_constraints,
      preferences: [],
    } };
  },
  async getRun(id) { await delay(); const current = find(id); if (id === TECHSCOUT_FIXTURE_ID || current.status === "completed") return { data: current }; const count = (polls.get(id) ?? 0) + 1; polls.set(id, count); const stages = ["plan", "research", "verify", "decide"] as const; const done = count >= 4; const updated: TechScoutRunDetail = { ...current, status: done ? "completed" : "running", progress: { stage: done ? "terminal" : stages[count], completed_stages: stages.slice(0, count), current_skill: done ? null : `${stages[count]}-fixture-skill`, current_tool: count === 2 ? "poc.run_allowlisted" : null, elapsed_seconds: count * 2 }, finished_at: done ? new Date().toISOString() : null, candidates: done ? techScoutCandidates : current.candidates }; runs.set(id, updated); return { data: updated, retryAfterSeconds: 2 }; },
  async getReport(id) { await delay(); find(id); return { data: { ...techScoutReport, run_id: id } }; },
  async getCandidate(id, candidateId) { await delay(); find(id); const item = techScoutCandidates.find((candidate) => candidate.candidate_id === candidateId); if (!item) throw new ApiError(404, "candidate_not_found", "Candidate not found"); return { data: item }; },
  async getEvidence(id) { await delay(); find(id); return { data: { items: techScoutEvidence } }; },
  async getEvidenceItem(id, evidenceId) { await delay(); find(id); const item = techScoutEvidence.find((evidence) => evidence.evidence_id === evidenceId); if (!item) throw new ApiError(404, "evidence_not_found", "Evidence not found"); return { data: item }; },
  async getTrace(id) { await delay(); find(id); return { data: fixtureTrace }; },
};
