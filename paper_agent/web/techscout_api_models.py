from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, UUID4, field_validator

from paper_agent.modeling import StrictModel


TechScoutStatus = Literal[
    "queued", "running", "completed", "completed_with_limitations", "failed",
    "cancelled", "timed_out", "interrupted", "dead_letter",
]
TechScoutStage = Literal["plan", "research", "verify", "decide", "terminal"]


class TechScoutEnvironmentRequest(StrictModel):
    python_version: str = Field(min_length=1, max_length=32)
    operating_system: str = Field(min_length=1, max_length=80)
    deployment: str = Field(min_length=1, max_length=120)


class TechScoutCandidateInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    package_name: str | None = Field(default=None, max_length=100)
    requested_version: str | None = Field(default=None, max_length=64)


class TechScoutCreateRunRequest(StrictModel):
    question: str = Field(min_length=3, max_length=1000)
    project_context: str = Field(min_length=3, max_length=2000)
    environment: TechScoutEnvironmentRequest
    hard_constraints: list[str] = Field(min_length=1, max_length=5)
    candidates: list[TechScoutCandidateInput] = Field(default_factory=list, max_length=3)
    mode: Literal["fast", "verified"] = "fast"

    @field_validator("question", "project_context", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("hard_constraints")
    @classmethod
    def unique_constraints(cls, values: list[str]) -> list[str]:
        trimmed = [value.strip() for value in values]
        if any(not value for value in trimmed) or len(set(trimmed)) != len(trimmed):
            raise ValueError("hard constraints must be non-empty and unique")
        return trimmed


class TechScoutProgress(StrictModel):
    stage: TechScoutStage
    completed_stages: list[Literal["plan", "research", "verify", "decide"]]
    current_skill: str | None = None
    current_tool: str | None = None
    elapsed_seconds: float = Field(ge=0)


class TechScoutRunSummary(StrictModel):
    id: UUID4
    status: TechScoutStatus
    synthetic: bool
    fixture_name: str | None = None
    question: str
    mode: Literal["fast", "verified"]
    progress: TechScoutProgress
    created_at: datetime
    finished_at: datetime | None = None


class TechScoutCandidateProjection(StrictModel):
    candidate_id: str
    name: str
    support_level: Literal["v1_supported", "research_only"]
    requested_version: str | None = None
    resolved_version: str | None = None
    compatibility: Literal["compatible", "incompatible", "unknown"]
    verdict: Literal["recommended", "not_recommended", "insufficient_evidence"]
    evidence_ids: list[str]


class TechScoutEvidenceProjection(StrictModel):
    evidence_id: str
    candidate_id: str
    kind: Literal["retrieved_fact", "local_measurement", "model_inference"]
    claim: str
    source_title: str
    source_type: Literal["official_documentation", "github_repository", "package_metadata", "poc"]
    source_url: str | None = None
    as_of: datetime
    acquisition_state: Literal["live", "cache", "unavailable", "synthetic"]
    snapshot_sha256: str


class TechScoutPocProjection(StrictModel):
    candidate_id: str
    recipe_id: str | None = None
    status: Literal["passed", "failed", "timed_out", "research_only"]
    checks: list[str]
    duration_ms: int = Field(ge=0)
    synthetic: bool
    verified: bool


class TechScoutConstraintProjection(StrictModel):
    constraint: str
    candidate_id: str
    status: Literal["satisfied", "not_satisfied", "unknown"]
    evidence_ids: list[str]
    reason: str | None = None


class TechScoutRecoveryProjection(StrictModel):
    attempted: bool
    failed_stage: str | None = None
    action: str | None = None
    outcome: Literal["not_needed", "recovered", "exhausted"]
    attempts_used: int = Field(ge=0, le=1)


class TechScoutApprovalProjection(StrictModel):
    required: bool
    status: Literal["not_required", "pending", "approved", "denied"]
    reason: str | None = None


class TechScoutIssueProjection(StrictModel):
    stage: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    code: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_]+$")
    retryable_by_new_run: bool = False


class TechScoutRunDetail(TechScoutRunSummary):
    project_context: str
    environment: TechScoutEnvironmentRequest
    hard_constraints: list[str]
    candidates: list[TechScoutCandidateProjection]
    recovery: TechScoutRecoveryProjection
    approval: TechScoutApprovalProjection
    issues: list[TechScoutIssueProjection]


class TechScoutRunList(StrictModel):
    items: list[TechScoutRunSummary]
    next_cursor: str | None = None


class TechScoutReportProjection(StrictModel):
    run_id: UUID4
    verdict: Literal["recommended", "no_safe_winner"]
    recommendation: str | None = None
    summary: str
    constraints: list[TechScoutConstraintProjection]
    poc_results: list[TechScoutPocProjection]
    limitations: list[str]
    evidence_ids: list[str]
    synthetic: bool


class TechScoutCandidateList(StrictModel):
    items: list[TechScoutCandidateProjection]


class TechScoutEvidenceList(StrictModel):
    items: list[TechScoutEvidenceProjection]


class TraceEvent(StrictModel):
    cursor: str
    event_type: Literal["run", "stage", "skill", "tool", "recovery", "approval"]
    stage: str | None = None
    status: str
    label: str
    skill: str | None = None
    tool: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    created_at: datetime


class TracePage(StrictModel):
    items: list[TraceEvent]
    next_cursor: str | None = None
