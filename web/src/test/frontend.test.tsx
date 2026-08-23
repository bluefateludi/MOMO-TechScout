import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { techScoutApi } from "../api";
import { TECHSCOUT_FIXTURE_ID, fixtureTrace, syntheticNotice, techScoutEvidence, techScoutReport, techScoutRun } from "../api/techscoutFixtures";
import { CandidatePage } from "../routes/CandidatePage";
import { Layout } from "../components/Layout";
import { I18nProvider } from "../i18n";
import { EvidencePage } from "../routes/EvidencePage";
import { HomePage } from "../routes/HomePage";
import { ReportPage } from "../routes/ReportPage";
import { RunPage } from "../routes/RunPage";

beforeEach(() => {
  localStorage.clear();
  vi.spyOn(techScoutApi, "listRuns").mockResolvedValue({ data: { items: [techScoutRun], next_cursor: null } });
  vi.spyOn(techScoutApi, "getRun").mockResolvedValue({ data: techScoutRun });
  vi.spyOn(techScoutApi, "getReport").mockResolvedValue({ data: techScoutReport });
  vi.spyOn(techScoutApi, "getEvidence").mockResolvedValue({ data: { items: techScoutEvidence } });
  vi.spyOn(techScoutApi, "getEvidenceItem").mockResolvedValue({ data: techScoutEvidence[0] });
  vi.spyOn(techScoutApi, "getCandidate").mockResolvedValue({ data: techScoutRun.candidates[0] });
  vi.spyOn(techScoutApi, "getTrace").mockResolvedValue({ data: fixtureTrace });
});

describe("TechScout task input", () => {
  it("switches language accessibly and persists the choice", async () => {
    const view = render(<I18nProvider><MemoryRouter><Routes><Route element={<Layout/>}><Route path="/" element={<HomePage/>}/></Route></Routes></MemoryRouter></I18nProvider>);
    await userEvent.click(screen.getByRole("button", { name: "中文" }));
    expect(screen.getByRole("button", { name: "启动 TechScout 任务" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "中文" })).toHaveAttribute("aria-pressed", "true");
    expect(localStorage.getItem("momo-techscout-locale:v1")).toBe("zh-CN");
    view.unmount();
    render(<I18nProvider><MemoryRouter><HomePage/></MemoryRouter></I18nProvider>);
    expect(screen.getByRole("button", { name: "启动 TechScout 任务" })).toBeInTheDocument();
  });
  it("captures environment, hard constraints, candidates, and mode", async () => {
    render(<MemoryRouter><HomePage/></MemoryRouter>);
    expect(screen.getByRole("textbox", { name: /python version/i })).toHaveValue("3.11");
    expect(screen.getByRole("textbox", { name: /hard constraints/i })).toHaveValue("local persistence\nmetadata equality filtering");
    expect(screen.getByRole("textbox", { name: /candidate shortlist/i })).toHaveValue("Chroma, Qdrant Local, pgvector");
    expect(screen.getByRole("radio", { name: "Fast Demo" })).toBeChecked();
    expect(await screen.findByText(/Synthetic offline fixture/i)).toBeInTheDocument();
  });

  it("navigates after the synthetic mock accepts a task", async () => {
    vi.spyOn(techScoutApi, "createRun").mockResolvedValue({ data: techScoutRun });
    render(<MemoryRouter><Routes><Route path="/" element={<HomePage/>}/><Route path="/runs/:id" element={<LocationProbe/>}/></Routes></MemoryRouter>);
    await userEvent.type(screen.getByRole("textbox", { name: /decision question/i }), "Choose a safe local vector store");
    await userEvent.click(screen.getByRole("button", { name: /start techscout task/i }));
    expect(await screen.findByText(`/runs/${TECHSCOUT_FIXTURE_ID}`)).toBeInTheDocument();
  });
});

describe("fixture-backed TechScout views", () => {
  it("renders the four-stage progress, candidate, recovery, approval, and collapsed Trace", async () => {
    render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}`]}><Routes><Route path="/runs/:id" element={<RunPage/>}/></Routes></MemoryRouter>);
    expect(await screen.findByText(techScoutRun.question)).toBeInTheDocument();
    for (const stage of ["Plan", "Research", "Verify", "Decide"]) expect(screen.getByText(stage)).toBeInTheDocument();
    expect(screen.getByText(/not needed · 0\/1/i)).toBeInTheDocument();
    expect(screen.getByText(/not required/i)).toBeInTheDocument();
    expect(screen.queryByText(/Investigation plan frozen/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /trace feed/i }));
    expect(await screen.findByText(/Investigation plan frozen/i)).toBeInTheDocument();
  });

  it("shows truthful failed and limited terminal states", async () => {
    const failed = { ...techScoutRun, synthetic: false, status: "failed" as const, issues: [{ stage: "verify", code: "poc_timeout", retryable_by_new_run: true }] };
    vi.mocked(techScoutApi.getRun).mockResolvedValue({ data: failed });
    const failedView = render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}`]}><Routes><Route path="/runs/:id" element={<RunPage/>}/></Routes></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("no report was published");
    expect(screen.getByText(/verify \/ poc_timeout/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /open decision report/i })).not.toBeInTheDocument();
    failedView.unmount();

    const limited = { ...techScoutRun, status: "completed_with_limitations" as const, issues: [{ stage: "verify", code: "research_only_candidate", retryable_by_new_run: false }] };
    vi.mocked(techScoutApi.getRun).mockResolvedValue({ data: limited });
    render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}`]}><Routes><Route path="/runs/:id" element={<RunPage/>}/></Routes></MemoryRouter>);
    expect(await screen.findByText("Completed with limitations.")).toBeInTheDocument();
    expect(screen.getByText(/no trusted verification recipe/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open decision report/i })).toBeInTheDocument();
  });

  it("exposes the current stage to assistive technology", async () => {
    const running = { ...techScoutRun, status: "running" as const, progress: { ...techScoutRun.progress, stage: "research" as const, completed_stages: ["plan" as const] } };
    vi.mocked(techScoutApi.getRun).mockResolvedValue({ data: running });
    render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}`]}><Routes><Route path="/runs/:id" element={<RunPage/>}/></Routes></MemoryRouter>);
    const current = (await screen.findByText("Research")).closest("li");
    expect(current).toHaveAttribute("aria-current", "step");
    expect(current).toHaveTextContent("current");
  });

  it("keeps the synthetic warning on report, candidate, and evidence views", async () => {
    const routes = <Routes><Route path="/runs/:id/report" element={<ReportPage/>}/><Route path="/runs/:id/candidates/:candidateId" element={<CandidatePage/>}/><Route path="/runs/:id/evidence/:evidenceId" element={<EvidencePage/>}/></Routes>;
    const { unmount } = render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}/report`]}>{routes}</MemoryRouter>);
    expect(await screen.findByRole("note")).toHaveTextContent(syntheticNotice); expect(screen.getByText(/Allowlisted checks/i)).toBeInTheDocument(); unmount();
    const candidate = render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}/candidates/chroma`]}>{routes}</MemoryRouter>);
    expect(await screen.findByRole("note")).toHaveTextContent(syntheticNotice); candidate.unmount();
    render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}/evidence/ev-chroma-persistence`]}>{routes}</MemoryRouter>);
    expect(await screen.findByRole("note")).toHaveTextContent(syntheticNotice); expect(screen.getByText(/no external URL/i)).toBeInTheDocument();
  });

  it("does not label a live candidate as a synthetic fixture", async () => {
    vi.mocked(techScoutApi.getEvidence).mockResolvedValue({ data: { items: techScoutEvidence.map((item) => ({ ...item, acquisition_state: "live" as const })) } });
    render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}/candidates/chroma`]}><Routes><Route path="/runs/:id/candidates/:candidateId" element={<CandidatePage/>}/></Routes></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: techScoutRun.candidates[0].name })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("note")).not.toBeInTheDocument());
  });

  it("renders live, cached, PoC verified, research-only, and limited authority explicitly", async () => {
    vi.mocked(techScoutApi.getReport).mockResolvedValue({ data: {
      ...techScoutReport,
      synthetic: false,
      limitations: ["docker_unavailable"],
      poc_results: techScoutReport.poc_results.map((item, index) => ({
        ...item,
        synthetic: false,
        verified: index === 0,
        status: index === 2 ? "research_only" as const : item.status,
      })),
    } });
    const report = render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}/report`]}><Routes><Route path="/runs/:id/report" element={<ReportPage/>}/></Routes></MemoryRouter>);
    expect(await screen.findByText("PoC verified")).toBeInTheDocument();
    expect(screen.getByText(/research-only/)).toBeInTheDocument();
    expect(screen.getByText("docker_unavailable")).toBeInTheDocument();
    report.unmount();

    vi.mocked(techScoutApi.getEvidenceItem).mockResolvedValue({ data: {
      ...techScoutEvidence[0], acquisition_state: "cache",
    } });
    render(<MemoryRouter initialEntries={[`/runs/${TECHSCOUT_FIXTURE_ID}/evidence/${techScoutEvidence[0].evidence_id}`]}><Routes><Route path="/runs/:id/evidence/:evidenceId" element={<EvidencePage/>}/></Routes></MemoryRouter>);
    expect(await screen.findByRole("note")).toHaveTextContent("Cached evidence");
    expect(screen.getByText("Cached")).toBeInTheDocument();
  });
});

function LocationProbe() { return <span>{useLocation().pathname}</span>; }
