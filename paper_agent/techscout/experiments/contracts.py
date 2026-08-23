"""Strict, auditable contracts for generic experiment recipe execution."""

from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath

from pydantic import (
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)
from typing_extensions import Annotated, Self

from paper_agent.techscout.errors import FailureCode, StableId
from paper_agent.techscout.models import NonEmptyStr, Sha256, TechScoutModel
from paper_agent.techscout.sandbox.types import ImageRef, NetworkAccess, SandboxLimits


RelativeArtifactPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[^\\]+$"),
]


class RecipeDisposition(str, Enum):
    RESEARCH_ONLY = "research_only"
    OFFLINE_EXECUTABLE = "offline_executable"


class CheckStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class ExecutionTerminalStatus(str, Enum):
    SUCCEEDED = "succeeded"
    RESEARCH_ONLY = "research_only"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    BUDGET_EXHAUSTED = "budget_exhausted"


class ReviewedCommand(TechScoutModel):
    """A fixed command reviewed as part of a versioned Recipe."""

    argv: tuple[NonEmptyStr, ...] = Field(min_length=1)
    image: ImageRef
    network_access: NetworkAccess = NetworkAccess.NONE


class ExperimentCheck(TechScoutModel):
    check_id: StableId
    title: NonEmptyStr
    description: NonEmptyStr
    command: ReviewedCommand


class ExperimentRecipe(TechScoutModel):
    recipe_id: StableId
    version: NonEmptyStr
    title: NonEmptyStr
    purpose: NonEmptyStr
    disposition: RecipeDisposition
    checks: tuple[ExperimentCheck, ...] = ()
    research_only_reason: NonEmptyStr | None = None

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        check_ids = [check.check_id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("Recipe Check identifiers must be unique")
        if self.disposition is RecipeDisposition.RESEARCH_ONLY:
            if self.checks or self.research_only_reason is None:
                raise ValueError("research-only Recipe requires only a reason")
        else:
            if not self.checks or self.research_only_reason is not None:
                raise ValueError(
                    "offline Recipe requires Checks and no research-only reason"
                )
            if any(
                check.command.network_access is not NetworkAccess.NONE
                for check in self.checks
            ):
                raise ValueError("offline Recipe Checks cannot request network access")
        return self


class ExecutionBudget(TechScoutModel):
    """The complete execution and artifact envelope for one Experiment."""

    max_checks: int = Field(default=8, ge=0, le=64)
    wall_timeout_seconds: float = Field(default=120.0, gt=0, le=300)
    check_timeout_seconds: float = Field(default=60.0, gt=0, le=120)
    max_artifact_bytes: int = Field(default=256 * 1024, ge=1024, le=16 * 1024 * 1024)
    max_measurements: int = Field(default=64, ge=0, le=1024)
    resources: SandboxLimits = Field(default_factory=SandboxLimits)


class ExecutionRequest(TechScoutModel):
    execution_id: StableId
    subject_id: StableId
    recipe_id: StableId
    idempotency_key: StableId
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)


class ExperimentArtifact(TechScoutModel):
    artifact_id: StableId
    check_id: StableId
    kind: NonEmptyStr
    sha256: Sha256
    size_bytes: int = Field(ge=0)
    relative_path: RelativeArtifactPath

    @field_validator("relative_path")
    @classmethod
    def keep_artifact_path_relative(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Experiment Artifact path must stay relative")
        return value


class Measurement(TechScoutModel):
    measurement_id: StableId
    check_id: StableId
    name: NonEmptyStr
    value: JsonValue
    unit: NonEmptyStr
    artifact_id: StableId


class CheckResult(TechScoutModel):
    check_id: StableId
    status: CheckStatus
    duration_ms: int = Field(ge=0)
    artifact_ids: tuple[StableId, ...]
    measurement_ids: tuple[StableId, ...]
    failure_code: FailureCode | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is CheckStatus.PASSED and self.failure_code is not None:
            raise ValueError("passed Check cannot include a failure")
        if self.status is not CheckStatus.PASSED and self.failure_code is None:
            raise ValueError("unsuccessful Check requires a failure code")
        return self


class ExecutionFailure(TechScoutModel):
    code: FailureCode
    message: NonEmptyStr
    failed_check_id: StableId | None = None


class ExperimentResult(TechScoutModel):
    execution_id: StableId
    subject_id: StableId
    recipe_id: StableId
    recipe_version: NonEmptyStr
    recipe_sha256: Sha256
    terminal_status: ExecutionTerminalStatus
    terminal_reason: NonEmptyStr
    started_at: datetime
    finished_at: datetime
    budget: ExecutionBudget
    check_results: tuple[CheckResult, ...]
    artifacts: tuple[ExperimentArtifact, ...]
    measurements: tuple[Measurement, ...]
    failure: ExecutionFailure | None = None
    cleanup_complete: bool

    @field_validator("started_at", "finished_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Experiment timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_terminal_contract(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("Experiment cannot finish before it starts")
        artifact_ids = {artifact.artifact_id for artifact in self.artifacts}
        measurement_ids = {
            measurement.measurement_id for measurement in self.measurements
        }
        check_ids = {result.check_id for result in self.check_results}
        if len(artifact_ids) != len(self.artifacts):
            raise ValueError("Experiment Artifact identifiers must be unique")
        if len(measurement_ids) != len(self.measurements):
            raise ValueError("Measurement identifiers must be unique")
        if len(check_ids) != len(self.check_results):
            raise ValueError("CheckResult identifiers must be unique")
        for result in self.check_results:
            if not set(result.artifact_ids).issubset(artifact_ids):
                raise ValueError("CheckResult references an unknown Artifact")
            if not set(result.measurement_ids).issubset(measurement_ids):
                raise ValueError("CheckResult references an unknown Measurement")
        artifact_check_ids = {
            artifact.artifact_id: artifact.check_id for artifact in self.artifacts
        }
        if not set(artifact_check_ids.values()).issubset(check_ids):
            raise ValueError("Experiment Artifact references an unknown CheckResult")
        for measurement in self.measurements:
            if measurement.artifact_id not in artifact_check_ids:
                raise ValueError(
                    "Measurement references an unknown Experiment Artifact"
                )
            if artifact_check_ids[measurement.artifact_id] != measurement.check_id:
                raise ValueError("Measurement and Experiment Artifact Check must match")
            if measurement.check_id not in check_ids:
                raise ValueError("Measurement references an unknown CheckResult")
        if self.terminal_status in {
            ExecutionTerminalStatus.SUCCEEDED,
            ExecutionTerminalStatus.RESEARCH_ONLY,
        }:
            if self.failure is not None or not self.cleanup_complete:
                raise ValueError(
                    "successful terminal contract cannot include failure or cleanup debt"
                )
        elif self.failure is None:
            raise ValueError("unsuccessful terminal contract requires a sealed failure")
        expected_codes = {
            ExecutionTerminalStatus.CANCELLED: {FailureCode.EXPERIMENT_CANCELLED},
            ExecutionTerminalStatus.TIMED_OUT: {
                FailureCode.DEADLINE_EXCEEDED,
                FailureCode.POC_TIMEOUT,
            },
            ExecutionTerminalStatus.BUDGET_EXHAUSTED: {FailureCode.BUDGET_EXHAUSTED},
        }
        if (
            self.failure is not None
            and self.terminal_status in expected_codes
            and self.failure.code not in expected_codes[self.terminal_status]
        ):
            raise ValueError("terminal status and sealed failure code must match")
        if not self.cleanup_complete and (
            self.failure is None
            or self.failure.code is not FailureCode.EXPERIMENT_CLEANUP_FAILED
        ):
            raise ValueError("cleanup debt requires experiment_cleanup_failed")
        if self.terminal_status is ExecutionTerminalStatus.RESEARCH_ONLY and (
            self.check_results or self.artifacts or self.measurements
        ):
            raise ValueError(
                "research-only terminal contract cannot claim execution output"
            )
        if self.terminal_status is ExecutionTerminalStatus.SUCCEEDED and any(
            result.status is not CheckStatus.PASSED for result in self.check_results
        ):
            raise ValueError("successful Experiment requires every Check to pass")
        return self


class SealedExecution(TechScoutModel):
    result: ExperimentResult
    result_sha256: Sha256
