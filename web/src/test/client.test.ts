import { afterEach, describe, expect, it, vi } from "vitest";
import { httpApi, messageForCode } from "../api/client";
import { DEMO_ID, EV1, demoRun } from "../api/fixtures";
import { techScoutHttpApi } from "../api/techscout";
import { TECHSCOUT_FIXTURE_ID, techScoutRun } from "../api/techscoutFixtures";

afterEach(() => vi.unstubAllGlobals());

describe("same-origin HTTP client", () => {
  it("uses /api/v1, preserves Location and respects Retry-After", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(demoRun), {
      status: 202,
      headers: { "Content-Type": "application/json", Location: `/api/v1/runs/${DEMO_ID}`, "Retry-After": "2" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const response = await httpApi.createRun(demoRun);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/runs", expect.objectContaining({ method: "POST" }));
    expect(response.location).toBe(`/api/v1/runs/${DEMO_ID}`);
    expect(response.retryAfterSeconds).toBe(2);
  });

  it("URL-encodes opaque Evidence IDs exactly once", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);
    const opaque = `${EV1}/part%?#`;
    await httpApi.getEvidenceItem(DEMO_ID, opaque);
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/runs/${DEMO_ID}/evidence/${encodeURIComponent(opaque)}`);
  });

  it.each([
    [404, "run_not_found"], [409, "artifact_not_ready"], [429, "run_busy"], [503, "queue_full"],
  ])("maps %s %s envelopes without exposing server text", async (status, code) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code, message: "raw server text", details: {} } }), {
      status, headers: { "Content-Type": "application/json" },
    })));
    await expect(httpApi.getRun(DEMO_ID)).rejects.toMatchObject({ status, code, message: messageForCode(code) });
  });
});

describe("TechScout v2 HTTP client", () => {
  it("uses /api/v2 and cursor-encodes the bounded Trace query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], next_cursor: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await techScoutHttpApi.getTrace(TECHSCOUT_FIXTURE_ID, "event cursor", 25);
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v2/runs/${TECHSCOUT_FIXTURE_ID}/trace?limit=25&cursor=event%20cursor`);
  });

  it("encodes candidate IDs without constructing filesystem paths", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(techScoutRun.candidates[0]), { status: 200, headers: { "Content-Type": "application/json" } })));
    await techScoutHttpApi.getCandidate(TECHSCOUT_FIXTURE_ID, "candidate/with?#opaque");
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain("candidate%2Fwith%3F%23opaque");
  });

  it("reads the canonical Decision Context projection from its v2 route", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } })));
    await techScoutHttpApi.getDecisionContext(TECHSCOUT_FIXTURE_ID);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(`/api/v2/runs/${TECHSCOUT_FIXTURE_ID}/decision-context`);
  });
});
