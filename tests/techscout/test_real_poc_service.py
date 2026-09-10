import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from paper_agent.techscout.errors import FailureCode
from paper_agent.techscout.models import Candidate, PocPlan, PocStatus
from paper_agent.techscout.sandbox.compiler import PocCompiler
from paper_agent.techscout.sandbox.runner import FakeSandboxRunner
from paper_agent.techscout.sandbox.service import (
    PocStageAttempt,
    RealPocAdapter,
    RealPocService,
)
from paper_agent.techscout.sandbox.types import (
    ExecutionStatus,
    PocStage,
    SandboxResult,
)
from paper_agent.techscout.tools.contracts import SmokeTestInput


def _candidate() -> Candidate:
    return Candidate(
        candidate_id="candidate:qdrant-client",
        name="Qdrant Local",
        package_name="qdrant-client",
        requested_version="1.15.*",
    )


def _plan(recipe_id: str = "recipe:qdrant-local@1") -> PocPlan:
    return PocPlan(
        poc_plan_id="poc-plan:qdrant:real",
        candidate_id="candidate:qdrant-client",
        recipe_id=recipe_id,
        trusted=True,
        checks=(
            "install",
            "import",
            "create",
            "persistence",
            "upsert",
            "query",
            "filter",
        ),
    )


def _command(stage: PocStage):
    return PocCompiler().compile(_plan(), _candidate(), stage)


def _sandbox_result(
    stage: PocStage,
    status: ExecutionStatus = ExecutionStatus.SUCCEEDED,
    *,
    stdout: str = "ok",
    stderr: str = "",
    exit_code: int | None = 0,
    failure_code: FailureCode | None = None,
) -> SandboxResult:
    return SandboxResult(
        command=_command(stage),
        status=status,
        exit_code=exit_code,
        timed_out=status is ExecutionStatus.TIMED_OUT,
        duration_ms=11 if stage is PocStage.INSTALL else 13,
        stdout=stdout,
        stderr=stderr,
        failure_code=failure_code,
    )


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "run-001"
    workspace.mkdir()
    return workspace


def test_service_runs_both_reviewed_stages_and_writes_sanitized_artifact(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    secret = "super-secret-value"
    runner = FakeSandboxRunner()
    runner.queue(_sandbox_result(PocStage.INSTALL, stdout=f"installed {secret}"))
    runner.queue(
        _sandbox_result(
            PocStage.TEST,
            stdout=f"checks passed in {workspace} with {secret}",
        )
    )

    result = RealPocService(runner, secrets=(secret,)).execute(
        _plan(),
        _candidate(),
        run_workspace=workspace,
        attempt=1,
    )

    assert result.status is PocStatus.PASSED
    assert result.resolved_version == "1.15.1"
    assert result.exit_code == 0
    assert result.duration_ms == 24
    assert [call[0].stage for call in runner.calls] == [PocStage.INSTALL, PocStage.TEST]
    assert len(result.artifacts) == 1
    artifact_path = workspace / "poc-artifacts" / f"{result.artifacts[0].sha256}.json"
    artifact_bytes = artifact_path.read_bytes()
    assert len(artifact_bytes) == result.artifacts[0].size_bytes
    artifact = json.loads(artifact_bytes)
    assert artifact["attempt"] == 1
    assert artifact["status"] == "passed"
    assert [stage["stage"] for stage in artifact["stages"]] == ["install", "test"]
    serialized = artifact_bytes.decode("utf-8")
    assert secret not in serialized
    assert str(workspace) not in serialized
    assert "[REDACTED]" in serialized
    assert all("argv" not in stage for stage in artifact["stages"])
    assert all("argv_sha256" in stage for stage in artifact["stages"])


def test_unsupported_recipe_is_research_only_without_runner_call(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runner = FakeSandboxRunner()
    candidate = _candidate().model_copy(
        update={"name": "pgvector", "package_name": "pgvector"}
    )

    result = RealPocService(runner).execute(
        _plan("recipe:pgvector@1"),
        candidate,
        run_workspace=workspace,
    )

    assert result.status is PocStatus.RESEARCH_ONLY
    assert result.failure_code is FailureCode.POC_RECIPE_UNSUPPORTED
    assert runner.calls == []
    assert len(result.artifacts) == 1


class _DeniedInstallRunner:
    def __init__(self) -> None:
        self.calls = []

    def run(self, command, run_workspace):
        self.calls.append((command, run_workspace))
        raise PermissionError("install egress is not configured")


def test_missing_install_egress_is_typed_unavailable(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runner = _DeniedInstallRunner()

    result = RealPocService(runner).execute(
        _plan(),
        _candidate(),
        run_workspace=workspace,
    )

    assert result.status is PocStatus.FAILED
    assert result.failure_code is FailureCode.TOOL_UNAVAILABLE
    assert result.exit_code is None
    assert [call[0].stage for call in runner.calls] == [PocStage.INSTALL]


@pytest.mark.parametrize(
    ("sandbox_result", "expected_status", "expected_failure"),
    (
        (
            _sandbox_result(
                PocStage.TEST,
                ExecutionStatus.TIMED_OUT,
                exit_code=None,
                failure_code=FailureCode.POC_TIMEOUT,
            ),
            PocStatus.TIMED_OUT,
            FailureCode.POC_TIMEOUT,
        ),
        (
            _sandbox_result(
                PocStage.TEST,
                ExecutionStatus.FAILED,
                exit_code=7,
                failure_code=FailureCode.POC_NONZERO_EXIT,
            ),
            PocStatus.FAILED,
            FailureCode.POC_NONZERO_EXIT,
        ),
        (
            _sandbox_result(
                PocStage.TEST,
                ExecutionStatus.UNAVAILABLE,
                exit_code=None,
                failure_code=FailureCode.TOOL_UNAVAILABLE,
            ),
            PocStatus.FAILED,
            FailureCode.TOOL_UNAVAILABLE,
        ),
        (
            _sandbox_result(
                PocStage.TEST,
                ExecutionStatus.FAILED,
                exit_code=1,
                stderr="Cannot connect to the Docker daemon. Is the docker daemon running?",
                failure_code=FailureCode.POC_NONZERO_EXIT,
            ),
            PocStatus.FAILED,
            FailureCode.TOOL_UNAVAILABLE,
        ),
    ),
)
def test_service_preserves_timeout_failure_and_daemon_unavailability(
    tmp_path: Path,
    sandbox_result: SandboxResult,
    expected_status: PocStatus,
    expected_failure: FailureCode,
) -> None:
    workspace = _workspace(tmp_path)
    runner = FakeSandboxRunner()
    runner.queue(_sandbox_result(PocStage.INSTALL))
    runner.queue(sandbox_result)

    result = RealPocService(runner).execute(
        _plan(),
        _candidate(),
        run_workspace=workspace,
    )

    assert result.status is expected_status
    assert result.failure_code is expected_failure
    assert len(runner.calls) == 2


def test_attempt_contract_allows_only_initial_call_and_one_stage_rerun(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    runner = FakeSandboxRunner()
    runner.queue(_sandbox_result(PocStage.TEST))

    result = RealPocService(runner).rerun_stage(
        _plan(),
        _candidate(),
        run_workspace=workspace,
        stage=PocStage.TEST,
    )

    assert result.attempt == 2
    assert result.stage is PocStage.TEST
    assert result.status is PocStatus.PASSED
    assert not hasattr(result, "poc_result_id")
    assert [call[0].stage for call in runner.calls] == [PocStage.TEST]
    with pytest.raises(ValueError, match="execute produces only complete initial"):
        RealPocService(runner).execute(
            _plan(),
            _candidate(),
            run_workspace=workspace,
            attempt=2,
        )
    with pytest.raises(ValueError, match="attempt must be 1 or 2"):
        RealPocService(runner).execute(
            _plan(),
            _candidate(),
            run_workspace=workspace,
            attempt=3,
        )


def test_install_only_recovery_cannot_claim_complete_poc_pass(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runner = FakeSandboxRunner()
    runner.queue(_sandbox_result(PocStage.INSTALL))

    stage_attempt = RealPocService(runner).rerun_stage(
        _plan(),
        _candidate(),
        run_workspace=workspace,
        stage=PocStage.INSTALL,
    )

    assert stage_attempt.stage is PocStage.INSTALL
    assert stage_attempt.status is PocStatus.PASSED
    assert stage_attempt.artifact.kind == "real-docker-poc-stage-attempt"
    assert [call[0].stage for call in runner.calls] == [PocStage.INSTALL]


def test_stage_rerun_maps_artifact_publication_failure(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "poc-artifacts").write_text("blocks artifact directory", encoding="utf-8")
    runner = FakeSandboxRunner()
    runner.queue(_sandbox_result(PocStage.TEST))

    stage_attempt = RealPocService(runner).rerun_stage(
        _plan(),
        _candidate(),
        run_workspace=workspace,
        stage=PocStage.TEST,
    )

    assert stage_attempt.status is PocStatus.FAILED
    assert stage_attempt.failure_code is FailureCode.POC_ARTIFACT_INVALID
    assert stage_attempt.artifact is None


@pytest.mark.parametrize(
    "updates",
    (
        {
            "status": PocStatus.PASSED,
            "exit_code": 7,
            "failure_code": FailureCode.POC_NONZERO_EXIT,
        },
        {
            "status": PocStatus.TIMED_OUT,
            "timed_out": False,
            "failure_code": FailureCode.POC_TIMEOUT,
        },
        {"status": PocStatus.FAILED, "failure_code": None},
    ),
)
def test_stage_attempt_rejects_inconsistent_status(updates: dict[str, object]) -> None:
    payload = {
        "poc_plan_id": "poc-plan:qdrant:real",
        "candidate_id": "candidate:qdrant-client",
        "recipe_id": "recipe:qdrant-local@1",
        "stage": PocStage.TEST,
        "status": PocStatus.FAILED,
        "exit_code": 1,
        "timed_out": False,
        "duration_ms": 1,
        "failure_code": FailureCode.POC_NONZERO_EXIT,
    }

    with pytest.raises(ValidationError):
        PocStageAttempt(**{**payload, **updates})


def test_direct_service_rejects_candidate_impersonating_reviewed_recipe(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    runner = FakeSandboxRunner()
    impersonator = _candidate().model_copy(update={"candidate_id": "candidate:pgvector"})
    plan = _plan().model_copy(update={"candidate_id": "candidate:pgvector"})

    result = RealPocService(runner).execute(
        plan,
        impersonator,
        run_workspace=workspace,
    )

    assert result.status is PocStatus.RESEARCH_ONLY
    assert result.failure_code is FailureCode.POC_RECIPE_UNSUPPORTED
    assert runner.calls == []


def test_result_identifier_distinguishes_plans_for_the_same_candidate(
    tmp_path: Path,
) -> None:
    first_workspace = _workspace(tmp_path)
    second_workspace = tmp_path / "run-002"
    second_workspace.mkdir()
    runner = FakeSandboxRunner()
    for _ in range(2):
        runner.queue(_sandbox_result(PocStage.INSTALL))
        runner.queue(_sandbox_result(PocStage.TEST))

    first = RealPocService(runner).execute(
        _plan(),
        _candidate(),
        run_workspace=first_workspace,
    )
    second = RealPocService(runner).execute(
        _plan().model_copy(update={"poc_plan_id": "poc-plan:qdrant:alternate"}),
        _candidate(),
        run_workspace=second_workspace,
    )

    assert first.poc_result_id != second.poc_result_id


def test_adapter_projects_typed_service_result_without_hiding_failure_code(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)

    class UnavailableTestRunner:
        def run(self, command, run_workspace):
            if command.stage is PocStage.INSTALL:
                return SandboxResult(
                    command=command,
                    status=ExecutionStatus.SUCCEEDED,
                    exit_code=0,
                    timed_out=False,
                    duration_ms=11,
                )
            return SandboxResult(
                command=command,
                status=ExecutionStatus.UNAVAILABLE,
                exit_code=None,
                timed_out=False,
                duration_ms=13,
                failure_code=FailureCode.TOOL_UNAVAILABLE,
            )

    runner = UnavailableTestRunner()
    adapter = RealPocAdapter(RealPocService(runner), run_workspace=workspace)

    output = adapter.run_smoke_test(
        SmokeTestInput(
            candidate_id="candidate:qdrant-client",
            recipe_id="recipe:qdrant-local@1",
            checks=(
                "install",
                "import",
                "create",
                "persistence",
                "upsert",
                "query",
                "filter",
            ),
            requested_version="1.15.*",
        )
    )

    assert output.status == "failed"
    assert output.failure_code is FailureCode.TOOL_UNAVAILABLE
    assert output.artifact_sha256 is not None


def test_adapter_rejects_candidate_recipe_mismatch_without_runner_call(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    runner = FakeSandboxRunner()
    adapter = RealPocAdapter(RealPocService(runner), run_workspace=workspace)

    output = adapter.run_smoke_test(
        SmokeTestInput(
            candidate_id="candidate:pgvector",
            recipe_id="recipe:qdrant-local@1",
            checks=("import", "create", "persistence", "upsert", "query", "filter"),
        )
    )

    assert output.status == "research_only"
    assert output.failure_code is FailureCode.POC_RECIPE_UNSUPPORTED
    assert runner.calls == []


def test_adapter_rejects_unknown_recipe_without_runner_call(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    runner = FakeSandboxRunner()
    adapter = RealPocAdapter(RealPocService(runner), run_workspace=workspace)

    output = adapter.run_smoke_test(
        SmokeTestInput(
            candidate_id="candidate:chroma",
            recipe_id="recipe:attacker-controlled@1",
            checks=("import",),
        )
    )

    assert output.status == "research_only"
    assert output.failure_code is FailureCode.POC_RECIPE_UNSUPPORTED
    assert runner.calls == []
