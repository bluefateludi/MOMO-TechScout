import type { ApiErrorBody, ApiResponse, DecisionContext, TechScoutApi, TechScoutCandidate, TechScoutCreateRunRequest, TechScoutEvidence, TechScoutReport, TechScoutRunDetail, TechScoutRunList, TechScoutRunSummary, TracePage } from "./contracts";
import { ApiError, messageForCode } from "./client";

async function request<T>(path: string, init?: RequestInit): Promise<ApiResponse<T>> {
  const response = await fetch(`/api/v2${path}`, { ...init, headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers } });
  if (!response.ok) {
    let body: ApiErrorBody | undefined;
    try { body = await response.json() as ApiErrorBody; } catch { /* safe fallback */ }
    const code = body?.error.code ?? "internal_error";
    throw new ApiError(response.status, code, messageForCode(code), body?.error.details);
  }
  const retry = Number(response.headers.get("Retry-After"));
  return { data: await response.json() as T, retryAfterSeconds: Number.isFinite(retry) && retry > 0 ? retry : undefined, location: response.headers.get("Location") ?? undefined };
}

export const techScoutHttpApi: TechScoutApi = {
  listRuns: () => request<TechScoutRunList>("/runs"),
  createRun: (body: TechScoutCreateRunRequest) => request<TechScoutRunSummary>("/runs", { method: "POST", body: JSON.stringify(body) }),
  getDecisionContext: (id) => request<DecisionContext>(`/runs/${encodeURIComponent(id)}/decision-context`),
  getRun: (id) => request<TechScoutRunDetail>(`/runs/${encodeURIComponent(id)}`),
  getReport: (id) => request<TechScoutReport>(`/runs/${encodeURIComponent(id)}/report`),
  getCandidate: (id, candidateId) => request<TechScoutCandidate>(`/runs/${encodeURIComponent(id)}/candidates/${encodeURIComponent(candidateId)}`),
  getEvidence: (id) => request<{ items: TechScoutEvidence[] }>(`/runs/${encodeURIComponent(id)}/evidence`),
  getEvidenceItem: (id, evidenceId) => request<TechScoutEvidence>(`/runs/${encodeURIComponent(id)}/evidence/${encodeURIComponent(evidenceId)}`),
  getTrace: (id, cursor, limit = 50) => request<TracePage>(`/runs/${encodeURIComponent(id)}/trace?limit=${limit}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`),
};
