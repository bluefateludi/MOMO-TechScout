from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from typing_extensions import Annotated, Self


StableId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        pattern=r"^[a-z][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._:@/*-]*$",
    ),
]


class FailureStage(str, Enum):
    INTAKE = "intake"
    PLANNING = "planning"
    RESEARCH = "research"
    CONTEXT = "context"
    POC_PLANNING = "poc_planning"
    POC_EXECUTION = "poc_execution"
    VALIDATION = "validation"
    REPORTING = "reporting"
    POLICY = "policy"
    ORCHESTRATION = "orchestration"
    PUBLISHING = "publishing"


class FailureCode(str, Enum):
    INVALID_REQUEST = "invalid_request"
    SEARCH_TIMEOUT = "search_timeout"
    SEARCH_RATE_LIMITED = "search_rate_limited"
    SEARCH_UNAVAILABLE = "search_unavailable"
    PAGE_PARSING_FAILED = "page_parsing_failed"
    MALFORMED_MCP_RESPONSE = "malformed_mcp_response"
    TOOL_TIMEOUT = "tool_timeout"
    TOOL_UNAVAILABLE = "tool_unavailable"
    DEPENDENCY_CONFLICT = "dependency_conflict"
    VERSION_CONFLICT = "version_conflict"
    POC_RECIPE_UNSUPPORTED = "poc_recipe_unsupported"
    POC_TIMEOUT = "poc_timeout"
    POC_NONZERO_EXIT = "poc_nonzero_exit"
    POC_ARTIFACT_INVALID = "poc_artifact_invalid"
    REPORT_SCHEMA_INVALID = "report_schema_invalid"
    REPORT_EVIDENCE_INVALID = "report_evidence_invalid"
    UNSAFE_REQUEST = "unsafe_request"
    APPROVAL_DENIED = "approval_denied"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    EXPERIMENT_CANCELLED = "experiment_cancelled"
    EXPERIMENT_CLEANUP_FAILED = "experiment_cleanup_failed"
    ARTIFACT_PUBLISH_FAILED = "artifact_publish_failed"


class RecoveryAction(str, Enum):
    USE_CACHE_OR_RETRY_SEARCH = "use_cache_or_retry_search"
    FETCH_ALTERNATE_SOURCE = "fetch_alternate_source"
    RETRY_TOOL_CALL = "retry_tool_call"
    PIN_VERSION_AND_RERUN_POC = "pin_version_and_rerun_poc"
    DIAGNOSE_AND_RERUN_POC = "diagnose_and_rerun_poc"
    REPAIR_REPORT = "repair_report"
    DOWNGRADE_TO_RESEARCH_ONLY = "downgrade_to_research_only"
    REQUEST_APPROVAL = "request_approval"
    PUBLISH_LIMITED_RESULT = "publish_limited_result"
    FAIL_SAFELY = "fail_safely"


class Failure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    failure_id: StableId
    code: FailureCode
    stage: FailureStage
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    recoverable: bool
    recovery_action: RecoveryAction | None = None
    attempt: int = Field(ge=1)
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_recovery(self) -> Self:
        if self.recoverable and self.recovery_action is None:
            raise ValueError("recoverable failure requires a recovery action")
        if not self.recoverable and self.recovery_action not in {
            None,
            RecoveryAction.PUBLISH_LIMITED_RESULT,
            RecoveryAction.FAIL_SAFELY,
        }:
            raise ValueError("non-recoverable failure cannot request a retry action")
        return self
