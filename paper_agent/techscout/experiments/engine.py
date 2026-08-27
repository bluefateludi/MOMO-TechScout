"""Auditable, idempotent execution of closed generic Experiment Recipes."""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from paper_agent.observability.sanitize import sanitize_bounded_event_data
from paper_agent.techscout.errors import FailureCode
from paper_agent.techscout.experiments.contracts import (
    CheckResult,
    CheckStatus,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionTerminalStatus,
    ExperimentArtifact,
    ExperimentCheck,
    ExperimentRecipe,
    ExperimentResult,
    Measurement,
    RecipeDisposition,
    SealedExecution,
)
from paper_agent.techscout.experiments.registry import (
    ExperimentRecipeRegistry,
    UnsupportedExperimentRecipeError,
)
from paper_agent.techscout.sandbox.types import ExecutionStatus, SandboxResult


class ExperimentCheckRunner(Protocol):
    def run_check(
        self,
        recipe: ExperimentRecipe,
        check: ExperimentCheck,
        request: ExecutionRequest,
        *,
        run_workspace: Path,
        timeout_seconds: float,
        cancel_requested: Callable[[], bool],
    ) -> SandboxResult: ...


class CancellationToken:
    """Thread-safe cooperative cancellation observed at the runner seam."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class IdempotencyConflictError(RuntimeError):
    """An idempotency key was reused with a different immutable request."""


class ExecutionInProgressError(RuntimeError):
    """Another caller owns the same workspace-scoped execution key."""


class InvalidExecutionSealError(RuntimeError):
    """A stored terminal result no longer matches its content seal."""


class ExperimentEngine:
    """Execute a Recipe once and always publish one sealed terminal contract."""

    def __init__(
        self,
        check_runner: ExperimentCheckRunner,
        *,
        registry: ExperimentRecipeRegistry | None = None,
        secrets: tuple[str, ...] = (),
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        remove_tree: Callable[[Path], None] | None = None,
    ) -> None:
        self._check_runner = check_runner
        self._registry = registry or ExperimentRecipeRegistry()
        self._secrets = secrets
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._remove_tree = remove_tree or _remove_tree

    def execute(
        self,
        request: ExecutionRequest,
        *,
        run_workspace: Path,
        cancellation: CancellationToken | None = None,
    ) -> SealedExecution:
        root = run_workspace.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("run workspace must be a directory")
        request_sha256 = _sha256_model(request)
        execution_dir = _execution_dir(root, request.idempotency_key)
        result_path = execution_dir / "result.json"
        existing = self._load_existing(result_path, request_sha256)
        if existing is not None:
            return existing
        self._raise_existing_denial(
            execution_dir / "denial.json",
            request_sha256,
        )

        execution_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = execution_dir / "execution.lock"
        try:
            lock_path.open("x", encoding="utf-8").close()
        except FileExistsError as exc:
            existing = self._load_existing(result_path, request_sha256)
            if existing is not None:
                return existing
            raise ExecutionInProgressError(
                "execution with this idempotency key is already in progress"
            ) from exc

        try:
            try:
                recipe = self._registry.get(request.recipe_id)
            except UnsupportedExperimentRecipeError:
                denial = {
                    "schema_version": "1.0",
                    "execution_id": request.execution_id,
                    "subject_id": request.subject_id,
                    "recipe_id": request.recipe_id,
                    "decision": "denied",
                    "failure_code": FailureCode.POC_RECIPE_UNSUPPORTED.value,
                    "reason": "Recipe is not reviewed and cannot execute.",
                    "request_sha256": request_sha256,
                    "runner_invoked": False,
                }
                _write_json_atomic(
                    execution_dir / "denial.json",
                    {
                        "denial": denial,
                        "denial_sha256": _sha256_model(denial),
                    },
                )
                raise
            sealed = self._execute_once(
                request,
                recipe,
                root=root,
                execution_dir=execution_dir,
                cancellation=cancellation or CancellationToken(),
            )
            try:
                _write_json_atomic(
                    result_path,
                    _stored_payload(request_sha256, sealed),
                )
            except OSError:
                if not sealed.result.cleanup_complete:
                    return sealed
                reason = "The Experiment terminal contract could not be persisted."
                failed_result = sealed.result.model_copy(
                    update={
                        "terminal_status": ExecutionTerminalStatus.FAILED,
                        "terminal_reason": reason,
                        "failure": ExecutionFailure(
                            code=FailureCode.ARTIFACT_PUBLISH_FAILED,
                            message=reason,
                        ),
                    }
                )
                return SealedExecution(
                    result=failed_result,
                    result_sha256=_sha256_model(failed_result),
                )
            return sealed
        finally:
            lock_path.unlink(missing_ok=True)

    def _execute_once(
        self,
        request: ExecutionRequest,
        recipe: ExperimentRecipe,
        *,
        root: Path,
        execution_dir: Path,
        cancellation: CancellationToken,
    ) -> SealedExecution:
        started_at = self._clock()
        started_monotonic = self._monotonic()
        recipe_sha256 = _sha256_model(recipe)
        work_dir = execution_dir / "work"
        work_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
        artifacts: list[ExperimentArtifact] = []
        measurements: list[Measurement] = []
        check_results: list[CheckResult] = []
        artifact_bytes = 0
        terminal_status = ExecutionTerminalStatus.SUCCEEDED
        terminal_reason = "Every reviewed offline Check passed."
        failure: ExecutionFailure | None = None

        if recipe.disposition is RecipeDisposition.RESEARCH_ONLY:
            terminal_status = ExecutionTerminalStatus.RESEARCH_ONLY
            terminal_reason = recipe.research_only_reason or "Recipe is research-only."
        elif len(recipe.checks) > request.budget.max_checks:
            terminal_status = ExecutionTerminalStatus.BUDGET_EXHAUSTED
            terminal_reason = "Recipe Check count exceeds the Execution Budget."
            failure = ExecutionFailure(
                code=FailureCode.BUDGET_EXHAUSTED,
                message=terminal_reason,
            )
        else:
            for check in recipe.checks:
                terminal = self._pre_check_terminal(
                    request,
                    check,
                    cancellation,
                    started_monotonic,
                )
                if terminal is not None:
                    terminal_status, terminal_reason, failure = terminal
                    break
                remaining = request.budget.wall_timeout_seconds - (
                    self._monotonic() - started_monotonic
                )
                timeout_seconds = min(request.budget.check_timeout_seconds, remaining)
                try:
                    sandbox_result = self._check_runner.run_check(
                        recipe,
                        check,
                        request,
                        run_workspace=work_dir,
                        timeout_seconds=timeout_seconds,
                        cancel_requested=cancellation.is_cancelled,
                    )
                except (
                    Exception
                ) as exc:  # the seam must fail closed into a sealed result
                    terminal_status = ExecutionTerminalStatus.FAILED
                    terminal_reason = (
                        "Experiment policy denied an unauthorized network, filesystem, "
                        "or resource request."
                        if isinstance(exc, (PermissionError, ValueError))
                        else "The sandbox adapter failed before returning a result."
                    )
                    failure = ExecutionFailure(
                        code=(
                            FailureCode.UNSAFE_REQUEST
                            if isinstance(exc, (PermissionError, ValueError))
                            else FailureCode.TOOL_UNAVAILABLE
                        ),
                        message=terminal_reason,
                        failed_check_id=check.check_id,
                    )
                    break

                sandbox_result = self._apply_post_run_controls(
                    sandbox_result,
                    request,
                    cancellation,
                    started_monotonic,
                )
                try:
                    artifact, payload_size = self._persist_check_artifact(
                        root,
                        execution_dir,
                        check,
                        sandbox_result,
                    )
                except OSError:
                    terminal_status = ExecutionTerminalStatus.FAILED
                    terminal_reason = (
                        "The Experiment Check Artifact could not be sealed."
                    )
                    failure = ExecutionFailure(
                        code=FailureCode.ARTIFACT_PUBLISH_FAILED,
                        message=terminal_reason,
                        failed_check_id=check.check_id,
                    )
                    break
                if artifact_bytes + payload_size > request.budget.max_artifact_bytes:
                    _absolute_artifact_path(root, artifact).unlink(missing_ok=True)
                    terminal_status = ExecutionTerminalStatus.BUDGET_EXHAUSTED
                    terminal_reason = (
                        "Experiment Artifact bytes exceed the Execution Budget."
                    )
                    failure = ExecutionFailure(
                        code=FailureCode.BUDGET_EXHAUSTED,
                        message=terminal_reason,
                        failed_check_id=check.check_id,
                    )
                    break

                next_measurements = _measurements(check, artifact, sandbox_result)
                if (
                    len(measurements) + len(next_measurements)
                    > request.budget.max_measurements
                ):
                    _absolute_artifact_path(root, artifact).unlink(missing_ok=True)
                    terminal_status = ExecutionTerminalStatus.BUDGET_EXHAUSTED
                    terminal_reason = "Measurement count exceeds the Execution Budget."
                    failure = ExecutionFailure(
                        code=FailureCode.BUDGET_EXHAUSTED,
                        message=terminal_reason,
                        failed_check_id=check.check_id,
                    )
                    break

                artifacts.append(artifact)
                artifact_bytes += payload_size
                measurements.extend(next_measurements)
                check_status = _check_status(sandbox_result.status)
                check_result = CheckResult(
                    check_id=check.check_id,
                    status=check_status,
                    duration_ms=sandbox_result.duration_ms,
                    artifact_ids=(artifact.artifact_id,),
                    measurement_ids=tuple(
                        item.measurement_id for item in next_measurements
                    ),
                    failure_code=sandbox_result.failure_code,
                )
                check_results.append(check_result)
                if check_status is not CheckStatus.PASSED:
                    terminal_status, terminal_reason = _terminal_for_check(check_status)
                    failure = ExecutionFailure(
                        code=sandbox_result.failure_code
                        or FailureCode.POC_NONZERO_EXIT,
                        message=terminal_reason,
                        failed_check_id=check.check_id,
                    )
                    break

        cleanup_complete = True
        try:
            self._remove_tree(work_dir)
        except OSError:
            cleanup_complete = False
            terminal_status = ExecutionTerminalStatus.FAILED
            terminal_reason = "The isolated Experiment workspace could not be cleaned."
            failure = ExecutionFailure(
                code=FailureCode.EXPERIMENT_CLEANUP_FAILED,
                message=terminal_reason,
            )

        result = ExperimentResult(
            execution_id=request.execution_id,
            subject_id=request.subject_id,
            recipe_id=recipe.recipe_id,
            recipe_version=recipe.version,
            recipe_sha256=recipe_sha256,
            terminal_status=terminal_status,
            terminal_reason=terminal_reason,
            started_at=started_at,
            finished_at=self._clock(),
            budget=request.budget,
            check_results=tuple(check_results),
            artifacts=tuple(artifacts),
            measurements=tuple(measurements),
            failure=failure,
            cleanup_complete=cleanup_complete,
        )
        return SealedExecution(result=result, result_sha256=_sha256_model(result))

    def _pre_check_terminal(
        self,
        request: ExecutionRequest,
        check: ExperimentCheck,
        cancellation: CancellationToken,
        started_monotonic: float,
    ) -> tuple[ExecutionTerminalStatus, str, ExecutionFailure] | None:
        if cancellation.is_cancelled():
            reason = "Experiment cancellation was requested."
            return (
                ExecutionTerminalStatus.CANCELLED,
                reason,
                ExecutionFailure(
                    code=FailureCode.EXPERIMENT_CANCELLED,
                    message=reason,
                    failed_check_id=check.check_id,
                ),
            )
        if self._monotonic() - started_monotonic >= request.budget.wall_timeout_seconds:
            reason = "Experiment wall-clock Execution Budget was exhausted."
            return (
                ExecutionTerminalStatus.TIMED_OUT,
                reason,
                ExecutionFailure(
                    code=FailureCode.DEADLINE_EXCEEDED,
                    message=reason,
                    failed_check_id=check.check_id,
                ),
            )
        return None

    def _apply_post_run_controls(
        self,
        result: SandboxResult,
        request: ExecutionRequest,
        cancellation: CancellationToken,
        started_monotonic: float,
    ) -> SandboxResult:
        if (
            cancellation.is_cancelled()
            and result.status is not ExecutionStatus.CANCELLED
        ):
            return result.model_copy(
                update={
                    "status": ExecutionStatus.CANCELLED,
                    "exit_code": None,
                    "timed_out": False,
                    "failure_code": FailureCode.EXPERIMENT_CANCELLED,
                }
            )
        if (
            self._monotonic() - started_monotonic >= request.budget.wall_timeout_seconds
            and result.status is not ExecutionStatus.TIMED_OUT
        ):
            return result.model_copy(
                update={
                    "status": ExecutionStatus.TIMED_OUT,
                    "exit_code": None,
                    "timed_out": True,
                    "failure_code": FailureCode.POC_TIMEOUT,
                }
            )
        return result

    def _persist_check_artifact(
        self,
        root: Path,
        execution_dir: Path,
        check: ExperimentCheck,
        result: SandboxResult,
    ) -> tuple[ExperimentArtifact, int]:
        command_bytes = _canonical_json_bytes(result.command.model_dump(mode="json"))
        payload = sanitize_bounded_event_data(
            {
                "schema_version": "1.0",
                "check_id": check.check_id,
                "status": result.status.value,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
                "failure_code": (
                    result.failure_code.value
                    if result.failure_code is not None
                    else None
                ),
                "command_sha256": hashlib.sha256(command_bytes).hexdigest(),
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
            secrets=self._secrets,
            max_string_length=2_048,
        )
        payload_bytes = _canonical_json_bytes(payload)
        digest = hashlib.sha256(payload_bytes).hexdigest()
        artifact_dir = execution_dir / "artifacts"
        artifact_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
        artifact_path = artifact_dir / f"{digest}.json"
        if artifact_path.exists():
            if artifact_path.read_bytes() != payload_bytes:
                raise OSError("Experiment Artifact digest collision")
        else:
            _write_bytes_atomic(artifact_path, payload_bytes)
        relative_path = artifact_path.relative_to(root).as_posix()
        return (
            ExperimentArtifact(
                artifact_id=f"experiment-artifact:{digest}",
                check_id=check.check_id,
                kind="sandbox-check-record",
                sha256=digest,
                size_bytes=len(payload_bytes),
                relative_path=relative_path,
            ),
            len(payload_bytes),
        )

    def _load_existing(
        self,
        path: Path,
        request_sha256: str,
    ) -> SealedExecution | None:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("request_sha256") != request_sha256:
            raise IdempotencyConflictError(
                "idempotency key is already sealed for a different request"
            )
        sealed = SealedExecution.model_validate_json(
            _canonical_json_bytes(payload["sealed_execution"])
        )
        if _sha256_model(sealed.result) != sealed.result_sha256:
            raise InvalidExecutionSealError(
                "stored Experiment terminal seal is invalid"
            )
        return sealed

    def _raise_existing_denial(self, path: Path, request_sha256: str) -> None:
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        denial = payload.get("denial")
        if not isinstance(denial, dict) or payload.get("denial_sha256") != _sha256_model(
            denial
        ):
            raise InvalidExecutionSealError(
                "stored Experiment denial seal is invalid"
            )
        if denial.get("request_sha256") != request_sha256:
            raise IdempotencyConflictError(
                "idempotency key is already denied for a different request"
            )
        raise UnsupportedExperimentRecipeError(
            "Recipe is not reviewed and cannot execute"
        )


def _execution_dir(root: Path, idempotency_key: str) -> Path:
    key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    parent = root / "experiment-executions"
    parent.mkdir(mode=0o700, parents=False, exist_ok=True)
    execution_dir = parent / key_digest
    resolved_parent = parent.resolve(strict=True)
    resolved_execution = execution_dir.resolve(strict=False)
    if resolved_parent not in resolved_execution.parents:
        raise PermissionError(
            "Experiment execution directory escaped the run workspace"
        )
    return resolved_execution


def _measurements(
    check: ExperimentCheck,
    artifact: ExperimentArtifact,
    result: SandboxResult,
) -> tuple[Measurement, ...]:
    prefix = hashlib.sha256(
        f"{check.check_id}:{artifact.sha256}".encode("utf-8")
    ).hexdigest()[:24]
    values = [
        Measurement(
            measurement_id=f"measurement:{prefix}:duration-ms",
            check_id=check.check_id,
            name="duration",
            value=result.duration_ms,
            unit="ms",
            artifact_id=artifact.artifact_id,
        )
    ]
    if result.exit_code is not None:
        values.append(
            Measurement(
                measurement_id=f"measurement:{prefix}:exit-code",
                check_id=check.check_id,
                name="exit code",
                value=result.exit_code,
                unit="integer",
                artifact_id=artifact.artifact_id,
            )
        )
    return tuple(values)


def _check_status(status: ExecutionStatus) -> CheckStatus:
    return {
        ExecutionStatus.SUCCEEDED: CheckStatus.PASSED,
        ExecutionStatus.FAILED: CheckStatus.FAILED,
        ExecutionStatus.UNAVAILABLE: CheckStatus.FAILED,
        ExecutionStatus.TIMED_OUT: CheckStatus.TIMED_OUT,
        ExecutionStatus.CANCELLED: CheckStatus.CANCELLED,
    }[status]


def _terminal_for_check(
    status: CheckStatus,
) -> tuple[ExecutionTerminalStatus, str]:
    if status is CheckStatus.TIMED_OUT:
        return (
            ExecutionTerminalStatus.TIMED_OUT,
            "A reviewed Experiment Check timed out.",
        )
    if status is CheckStatus.CANCELLED:
        return (
            ExecutionTerminalStatus.CANCELLED,
            "Experiment cancellation was requested.",
        )
    return ExecutionTerminalStatus.FAILED, "A reviewed Experiment Check failed."


def _absolute_artifact_path(root: Path, artifact: ExperimentArtifact) -> Path:
    path = (root / artifact.relative_path).resolve(strict=False)
    if root != path and root not in path.parents:
        raise PermissionError("Experiment Artifact escaped the run workspace")
    return path


def _sha256_model(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    _write_bytes_atomic(path, _canonical_json_bytes(payload))


def _stored_payload(request_sha256: str, sealed: SealedExecution) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "request_sha256": request_sha256,
        "sealed_execution": sealed.model_dump(mode="json"),
    }


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temporary.write_bytes(payload)
        temporary.chmod(0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path)
