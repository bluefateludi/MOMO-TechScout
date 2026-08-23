from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, UUID4, field_validator, model_validator

from paper_agent.modeling import StrictModel
from paper_agent.techscout.decision_context import (
    DecisionContext,
    EnvironmentSpec,
    flatten_decision_request,
)


TechScoutStatus = Literal[
    "queued", "running", "completed", "completed_with_limitations", "failed",
    "cancelled", "timed_out", "interrupted", "dead_letter",
]
TechScoutStage = Literal["plan", "research", "verify", "decide", "terminal"]


class TechScoutEnvironmentRequest(EnvironmentSpec):
    """Compatibility name for the environment projection."""


class TechScoutCandidateInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    package_name: str | None = Field(default=None, max_length=100)
    requested_version: str | None = Field(default=None, max_length=64)


class TechScoutCreateRunRequest(StrictModel):
    question: str = Field(min_length=3, max_length=1000)
    project_context: str = Field(min_length=3, max_length=2000)
    environment: TechScoutEnvironmentRequest
    hard_constraints: list[str] = Field(min_length=1, max_length=5)
    current_stack: list[str] = Field(default_factory=list, max_length=20)
    use_cases: list[str] = Field(default_factory=list, max_length=12)
    team_capabilities: list[str] = Field(default_factory=list, max_length=12)
    performance_requirements: list[str] = Field(default_factory=list, max_length=12)
    budget_constraints: list[str] = Field(default_factory=list, max_length=12)
    security_requirements: list[str] = Field(default_factory=list, max_length=12)
    license_requirements: list[str] = Field(default_factory=list, max_length=12)
    preferences: list[str] = Field(default_factory=list, max_length=12)
    candidates: list[TechScoutCandidateInput] = Field(default_factory=list, max_length=3)
    mode: Literal["fast", "verified"] = "fast"

    @model_validator(mode="before")
    @classmethod
    def accept_canonical_context(cls, value: object) -> object:
        return flatten_decision_request(value)

    @field_validator("question", "project_context", mode="before")
    @classmethod
    def trim_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator(
        "hard_constraints",
        "current_stack",
        "use_cases",
        "team_capabilities",
        "performance_requirements",
        "budget_constraints",
        "security_requirements",
        "license_requirements",
        "preferences",
        mode="before",
    )
    @classmethod
    def trim_decision_items(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        return [item.strip() if isinstance(item, str) else item for item in value]

    @model_validator(mode="after")
    def validate_decision_context(self) -> "TechScoutCreateRunRequest":
        self.decision_context
        return self

    @property
    def decision_context(self) -> DecisionContext:
        return DecisionContext(
            question=self.question,
            project_summary=self.project_context,
            current_stack=tuple(self.current_stack),
            use_cases=tuple(self.use_cases),
            deployment=EnvironmentSpec.model_validate(self.environment.model_dump()),
            team_capabilities=tuple(self.team_capabilities),
            performance_requirements=tuple(self.performance_requirements),
            budget_constraints=tuple(self.budget_constraints),
            security_requirements=tuple(self.security_requirements),
            license_requirements=tuple(self.license_requirements),
            must_haves=tuple(self.hard_constraints),
            preferences=tuple(self.preferences),
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "decision_context": self.decision_context.model_dump(mode="json"),
            "candidates": [candidate.model_dump(mode="json") for candidate in self.candidates],
            "mode": self.mode,
        }


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
