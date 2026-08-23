from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


_MESSAGES = {
    "run_not_found": "The requested run was not found.",
    "paper_not_found": "The requested paper was not found.",
    "evidence_not_found": "The requested evidence was not found.",
    "artifact_not_found": "The requested artifact was not found.",
    "artifact_not_ready": "The requested artifact is not available yet.",
    "report_unavailable": "This run does not have an available report.",
    "artifact_corrupt": "A persisted run artifact could not be read safely.",
    "validation_error": "The request did not satisfy the API contract.",
    "queue_full": "The run queue is full.",
    "execution_unavailable": "Run execution is unavailable.",
    "techscout_execution_unavailable": "TechScout execution is not connected in the Wave 1 API shell.",
    "candidate_not_found": "The requested candidate was not found.",
    "run_busy": "The requested run is busy.",
    "origin_not_allowed": "The request origin is not allowed.",
    "internal_error": "The request could not be completed.",
    "executor_unavailable": "Run execution is unavailable.",
    "idempotency_conflict": "The idempotency key was already used for a different request.",
    "invalid_state_transition": "The run state changed before this operation could complete.",
    "rate_limited": "Too many requests were submitted.",
    "run_cancelled": "The run was cancelled.",
    "deadline_exceeded": "The run deadline was exceeded.",
}


class ErrorKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    DEADLINE = "deadline"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"


@dataclass(slots=True)
class WebError(Exception):
    status_code: int
    code: str
    details: dict[str, object] | None = None

    @property
    def message(self) -> str:
        return _MESSAGES.get(self.code, _MESSAGES["internal_error"])


class ConflictError(WebError):
    def __init__(self, code: str = "invalid_state_transition") -> None:
        super().__init__(409, code)


class DeadlineExceededError(WebError):
    def __init__(self) -> None:
        super().__init__(408, "deadline_exceeded")


class RunCancelledError(WebError):
    def __init__(self) -> None:
        super().__init__(409, "run_cancelled")


@dataclass(frozen=True, slots=True)
class ClassifiedError:
    kind: ErrorKind
    code: str
    retryable: bool
    safe_details: dict[str, object]


def classify_exception(
    error: BaseException,
    *,
    attempt: int,
    max_attempts: int = 2,
) -> ClassifiedError:
    if isinstance(error, DeadlineExceededError):
        return ClassifiedError(ErrorKind.DEADLINE, error.code, False, {})
    if isinstance(error, RunCancelledError):
        return ClassifiedError(ErrorKind.CANCELLED, error.code, False, {})
    transient = isinstance(error, (TimeoutError, ConnectionError))
    retryable = transient and attempt < max_attempts
    return ClassifiedError(
        ErrorKind.TRANSIENT if transient else ErrorKind.PERMANENT,
        "transient_execution_failure" if retryable else "execution_failed",
        retryable,
        {"exception_type": type(error).__name__},
    )
