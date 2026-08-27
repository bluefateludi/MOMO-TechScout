"""Closed-recipe orchestration for real Docker PoC execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator
from typing_extensions import Self

from paper_agent.observability.sanitize import sanitize_bounded_event_data
from paper_agent.techscout.errors import FailureCode, StableId
from paper_agent.techscout.models import (
    Candidate,
    PocArtifact,
    PocPlan,
    PocResult,
    PocStatus,
    TechScoutModel,
)
from paper_agent.techscout.recovery.classifier import FailureClassifier
from paper_agent.techscout.sandbox.compiler import PocCompiler
from paper_agent.techscout.sandbox.recipes import RecipeRegistry, UnsupportedRecipeError
from paper_agent.techscout.sandbox.runner import SandboxRunner
from paper_agent.techscout.sandbox.types import (
    CompilationDisposition,
    CompiledCommand,
    ExecutionStatus,
    PocStage,
    SandboxResult,
)
from paper_agent.techscout.tools.contracts import SmokeTestInput, SmokeTestOutput


_STAGES = (PocStage.INSTALL, PocStage.TEST)
_DOCKER_UNAVAILABLE_MARKERS = (
    "cannot connect to the docker daemon",
    "error during connect",
    "is the docker daemon running",
    "open //./pipe/docker",
)


class PocStageAttempt(TechScoutModel):
    """One caller-authorized recovery attempt for a single PoC stage."""

    poc_plan_id: StableId
    candidate_id: StableId
    recipe_id: StableId
    stage: PocStage
    attempt: Literal[2] = 2
    status: PocStatus
    exit_code: int | None = None
    timed_out: bool
    duration_ms: int = Field(ge=0)
    artifact: PocArtifact | None = None
    failure_code: FailureCode | None = None

    @model_validator(mode="after")
    def status_is_consistent(self) -> Self:
        if self.status is PocStatus.PASSED:
            if (
                self.exit_code != 0
                or self.timed_out
                or self.failure_code is not None
                or self.artifact is None
            ):
                raise ValueError("passed stage attempt requires exit zero and an artifact")
        elif self.status is PocStatus.TIMED_OUT:
            if not self.timed_out or self.failure_code is not FailureCode.POC_TIMEOUT:
                raise ValueError("timed out stage attempt requires poc_timeout")
        elif self.status is PocStatus.FAILED and self.failure_code is None:
            raise ValueError("failed stage attempt requires a failure code")
        elif self.status is PocStatus.RESEARCH_ONLY and (
            self.failure_code is not FailureCode.POC_RECIPE_UNSUPPORTED
        ):
            raise ValueError("research-only stage attempt requires unsupported recipe")
        return self


class RealPocService:
    """Compile and execute one reviewed PoC attempt without owning retry policy."""

    def __init__(
        self,
        runner: SandboxRunner,
        *,
        registry: RecipeRegistry | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self._runner = runner
        self._registry = registry or RecipeRegistry()
        self._compiler = PocCompiler(self._registry)
        self._classifier = FailureClassifier()
        self._secrets = secrets

    def execute(
        self,
        plan: PocPlan,
        candidate: Candidate,
        *,
        run_workspace: Path,
        attempt: int = 1,
    ) -> PocResult:
        """Run the complete initial PoC; partial recovery uses ``rerun_stage``."""
        if attempt not in {1, 2}:
            raise ValueError("attempt must be 1 or 2")
        if attempt != 1:
            raise ValueError("execute produces only complete initial PoC results")
        workspace = run_workspace.resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("run workspace must be a directory")
        result_id = _result_id(plan, attempt)

        commands: list[CompiledCommand] = []
        for stage in _STAGES:
            compilation = self._compiler.compile_or_research_only(plan, candidate, stage)
            if compilation.disposition is CompilationDisposition.RESEARCH_ONLY:
                return self._finalize(
                    result_id=result_id,
                    plan=plan,
                    candidate=candidate,
                    workspace=workspace,
                    attempt=attempt,
                    status=PocStatus.RESEARCH_ONLY,
                    failure_code=FailureCode.POC_RECIPE_UNSUPPORTED,
                    exit_code=None,
                    timed_out=False,
                    duration_ms=0,
                    resolved_version=None,
                    stages=(),
                    reason=compilation.reason,
                )
            if compilation.command is None:  # pragma: no cover - guarded by model
                raise RuntimeError("executable compilation omitted its command")
            commands.append(compilation.command)

        stage_records: list[dict[str, object]] = []
        duration_ms = 0
        for command in commands:
            try:
                sandbox_result = self._runner.run(command, workspace)
            except PermissionError as exc:
                stage_records.append(_exception_record(command, exc, self._secrets))
                return self._finalize(
                    result_id=result_id,
                    plan=plan,
                    candidate=candidate,
                    workspace=workspace,
                    attempt=attempt,
                    status=PocStatus.FAILED,
                    failure_code=FailureCode.TOOL_UNAVAILABLE,
                    exit_code=None,
                    timed_out=False,
                    duration_ms=duration_ms,
                    resolved_version=None,
                    stages=tuple(stage_records),
                    reason="The reviewed install network or Docker boundary is unavailable.",
                )
            except (OSError, ValueError) as exc:
                stage_records.append(_exception_record(command, exc, self._secrets))
                return self._finalize(
                    result_id=result_id,
                    plan=plan,
                    candidate=candidate,
                    workspace=workspace,
                    attempt=attempt,
                    status=PocStatus.FAILED,
                    failure_code=(
                        FailureCode.TOOL_UNAVAILABLE
                        if isinstance(exc, OSError)
                        else FailureCode.UNSAFE_REQUEST
                    ),
                    exit_code=None,
                    timed_out=False,
                    duration_ms=duration_ms,
                    resolved_version=None,
                    stages=tuple(stage_records),
                    reason="The reviewed Docker boundary rejected or could not run the PoC.",
                )

            duration_ms += sandbox_result.duration_ms
            stage_records.append(_stage_record(sandbox_result, self._secrets))
            if sandbox_result.status is not ExecutionStatus.SUCCEEDED:
                docker_unavailable = _docker_daemon_unavailable(sandbox_result)
                failure = (
                    None
                    if docker_unavailable
                    else self._classifier.classify_sandbox(
                        sandbox_result,
                        failure_id=f"failure:{result_id}:sandbox",
                        attempt=attempt,
                    )
                )
                if docker_unavailable:
                    failure_code = FailureCode.TOOL_UNAVAILABLE
                    reason = "The Docker daemon is unavailable."
                else:
                    failure_code = (
                        failure.code
                        if failure is not None
                        else sandbox_result.failure_code or FailureCode.POC_NONZERO_EXIT
                    )
                    reason = failure.message if failure is not None else "The PoC failed."
                return self._finalize(
                    result_id=result_id,
                    plan=plan,
                    candidate=candidate,
                    workspace=workspace,
                    attempt=attempt,
                    status=(
                        PocStatus.TIMED_OUT
                        if sandbox_result.status is ExecutionStatus.TIMED_OUT
                        else PocStatus.FAILED
                    ),
                    failure_code=failure_code,
                    exit_code=sandbox_result.exit_code,
                    timed_out=sandbox_result.timed_out,
                    duration_ms=duration_ms,
                    resolved_version=None,
                    stages=tuple(stage_records),
                    reason=reason,
                )

        recipe = self._registry.get(plan.recipe_id)
        return self._finalize(
            result_id=result_id,
            plan=plan,
            candidate=candidate,
            workspace=workspace,
            attempt=attempt,
            status=PocStatus.PASSED,
            failure_code=None,
            exit_code=0,
            timed_out=False,
            duration_ms=duration_ms,
            resolved_version=recipe.package_version,
            stages=tuple(stage_records),
            reason="Both reviewed PoC stages completed successfully.",
        )

    def rerun_stage(
        self,
        plan: PocPlan,
        candidate: Candidate,
        *,
        run_workspace: Path,
        stage: PocStage,
    ) -> PocStageAttempt:
        """Run only the caller-selected failed stage as recovery attempt two."""
        workspace = run_workspace.resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("run workspace must be a directory")
        compilation = self._compiler.compile_or_research_only(plan, candidate, stage)
        if compilation.disposition is CompilationDisposition.RESEARCH_ONLY:
            status = PocStatus.RESEARCH_ONLY
            failure_code = FailureCode.POC_RECIPE_UNSUPPORTED
            exit_code = None
            timed_out = False
            duration_ms = 0
            stage_records: tuple[dict[str, object], ...] = ()
            reason = compilation.reason
        else:
            command = compilation.command
            if command is None:  # pragma: no cover - guarded by model
                raise RuntimeError("executable compilation omitted its command")
            try:
                sandbox_result = self._runner.run(command, workspace)
            except PermissionError as exc:
                status = PocStatus.FAILED
                failure_code = FailureCode.TOOL_UNAVAILABLE
                exit_code = None
                timed_out = False
                duration_ms = 0
                stage_records = (_exception_record(command, exc, self._secrets),)
                reason = "The reviewed install network or Docker boundary is unavailable."
            except (OSError, ValueError) as exc:
                status = PocStatus.FAILED
                failure_code = (
                    FailureCode.TOOL_UNAVAILABLE
                    if isinstance(exc, OSError)
                    else FailureCode.UNSAFE_REQUEST
                )
                exit_code = None
                timed_out = False
                duration_ms = 0
                stage_records = (_exception_record(command, exc, self._secrets),)
                reason = "The reviewed Docker boundary rejected or could not run the stage."
            else:
                status, failure_code, reason = self._normalized_status(
                    sandbox_result,
                    failure_id=f"failure:{_result_id(plan, 2)}:sandbox",
                    attempt=2,
                )
                exit_code = sandbox_result.exit_code
                timed_out = sandbox_result.timed_out
                duration_ms = sandbox_result.duration_ms
                stage_records = (_stage_record(sandbox_result, self._secrets),)

        payload = sanitize_bounded_event_data(
            {
                "schema_version": "1.0",
                "poc_plan_id": plan.poc_plan_id,
                "candidate_id": candidate.candidate_id,
                "recipe_id": plan.recipe_id,
                "attempt": 2,
                "rerun_stage": stage.value,
                "status": status.value,
                "failure_code": failure_code.value if failure_code is not None else None,
                "duration_ms": duration_ms,
                "reason": reason,
                "stages": stage_records,
            },
            secrets=self._secrets,
            max_string_length=2_048,
        )
        try:
            artifact = _write_artifact(
                workspace,
                payload,
                kind="real-docker-poc-stage-attempt",
            )
        except OSError:
            status = PocStatus.FAILED
            failure_code = FailureCode.POC_ARTIFACT_INVALID
            exit_code = None
            timed_out = False
            artifact = None
        return PocStageAttempt(
            poc_plan_id=plan.poc_plan_id,
            candidate_id=candidate.candidate_id,
            recipe_id=plan.recipe_id or "recipe:unsupported",
            stage=stage,
            attempt=2,
            status=status,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            artifact=artifact,
            failure_code=failure_code,
        )

    def _normalized_status(
        self,
        result: SandboxResult,
        *,
        failure_id: str,
        attempt: int,
    ) -> tuple[PocStatus, FailureCode | None, str]:
        if result.status is ExecutionStatus.SUCCEEDED:
            return PocStatus.PASSED, None, "The reviewed PoC stage completed successfully."
        if _docker_daemon_unavailable(result):
            return PocStatus.FAILED, FailureCode.TOOL_UNAVAILABLE, "The Docker daemon is unavailable."
        failure = self._classifier.classify_sandbox(
            result,
            failure_id=failure_id,
            attempt=attempt,
        )
        code = failure.code if failure is not None else result.failure_code
        status = (
            PocStatus.TIMED_OUT
            if result.status is ExecutionStatus.TIMED_OUT
            else PocStatus.FAILED
        )
        return status, code or FailureCode.POC_NONZERO_EXIT, (
            failure.message if failure is not None else "The PoC stage failed."
        )

    def _finalize(
        self,
        *,
        result_id: str,
        plan: PocPlan,
        candidate: Candidate,
        workspace: Path,
        attempt: int,
        status: PocStatus,
        failure_code: FailureCode | None,
        exit_code: int | None,
        timed_out: bool,
        duration_ms: int,
        resolved_version: str | None,
        stages: tuple[dict[str, object], ...],
        reason: str,
    ) -> PocResult:
        payload = sanitize_bounded_event_data(
            {
                "schema_version": "1.0",
                "poc_result_id": result_id,
                "poc_plan_id": plan.poc_plan_id,
                "candidate_id": candidate.candidate_id,
                "recipe_id": plan.recipe_id,
                "attempt": attempt,
                "status": status.value,
                "failure_code": failure_code.value if failure_code is not None else None,
                "resolved_version": resolved_version,
                "duration_ms": duration_ms,
                "reason": reason,
                "stages": stages,
            },
            secrets=self._secrets,
            max_string_length=2_048,
        )
        try:
            artifact = _write_artifact(workspace, payload)
        except OSError:
            return PocResult(
                poc_result_id=result_id,
                poc_plan_id=plan.poc_plan_id,
                candidate_id=candidate.candidate_id,
                status=PocStatus.FAILED,
                exit_code=None,
                timed_out=False,
                duration_ms=duration_ms,
                failure_code=FailureCode.POC_ARTIFACT_INVALID,
            )
        return PocResult(
            poc_result_id=result_id,
            poc_plan_id=plan.poc_plan_id,
            candidate_id=candidate.candidate_id,
            status=status,
            resolved_version=resolved_version,
            exit_code=exit_code,
            timed_out=timed_out,
            duration_ms=duration_ms,
            artifacts=(artifact,),
            failure_code=failure_code,
        )


class RealPocAdapter:
    """Project ``RealPocService`` into the existing local MCP adapter contract."""

    def __init__(
        self,
        service: RealPocService,
        *,
        run_workspace: Path,
        registry: RecipeRegistry | None = None,
    ) -> None:
        self._service = service
        self._run_workspace = run_workspace
        self._registry = registry or RecipeRegistry()

    def run_smoke_test(
        self,
        request: SmokeTestInput,
    ) -> SmokeTestOutput:
        trusted = False
        try:
            recipe = self._registry.get(request.recipe_id)
            if request.candidate_id in recipe.candidate_ids:
                name = recipe.package_name
                package_name: str | None = recipe.package_name
                trusted = True
            else:
                name = "unsupported-candidate"
                package_name = None
        except UnsupportedRecipeError:
            name = "unsupported-candidate"
            package_name = None
        identity = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        plan = PocPlan(
            poc_plan_id=f"poc-plan:real-{identity}",
            candidate_id=request.candidate_id,
            recipe_id=request.recipe_id,
            trusted=trusted,
            checks=request.checks,
        )
        candidate = Candidate(
            candidate_id=request.candidate_id,
            name=name,
            package_name=package_name,
            requested_version=request.requested_version,
        )
        result = self._service.execute(
            plan,
            candidate,
            run_workspace=self._run_workspace,
        )
        status = {
            PocStatus.PASSED: "passed",
            PocStatus.FAILED: "failed",
            PocStatus.TIMED_OUT: "timed_out",
            PocStatus.RESEARCH_ONLY: "research_only",
        }[result.status]
        artifact_sha256 = result.artifacts[0].sha256 if result.artifacts else None
        return SmokeTestOutput(
            candidate_id=result.candidate_id,
            recipe_id=request.recipe_id,
            status=status,
            resolved_version=result.resolved_version,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            artifact_sha256=artifact_sha256,
            failure_code=result.failure_code,
        )


def _result_id(plan: PocPlan, attempt: int) -> str:
    candidate = plan.candidate_id.split(":", 1)[-1]
    plan_digest = hashlib.sha256(plan.poc_plan_id.encode("utf-8")).hexdigest()[:12]
    return f"poc-result:{candidate}-{plan_digest}:attempt-{attempt}"


def _command_identity(command: CompiledCommand) -> dict[str, object]:
    argv_bytes = json.dumps(
        command.argv,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return {
        "stage": command.stage.value,
        "recipe_id": command.recipe_id,
        "image": command.image,
        "network_access": command.network_access.value,
        "argv_sha256": hashlib.sha256(argv_bytes).hexdigest(),
    }


def _stage_record(
    result: SandboxResult,
    secrets: tuple[str, ...],
) -> dict[str, object]:
    return sanitize_bounded_event_data(
        {
            **_command_identity(result.command),
            "status": result.status.value,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "duration_ms": result.duration_ms,
            "failure_code": (
                result.failure_code.value if result.failure_code is not None else None
            ),
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
        secrets=secrets,
        max_string_length=2_048,
    )


def _docker_daemon_unavailable(result: SandboxResult) -> bool:
    if result.status is ExecutionStatus.UNAVAILABLE:
        return True
    output = f"{result.stdout}\n{result.stderr}".casefold()
    return any(marker in output for marker in _DOCKER_UNAVAILABLE_MARKERS)


def _exception_record(
    command: CompiledCommand,
    error: Exception,
    secrets: tuple[str, ...],
) -> dict[str, object]:
    return sanitize_bounded_event_data(
        {
            **_command_identity(command),
            "status": "unavailable",
            "error": str(error),
        },
        secrets=secrets,
        max_string_length=2_048,
    )


def _write_artifact(
    workspace: Path,
    payload: dict[str, object],
    *,
    kind: str = "real-docker-poc",
) -> PocArtifact:
    artifact_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    artifact_dir = workspace / "poc-artifacts"
    artifact_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
    resolved_dir = artifact_dir.resolve(strict=True)
    if workspace != resolved_dir and workspace not in resolved_dir.parents:
        raise PermissionError("PoC artifact directory escaped the run workspace")
    artifact_path = resolved_dir / f"{digest}.json"
    if artifact_path.exists():
        if artifact_path.read_bytes() != artifact_bytes:
            raise OSError("PoC artifact digest collision")
    else:
        temporary = resolved_dir / f".{digest}.{uuid4().hex}.tmp"
        try:
            temporary.write_bytes(artifact_bytes)
            temporary.chmod(0o600)
            temporary.replace(artifact_path)
        finally:
            temporary.unlink(missing_ok=True)
    return PocArtifact(
        artifact_id=f"poc-artifact:{digest}",
        kind=kind,
        sha256=digest,
        size_bytes=len(artifact_bytes),
    )
