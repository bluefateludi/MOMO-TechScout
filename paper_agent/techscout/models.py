from datetime import datetime
from enum import Enum
from typing import TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated, Self

from paper_agent.techscout.decision_context import (
    DecisionContext,
    EnvironmentSpec,
    normalize_decision_request,
)
from paper_agent.techscout.errors import FailureCode, RecoveryAction, StableId


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
HttpsUrl = Annotated[str, StringConstraints(pattern=r"^https://[^\s]+$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
JsonObject: TypeAlias = dict[str, JsonValue]


class TechScoutModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RunMode(str, Enum):
    FAST = "fast"
    VERIFIED = "verified"
    BENCHMARK = "benchmark"


class TerminalStatus(str, Enum):
    COMPLETED = "completed"
    COMPLETED_WITH_LIMITATIONS = "completed_with_limitations"
    FAILED = "failed"


class GateOutcome(str, Enum):
    PASSED = "passed"
    RECOVER = "recover"
    LIMITED = "limited"
    FAILED = "failed"


class SourceType(str, Enum):
    OFFICIAL_DOCUMENTATION = "official_documentation"
    GITHUB_REPOSITORY = "github_repository"
    GITHUB_RELEASE = "github_release"
    GITHUB_ISSUE = "github_issue"
    PACKAGE_METADATA = "package_metadata"
    PAPER = "paper"


class EvidenceKind(str, Enum):
    RETRIEVED_FACT = "retrieved_fact"
    LOCAL_MEASUREMENT = "local_measurement"
    MODEL_INFERENCE = "model_inference"


class ToolStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    DENIED = "denied"


class CacheStatus(str, Enum):
    HIT = "hit"
    MISS = "miss"
    STALE = "stale"
    NOT_APPLICABLE = "not_applicable"


class PocStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    RESEARCH_ONLY = "research_only"


class Verdict(str, Enum):
    RECOMMENDED = "recommended"
    NOT_RECOMMENDED = "not_recommended"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ConstraintStatus(str, Enum):
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"
    UNKNOWN = "unknown"


class Candidate(TechScoutModel):
    candidate_id: StableId
    name: NonEmptyStr
    repository_url: HttpsUrl | None = None
    package_name: NonEmptyStr | None = None
    requested_version: NonEmptyStr | None = None
    resolved_version: NonEmptyStr | None = None


class ResearchRequest(TechScoutModel):
    run_id: StableId
    decision_context: DecisionContext
    candidates: tuple[Candidate, ...] = Field(default=(), max_length=3)
    mode: RunMode = RunMode.FAST

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_context(cls, value: object) -> object:
        normalized = normalize_decision_request(value)
        if isinstance(normalized, dict):
            normalized = dict(normalized)
            if isinstance(normalized.get("candidates"), list):
                normalized["candidates"] = tuple(normalized["candidates"])
            if isinstance(normalized.get("mode"), str):
                normalized["mode"] = RunMode(normalized["mode"])
        return normalized

    @model_validator(mode="after")
    def identifiers_and_constraints_are_unique(self) -> Self:
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate identifiers must be unique")
        if len(self.hard_constraints) != len(set(self.hard_constraints)):
            raise ValueError("hard constraints must be unique")
        return self

    @property
    def question(self) -> str:
        return self.decision_context.question

    @property
    def project_context(self) -> str:
        return self.decision_context.project_summary

    @property
    def environment(self) -> EnvironmentSpec:
        return self.decision_context.deployment

    @property
    def hard_constraints(self) -> tuple[str, ...]:
        return self.decision_context.must_haves


class ResearchPlan(TechScoutModel):
    plan_id: StableId
    criteria_contract_id: StableId | None = None
    investigation_dimensions: tuple[NonEmptyStr, ...] = Field(min_length=1)
    required_capabilities: tuple[NonEmptyStr, ...] = Field(min_length=1)
    planned_evidence: tuple[NonEmptyStr, ...] = Field(min_length=1)
    poc_intent: NonEmptyStr


class SkillSpec(TechScoutModel):
    skill_id: StableId
    name: NonEmptyStr
    version: NonEmptyStr
    stage: NonEmptyStr
    instructions: NonEmptyStr
    completion_criteria: tuple[NonEmptyStr, ...] = Field(min_length=1)
    allowed_tools: tuple[NonEmptyStr, ...]
    source_budget: int = Field(ge=0)
    tool_call_budget: int = Field(ge=0)
    step_budget: int = Field(ge=1)
    token_budget: int = Field(ge=1)
    handled_failure_codes: tuple[FailureCode, ...] = ()


class SkillSelection(TechScoutModel):
    selection_id: StableId
    skill_id: StableId
    stage: NonEmptyStr
    reason: NonEmptyStr


class ToolCall(TechScoutModel):
    tool_call_id: StableId
    tool_name: NonEmptyStr
    skill_id: StableId
    arguments: JsonObject = Field(default_factory=dict)


class ToolResult(TechScoutModel):
    tool_call_id: StableId
    status: ToolStatus
    output: JsonObject = Field(default_factory=dict)
    error_code: FailureCode | None = None
    latency_ms: int = Field(ge=0)
    cache_status: CacheStatus = CacheStatus.NOT_APPLICABLE

    @model_validator(mode="after")
    def validate_error_code(self) -> Self:
        if self.status is ToolStatus.SUCCEEDED and self.error_code is not None:
            raise ValueError("successful tool result cannot have an error code")
        if self.status is not ToolStatus.SUCCEEDED and self.error_code is None:
            raise ValueError("unsuccessful tool result requires an error code")
        return self


class SourceDocument(TechScoutModel):
    source_id: StableId
    candidate_id: StableId
    source_type: SourceType
    url: HttpsUrl
    title: NonEmptyStr
    version: NonEmptyStr | None = None
    as_of: datetime
    content_sha256: Sha256

    @field_validator("as_of")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        return value


class SourceChunk(TechScoutModel):
    chunk_id: StableId
    source_id: StableId
    text: NonEmptyStr
    ordinal: int = Field(ge=0)
    content_sha256: Sha256


class CandidateEvidence(TechScoutModel):
    evidence_id: StableId
    candidate_id: StableId
    constraint: NonEmptyStr
    claim: NonEmptyStr
    source_ids: tuple[StableId, ...]
    chunk_ids: tuple[StableId, ...]
    kind: EvidenceKind

    @model_validator(mode="after")
    def retrieved_facts_have_sources(self) -> Self:
        if self.kind is EvidenceKind.RETRIEVED_FACT and (
            not self.source_ids or not self.chunk_ids
        ):
            raise ValueError("retrieved fact requires source and chunk identifiers")
        return self


class PocPlan(TechScoutModel):
    poc_plan_id: StableId
    candidate_id: StableId
    recipe_id: StableId | None = None
    trusted: bool
    checks: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def trusted_plan_has_recipe(self) -> Self:
        if self.trusted and self.recipe_id is None:
            raise ValueError("trusted PoC plan requires a recipe identifier")
        return self


class PocArtifact(TechScoutModel):
    artifact_id: StableId
    kind: NonEmptyStr
    sha256: Sha256
    size_bytes: int = Field(ge=0)


class PocResult(TechScoutModel):
    poc_result_id: StableId
    poc_plan_id: StableId
    candidate_id: StableId
    status: PocStatus
    resolved_version: NonEmptyStr | None = None
    exit_code: int | None = None
    timed_out: bool
    duration_ms: int = Field(ge=0)
    artifacts: tuple[PocArtifact, ...] = ()
    failure_code: FailureCode | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is PocStatus.PASSED:
            if self.exit_code != 0 or self.timed_out or self.failure_code is not None:
                raise ValueError("passed PoC requires exit code zero and no failure")
        elif self.status is PocStatus.TIMED_OUT:
            if not self.timed_out or self.failure_code is not FailureCode.POC_TIMEOUT:
                raise ValueError("timed out PoC requires poc_timeout failure")
        elif self.status is PocStatus.FAILED and self.failure_code is None:
            raise ValueError("failed PoC requires a failure code")
        return self


class GateDecision(TechScoutModel):
    gate_id: StableId
    outcome: GateOutcome
    checked_constraints: tuple[NonEmptyStr, ...]
    reasons: tuple[NonEmptyStr, ...] = Field(min_length=1)
    failure_ids: tuple[StableId, ...] = ()
    recovery_action: RecoveryAction | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.outcome is GateOutcome.PASSED and (
            self.failure_ids or self.recovery_action is not None
        ):
            raise ValueError("passed gate cannot include failures or recovery")
        if self.outcome is GateOutcome.RECOVER and self.recovery_action is None:
            raise ValueError("recover gate requires a recovery action")
        return self


class ConstraintResult(TechScoutModel):
    candidate_id: StableId
    constraint: NonEmptyStr
    status: ConstraintStatus
    evidence_ids: tuple[StableId, ...]
    reason: NonEmptyStr | None = None


class DecisionReport(TechScoutModel):
    report_id: StableId
    run_id: StableId
    recommendation: StableId | None = None
    verdict: Verdict
    summary: NonEmptyStr
    constraint_results: tuple[ConstraintResult, ...] = Field(min_length=1)
    limitations: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def validate_recommendation(self) -> Self:
        if self.verdict is Verdict.RECOMMENDED and self.recommendation is None:
            raise ValueError("recommended verdict requires a candidate identifier")
        if self.verdict is not Verdict.RECOMMENDED and self.recommendation is not None:
            raise ValueError("only a recommended verdict may name a recommendation")
        return self


class RunManifest(TechScoutModel):
    run_id: StableId
    terminal_status: TerminalStatus
    report_id: StableId | None = None
    artifact_ids: tuple[StableId, ...]
    limitation_codes: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def validate_terminal_semantics(self) -> Self:
        if self.terminal_status is TerminalStatus.COMPLETED:
            if self.report_id is None:
                raise ValueError("completed run requires a report")
            if self.limitation_codes:
                raise ValueError("completed run cannot have limitations")
        elif self.terminal_status is TerminalStatus.COMPLETED_WITH_LIMITATIONS:
            if self.report_id is None or not self.limitation_codes:
                raise ValueError("limited run requires a report and limitations")
        elif self.report_id is not None:
            raise ValueError("failed run cannot claim a report")
        return self
