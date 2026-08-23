import type { DecisionWorkspaceDraft, RequirementKind, WorkspaceReview, WorkspaceReviewItem } from "./model";

function stableId(prefix: string, text: string, index: number): string {
  const slug = text.toLocaleLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "").slice(0, 32) || "item";
  return `${prefix}:${index + 1}:${slug}`;
}

function requirementItems(kind: RequirementKind, statements: string[]): WorkspaceReviewItem[] {
  return statements.map((statement, index) => {
    const requirementId = stableId(`requirement:${kind}`, statement, index);
    return { itemId: stableId(kind, statement, index), kind, statement, requirementIds: [requirementId], authority: "user" };
  });
}

/** Deterministic seam for the future criteria preview/confirmation endpoint. */
export function buildWorkspaceReviewFixture(draft: DecisionWorkspaceDraft): WorkspaceReview {
  const mustHaves = requirementItems("must_have", draft.mustHaves);
  const preferences = requirementItems("preference", draft.preferences);
  const unknowns = requirementItems("unknown", draft.unknowns);
  const researchQuestions: WorkspaceReviewItem[] = unknowns.map((item, index) => ({
    itemId: stableId("research-question", item.statement, index), kind: "research_question",
    statement: `What authoritative evidence can establish ${item.statement}?`, requirementIds: item.requirementIds, authority: "planner_fixture",
  }));
  const pocChecks: WorkspaceReviewItem[] = mustHaves.map((item, index) => ({
    itemId: stableId("poc-check", item.statement, index), kind: "poc_check",
    statement: `Run the allowlisted local check for: ${item.statement}.`, requirementIds: item.requirementIds, authority: "planner_fixture",
  }));
  return { reviewId: "fixture-review:v1", items: [...mustHaves, ...preferences, ...unknowns, ...researchQuestions, ...pocChecks], generatedAt: "2026-08-23T00:00:00Z", integration: "fixture_preview" };
}
