import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { techScoutApi } from "../api";
import { ApiError } from "../api/client";
import type { DecisionContext, DecisionWorkflow, TechScoutApi } from "../api/contracts";
import { TECHSCOUT_FIXTURE_ID, techScoutRun } from "../api/techscoutFixtures";
import { createDecisionWorkspaceAdapter } from "../decision-workspace/adapter";
import { initialDecisionDraft, toCreateRunRequest, validateDecisionDraft } from "../decision-workspace/model";
import { DecisionWorkspacePage } from "../routes/DecisionWorkspacePage";

const publicRun = { ...techScoutRun, id: "00000000-0000-4000-8000-000000000110", status: "queued" as const };
const savedRequirements = {
  run_id: publicRun.id, state: "requirements_review", version: 2,
  decision_context: {} as DecisionContext,
  requirements: [{ requirement_id: "requirement:web-1", kind: "hard_constraint", statement: "local persistence" }],
  requirements_confirmed: false, selection_criteria: null, research_plan: null,
  created_at: publicRun.created_at, updated_at: publicRun.created_at,
} as DecisionWorkflow;

describe("Decision Workspace model", () => {
  it("maps every OpenAPI Decision Context field without sending workspace-only proposals", () => {
    const request = toCreateRunRequest(initialDecisionDraft);
    expect(request).toMatchObject({
      question: initialDecisionDraft.question,
      current_stack: ["Python 3.11", "FastAPI"],
      use_cases: ["semantic retrieval", "metadata-scoped lookup"],
      hard_constraints: ["local persistence", "metadata equality filtering"],
      preferences: ["low operational overhead"],
      candidates: [{ name: "Chroma" }, { name: "Qdrant Local" }, { name: "pgvector" }],
    });
    expect(request).not.toHaveProperty("unknowns");
    expect(request).not.toHaveProperty("research_questions");
    expect(request).not.toHaveProperty("poc_checks");
  });

  it("rejects ambiguous overlap and out-of-contract candidate counts", () => {
    const issues = validateDecisionDraft({ ...initialDecisionDraft, preferences: ["LOCAL PERSISTENCE"], candidates: ["a", "b", "c", "d"] });
    expect(issues.map((issue) => issue.field)).toEqual(expect.arrayContaining(["preferences", "candidates"]));
  });
});

describe("Decision Workspace adapter", () => {
  it("persists the public workflow in order before research can start", async () => {
    const created = { ...techScoutRun, id: "00000000-0000-4000-8000-000000000110" };
    const requirementsReview = {
      run_id: created.id, state: "requirements_review", version: 2,
      decision_context: {} as DecisionContext, requirements: [], requirements_confirmed: false,
      selection_criteria: null, research_plan: null, created_at: created.created_at,
      updated_at: created.created_at,
    } as DecisionWorkflow;
    const criteriaConfirmation = {
      ...requirementsReview, state: "criteria_confirmation", version: 3,
      requirements_confirmed: true,
      selection_criteria: { contract_id: "criteria:public-workflow" },
    } as DecisionWorkflow;
    const researchReady = { ...criteriaConfirmation, state: "research_ready", version: 4 } as DecisionWorkflow;
    const api = {
      createRun: vi.fn().mockResolvedValue({ data: created }),
      reviewRequirements: vi.fn().mockResolvedValue({ data: requirementsReview }),
      confirmRequirements: vi.fn().mockResolvedValue({ data: criteriaConfirmation }),
      confirmCriteria: vi.fn().mockResolvedValue({ data: researchReady }),
    } as unknown as TechScoutApi;
    const adapter = createDecisionWorkspaceAdapter(api);

    const reviewed = await adapter.startReview(initialDecisionDraft);
    const criteria = await adapter.confirmRequirements(reviewed);
    const ready = await adapter.confirmCriteria(criteria);

    expect(api.createRun).toHaveBeenCalledOnce();
    expect(api.reviewRequirements).toHaveBeenCalledWith(created.id, expect.objectContaining({
      requirements: expect.arrayContaining([
        expect.objectContaining({ kind: "hard_constraint", statement: "local persistence" }),
        expect.objectContaining({ kind: "evaluation_criterion", statement: "low operational overhead" }),
        expect.objectContaining({ kind: "unknown", statement: "expected corpus size after twelve months" }),
      ]),
    }), expect.any(String));
    expect(api.confirmRequirements).toHaveBeenCalledWith(created.id, expect.any(String));
    expect(api.confirmCriteria).toHaveBeenCalledWith(created.id, "criteria:public-workflow", expect.any(String));
    expect(ready.state).toBe("research_ready");
  });

  it("loads the canonical Decision Context when the v2 endpoint is available", async () => {
    const context: DecisionContext = {
      question: "Choose a queue", project_summary: "A local worker service", current_stack: ["Python"], use_cases: ["jobs"],
      deployment: { python_version: "3.12", operating_system: "Linux", deployment: "one host" }, team_capabilities: [],
      performance_requirements: [], budget_constraints: [], security_requirements: [], license_requirements: [],
      must_haves: ["durable jobs"], preferences: ["simple operations"],
    };
    const api = { getDecisionContext: vi.fn().mockResolvedValue({ data: context }), getWorkflow: vi.fn().mockResolvedValue({ data: { state: "requirements_review" } }), getRun: vi.fn().mockResolvedValue({ data: techScoutRun }) } as unknown as TechScoutApi;
    const loaded = await createDecisionWorkspaceAdapter(api).load("run-1");
    expect(loaded.compatibility).toBe("native");
    expect(loaded.draft).toMatchObject({ question: "Choose a queue", mustHaves: ["durable jobs"], preferences: ["simple operations"], unknowns: [] });
  });

  it("falls back explicitly for an old run without a Decision Context projection", async () => {
    const api = { getDecisionContext: vi.fn().mockRejectedValue(new ApiError(404, "run_not_found", "missing")), getWorkflow: vi.fn().mockRejectedValue(new ApiError(404, "workflow_not_found", "missing")), getRun: vi.fn().mockResolvedValue({ data: techScoutRun }) } as unknown as TechScoutApi;
    const loaded = await createDecisionWorkspaceAdapter(api).load(TECHSCOUT_FIXTURE_ID);
    expect(loaded.compatibility).toBe("legacy_projection");
    expect(loaded.draft.mustHaves).toEqual(techScoutRun.hard_constraints);
    expect(loaded.draft.preferences).toEqual([]);
  });
});

describe("Decision Workspace interaction", () => {
  it("restores the persisted Requirements Review after a route refresh", async () => {
    vi.spyOn(techScoutApi, "listRuns").mockResolvedValue({ data: { items: [publicRun], next_cursor: null } });
    vi.spyOn(techScoutApi, "getDecisionContext").mockResolvedValue({ data: {
      question: initialDecisionDraft.question, project_summary: initialDecisionDraft.projectSummary,
      current_stack: initialDecisionDraft.currentStack, use_cases: initialDecisionDraft.useCases,
      deployment: { python_version: "3.11", operating_system: "Linux", deployment: "single-node local" },
      team_capabilities: [], performance_requirements: [], budget_constraints: [], security_requirements: [], license_requirements: [],
      must_haves: initialDecisionDraft.mustHaves, preferences: initialDecisionDraft.preferences,
    } });
    vi.spyOn(techScoutApi, "getWorkflow").mockResolvedValue({ data: savedRequirements });
    vi.spyOn(techScoutApi, "getRun").mockResolvedValue({ data: publicRun });

    render(<MemoryRouter initialEntries={[`/runs/${publicRun.id}/workflow`]}><Routes><Route path="/runs/:id/workflow" element={<DecisionWorkspacePage/>}/></Routes></MemoryRouter>);

    expect(await screen.findByRole("heading", { name: /review user requirements/i })).toBeInTheDocument();
    expect(screen.getByText("local persistence")).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/now saved/i);
  });

  it("supports the keyboard review shortcut and reports the fixture boundary", async () => {
    vi.spyOn(techScoutApi, "listRuns").mockResolvedValue({ data: { items: [], next_cursor: null } });
    vi.spyOn(techScoutApi, "createRun").mockResolvedValue({ data: publicRun });
    vi.spyOn(techScoutApi, "reviewRequirements").mockResolvedValue({ data: savedRequirements });
    render(<MemoryRouter><DecisionWorkspacePage/></MemoryRouter>);
    fireEvent.keyDown(screen.getByRole("textbox", { name: /decision question/i }), { key: "Enter", ctrlKey: true });
    expect(await screen.findByRole("heading", { name: /review user requirements/i })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/now saved/i);
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
  });

  it("keeps a confirmed review visible when launch fails", async () => {
    vi.spyOn(techScoutApi, "listRuns").mockResolvedValue({ data: { items: [], next_cursor: null } });
    vi.spyOn(techScoutApi, "createRun").mockResolvedValue({ data: publicRun });
    vi.spyOn(techScoutApi, "reviewRequirements").mockResolvedValue({ data: savedRequirements });
    vi.spyOn(techScoutApi, "confirmRequirements").mockRejectedValue(new ApiError(503, "queue_full", "The local queue is full."));
    render(<MemoryRouter><DecisionWorkspacePage/></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /review decision rules/i }));
    await screen.findByRole("heading", { name: /review user requirements/i });
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: /confirm requirements review/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The local queue is full.");
    expect(screen.getByRole("heading", { name: /review user requirements/i })).toBeInTheDocument();
  });

  it("labels the limited field recovery path for old runs", async () => {
    vi.spyOn(techScoutApi, "listRuns").mockResolvedValue({ data: { items: [techScoutRun], next_cursor: null } });
    vi.spyOn(techScoutApi, "getDecisionContext").mockRejectedValue(new ApiError(404, "run_not_found", "missing"));
    vi.spyOn(techScoutApi, "getRun").mockResolvedValue({ data: techScoutRun });
    render(<MemoryRouter><Routes><Route path="/" element={<DecisionWorkspacePage/>}/></Routes></MemoryRouter>);
    await userEvent.click(await screen.findByRole("button", { name: /use as starting point/i }));
    expect(await screen.findByRole("status")).toHaveTextContent(/Legacy run projection/i);
    expect(screen.getByRole("textbox", { name: /decision question/i })).toHaveValue(techScoutRun.question);
  });
});
