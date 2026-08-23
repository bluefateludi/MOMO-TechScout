from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from typing_extensions import Self

from paper_agent.techscout.errors import FailureCode, StableId
from paper_agent.techscout.models import (
    NonEmptyStr,
    ResearchRequest,
    RunMode,
    TechScoutModel,
    TerminalStatus,
    Verdict,
)


class LiveCaseCategory(str, Enum):
    SUPPORTED_RECOMMENDATION = "supported_recommendation"
    SAFE_BOUNDARY = "safe_boundary"
    CONTROLLED_RECOVERY = "controlled_recovery"


class ResearchAuthority(str, Enum):
    COLD_LIVE = "cold_live"
    FORCED_UNAVAILABLE = "forced_unavailable"


class DockerAuthority(str, Enum):
    REQUIRED = "required"
    FORCED_UNAVAILABLE = "forced_unavailable"


class LiveRunCondition(TechScoutModel):
    research_authority: ResearchAuthority
    docker_authority: DockerAuthority
    injected_failure_code: FailureCode | None = None
    maximum_recovery_attempts: Literal[0, 1] = 0

    @model_validator(mode="after")
    def validate_fault_boundary(self) -> Self:
        if self.injected_failure_code is None and self.maximum_recovery_attempts:
            raise ValueError("a recovery attempt requires a controlled injected failure")
        if self.injected_failure_code is not None and not self.maximum_recovery_attempts:
            raise ValueError("a controlled injected failure requires one recovery attempt")
        return self


class LiveExpectedOutcome(TechScoutModel):
    allowed_terminal_statuses: tuple[TerminalStatus, ...] = Field(min_length=1)
    allowed_verdicts: tuple[Verdict, ...] = Field(min_length=1)
    eligible_recommendations: tuple[StableId, ...] = ()
    prohibited_recommendations: tuple[StableId, ...] = ()
    required_limitation_codes: tuple[NonEmptyStr, ...] = ()
    recovery_required: bool = False
    recovery_must_succeed: bool | None = None

    @model_validator(mode="after")
    def validate_oracle(self) -> Self:
        if len(self.allowed_terminal_statuses) != len(set(self.allowed_terminal_statuses)):
            raise ValueError("allowed terminal statuses must be unique")
        if len(self.allowed_verdicts) != len(set(self.allowed_verdicts)):
            raise ValueError("allowed verdicts must be unique")
        overlap = set(self.eligible_recommendations) & set(self.prohibited_recommendations)
        if overlap:
            raise ValueError("eligible and prohibited recommendations must be disjoint")
        if Verdict.RECOMMENDED in self.allowed_verdicts and not self.eligible_recommendations:
            raise ValueError("a recommended verdict requires at least one eligible candidate")
        if Verdict.RECOMMENDED not in self.allowed_verdicts and self.eligible_recommendations:
            raise ValueError("eligible recommendations require a recommended verdict")
        if self.recovery_required != (self.recovery_must_succeed is not None):
            raise ValueError("required recovery must declare whether it must succeed")
        return self


class LiveEvaluationCase(TechScoutModel):
    schema_version: Literal["techscout-live-eval-case-v1"]
    fixture_kind: Literal["live_preregistered_evaluation"]
    case_id: StableId
    category: LiveCaseCategory
    request: ResearchRequest
    condition: LiveRunCondition
    expected_outcome: LiveExpectedOutcome
    forbidden_claims: tuple[NonEmptyStr, ...] = Field(min_length=1)
    reviewer_rationale: NonEmptyStr

    @model_validator(mode="after")
    def validate_case_boundary(self) -> Self:
        if self.request.mode is not RunMode.VERIFIED:
            raise ValueError("live evaluation cases must use verified mode")
        candidate_ids = {candidate.candidate_id for candidate in self.request.candidates}
        declared_ids = set(self.expected_outcome.eligible_recommendations) | set(
            self.expected_outcome.prohibited_recommendations
        )
        if not declared_ids <= candidate_ids:
            raise ValueError("oracle recommendations must refer to request candidates")
        if self.category is LiveCaseCategory.SUPPORTED_RECOMMENDATION:
            if Verdict.RECOMMENDED not in self.expected_outcome.allowed_verdicts:
                raise ValueError("supported recommendation cases must allow recommendation")
            if self.condition.injected_failure_code is not None:
                raise ValueError("supported recommendation cases cannot inject faults")
        elif self.category is LiveCaseCategory.SAFE_BOUNDARY:
            if Verdict.RECOMMENDED in self.expected_outcome.allowed_verdicts:
                raise ValueError("safe-boundary cases cannot allow a recommendation")
            if self.condition.injected_failure_code is not None:
                raise ValueError("safe-boundary cases cannot inject faults")
        elif self.condition.injected_failure_code is None:
            raise ValueError("controlled-recovery cases require an injected failure")
        return self


class LiveRubricDimension(TechScoutModel):
    dimension_id: NonEmptyStr
    weight: float = Field(gt=0, le=1)
    maximum_points: int = Field(ge=1, le=5)
    pass_description: NonEmptyStr
    fail_description: NonEmptyStr


class LiveEvaluationRubric(TechScoutModel):
    schema_version: Literal["techscout-live-eval-rubric-v1"]
    dimensions: tuple[LiveRubricDimension, ...] = Field(min_length=1)
    passing_weighted_score: float = Field(ge=0, le=1)
    require_outcome_contract_match: Literal[True] = True

    @model_validator(mode="after")
    def validate_dimensions(self) -> Self:
        dimension_ids = [dimension.dimension_id for dimension in self.dimensions]
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("rubric dimension identifiers must be unique")
        if abs(sum(dimension.weight for dimension in self.dimensions) - 1.0) > 1e-9:
            raise ValueError("rubric weights must sum to one")
        return self


class LiveEvaluationPolicy(TechScoutModel):
    repetitions_per_case: Literal[2] = 2
    per_run_timeout_seconds: int = Field(ge=60, le=1800)
    total_run_budget_seconds: int = Field(ge=720, le=43200)
    maximum_approved_cost_usd: Literal[0.0] = 0.0
    execution_authorized: Literal[False] = False
    freeze_before_first_run: Literal[True] = True
    infrastructure_reruns_per_case: Literal[0, 1] = 1


class LiveAuthorityRequirements(TechScoutModel):
    live_research: Literal[True] = True
    real_docker_poc: Literal[True] = True
    model_backed_reasoning: Literal[True] = True
    capture_exact_model_revision: Literal[True] = True
    capture_provider_token_usage: Literal[True] = True


class LiveEvaluationRegistration(TechScoutModel):
    schema_version: Literal["techscout-live-eval-registration-v1"]
    suite_id: StableId
    status: Literal["draft_preregistration"]
    baseline_git_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    cases: tuple[LiveEvaluationCase, ...] = Field(min_length=12, max_length=12)
    rubric: LiveEvaluationRubric
    policy: LiveEvaluationPolicy
    required_authorities: LiveAuthorityRequirements
    authority_notice: NonEmptyStr

    @model_validator(mode="after")
    def validate_registration(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("live evaluation case identifiers must be unique")
        expected_counts = {
            LiveCaseCategory.SUPPORTED_RECOMMENDATION: 6,
            LiveCaseCategory.SAFE_BOUNDARY: 4,
            LiveCaseCategory.CONTROLLED_RECOVERY: 2,
        }
        actual_counts = {
            category: sum(case.category is category for case in self.cases)
            for category in LiveCaseCategory
        }
        if actual_counts != expected_counts:
            raise ValueError(f"live evaluation category counts must be {expected_counts}")
        return self


def load_live_evaluation_registration(
    path: Path,
) -> tuple[LiveEvaluationRegistration, str]:
    payload = path.read_bytes()
    registration = LiveEvaluationRegistration.model_validate_json(payload)
    return registration, hashlib.sha256(payload).hexdigest()
