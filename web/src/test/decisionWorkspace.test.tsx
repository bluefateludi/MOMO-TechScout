import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { techScoutApi } from "../api";
import { ApiError } from "../api/client";
import type { DecisionContext, TechScoutApi } from "../api/contracts";
import { TECHSCOUT_FIXTURE_ID, techScoutRun } from "../api/techscoutFixtures";
import { createDecisionWorkspaceAdapter } from "../decision-workspace/adapter";
import { buildWorkspaceReviewFixture } from "../decision-workspace/fixtures";
import { initialDecisionDraft, toCreateRunRequest, validateDecisionDraft } from "../decision-workspace/model";
import { DecisionWorkspacePage } from "../routes/DecisionWorkspacePage";

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

  it("keeps the five review kinds separate and traceable", () => {
    const review = buildWorkspaceReviewFixture(initialDecisionDraft);
    expect(new Set(review.items.map((item) => item.kind))).toEqual(new Set(["must_have", "preference", "unknown", "research_question", "poc_check"]));
    expect(review.items.filter((item) => item.authority === "planner_fixture").every((item) => item.requirementIds.length > 0)).toBe(true);
    expect(review.items.filter((item) => item.kind === "must_have").every((item) => item.authority === "user")).toBe(true);
  });

  it("rejects ambiguous overlap and out-of-contract candidate counts", () => {
    const issues = validateDecisionDraft({ ...initialDecisionDraft, preferences: ["LOCAL PERSISTENCE"], candidates: ["a", "b", "c", "d"] });
    expect(issues.map((issue) => issue.field)).toEqual(expect.arrayContaining(["preferences", "candidates"]));
  });
});

describe("Decision Workspace adapter", () => {
  it("loads the canonical Decision Context when the v2 endpoint is available", async () => {
    const context: DecisionContext = {
      question: "Choose a queue", project_summary: "A local worker service", current_stack: ["Python"], use_cases: ["jobs"],
      deployment: { python_version: "3.12", operating_system: "Linux", deployment: "one host" }, team_capabilities: [],
      performance_requirements: [], budget_constraints: [], security_requirements: [], license_requirements: [],
      must_haves: ["durable jobs"], preferences: ["simple operations"],
    };
    const api = { getDecisionContext: vi.fn().mockResolvedValue({ data: context }) } as unknown as TechScoutApi;
    const loaded = await createDecisionWorkspaceAdapter(api).load("run-1");
    expect(loaded.compatibility).toBe("native");
    expect(loaded.draft).toMatchObject({ question: "Choose a queue", mustHaves: ["durable jobs"], preferences: ["simple operations"], unknowns: [] });
  });

  it("falls back explicitly for an old run without a Decision Context projection", async () => {
    const api = { getDecisionContext: vi.fn().mockRejectedValue(new ApiError(404, "run_not_found", "missing")), getRun: vi.fn().mockResolvedValue({ data: techScoutRun }) } as unknown as TechScoutApi;
    const loaded = await createDecisionWorkspaceAdapter(api).load(TECHSCOUT_FIXTURE_ID);
    expect(loaded.compatibility).toBe("legacy_projection");
    expect(loaded.draft.mustHaves).toEqual(techScoutRun.hard_constraints);
    expect(loaded.draft.preferences).toEqual([]);
  });
});

describe("Decision Workspace interaction", () => {
  it("supports the keyboard review shortcut and reports the fixture boundary", async () => {
    vi.spyOn(techScoutApi, "listRuns").mockResolvedValue({ data: { items: [], next_cursor: null } });
    render(<MemoryRouter><DecisionWorkspacePage/></MemoryRouter>);
    fireEvent.keyDown(screen.getByRole("textbox", { name: /decision question/i }), { key: "Enter", ctrlKey: true });
    expect(await screen.findByRole("heading", { name: /confirm the work/i })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent(/not yet persisted/i);
    expect(screen.getAllByRole("checkbox")).toHaveLength(5);
  });

  it("keeps a confirmed review visible when launch fails", async () => {
    vi.spyOn(techScoutApi, "listRuns").mockResolvedValue({ data: { items: [], next_cursor: null } });
    vi.spyOn(techScoutApi, "createRun").mockRejectedValue(new ApiError(503, "queue_full", "The local queue is full."));
    render(<MemoryRouter><DecisionWorkspacePage/></MemoryRouter>);
    await userEvent.click(screen.getByRole("button", { name: /review decision rules/i }));
    await screen.findByRole("heading", { name: /confirm the work/i });
    for (const checkbox of screen.getAllByRole("checkbox")) await userEvent.click(checkbox);
    await userEvent.click(screen.getByRole("button", { name: /start techscout task/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The local queue is full.");
    expect(screen.getByRole("heading", { name: /confirm the work/i })).toBeInTheDocument();
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
