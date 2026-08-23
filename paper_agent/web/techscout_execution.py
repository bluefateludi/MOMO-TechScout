"""Deep composition boundary for queued TechScout Fast Demo execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Protocol

from pydantic import Field

from paper_agent.generation import GenerationMessage, GenerationProvider
from paper_agent.modeling import StrictModel
from paper_agent.techscout.errors import Failure, FailureCode, FailureStage, RecoveryAction
from paper_agent.techscout.harness import (
    SQLiteCheckpointAdapter,
    StageArtifacts,
    StageDeadline,
    StageDeadlineExceeded,
    StageResult,
    TechScoutHarness,
)
from paper_agent.techscout.models import (
    Candidate,
    CandidateEvidence,
    ConstraintResult,
    ConstraintStatus,
    DecisionReport,
    EnvironmentSpec,
    EvidenceKind,
    GateOutcome,
    PocArtifact,
    PocPlan,
    PocResult,
    PocStatus,
    ResearchPlan,
    ResearchRequest,
    RunManifest,
    RunMode,
    SourceChunk,
    SourceDocument,
    SourceType,
    TerminalStatus,
    ToolCall,
    ToolStatus,
    Verdict,
)
from paper_agent.techscout.observability.adapters import (
    TracingSkillRouter,
    TracingStageServices,
    TracingToolRuntime,
)
from paper_agent.techscout.observability.recorder import TechScoutTraceRecorder
from paper_agent.techscout.runtime_skills import fixed_skill_registry
from paper_agent.techscout.context import CandidateContextData, ContextEngine, ContextStage
from paper_agent.techscout.research import (
    AcquisitionState,
    LiveEvidenceResearchService,
    hero_case_policy,
)
from paper_agent.techscout.sandbox.recipes import RecipeRegistry
from paper_agent.techscout.sandbox.service import RealPocService
from paper_agent.techscout.sandbox.types import PocStage
from paper_agent.techscout.state import ResearchStage, ResearchState, RunBudget
from paper_agent.techscout.tools.contracts import SearchOutput, SmokeTestOutput
from paper_agent.techscout.tools.runtime import PolicyToolRuntime, StdioMcpRuntime
from paper_agent.techscout.validation import REQUIRED_TERMINAL_ARTIFACTS, ValidationGate, ValidationInput
from paper_agent.web.registry import RunRegistry, TechScoutRegistryRun, utc_now
from paper_agent.web.techscout_api_models import (
    TechScoutApprovalProjection,
    TechScoutCandidateProjection,
    TechScoutConstraintProjection,
    TechScoutCreateRunRequest,
    TechScoutEvidenceProjection,
    TechScoutIssueProjection,
    TechScoutPocProjection,
    TechScoutProgress,
    TechScoutRecoveryProjection,
    TechScoutReportProjection,
    TechScoutRunDetail,
)


_TERMINAL_FILES = frozenset(REQUIRED_TERMINAL_ARTIFACTS)
_STAGE_MAP = {
    ResearchStage.NORMALIZE_REQUEST: "plan",
    ResearchStage.PLAN_RESEARCH: "plan",
    ResearchStage.RESEARCH_CANDIDATES: "research",
    ResearchStage.SELECT_CONTEXT: "research",
    ResearchStage.PLAN_POC: "verify",
    ResearchStage.EXECUTE_POC: "verify",
    ResearchStage.VALIDATE: "verify",
    ResearchStage.REVIEW_REPORT: "decide",
    ResearchStage.PUBLISH: "decide",
    ResearchStage.TERMINAL: "terminal",
}
_COMPLETED_BY_STAGE = {
    "plan": [],
    "research": ["plan"],
    "verify": ["plan", "research"],
    "decide": ["plan", "research", "verify"],
    "terminal": ["plan", "research", "verify", "decide"],
}


class ModelDecisionDraft(StrictModel):
    """Narrow model contribution; deterministic code retains all authority."""

    preferred_candidate_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^candidate:[a-z0-9][a-z0-9._-]*$",
    )
    summary: str = Field(min_length=1, max_length=2_000)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:48] or "candidate"


def _recipe_for(name: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    if normalized in {"chroma", "chromadb"}:
        return "recipe:chroma-local@1"
    if normalized in {"qdrant", "qdrant local", "qdrant client"}:
        return "recipe:qdrant-local@1"
    return None


class TechScoutProjectionBundle(StrictModel):
    detail: TechScoutRunDetail
    report: TechScoutReportProjection | None
    evidence: list[TechScoutEvidenceProjection]


class _LocalMcpInvoker:
    """Synchronous seam over the official async stdio MCP client."""

    def __init__(self, scenario: str, trace: TechScoutTraceRecorder | None = None) -> None:
        self._scenario = scenario
        self._skills = fixed_skill_registry()
        self._trace = trace

    def invoke(self, call: ToolCall):
        async def execute():
            environment = demo_mcp_environment(self._scenario)
            async with StdioMcpRuntime(
                command=sys.executable,
                args=("-m", "paper_agent.techscout.tools.demo_mcp"),
                env=environment,
                timeout_seconds=10,
            ) as local:
                runtime = PolicyToolRuntime(
                    delegate=local,
                    skills=self._skills,
                    local_allowlist={"web.search", "sandbox.run_smoke_test"},
                )
                traced = TracingToolRuntime(runtime, self._trace) if self._trace else runtime
                return await traced.invoke(call)

        return asyncio.run(execute())


def demo_mcp_environment(scenario: str) -> dict[str, str]:
    allowed = ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment["TECHSCOUT_DEMO_SCENARIO"] = scenario
    return environment


class DeterministicStageServices:
    """Deterministic frozen stages behind the real Harness and local MCP seams."""

    synthetic = True
    fixture_name_prefix = "wave2"

    def __init__(
        self,
        *,
        run_dir: Path,
        scenario: str,
        progress_sink: Callable[[ResearchStage, str | None, str | None, str], None],
        trace_sink: Callable[[str, str, str, str], None] | None = None,
        trace: TechScoutTraceRecorder | None = None,
    ) -> None:
        self.run_dir = run_dir
        self.scenario = scenario
        self.progress_sink = progress_sink
        self.trace_sink = trace_sink or (lambda *args: None)
        skill_registry = fixed_skill_registry()
        self.skills = TracingSkillRouter(skill_registry, trace) if trace else skill_registry
        self.tools = _LocalMcpInvoker(scenario, trace)
        self.gate = ValidationGate()
        self.recipe_registry = RecipeRegistry()
        self.sources: list[SourceDocument] = []
        self.chunks: list[SourceChunk] = []
        self.evidence: list[CandidateEvidence] = []
        self.poc_plans: list[PocPlan] = []
        self.poc_results: list[PocResult] = []
        self.poc_history: list[PocResult] = []
        self._draft_report: DecisionReport | None = None
        self._draft_manifest: RunManifest | None = None
        self._load_workspace()

    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        if datetime.now(timezone.utc) >= deadline.deadline_at:
            raise StageDeadlineExceeded("verified run deadline reached")
        self.progress_sink(stage, None, None, f"Harness entered {stage.value}.")
        if stage is ResearchStage.PLAN_RESEARCH:
            result = self._plan(state)
        elif stage is ResearchStage.RESEARCH_CANDIDATES:
            result = self._research(state)
        elif stage is ResearchStage.PLAN_POC:
            result = self._plan_poc(state)
        elif stage is ResearchStage.EXECUTE_POC:
            result = self._execute_poc(state)
        elif stage is ResearchStage.VALIDATE:
            result = self._validate(state)
        elif stage is ResearchStage.REVIEW_REPORT:
            if self._draft_report is None:
                raise ValueError("validated report is missing")
            result = StageResult(
                state=state,
                artifacts=StageArtifacts(report=self._draft_report),
                tokens=80,
            )
        elif stage is ResearchStage.PUBLISH:
            if self._draft_manifest is None:
                raise ValueError("validated manifest is missing")
            result = StageResult(
                state=state,
                artifacts=StageArtifacts(manifest=self._draft_manifest),
                tokens=40,
            )
        else:
            result = StageResult(state=state, tokens=20)
        if datetime.now(timezone.utc) >= deadline.deadline_at:
            raise StageDeadlineExceeded("verified stage exceeded the run deadline")
        self._save_workspace()
        return result

    def _plan(self, state: ResearchState) -> StageResult:
        plan = ResearchPlan(
            plan_id=f"plan:{state.run_id.split(':', 1)[1]}",
            investigation_dimensions=("compatibility", "local deployment", "evidence provenance"),
            required_capabilities=("official-doc-research", "python-package-smoke-test"),
            planned_evidence=("frozen official documentation", "deterministic local PoC"),
            poc_intent="Run one allowlisted deterministic local compatibility smoke test.",
        )
        return StageResult(state=state.model_copy(update={"plan": plan}), tokens=80)

    def _research(self, state: ResearchState) -> StageResult:
        self.sources, self.chunks, self.evidence = [], [], []
        run_suffix = state.run_id.split(":", 1)[1]
        for candidate in state.request.candidates:
            slug = _slug(candidate.name)
            selection = self.skills.route(
                "official-doc-research", ResearchStage.RESEARCH_CANDIDATES,
                selection_id=f"selection:{run_suffix}:research-{slug}",
                reason="Fast Demo requires bounded frozen official evidence per candidate.",
            )
            self.progress_sink(
                ResearchStage.RESEARCH_CANDIDATES, selection.skill_id, None,
                f"Routed {slug} research through the official-doc skill.",
            )
            call = ToolCall(
                tool_call_id=f"tool-call:{run_suffix}:search-{slug}",
                tool_name="web.search", skill_id=selection.skill_id,
                arguments={"query": f"{candidate.name} local persistence metadata filtering", "candidate_id": candidate.candidate_id, "domains": ["docs.example.test"], "max_results": 1},
            )
            result = self.tools.invoke(call)
            self.progress_sink(
                ResearchStage.RESEARCH_CANDIDATES, selection.skill_id, call.tool_name,
                f"Local MCP returned frozen evidence for {slug}.",
            )
            if result.status is not ToolStatus.SUCCEEDED:
                failure = Failure(
                    failure_id=f"failure:{run_suffix}:research-{slug}",
                    code=result.error_code or FailureCode.TOOL_UNAVAILABLE,
                    stage=FailureStage.RESEARCH,
                    message="Frozen local MCP research was unavailable.", recoverable=False, attempt=1,
                )
                return StageResult(state=state.model_copy(update={"failures": (*state.failures, failure)}), tool_calls=len(self.sources) + 1)
            output = SearchOutput.model_validate_json(json.dumps(result.output))
            hit = output.results[0]
            source_id = f"source:{slug}-frozen-docs"
            chunk_id = f"chunk:{slug}-frozen-docs-0001"
            self.sources.append(SourceDocument(
                source_id=source_id,
                candidate_id=candidate.candidate_id,
                source_type=SourceType.OFFICIAL_DOCUMENTATION,
                url=hit.url,
                title=hit.title,
                as_of=output.provenance.retrieved_at,
                content_sha256=output.provenance.snapshot_sha256,
            ))
            self.chunks.append(SourceChunk(
                chunk_id=chunk_id,
                source_id=source_id,
                text=hit.snippet,
                ordinal=0,
                content_sha256=_sha(hit.snippet),
            ))
            self.evidence.extend(
                CandidateEvidence(
                    evidence_id=f"evidence:{slug}-{index:02d}", candidate_id=candidate.candidate_id,
                    constraint=constraint, claim=f"Frozen synthetic evidence addresses: {constraint}.",
                    source_ids=(source_id,), chunk_ids=(chunk_id,), kind=EvidenceKind.RETRIEVED_FACT,
                )
                for index, constraint in enumerate(state.request.hard_constraints, start=1)
            )
        return StageResult(
            state=state.model_copy(
                update={
                    "source_ids": tuple(item.source_id for item in self.sources),
                    "evidence_ids": tuple(item.evidence_id for item in self.evidence),
                }
            ),
            tool_calls=len(state.request.candidates), tokens=160 * len(state.request.candidates),
        )

    def _plan_poc(self, state: ResearchState) -> StageResult:
        self.poc_plans = [
            PocPlan(
                poc_plan_id=f"poc-plan:{_slug(candidate.name)}",
                candidate_id=candidate.candidate_id,
                recipe_id=_recipe_for(candidate.name),
                trusted=_recipe_for(candidate.name) is not None,
                checks=("import", "persistence", "query", "filter"),
            )
            for candidate in state.request.candidates
        ]
        return StageResult(state=state, tokens=60)

    def _execute_poc(self, state: ResearchState) -> StageResult:
        completed: list[PocResult] = []
        tool_calls = 0
        recovering = self.scenario == "single_recovery" and any(
            item.status is PocStatus.FAILED for item in self.poc_history
        )
        if recovering:
            checkpoint = state.checkpoint.checkpoint_id if state.checkpoint else "checkpoint:unavailable"
            self.trace_sink(
                "recovery", "verify", "running",
                f"attempt=1 action=pin_version_and_rerun_poc checkpoint={checkpoint}",
            )
        for plan in self.poc_plans:
            if not plan.trusted or plan.recipe_id is None:
                poc = PocResult(
                    poc_result_id=f"poc-result:{_slug(plan.candidate_id)}-research-only",
                    poc_plan_id=plan.poc_plan_id, candidate_id=plan.candidate_id,
                    status=PocStatus.RESEARCH_ONLY, timed_out=False, duration_ms=0,
                    failure_code=FailureCode.POC_RECIPE_UNSUPPORTED,
                )
                completed.append(poc)
                if not any(item.poc_result_id == poc.poc_result_id for item in self.poc_history):
                    self.poc_history.append(poc)
                continue
            selection = self.skills.route(
                "python-package-smoke-test", ResearchStage.EXECUTE_POC,
                selection_id=f"selection:{state.run_id.split(':', 1)[1]}:poc-{_slug(plan.candidate_id)}-{state.recovery_count}",
                reason="Fast Demo uses an allowlisted deterministic local PoC.",
            )
            self.progress_sink(ResearchStage.EXECUTE_POC, selection.skill_id, None, f"Routed {_slug(plan.candidate_id)} PoC through the reviewed skill.")
            inject_conflict = self.scenario == "single_recovery" and not self.poc_history
            call = ToolCall(
                tool_call_id=f"tool-call:{state.run_id.split(':', 1)[1]}:poc-{_slug(plan.candidate_id)}-{state.recovery_count}",
                tool_name="sandbox.run_smoke_test", skill_id=selection.skill_id,
                arguments={"candidate_id": plan.candidate_id, "recipe_id": plan.recipe_id, "checks": list(plan.checks), "requested_version": "demo-conflict" if inject_conflict else None},
            )
            tool_result = self.tools.invoke(call)
            tool_calls += 1
            self.progress_sink(ResearchStage.EXECUTE_POC, selection.skill_id, call.tool_name, f"Local MCP completed {_slug(plan.candidate_id)} PoC.")
            if tool_result.status is not ToolStatus.SUCCEEDED:
                raise ValueError("local MCP returned an invalid PoC response")
            output = SmokeTestOutput.model_validate_json(json.dumps(tool_result.output))
            result_id = f"poc-result:{_slug(plan.candidate_id)}-{state.recovery_count + 1}"
            if output.status == "failed":
                poc = PocResult(
                    poc_result_id=result_id, poc_plan_id=plan.poc_plan_id, candidate_id=plan.candidate_id,
                    status=PocStatus.FAILED, exit_code=output.exit_code, timed_out=False,
                    duration_ms=output.duration_ms, failure_code=FailureCode.DEPENDENCY_CONFLICT,
                )
                self.poc_results = [poc]
                self.poc_history.append(poc)
                checkpoint = state.checkpoint.checkpoint_id if state.checkpoint else "checkpoint:unavailable"
                self.trace_sink(
                    "stage", "verify", "failed",
                    f"dependency_conflict attempt=1 checkpoint={checkpoint}",
                )
                failure = Failure(
                    failure_id=f"failure:{state.run_id.split(':', 1)[1]}:poc-conflict",
                    code=FailureCode.DEPENDENCY_CONFLICT, stage=FailureStage.POC_EXECUTION,
                    message="The deterministic PoC found a dependency conflict.", recoverable=True,
                    recovery_action=RecoveryAction.PIN_VERSION_AND_RERUN_POC, attempt=1,
                )
                return StageResult(state=state.model_copy(update={"failures": (*state.failures, failure)}), tool_calls=tool_calls, tokens=100)
            artifact = PocArtifact(
                artifact_id=f"artifact:{_slug(plan.candidate_id)}-integrity", kind="deterministic-local-poc",
                sha256=output.artifact_sha256 or _sha(result_id), size_bytes=64,
            )
            poc = PocResult(
                poc_result_id=result_id, poc_plan_id=plan.poc_plan_id, candidate_id=plan.candidate_id,
                status=PocStatus.PASSED, resolved_version=output.resolved_version, exit_code=0,
                timed_out=False, duration_ms=output.duration_ms, artifacts=(artifact,),
            )
            completed.append(poc)
            self.poc_history.append(poc)
        self.poc_results = completed
        if recovering:
            self.trace_sink(
                "recovery", "verify", "completed",
                "attempt=1 outcome=recovered repeated_stage=execute_poc",
            )
        return StageResult(
            state=state.model_copy(update={"poc_result_ids": tuple(item.poc_result_id for item in completed)}),
            tool_calls=tool_calls, tokens=100 * max(1, tool_calls),
        )

    def _validate(self, state: ResearchState) -> StageResult:
        passed_ids = {
            item.candidate_id
            for item in self.poc_results
            if item.status is PocStatus.PASSED
        }
        candidate = next(
            (item for item in state.request.candidates if item.candidate_id in passed_ids),
            state.request.candidates[0],
        )
        evidence_by_candidate_constraint = {
            (item.candidate_id, item.constraint): item.evidence_id
            for item in self.evidence
        }
        explicit_limited = self.scenario in {
            "cached_degradation", "verified_limited", "research_only",
            "docker_unavailable", "research_unavailable",
        }
        default_summary = (
            "No safe winner is claimed because the frozen provider cache was used."
            if self.scenario == "cached_degradation"
            else "Verified mode is limited because live providers and a real Docker PoC are not connected."
            if self.scenario == "verified_limited"
            else "No safe winner is claimed because the candidate has no reviewed local PoC recipe."
            if self.scenario == "research_only"
            else "No safe winner is claimed because the reviewed Docker PoC is unavailable."
            if self.scenario == "docker_unavailable"
            else "No safe winner is claimed because neither live nor cached evidence is available."
            if self.scenario == "research_unavailable"
            else "No safe winner is claimed because the reviewed PoC failed after bounded recovery."
            if self.scenario == "verification_failed"
            else "Live evidence and reviewed Docker PoCs satisfy the Hero Case gates; the first equally qualified item in the user-provided shortlist is the deterministic tie-break."
            if self.scenario == "verified" and passed_ids
            else "All supported candidates passed frozen evidence and deterministic local PoC validation; the first equally qualified item in the user-provided shortlist is the deterministic tie-break."
            if passed_ids
            else "The first deterministic PoC attempt requires one bounded recovery."
        )
        candidate, summary, model_tokens = self._decision_report_contribution(
            state, passed_ids=passed_ids, default_candidate=candidate,
            default_summary=default_summary,
        )
        passed = candidate.candidate_id in passed_ids
        limited = explicit_limited or not passed
        report = DecisionReport(
            report_id=f"report:{state.run_id.split(':', 1)[1]}",
            run_id=state.run_id,
            recommendation=None if limited else candidate.candidate_id,
            verdict=Verdict.INSUFFICIENT_EVIDENCE if limited else Verdict.RECOMMENDED,
            summary=summary,
            constraint_results=tuple(
                ConstraintResult(
                    candidate_id=item.candidate_id,
                    constraint=constraint,
                    status=(
                        ConstraintStatus.UNKNOWN
                        if limited or _recipe_for(item.name) is None
                        else ConstraintStatus.SATISFIED
                    ),
                    evidence_ids=(
                        ()
                        if limited or _recipe_for(item.name) is None
                        else (evidence_by_candidate_constraint[(item.candidate_id, constraint)],)
                    ),
                    reason=(
                        "Frozen cache fallback limits the decision."
                        if self.scenario == "cached_degradation"
                        else "Live provider and Docker verification are unavailable."
                        if self.scenario == "verified_limited"
                        else "No reviewed local PoC recipe is available."
                        if self.scenario == "research_only"
                        else "The reviewed Docker PoC is unavailable."
                        if self.scenario == "docker_unavailable"
                        else "Live and cached evidence are unavailable."
                        if self.scenario == "research_unavailable"
                        else "The reviewed PoC failed after bounded recovery."
                        if self.scenario == "verification_failed"
                        else "The PoC requires bounded recovery."
                        if limited
                        else None
                    ),
                )
                for item in state.request.candidates
                for constraint in state.request.hard_constraints
            ),
            limitations=(
                ("cached_provider_degradation",)
                if self.scenario == "cached_degradation"
                else ("live_execution_unavailable",)
                if self.scenario == "verified_limited"
                else ("research_only_candidate",)
                if self.scenario == "research_only"
                else ("docker_unavailable",)
                if self.scenario == "docker_unavailable"
                else ("live_evidence_unavailable",)
                if self.scenario == "research_unavailable"
                else ("verification_failed",)
                if self.scenario == "verification_failed"
                else ()
            ),
        )
        terminal = TerminalStatus.COMPLETED_WITH_LIMITATIONS if limited else TerminalStatus.COMPLETED
        limitation_codes = report.limitations or (("poc_recovery_pending",) if not passed else ())
        manifest = RunManifest(
            run_id=state.run_id,
            terminal_status=terminal,
            report_id=report.report_id,
            artifact_ids=(report.report_id,),
            limitation_codes=limitation_codes,
        )
        validation = self.gate.evaluate(
            ValidationInput(
                gate_id=f"gate:{state.run_id.split(':', 1)[1]}-{state.recovery_count}",
                request=state.request.model_copy(update={
                    "candidates": tuple(
                        item.model_copy(update={
                            "resolved_version": next(
                                (poc.resolved_version for poc in self.poc_results if poc.candidate_id == item.candidate_id and poc.resolved_version is not None),
                                item.resolved_version,
                            )
                        })
                        for item in state.request.candidates
                    )
                }),
                report=report,
                sources=tuple(self.sources),
                chunks=tuple(self.chunks),
                evidence=tuple(self.evidence),
                poc_plans=tuple(self.poc_plans),
                poc_results=tuple(self.poc_results),
                manifest=manifest,
                trusted_recipe_ids=self.recipe_registry.trusted_recipe_ids,
                verified_poc_artifact_ids=frozenset(
                    artifact.artifact_id
                    for result in self.poc_results
                    for artifact in result.artifacts
                ),
                terminal_artifact_names=_TERMINAL_FILES,
                trace_complete=True,
                recovery_count=state.recovery_count,
            )
        )
        outcome = validation.decision.outcome
        failures = (*state.failures, *validation.failures)
        if self.scenario == "verification_failed" and (
            state.recovery_count >= 1
            or not any(failure.recoverable for failure in failures)
        ):
            outcome = GateOutcome.FAILED
        if explicit_limited and outcome is GateOutcome.PASSED:
            outcome = GateOutcome.LIMITED
            limitation = Failure(
                failure_id=f"failure:{state.run_id.split(':', 1)[1]}:limited",
                code=(
                    FailureCode.POC_RECIPE_UNSUPPORTED
                    if self.scenario == "research_only"
                    else FailureCode.TOOL_UNAVAILABLE
                ),
                stage=(
                    FailureStage.POC_PLANNING
                    if self.scenario == "research_only"
                    else FailureStage.POC_EXECUTION
                ),
                message="The requested live verification boundary is unavailable.",
                recoverable=False,
                recovery_action=RecoveryAction.PUBLISH_LIMITED_RESULT,
                attempt=1,
            )
            failures = (*failures, limitation)
        if outcome not in {GateOutcome.RECOVER, GateOutcome.FAILED}:
            self._draft_report = report
            self._draft_manifest = manifest
        return StageResult(
            state=state.model_copy(update={"gate_outcome": outcome, "failures": failures}),
            tokens=120 + model_tokens,
        )

    def _decision_report_contribution(
        self,
        state: ResearchState,
        *,
        passed_ids: set[str],
        default_candidate: Candidate,
        default_summary: str,
    ) -> tuple[Candidate, str, int]:
        del state, passed_ids
        return default_candidate, default_summary, 0

    def _workspace_path(self) -> Path:
        return self.run_dir / "stage-workspace.json"

    def _save_workspace(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "sources": [item.model_dump(mode="json") for item in self.sources],
            "chunks": [item.model_dump(mode="json") for item in self.chunks],
            "evidence": [item.model_dump(mode="json") for item in self.evidence],
            "poc_plans": [item.model_dump(mode="json") for item in self.poc_plans],
            "poc_results": [item.model_dump(mode="json") for item in self.poc_results],
            "poc_history": [item.model_dump(mode="json") for item in self.poc_history],
            "draft_report": self._draft_report.model_dump(mode="json") if self._draft_report else None,
            "draft_manifest": self._draft_manifest.model_dump(mode="json") if self._draft_manifest else None,
            "acquisition_states": {
                key: value.value
                for key, value in getattr(self, "acquisition_states", {}).items()
            },
            "source_acquisition_states": {
                key: value.value
                for key, value in getattr(self, "source_acquisition_states", {}).items()
            },
            "failed_poc_stage": {
                key: value.value
                for key, value in getattr(self, "_failed_poc_stage", {}).items()
            },
        }
        path = self._workspace_path()
        temporary = path.with_suffix(".tmp")
        backup = path.with_suffix(".backup")
        if path.is_file():
            shutil.copyfile(path, backup)
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _load_workspace(self) -> None:
        path = self._workspace_path()
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = path.with_suffix(".backup")
            if not backup.is_file():
                raise
            payload = json.loads(backup.read_text(encoding="utf-8"))
        self.sources = [SourceDocument.model_validate(item) for item in payload["sources"]]
        self.chunks = [SourceChunk.model_validate(item) for item in payload["chunks"]]
        self.evidence = [CandidateEvidence.model_validate(item) for item in payload["evidence"]]
        self.poc_plans = [PocPlan.model_validate(item) for item in payload["poc_plans"]]
        self.poc_results = [PocResult.model_validate(item) for item in payload["poc_results"]]
        self.poc_history = [
            PocResult.model_validate(item)
            for item in payload.get("poc_history", payload["poc_results"])
        ]
        if payload["draft_report"]:
            self._draft_report = DecisionReport.model_validate(payload["draft_report"])
        if payload["draft_manifest"]:
            self._draft_manifest = RunManifest.model_validate(payload["draft_manifest"])
        if hasattr(self, "acquisition_states"):
            self.acquisition_states = {
                key: AcquisitionState(value)
                for key, value in payload.get("acquisition_states", {}).items()
            }
            self._failed_poc_stage = {
                key: PocStage(value)
                for key, value in payload.get("failed_poc_stage", {}).items()
            }
            self.source_acquisition_states = {
                key: AcquisitionState(value)
                for key, value in payload.get("source_acquisition_states", {}).items()
            }


class VerifiedStageServices:
    """Verified Hero Case stages composed from the existing live and Docker services."""

    synthetic = False
    fixture_name_prefix = "verified"

    def __init__(
        self,
        *,
        research_service: LiveEvidenceResearchService,
        context_engine: ContextEngine,
        poc_service: RealPocService,
        generation_provider: GenerationProvider | None = None,
        generation_timeout_seconds: float = 60.0,
        **kwargs,
    ) -> None:
        self.acquisition_states: dict[str, AcquisitionState] = {}
        self.source_acquisition_states: dict[str, AcquisitionState] = {}
        self._failed_poc_stage: dict[str, PocStage] = {}
        self.run_dir = kwargs["run_dir"]
        self.progress_sink = kwargs["progress_sink"]
        self.trace_sink = kwargs.get("trace_sink") or (lambda *args: None)
        self.gate = ValidationGate()
        self.recipe_registry = RecipeRegistry()
        self.sources: list[SourceDocument] = []
        self.chunks: list[SourceChunk] = []
        self.evidence: list[CandidateEvidence] = []
        self.poc_plans: list[PocPlan] = []
        self.poc_results: list[PocResult] = []
        self.poc_history: list[PocResult] = []
        self._draft_report: DecisionReport | None = None
        self._draft_manifest: RunManifest | None = None
        self.scenario = "verified"
        self._active_deadline: StageDeadline | None = None
        self._research_service = research_service
        self._context_engine = context_engine
        self._poc_service = poc_service
        self._generation_provider = generation_provider
        self._generation_timeout_seconds = generation_timeout_seconds
        self.model_prompt_tokens: int | None = None
        self.model_completion_tokens: int | None = None
        self.model_total_tokens: int | None = None
        self.model_revision: str | None = None
        DeterministicStageServices._load_workspace(self)

    def _workspace_path(self) -> Path:
        return self.run_dir / "stage-workspace.json"

    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        self._active_deadline = deadline
        if datetime.now(timezone.utc) >= deadline.deadline_at:
            raise StageDeadlineExceeded("verified run deadline reached")
        self.progress_sink(stage, None, None, f"Harness entered {stage.value}.")
        if stage is ResearchStage.PLAN_RESEARCH:
            result = self._plan(state)
        elif stage is ResearchStage.RESEARCH_CANDIDATES:
            result = self._research(state)
        elif stage is ResearchStage.PLAN_POC:
            result = self._plan_poc(state)
        elif stage is ResearchStage.EXECUTE_POC:
            result = self._execute_poc(state)
        elif stage is ResearchStage.VALIDATE:
            result = self._validate(state)
        elif stage is ResearchStage.REVIEW_REPORT:
            if self._draft_report is None:
                raise ValueError("validated report is missing")
            result = StageResult(state=state, artifacts=StageArtifacts(report=self._draft_report))
        elif stage is ResearchStage.PUBLISH:
            if self._draft_manifest is None:
                raise ValueError("validated manifest is missing")
            result = StageResult(state=state, artifacts=StageArtifacts(manifest=self._draft_manifest))
        else:
            result = StageResult(state=state)
        if datetime.now(timezone.utc) >= deadline.deadline_at:
            raise StageDeadlineExceeded("verified stage exceeded the run deadline")
        DeterministicStageServices._save_workspace(self)
        return result

    def _research(self, state: ResearchState) -> StageResult:
        self.sources, self.chunks, self.evidence = [], [], []
        as_of = datetime.now(timezone.utc)
        for candidate in state.request.candidates:
            self._require_remaining_seconds(26)
            try:
                policy = hero_case_policy(candidate)
            except ValueError:
                policy = None
            if policy is None:
                self.acquisition_states[candidate.candidate_id] = AcquisitionState.UNAVAILABLE
                continue
            delivery = self._research_service.research(
                request=state.request,
                policy=policy,
                stage=ContextStage.RESEARCH,
                as_of=as_of,
            )
            self.acquisition_states[candidate.candidate_id] = delivery.research.state
            self.sources.extend(delivery.research.documents)
            selected_chunks = tuple(delivery.context.chunks)
            self.chunks.extend(selected_chunks)
            attempt_by_reference = {
                item.reference.rstrip("/"): item.state
                for item in delivery.research.attempts
                if item.available
            }
            for source in delivery.research.documents:
                self.source_acquisition_states[source.source_id] = attempt_by_reference.get(
                    source.url.rstrip("/"), delivery.research.state
                )
            if delivery.context.chunks:
                selected = delivery.context.chunks[0]
                source = next(
                    item for item in delivery.context.sources
                    if item.source_id == selected.source_id
                )
                self.evidence.extend(
                    CandidateEvidence(
                        evidence_id=f"evidence:{_slug(candidate.name)}:{index:02d}",
                        candidate_id=candidate.candidate_id,
                        constraint=constraint,
                        claim=selected.text,
                        source_ids=(source.source_id,),
                        chunk_ids=(selected.chunk_id,),
                        kind=EvidenceKind.RETRIEVED_FACT,
                    )
                    for index, constraint in enumerate(
                        state.request.hard_constraints, start=1
                    )
                )
            self.trace_sink(
                "tool",
                "research",
                delivery.research.state.value,
                " ".join(
                    (
                        f"source_hash={source.content_sha256} cache_state={self.source_acquisition_states[source.source_id].value}"
                        for source in delivery.research.documents
                    )
                ) or f"cache_state={delivery.research.state.value}",
            )
        return StageResult(
            state=state.model_copy(update={
                "source_ids": tuple(item.source_id for item in self.sources),
                "evidence_ids": tuple(item.evidence_id for item in self.evidence),
            }),
            tool_calls=len(state.request.candidates),
        )

    def _plan(self, state: ResearchState) -> StageResult:
        plan = ResearchPlan(
            plan_id=f"plan:{state.run_id.split(':', 1)[1]}",
            investigation_dimensions=("Python 3.11 compatibility", "local RAG behavior", "source authority"),
            required_capabilities=("official-doc-research", "github-project-analysis", "python-package-smoke-test"),
            planned_evidence=("bounded live or cached official/GitHub evidence", "reviewed Docker PoC artifact"),
            poc_intent="Run the closed Chroma/Qdrant Local Hero Case recipes; keep unsupported candidates research-only.",
        )
        return StageResult(state=state.model_copy(update={"plan": plan}), tokens=80)

    def _plan_poc(self, state: ResearchState) -> StageResult:
        for candidate in state.request.candidates:
            context = self._candidate_context(candidate.candidate_id)
            if context.documents:
                self._context_engine.build(
                    packet_id=f"context:{state.run_id}:poc:{_slug(candidate.name)}",
                    stage=ContextStage.POC_PLANNING,
                    request=state.request,
                    candidate_context=context,
                    as_of=datetime.now(timezone.utc),
                    candidate_version=candidate.resolved_version or candidate.requested_version,
                    trusted_recipe_schema={"recipe_id": _recipe_for(candidate.name), "closed_registry": True},
                )
        hero_environment = (
            state.request.environment.python_version == "3.11"
            and "local" in state.request.environment.deployment.casefold()
        )
        self.poc_plans = [
            PocPlan(
                poc_plan_id=f"poc-plan:{_slug(candidate.name)}:verified",
                candidate_id=candidate.candidate_id,
                recipe_id=_recipe_for(candidate.name),
                trusted=hero_environment and _recipe_for(candidate.name) is not None,
                checks=("install", "import", "create", "upsert", "query", "filter", "persistence"),
            )
            for candidate in state.request.candidates
        ]
        return StageResult(state=state)

    def _execute_poc(self, state: ResearchState) -> StageResult:
        if self._failed_poc_stage:
            return self._recover_poc(state)
        results: list[PocResult] = []
        failures = list(state.failures)
        recoverable_failure: Failure | None = None
        by_id = {item.candidate_id: item for item in state.request.candidates}
        for plan in self.poc_plans:
            # A full recipe can block for two independently bounded Docker stages.
            self._require_remaining_seconds(90)
            result = self._poc_service.execute(
                plan,
                by_id[plan.candidate_id],
                run_workspace=self.run_dir,
            )
            results.append(result)
            self.poc_history.append(result)
            self.trace_sink(
                "tool", "verify", result.status.value,
                f"recipe_id={plan.recipe_id or 'none'} bounded_log_artifact={bool(result.artifacts)}",
            )
            if result.status in {PocStatus.FAILED, PocStatus.TIMED_OUT}:
                stage = self._failed_stage(result)
                can_recover = (
                    recoverable_failure is None
                    and result.failure_code not in {FailureCode.TOOL_UNAVAILABLE, FailureCode.POC_RECIPE_UNSUPPORTED}
                )
                failure = Failure(
                    failure_id=f"failure:{state.run_id.split(':', 1)[1]}:{_slug(result.candidate_id)}",
                    code=result.failure_code or FailureCode.POC_NONZERO_EXIT,
                    stage=FailureStage.POC_EXECUTION,
                    message="The reviewed Docker PoC stage failed.",
                    recoverable=can_recover,
                    recovery_action=(
                        RecoveryAction.DIAGNOSE_AND_RERUN_POC
                        if can_recover
                        else RecoveryAction.PUBLISH_LIMITED_RESULT
                    ),
                    attempt=1,
                )
                if can_recover:
                    self._failed_poc_stage[result.candidate_id] = stage
                    recoverable_failure = failure
                else:
                    failures.append(failure)
        if recoverable_failure is not None:
            failures.append(recoverable_failure)
        self.poc_results = results
        return StageResult(
            state=state.model_copy(update={
                "poc_result_ids": tuple(item.poc_result_id for item in results),
                "failures": tuple(failures),
            }),
            tool_calls=sum(item.status is not PocStatus.RESEARCH_ONLY for item in results),
        )

    def _recover_poc(self, state: ResearchState) -> StageResult:
        by_id = {item.candidate_id: item for item in state.request.candidates}
        plan_by_id = {item.candidate_id: item for item in self.poc_plans}
        recovered: list[PocResult] = []
        for result in self.poc_results:
            stage = self._failed_poc_stage.get(result.candidate_id)
            if stage is None:
                recovered.append(result)
                continue
            self._require_remaining_seconds(45)
            attempt = self._poc_service.rerun_stage(
                plan_by_id[result.candidate_id],
                by_id[result.candidate_id],
                run_workspace=self.run_dir,
                stage=stage,
            )
            artifacts = result.artifacts + ((attempt.artifact,) if attempt.artifact else ())
            complete_status = (
                attempt.status
                if stage is PocStage.TEST
                else PocStatus.FAILED
            )
            merged = result.model_copy(update={
                "status": complete_status,
                "exit_code": attempt.exit_code,
                "timed_out": attempt.timed_out,
                "duration_ms": result.duration_ms + attempt.duration_ms,
                "artifacts": artifacts,
                "failure_code": (
                    attempt.failure_code
                    if complete_status is not PocStatus.FAILED or attempt.failure_code is not None
                    else FailureCode.POC_NONZERO_EXIT
                ),
            })
            recovered.append(merged)
            self.poc_history.append(merged)
            checkpoint = state.checkpoint.checkpoint_id if state.checkpoint else "checkpoint:unavailable"
            self.trace_sink(
                "recovery", "verify", attempt.status.value,
                f"checkpoint={checkpoint} repeated_stage={stage.value} recipe_id={plan_by_id[result.candidate_id].recipe_id}",
            )
        self.poc_results = recovered
        return StageResult(
            state=state.model_copy(update={
                "poc_result_ids": tuple(item.poc_result_id for item in recovered),
            }),
            tool_calls=1,
        )

    def _failed_stage(self, result: PocResult) -> PocStage:
        if not result.artifacts:
            return PocStage.INSTALL
        path = self.run_dir / "poc-artifacts" / f"{result.artifacts[0].sha256}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return PocStage(payload["stages"][-1]["stage"])
        except (OSError, KeyError, IndexError, ValueError, json.JSONDecodeError):
            return PocStage.TEST

    def _validate(self, state: ResearchState) -> StageResult:
        poc_by_candidate = {item.candidate_id: item for item in self.poc_results}
        for candidate in state.request.candidates:
            context = self._candidate_context(candidate.candidate_id)
            if not context.documents:
                continue
            for stage in (ContextStage.VALIDATION, ContextStage.REPORTING):
                self._context_engine.build(
                    packet_id=f"context:{state.run_id}:{stage.value}:{_slug(candidate.name)}",
                    stage=stage,
                    request=state.request,
                    candidate_context=context,
                    as_of=datetime.now(timezone.utc),
                    candidate_version=candidate.resolved_version or candidate.requested_version,
                    poc_result=poc_by_candidate.get(candidate.candidate_id),
                    gate_rules=("cover every hard constraint",) if stage is ContextStage.VALIDATION else (),
                )
        states = set(self.acquisition_states.values())
        verification_failed = any(
            item.status in {PocStatus.FAILED, PocStatus.TIMED_OUT}
            and item.failure_code is not FailureCode.TOOL_UNAVAILABLE
            for item in self.poc_results
        )
        if verification_failed:
            self.scenario = "verification_failed"
        elif AcquisitionState.UNAVAILABLE in states:
            self.scenario = "research_unavailable"
        elif AcquisitionState.CACHE in states:
            self.scenario = "cached_degradation"
        elif any(item.status in {PocStatus.FAILED, PocStatus.TIMED_OUT} for item in self.poc_results):
            self.scenario = (
                "docker_unavailable"
                if any(item.failure_code is FailureCode.TOOL_UNAVAILABLE for item in self.poc_results)
                else "verification_failed"
            )
        elif not any(item.status is PocStatus.PASSED for item in self.poc_results):
            self.scenario = (
                "research_only"
                if self.poc_results and all(item.status is PocStatus.RESEARCH_ONLY for item in self.poc_results)
                else "docker_unavailable"
            )
        else:
            self.scenario = "verified"
        return DeterministicStageServices._validate(self, state)

    def _decision_report_contribution(
        self,
        state: ResearchState,
        *,
        passed_ids: set[str],
        default_candidate: Candidate,
        default_summary: str,
    ) -> tuple[Candidate, str, int]:
        if self._generation_provider is None:
            return default_candidate, default_summary, 0
        bounded_facts = {
            "question": state.request.question,
            "hard_constraints": list(state.request.hard_constraints),
            "candidate_ids": [item.candidate_id for item in state.request.candidates],
            "eligible_poc_passed_candidate_ids": sorted(passed_ids),
            "scenario": self.scenario,
            "evidence_ids": [item.evidence_id for item in self.evidence],
            "poc_statuses": {
                item.candidate_id: item.status.value for item in self.poc_results
            },
        }
        generated = self._generation_provider.generate_structured(
            operation="techscout_decision_report",
            messages=(
                GenerationMessage(
                    role="system",
                    content=(
                        "You draft a bounded TechScout decision. Choose only from "
                        "eligible_poc_passed_candidate_ids. If that list is empty, "
                        "preferred_candidate_id must be null and the summary must "
                        "state that there is no safe winner. Never request tools, "
                        "invent evidence, or extend Local results to Cloud, HA, or clusters."
                    ),
                ),
                GenerationMessage(
                    role="user",
                    content=json.dumps(bounded_facts, sort_keys=True),
                ),
            ),
            response_schema=ModelDecisionDraft,
            timeout=self._generation_timeout_seconds,
        )
        self.model_prompt_tokens = generated.prompt_tokens
        self.model_completion_tokens = generated.completion_tokens
        self.model_total_tokens = generated.total_tokens
        self.model_revision = generated.model
        preferred = next(
            (
                item
                for item in state.request.candidates
                if item.candidate_id == generated.result.preferred_candidate_id
                and item.candidate_id in passed_ids
            ),
            None,
        )
        # The model may rank only already-authorized candidates. The gate still owns
        # the report verdict, recommendation, constraints, evidence, and publication.
        if preferred is None:
            return default_candidate, default_summary, generated.total_tokens or 0
        return preferred, generated.result.summary, generated.total_tokens or 0

    def _require_remaining_seconds(self, required: float) -> None:
        if self._active_deadline is None:
            raise StageDeadlineExceeded("verified stage lacks a deadline")
        remaining = (
            self._active_deadline.deadline_at - datetime.now(timezone.utc)
        ).total_seconds()
        if remaining < required:
            raise StageDeadlineExceeded("insufficient whole-run deadline remains")

    def _candidate_context(self, candidate_id: str) -> CandidateContextData:
        source_ids = {item.source_id for item in self.sources if item.candidate_id == candidate_id}
        return CandidateContextData(
            candidate_id=candidate_id,
            documents=tuple(item for item in self.sources if item.candidate_id == candidate_id),
            chunks=tuple(item for item in self.chunks if item.source_id in source_ids),
            evidence=tuple(item for item in self.evidence if item.candidate_id == candidate_id),
        )


class StageServicesFactory(Protocol):
    def __call__(
        self,
        *,
        run_dir: Path,
        progress_sink: Callable[[ResearchStage, str | None, str | None, str], None],
        trace_sink: Callable[[str, str, str, str], None] | None = None,
        trace: TechScoutTraceRecorder | None = None,
    ) -> object: ...


class TechScoutRunEngine:
    """Run one request through Harness and publish durable projections/artifacts."""

    def __init__(
        self,
        output_root: Path,
        registry: RunRegistry,
        *,
        verified_services_factory: StageServicesFactory | None = None,
        verified_timeout_seconds: int = 300,
    ) -> None:
        if verified_timeout_seconds < 60 or verified_timeout_seconds > 1800:
            raise ValueError("verified timeout must be between 60 and 1800 seconds")
        self.output_root = output_root
        self.registry = registry
        self.verified_services_factory = verified_services_factory
        self.verified_timeout_seconds = verified_timeout_seconds

    def run(self, row: TechScoutRegistryRun) -> tuple[TechScoutProjectionBundle, str]:
        run_dir = self.output_root / "techscout" / row.id
        run_dir.mkdir(parents=True, exist_ok=True)
        core_id = f"run:{row.id}"
        scenario = self._scenario(row.request)
        started = time.monotonic()
        trace_path = run_dir / "traces.jsonl"
        trace_manifest = run_dir / "traces-manifest.json"
        if trace_path.exists():
            os.replace(trace_path, run_dir / "traces-interrupted.jsonl")
        if trace_manifest.exists():
            os.replace(trace_manifest, run_dir / "traces-interrupted-manifest.json")
        trace = TechScoutTraceRecorder(trace_path, run_id=core_id)

        def progress(stage: ResearchStage, skill: str | None, tool: str | None, label: str) -> None:
            api_stage = _STAGE_MAP.get(stage, "verify")
            current = TechScoutProgress(
                stage=api_stage,
                completed_stages=_COMPLETED_BY_STAGE[api_stage],
                current_skill=skill,
                current_tool=tool,
                elapsed_seconds=max(0, time.monotonic() - started),
            )
            event_type = "tool" if tool else "skill" if skill else "stage"
            self.registry.update_techscout_progress(
                row.id, current, event_type=event_type, label=label,
                skill=skill, tool=tool,
            )

        service_kwargs = {
            "run_dir": run_dir,
            "progress_sink": progress,
            "trace_sink": lambda event_type, stage, status, label: self.registry.append_event(
                row.id, event_type=event_type, stage=stage, status=status, label=label,
            ),
            "trace": trace,
        }
        if row.request.mode == "verified":
            if self.verified_services_factory is None:
                raise ValueError("verified stage services are unavailable")
            services = self.verified_services_factory(**service_kwargs)
        else:
            services = DeterministicStageServices(scenario=scenario, **service_kwargs)
        state = self._initial_state(
            core_id,
            row.request,
            verified_timeout_seconds=self.verified_timeout_seconds,
        )
        checkpoint_path = run_dir / "harness-checkpoints.sqlite3"
        with SQLiteCheckpointAdapter(checkpoint_path) as checkpoints:
            harness = TechScoutHarness(TracingStageServices(services, trace), checkpoints)
            if (run_dir / "stage-workspace.json").is_file():
                result = harness.run(run_id=core_id)
            else:
                result = harness.run(state)
        bundle = self._bundle(row, result, services, scenario, started)
        self._publish(run_dir, result, services, bundle)
        trace.record_terminal(
            terminal_status=result.state.terminal_status.value if result.state.terminal_status else "failed",
            gate_outcome=result.state.gate_outcome.value if result.state.gate_outcome else "failed",
            latency_ms=round((time.monotonic() - started) * 1000),
            prompt_tokens=(
                services.model_prompt_tokens
                if getattr(services, "model_prompt_tokens", None) is not None
                else result.state.token_count
            ),
            completion_tokens=getattr(services, "model_completion_tokens", None) or 0,
            retry_count=result.state.recovery_count,
            recovery_count=result.state.recovery_count,
            report_sha256=_sha(result.report.model_dump_json() if result.report else ""),
            manifest_sha256=_sha(result.manifest.model_dump_json() if result.manifest else ""),
            status="ok" if result.state.terminal_status is not TerminalStatus.FAILED else "error",
            context={
                "model_revision": getattr(services, "model_revision", None),
                "provider_usage_reported": getattr(services, "model_total_tokens", None) is not None,
            },
        )
        trace.seal()
        return bundle, str((run_dir / "web-projection.json").relative_to(self.output_root))

    def publish_failed_projection(
        self, row: TechScoutRegistryRun, code: str = "execution_initialization_failed",
    ) -> tuple[TechScoutProjectionBundle, str]:
        run_dir = self.output_root / "techscout" / row.id
        run_dir.mkdir(parents=True, exist_ok=True)
        progress = TechScoutProgress(
            stage="terminal", completed_stages=[], elapsed_seconds=0,
        )
        bundle = TechScoutProjectionBundle(
            detail=TechScoutRunDetail(
                id=row.id,
                status="failed",
                synthetic=row.request.mode == "fast",
                fixture_name=("wave2_failed_safe" if row.request.mode == "fast" else None),
                question=row.request.question,
                mode=row.request.mode,
                progress=progress,
                created_at=row.created_at,
                finished_at=utc_now(),
                project_context=row.request.project_context,
                environment=row.request.environment,
                hard_constraints=row.request.hard_constraints,
                candidates=[],
                recovery=TechScoutRecoveryProjection(
                    attempted=False, outcome="not_needed", attempts_used=0,
                ),
                approval=TechScoutApprovalProjection(
                    required=False, status="not_required",
                ),
                issues=[TechScoutIssueProjection(
                    stage="orchestration", code=code,
                    retryable_by_new_run=True,
                )],
            ),
            report=None,
            evidence=[],
        )
        path = run_dir / "web-projection.json"
        path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
        failed_manifest = RunManifest(
            run_id=f"run:{row.id}",
            terminal_status=TerminalStatus.FAILED,
            artifact_ids=(),
            limitation_codes=(),
        )
        failed_files = {
            "request.json": row.request.model_dump_json(indent=2),
            "research-plan.json": "{}",
            "source-snapshots.jsonl": "",
            "evidence.jsonl": "",
            "poc-plan.json": "[]",
            "poc-results.json": "[]",
            "run_manifest.json": failed_manifest.model_dump_json(indent=2),
        }
        for name, content in failed_files.items():
            (run_dir / name).write_text(content, encoding="utf-8")
        trace_path = run_dir / "traces.jsonl"
        manifest_path = run_dir / "traces-manifest.json"
        if trace_path.exists():
            os.replace(trace_path, run_dir / "traces-aborted.jsonl")
        if manifest_path.exists():
            os.replace(manifest_path, run_dir / "traces-aborted-manifest.json")
        trace = TechScoutTraceRecorder(trace_path, run_id=f"run:{row.id}")
        trace.record_terminal(
            terminal_status="failed", gate_outcome="failed", latency_ms=0,
            prompt_tokens=0, completion_tokens=0, retry_count=0, recovery_count=0,
            report_sha256=_sha(""), manifest_sha256=_sha(failed_manifest.model_dump_json()),
            status="error",
        )
        trace.seal()
        return bundle, str(path.relative_to(self.output_root))

    @staticmethod
    def _scenario(request: TechScoutCreateRunRequest) -> str:
        if request.mode == "verified":
            return "verified_limited"
        if request.candidates and all(
            _recipe_for(candidate.name) is None for candidate in request.candidates
        ):
            return "research_only"
        question = request.question.lower()
        if "recover safely" in question or "dependency conflict" in question:
            return "single_recovery"
        if "cached" in question or "cache fallback" in question:
            return "cached_degradation"
        return "happy"

    @staticmethod
    def _initial_state(
        core_id: str,
        request: TechScoutCreateRunRequest,
        *,
        verified_timeout_seconds: int = 300,
    ) -> ResearchState:
        candidates = tuple(
            Candidate(
                candidate_id=f"candidate:{_slug(item.name)}",
                name=item.name,
                package_name=item.package_name,
                requested_version=item.requested_version,
            )
            for item in request.candidates
        ) or (
            Candidate(
                candidate_id="candidate:qdrant-local",
                name="Qdrant Local",
                package_name="qdrant-client",
            ),
        )
        research_request = ResearchRequest(
            run_id=core_id,
            question=request.question,
            project_context=request.project_context,
            environment=EnvironmentSpec(
                python_version=request.environment.python_version,
                operating_system=request.environment.operating_system,
                deployment=request.environment.deployment,
            ),
            hard_constraints=tuple(request.hard_constraints),
            candidates=candidates,
            mode=RunMode(request.mode),
        )
        return ResearchState(
            run_id=core_id,
            request=research_request,
            budget=RunBudget(
                deadline_at=utc_now()
                + timedelta(
                    seconds=verified_timeout_seconds
                    if request.mode == "verified"
                    else 120
                )
            ),
            stage=ResearchStage.NORMALIZE_REQUEST,
            step_count=0,
            tool_call_count=0,
            token_count=0,
            recovery_count=0,
            candidate_ids=tuple(item.candidate_id for item in candidates),
            source_ids=(),
            evidence_ids=(),
            poc_result_ids=(),
            failures=(),
        )

    def _bundle(self, row, result, services, scenario, started) -> TechScoutProjectionBundle:
        terminal = result.state.terminal_status or TerminalStatus.FAILED
        status = terminal.value
        report = result.report
        candidates = [
            TechScoutCandidateProjection(
                candidate_id=item.candidate_id.split(":", 1)[-1],
                name=item.name,
                support_level=(
                    "v1_supported"
                    if _recipe_for(item.name) is not None
                    else "research_only"
                ),
                requested_version=item.requested_version,
                resolved_version=next((poc.resolved_version for poc in services.poc_results if poc.candidate_id == item.candidate_id), None),
                compatibility=(
                    "compatible"
                    if any(poc.candidate_id == item.candidate_id and poc.status is PocStatus.PASSED for poc in services.poc_results)
                    else "unknown"
                ),
                verdict=(
                    "recommended"
                    if report and report.recommendation == item.candidate_id
                    else "not_recommended"
                    if any(poc.candidate_id == item.candidate_id and poc.status is PocStatus.PASSED for poc in services.poc_results)
                    else "insufficient_evidence"
                ),
                evidence_ids=[e.evidence_id for e in services.evidence if e.candidate_id == item.candidate_id],
            )
            for item in result.state.request.candidates
        ]
        recovery = TechScoutRecoveryProjection(
            attempted=result.state.recovery_count > 0,
            failed_stage="verify" if result.state.recovery_count else None,
            action=(result.recovery.action.value if result.recovery else None),
            outcome="recovered" if result.state.recovery_count and terminal is TerminalStatus.COMPLETED else "exhausted" if result.state.recovery_count else "not_needed",
            attempts_used=result.state.recovery_count,
        )
        progress = TechScoutProgress(
            stage="terminal",
            completed_stages=["plan", "research", "verify", "decide"],
            elapsed_seconds=max(0, time.monotonic() - started),
        )
        issue_by_key = {
            (failure.stage.value, failure.code.value): TechScoutIssueProjection(
                stage=failure.stage.value,
                code=failure.code.value,
                retryable_by_new_run=terminal is TerminalStatus.FAILED,
            )
            for failure in result.state.failures
        }
        detail = TechScoutRunDetail(
            id=row.id,
            status=status,
            synthetic=services.synthetic,
            fixture_name=(
                f"{services.fixture_name_prefix}_{scenario}"
                if services.synthetic
                else None
            ),
            question=row.request.question,
            mode=row.request.mode,
            progress=progress,
            created_at=row.created_at,
            finished_at=utc_now(),
            project_context=row.request.project_context,
            environment=row.request.environment,
            hard_constraints=row.request.hard_constraints,
            candidates=candidates,
            recovery=recovery,
            approval=TechScoutApprovalProjection(required=False, status="not_required"),
            issues=list(issue_by_key.values())[-3:],
        )
        evidence = [
            TechScoutEvidenceProjection(
                evidence_id=item.evidence_id,
                candidate_id=item.candidate_id.split(":", 1)[-1],
                kind=item.kind.value,
                claim=item.claim,
                source_title=next(source.title for source in services.sources if source.source_id == item.source_ids[0]),
                source_type=next(source.source_type.value for source in services.sources if source.source_id == item.source_ids[0]),
                source_url=(
                    None
                    if services.synthetic
                    else next(source.url for source in services.sources if source.source_id == item.source_ids[0])
                ),
                as_of=next(source.as_of for source in services.sources if source.source_id == item.source_ids[0]),
                acquisition_state=(
                    "synthetic"
                    if services.synthetic
                    else getattr(services, "source_acquisition_states", {}).get(
                        item.source_ids[0], AcquisitionState.UNAVAILABLE
                    ).value
                ),
                snapshot_sha256=next(
                    source.content_sha256
                    for source in services.sources
                    if source.source_id == item.source_ids[0]
                ),
            )
            for item in services.evidence
        ]
        report_projection = None
        if report is not None:
            report_projection = TechScoutReportProjection(
                run_id=row.id,
                verdict="recommended" if report.verdict is Verdict.RECOMMENDED else "no_safe_winner",
                recommendation=report.recommendation.split(":", 1)[-1] if report.recommendation else None,
                summary=report.summary,
                constraints=[
                    TechScoutConstraintProjection(
                        constraint=item.constraint,
                        candidate_id=item.candidate_id.split(":", 1)[-1],
                        status=item.status.value,
                        evidence_ids=list(item.evidence_ids),
                        reason=item.reason,
                    )
                    for item in report.constraint_results
                ],
                poc_results=[
                    TechScoutPocProjection(
                        candidate_id=item.candidate_id.split(":", 1)[-1],
                        recipe_id=next((plan.recipe_id for plan in services.poc_plans if plan.poc_plan_id == item.poc_plan_id), None),
                        status=item.status.value,
                        checks=list(next((plan.checks for plan in services.poc_plans if plan.poc_plan_id == item.poc_plan_id), ())),
                        duration_ms=item.duration_ms,
                        synthetic=services.synthetic,
                        verified=(not services.synthetic and item.status is PocStatus.PASSED),
                    )
                    for item in services.poc_results
                ],
                limitations=[
                    *(
                        ["Synthetic frozen evidence served through a real local MCP transport; not live provider evidence."]
                        if services.synthetic
                        else []
                    ),
                    *report.limitations,
                ],
                evidence_ids=[item.evidence_id for item in services.evidence],
                synthetic=services.synthetic,
            )
        return TechScoutProjectionBundle(detail=detail, report=report_projection, evidence=evidence)

    def _publish(self, run_dir, result, services, bundle) -> None:
        request = result.state.request
        files = {
            "request.json": request.model_dump_json(indent=2),
            "research-plan.json": result.state.plan.model_dump_json(indent=2) if result.state.plan else "{}",
            "source-snapshots.jsonl": "\n".join(item.model_dump_json() for item in services.sources) + "\n",
            "evidence.jsonl": "\n".join(item.model_dump_json() for item in services.evidence) + "\n",
            "poc-plan.json": json.dumps([item.model_dump(mode="json") for item in services.poc_plans], indent=2),
            "poc-results.json": json.dumps([item.model_dump(mode="json") for item in services.poc_history], indent=2),
            "decision-report.json": result.report.model_dump_json(indent=2) if result.report else "{}",
            "decision-report.md": f"# TechScout decision\n\n{result.report.summary if result.report else 'Run failed safely.'}\n",
            "run_manifest.json": result.manifest.model_dump_json(indent=2) if result.manifest else "{}",
            "web-projection.json": bundle.model_dump_json(indent=2),
        }
        for name, content in files.items():
            (run_dir / name).write_text(content, encoding="utf-8")

class TechScoutSingleRunExecutor:
    def __init__(
        self,
        registry: RunRegistry,
        output_root: Path,
        *,
        verified_services_factory: StageServicesFactory | None = None,
    ) -> None:
        self.registry = registry
        self.engine = TechScoutRunEngine(
            output_root,
            registry,
            verified_services_factory=verified_services_factory,
        )
        self.available = False
        self._stop = False
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None
        self._logger = logging.getLogger("paper_agent.web.techscout_execution")

    def start(self) -> None:
        for row in self.registry.active_techscout():
            self.registry.requeue_techscout(row.id)
        self.available = True
        self._thread = threading.Thread(target=self._work, name="techscout-runner", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.available = False
        with self._condition:
            self._stop = True
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=5)

    def notify(self) -> None:
        with self._condition:
            self._condition.notify()

    def _work(self) -> None:
        while True:
            with self._condition:
                if self._stop:
                    return
            row = self.registry.claim_oldest_techscout()
            if row is None:
                with self._condition:
                    self._condition.wait(timeout=0.25)
                continue
            try:
                self._execute(row)
            except Exception:
                self._logger.error(
                    "TechScout worker isolated a failed terminalization",
                    extra={"run_id": row.id, "code": "terminalization_failed"},
                )
                try:
                    self.registry.fail_stuck_techscout(row.id)
                except Exception:
                    self._logger.error(
                        "TechScout queue release failed",
                        extra={"run_id": row.id, "code": "queue_release_failed"},
                    )

    def _execute(self, row: TechScoutRegistryRun) -> None:
        try:
            bundle, projection_path = self.engine.run(row)
            self.registry.terminal_techscout(
                row.id,
                bundle.detail.status,
                projection_path=projection_path,
                progress=bundle.detail.progress,
            )
        except Exception:
            self._logger.error(
                "TechScout execution failed safely",
                extra={"run_id": row.id, "code": "execution_initialization_failed"},
            )
            bundle, projection_path = self.engine.publish_failed_projection(row)
            self.registry.terminal_techscout(
                row.id, "failed", projection_path=projection_path,
                progress=bundle.detail.progress,
            )
