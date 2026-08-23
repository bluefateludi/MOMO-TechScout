import hashlib
import json
from pathlib import Path

import pytest

import paper_agent.techscout.experiments.engine as engine_module
from paper_agent.techscout.errors import FailureCode
from paper_agent.techscout.experiments import (
    OFFLINE_RECIPE_ID,
    RESEARCH_ONLY_RECIPE_ID,
    CancellationToken,
    ExecutionBudget,
    ExecutionRequest,
    ExecutionTerminalStatus,
    ExperimentEngine,
    ExperimentRecipeRegistry,
    IdempotencyConflictError,
    InvalidExecutionSealError,
    SandboxExperimentAdapter,
)
from paper_agent.techscout.sandbox.runner import FakeSandboxRunner
from paper_agent.techscout.sandbox.types import (
    CompiledCommand,
    ExecutionStatus,
    NetworkAccess,
    PocStage,
    SandboxLimits,
    SandboxResult,
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "run"
    workspace.mkdir()
    return workspace


def _request(
    *,
    recipe_id: str = OFFLINE_RECIPE_ID,
    idempotency_key: str = "idempotency:offline-001",
    budget: ExecutionBudget | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        execution_id="experiment:offline-001",
        subject_id="subject:python-runtime",
        recipe_id=recipe_id,
        idempotency_key=idempotency_key,
        budget=budget or ExecutionBudget(),
    )


def _command(request: ExecutionRequest, check_index: int) -> CompiledCommand:
    recipe = ExperimentRecipeRegistry().get(request.recipe_id)
    check = recipe.checks[check_index]
    return CompiledCommand(
        poc_plan_id=request.execution_id,
        candidate_id=request.subject_id,
        recipe_id=recipe.recipe_id,
        stage=PocStage.TEST,
        argv=check.command.argv,
        image=check.command.image,
        network_access=NetworkAccess.NONE,
    )


def _result(
    request: ExecutionRequest,
    check_index: int,
    *,
    status: ExecutionStatus = ExecutionStatus.SUCCEEDED,
    stdout: str = "ok",
) -> SandboxResult:
    failure_code = {
        ExecutionStatus.SUCCEEDED: None,
        ExecutionStatus.FAILED: FailureCode.POC_NONZERO_EXIT,
        ExecutionStatus.TIMED_OUT: FailureCode.POC_TIMEOUT,
        ExecutionStatus.CANCELLED: FailureCode.EXPERIMENT_CANCELLED,
        ExecutionStatus.UNAVAILABLE: FailureCode.TOOL_UNAVAILABLE,
    }[status]
    return SandboxResult(
        command=_command(request, check_index),
        status=status,
        exit_code=0 if status is ExecutionStatus.SUCCEEDED else None,
        timed_out=status is ExecutionStatus.TIMED_OUT,
        duration_ms=10 + check_index,
        stdout=stdout,
        failure_code=failure_code,
    )


def _engine(runner: FakeSandboxRunner, **kwargs) -> ExperimentEngine:
    return ExperimentEngine(SandboxExperimentAdapter(runner), **kwargs)


def test_offline_recipe_runs_through_existing_sandbox_seam_and_seals_audit(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    request = _request()
    runner = FakeSandboxRunner()
    runner.queue(_result(request, 0, stdout="runtime ok; secret-value"))
    runner.queue(_result(request, 1, stdout="json ok"))

    sealed = _engine(runner, secrets=("secret-value",)).execute(
        request,
        run_workspace=workspace,
    )

    result = sealed.result
    assert result.terminal_status is ExecutionTerminalStatus.SUCCEEDED
    assert [item.check_id for item in result.check_results] == [
        "check:python-runtime-version",
        "check:stdlib-json-roundtrip",
    ]
    assert len(result.artifacts) == 2
    assert len(result.measurements) == 4
    assert len(runner.calls) == 2
    assert all(call[0].network_access is NetworkAccess.NONE for call in runner.calls)
    assert all(not (call[1] / "nested").exists() for call in runner.calls)
    assert not any(workspace.glob("experiment-executions/*/work"))
    for artifact in result.artifacts:
        path = workspace / artifact.relative_path
        payload = path.read_bytes()
        assert hashlib.sha256(payload).hexdigest() == artifact.sha256
        assert len(payload) == artifact.size_bytes
        assert "secret-value" not in payload.decode("utf-8")
        assert "argv" not in json.loads(payload)
    assert (
        sealed.result_sha256
        == hashlib.sha256(
            json.dumps(
                result.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
    )


def test_research_only_recipe_never_crosses_runner_seam_and_is_idempotent(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    request = _request(recipe_id=RESEARCH_ONLY_RECIPE_ID)
    runner = FakeSandboxRunner()
    engine = _engine(runner)

    first = engine.execute(request, run_workspace=workspace)
    second = engine.execute(request, run_workspace=workspace)

    assert first == second
    assert first.result.terminal_status is ExecutionTerminalStatus.RESEARCH_ONLY
    assert first.result.check_results == ()
    assert runner.calls == []


def test_idempotency_key_replay_returns_original_and_conflict_fails_closed(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    request = _request()
    runner = FakeSandboxRunner()
    runner.queue(_result(request, 0))
    runner.queue(_result(request, 1))
    engine = _engine(runner)

    original = engine.execute(request, run_workspace=workspace)
    replay = engine.execute(request, run_workspace=workspace)

    assert replay == original
    assert len(runner.calls) == 2
    conflicting = request.model_copy(update={"subject_id": "subject:different"})
    with pytest.raises(IdempotencyConflictError):
        engine.execute(conflicting, run_workspace=workspace)
    assert len(runner.calls) == 2


@pytest.mark.parametrize(
    ("status", "terminal_status", "failure_code"),
    (
        (
            ExecutionStatus.FAILED,
            ExecutionTerminalStatus.FAILED,
            FailureCode.POC_NONZERO_EXIT,
        ),
        (
            ExecutionStatus.TIMED_OUT,
            ExecutionTerminalStatus.TIMED_OUT,
            FailureCode.POC_TIMEOUT,
        ),
        (
            ExecutionStatus.UNAVAILABLE,
            ExecutionTerminalStatus.FAILED,
            FailureCode.TOOL_UNAVAILABLE,
        ),
    ),
)
def test_failure_timeout_and_unavailable_are_sealed_and_cleanup_is_complete(
    tmp_path: Path,
    status: ExecutionStatus,
    terminal_status: ExecutionTerminalStatus,
    failure_code: FailureCode,
) -> None:
    workspace = _workspace(tmp_path)
    request = _request(idempotency_key=f"idempotency:{status.value}")
    runner = FakeSandboxRunner()
    runner.queue(_result(request, 0, status=status))

    sealed = _engine(runner).execute(request, run_workspace=workspace)

    assert sealed.result.terminal_status is terminal_status
    assert sealed.result.failure.code is failure_code
    assert sealed.result.cleanup_complete is True
    assert len(sealed.result.check_results) == 1
    assert not any(workspace.glob("experiment-executions/*/work"))
    assert list(workspace.glob("experiment-executions/*/result.json"))


def test_cancellation_before_and_at_runner_seam_is_terminal_and_bounded(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    request = _request(idempotency_key="idempotency:cancel-before")
    token = CancellationToken()
    token.cancel()
    runner = FakeSandboxRunner()

    before = _engine(runner).execute(
        request,
        run_workspace=workspace,
        cancellation=token,
    )

    assert before.result.terminal_status is ExecutionTerminalStatus.CANCELLED
    assert before.result.failure.code is FailureCode.EXPERIMENT_CANCELLED
    assert runner.calls == []

    at_seam_request = _request(idempotency_key="idempotency:cancel-at-seam")
    at_seam_token = CancellationToken()

    class CancellingFakeRunner(FakeSandboxRunner):
        def run(self, command, run_workspace, **kwargs):
            at_seam_token.cancel()
            return super().run(command, run_workspace, **kwargs)

    cancelling_runner = CancellingFakeRunner()
    at_seam = _engine(cancelling_runner).execute(
        at_seam_request,
        run_workspace=workspace,
        cancellation=at_seam_token,
    )
    assert at_seam.result.terminal_status is ExecutionTerminalStatus.CANCELLED
    assert (
        at_seam.result.check_results[0].failure_code is FailureCode.EXPERIMENT_CANCELLED
    )
    assert len(cancelling_runner.calls) == 1


def test_check_count_wall_time_artifact_and_measurement_budgets_fail_closed(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    check_budget = ExecutionBudget(max_checks=1)
    check_request = _request(
        idempotency_key="idempotency:check-budget",
        budget=check_budget,
    )
    no_calls = FakeSandboxRunner()
    exhausted = _engine(no_calls).execute(check_request, run_workspace=workspace)
    assert exhausted.result.terminal_status is ExecutionTerminalStatus.BUDGET_EXHAUSTED
    assert no_calls.calls == []

    wall_request = _request(
        idempotency_key="idempotency:wall-budget",
        budget=ExecutionBudget(wall_timeout_seconds=1),
    )
    times = iter((0.0, 2.0))
    wall_runner = FakeSandboxRunner()
    wall = _engine(wall_runner, monotonic=lambda: next(times)).execute(
        wall_request,
        run_workspace=workspace,
    )
    assert wall.result.terminal_status is ExecutionTerminalStatus.TIMED_OUT
    assert wall.result.failure.code is FailureCode.DEADLINE_EXCEEDED

    artifact_request = _request(
        idempotency_key="idempotency:artifact-budget",
        budget=ExecutionBudget(max_artifact_bytes=1024),
    )
    artifact_runner = FakeSandboxRunner()
    artifact_runner.queue(_result(artifact_request, 0, stdout="x" * 4000))
    artifact = _engine(artifact_runner).execute(
        artifact_request,
        run_workspace=workspace,
    )
    assert artifact.result.terminal_status is ExecutionTerminalStatus.BUDGET_EXHAUSTED
    assert artifact.result.artifacts == ()

    measurement_request = _request(
        idempotency_key="idempotency:measurement-budget",
        budget=ExecutionBudget(max_measurements=1),
    )
    measurement_runner = FakeSandboxRunner()
    measurement_runner.queue(_result(measurement_request, 0))
    measurement = _engine(measurement_runner).execute(
        measurement_request,
        run_workspace=workspace,
    )
    assert (
        measurement.result.terminal_status is ExecutionTerminalStatus.BUDGET_EXHAUSTED
    )
    assert measurement.result.measurements == ()
    assert measurement.result.artifacts == ()


def test_per_check_timeout_budget_is_passed_to_the_sandbox_seam(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    request = _request(
        idempotency_key="idempotency:check-timeout",
        budget=ExecutionBudget(check_timeout_seconds=7),
    )

    class CapturingFakeRunner(FakeSandboxRunner):
        def __init__(self) -> None:
            super().__init__()
            self.timeouts: list[float | None] = []

        def run(self, command, run_workspace, **kwargs):
            self.timeouts.append(kwargs.get("timeout_seconds"))
            return super().run(command, run_workspace, **kwargs)

    runner = CapturingFakeRunner()
    runner.queue(_result(request, 0))
    runner.queue(_result(request, 1))

    sealed = _engine(runner).execute(request, run_workspace=workspace)

    assert sealed.result.terminal_status is ExecutionTerminalStatus.SUCCEEDED
    assert runner.timeouts == pytest.approx([7, 7], rel=0, abs=0.1)


def test_resource_budget_mismatch_and_cleanup_failure_are_sealed(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    mismatched = _request(
        idempotency_key="idempotency:resource-mismatch",
        budget=ExecutionBudget(resources=SandboxLimits(cpus=0.5)),
    )
    runner = FakeSandboxRunner()
    resource_result = _engine(runner).execute(mismatched, run_workspace=workspace)
    assert resource_result.result.terminal_status is ExecutionTerminalStatus.FAILED
    assert resource_result.result.failure.code is FailureCode.UNSAFE_REQUEST

    cleanup_request = _request(idempotency_key="idempotency:cleanup-failure")
    cleanup_runner = FakeSandboxRunner()
    cleanup_runner.queue(_result(cleanup_request, 0))
    cleanup_runner.queue(_result(cleanup_request, 1))

    def fail_cleanup(path: Path) -> None:
        raise OSError("injected cleanup failure")

    cleanup_result = _engine(cleanup_runner, remove_tree=fail_cleanup).execute(
        cleanup_request,
        run_workspace=workspace,
    )
    assert cleanup_result.result.terminal_status is ExecutionTerminalStatus.FAILED
    assert cleanup_result.result.cleanup_complete is False
    assert cleanup_result.result.failure.code is FailureCode.EXPERIMENT_CLEANUP_FAILED
    assert cleanup_result.result_sha256


def test_artifact_and_terminal_publication_failures_return_sealed_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    artifact_request = _request(idempotency_key="idempotency:artifact-publish-failure")

    class ArtifactBlockingRunner(FakeSandboxRunner):
        def run(self, command, run_workspace, **kwargs):
            (run_workspace.parent / "artifacts").write_text(
                "blocks artifact directory",
                encoding="utf-8",
            )
            return super().run(command, run_workspace, **kwargs)

    artifact_runner = ArtifactBlockingRunner()
    artifact_runner.queue(_result(artifact_request, 0))
    artifact_result = _engine(artifact_runner).execute(
        artifact_request,
        run_workspace=workspace,
    )
    assert artifact_result.result.terminal_status is ExecutionTerminalStatus.FAILED
    assert artifact_result.result.failure.code is FailureCode.ARTIFACT_PUBLISH_FAILED
    assert artifact_result.result.cleanup_complete is True
    assert artifact_result.result.check_results == ()

    terminal_request = _request(idempotency_key="idempotency:terminal-publish-failure")
    terminal_runner = FakeSandboxRunner()
    terminal_runner.queue(_result(terminal_request, 0))
    terminal_runner.queue(_result(terminal_request, 1))
    monkeypatch.setattr(
        engine_module,
        "_write_json_atomic",
        lambda path, payload: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    terminal_result = _engine(terminal_runner).execute(
        terminal_request,
        run_workspace=workspace,
    )
    assert terminal_result.result.terminal_status is ExecutionTerminalStatus.FAILED
    assert terminal_result.result.failure.code is FailureCode.ARTIFACT_PUBLISH_FAILED
    assert terminal_result.result.cleanup_complete is True
    assert terminal_result.result_sha256


def test_tampered_terminal_result_is_rejected_before_replay(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    request = _request()
    runner = FakeSandboxRunner()
    runner.queue(_result(request, 0))
    runner.queue(_result(request, 1))
    engine = _engine(runner)
    engine.execute(request, run_workspace=workspace)
    result_path = next(workspace.glob("experiment-executions/*/result.json"))
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["sealed_execution"]["result"]["terminal_reason"] = "tampered"
    result_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidExecutionSealError):
        engine.execute(request, run_workspace=workspace)
