from datetime import datetime, timedelta, timezone

import pytest

from paper_agent.techscout.harness import (
    SQLiteCheckpointAdapter,
    StageArtifacts,
    StageDeadline,
    StageDeadlineExceeded,
    StageResult,
    TechScoutHarness,
)
from paper_agent.techscout.models import (
    Candidate,
    ConstraintResult,
    ConstraintStatus,
    DecisionReport,
    EnvironmentSpec,
    GateOutcome,
    ResearchPlan,
    ResearchRequest,
    RunManifest,
    TerminalStatus,
    Verdict,
)
from paper_agent.techscout.recovery import RecoveryPolicy
from paper_agent.techscout.errors import (
    Failure,
    FailureCode,
    FailureStage,
    RecoveryAction,
)
from paper_agent.techscout.state import ResearchStage, ResearchState, RunBudget


_NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


def _initial_state(*, max_steps: int = 16) -> ResearchState:
    request = ResearchRequest(
        run_id="run:harness-happy",
        question="Choose a local vector store.",
        project_context="A local Python RAG application.",
        environment=EnvironmentSpec(
            python_version="3.11",
            operating_system="linux",
            deployment="local",
        ),
        hard_constraints=("metadata filtering",),
        candidates=(
            Candidate(
                candidate_id="candidate:qdrant-local",
                name="Qdrant Local",
                package_name="qdrant-client",
            ),
        ),
    )
    return ResearchState(
        run_id=request.run_id,
        request=request,
        budget=RunBudget(
            max_steps=max_steps,
            deadline_at=_NOW + timedelta(minutes=5),
        ),
        stage=ResearchStage.NORMALIZE_REQUEST,
        step_count=0,
        tool_call_count=0,
        token_count=0,
        recovery_count=0,
        candidate_ids=("candidate:qdrant-local",),
        source_ids=(),
        evidence_ids=(),
        poc_result_ids=(),
        failures=(),
    )


def _harness(
    services: "DeterministicStageServices",
    checkpoints: SQLiteCheckpointAdapter,
) -> TechScoutHarness:
    return TechScoutHarness(services, checkpoints, now=lambda: _NOW)


class DeterministicStageServices:
    def __init__(self) -> None:
        self.calls: list[ResearchStage] = []

    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        self.calls.append(stage)
        updates: dict[str, object] = {}
        if stage is ResearchStage.PLAN_RESEARCH:
            updates["plan"] = ResearchPlan(
                plan_id="plan:harness-happy",
                investigation_dimensions=("compatibility",),
                required_capabilities=("official-doc-research",),
                planned_evidence=("official documentation",),
                poc_intent="verify local filtering",
            )
        elif stage is ResearchStage.RESEARCH_CANDIDATES:
            updates.update(
                source_ids=("source:qdrant-docs",),
                evidence_ids=("evidence:qdrant-filtering",),
            )
        elif stage is ResearchStage.EXECUTE_POC:
            updates["poc_result_ids"] = ("poc-result:qdrant-local",)
        elif stage is ResearchStage.VALIDATE:
            updates["gate_outcome"] = GateOutcome.PASSED
        elif stage is ResearchStage.REVIEW_REPORT:
            limited = state.gate_outcome is not GateOutcome.PASSED
            report = DecisionReport(
                report_id="report:harness-happy",
                run_id=state.run_id,
                recommendation=(
                    None if limited else "candidate:qdrant-local"
                ),
                verdict=(
                    Verdict.INSUFFICIENT_EVIDENCE
                    if limited
                    else Verdict.RECOMMENDED
                ),
                summary=(
                    "No safe winner after bounded recovery."
                    if limited
                    else "Qdrant satisfies the hard constraint."
                ),
                constraint_results=(
                    ConstraintResult(
                        candidate_id="candidate:qdrant-local",
                        constraint="metadata filtering",
                        status=(
                            ConstraintStatus.UNKNOWN
                            if limited
                            else ConstraintStatus.SATISFIED
                        ),
                        evidence_ids=(
                            ()
                            if limited
                            else ("evidence:qdrant-filtering",)
                        ),
                        reason="Recovery was exhausted." if limited else None,
                    ),
                ),
                limitations=("poc_failed",) if limited else (),
            )
            return StageResult(
                state=state,
                artifacts=StageArtifacts(report=report),
                tokens=100,
            )
        elif stage is ResearchStage.PUBLISH:
            assert artifacts.report is not None
            limited = state.gate_outcome is not GateOutcome.PASSED
            manifest = RunManifest(
                run_id=state.run_id,
                terminal_status=(
                    TerminalStatus.COMPLETED_WITH_LIMITATIONS
                    if limited
                    else TerminalStatus.COMPLETED
                ),
                report_id=artifacts.report.report_id,
                artifact_ids=(artifacts.report.report_id,),
                limitation_codes=("poc_failed",) if limited else (),
            )
            return StageResult(
                state=state,
                artifacts=StageArtifacts(manifest=manifest),
                tokens=100,
            )
        return StageResult(
            state=state.model_copy(update=updates),
            tool_calls=1 if stage is ResearchStage.RESEARCH_CANDIDATES else 0,
            tokens=100,
        )


def test_sqlite_checkpoint_uses_wal_and_busy_timeout(tmp_path) -> None:
    with SQLiteCheckpointAdapter(tmp_path / "settings.sqlite3") as checkpoints:
        connection = checkpoints.saver.conn
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]

    assert journal_mode == "wal"
    assert busy_timeout == 5_000


def test_frozen_request_reaches_completed_terminal_state(tmp_path) -> None:
    services = DeterministicStageServices()
    checkpoint_path = tmp_path / "harness.sqlite3"

    with SQLiteCheckpointAdapter(checkpoint_path) as checkpoints:
        result = _harness(services, checkpoints).run(_initial_state())

    assert result.report is not None
    assert result.manifest is not None
    assert result.state.stage is ResearchStage.TERMINAL
    assert result.state.terminal_status is TerminalStatus.COMPLETED
    assert result.state.gate_outcome is GateOutcome.PASSED
    assert result.state.step_count == 9
    assert services.calls == [
        ResearchStage.NORMALIZE_REQUEST,
        ResearchStage.PLAN_RESEARCH,
        ResearchStage.RESEARCH_CANDIDATES,
        ResearchStage.SELECT_CONTEXT,
        ResearchStage.PLAN_POC,
        ResearchStage.EXECUTE_POC,
        ResearchStage.VALIDATE,
        ResearchStage.REVIEW_REPORT,
        ResearchStage.PUBLISH,
    ]


class MissingArtifactServices(DeterministicStageServices):
    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        if stage in {ResearchStage.REVIEW_REPORT, ResearchStage.PUBLISH}:
            self.calls.append(stage)
            return StageResult(state=state)
        return super().execute(stage, state, artifacts, deadline)


def test_completed_gate_requires_matching_report_and_manifest(tmp_path) -> None:
    services = MissingArtifactServices()

    with SQLiteCheckpointAdapter(
        tmp_path / "missing-artifacts.sqlite3"
    ) as checkpoints:
        result = _harness(services, checkpoints).run(_initial_state())

    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.failures[-1].code is FailureCode.REPORT_SCHEMA_INVALID
    assert result.report is None
    assert result.manifest is None


def test_interrupted_run_resumes_without_repeating_completed_stages(tmp_path) -> None:
    services = DeterministicStageServices()
    checkpoint_path = tmp_path / "resume.sqlite3"

    with SQLiteCheckpointAdapter(checkpoint_path) as checkpoints:
        interrupted = _harness(services, checkpoints).run(
            _initial_state(),
            interrupt_after=ResearchStage.PLAN_RESEARCH,
        )

    assert interrupted.state.stage is ResearchStage.PLAN_RESEARCH
    assert services.calls == [
        ResearchStage.NORMALIZE_REQUEST,
        ResearchStage.PLAN_RESEARCH,
    ]

    with SQLiteCheckpointAdapter(checkpoint_path) as checkpoints:
        resumed = _harness(services, checkpoints).run(
            run_id="run:harness-happy"
        )

    assert resumed.state.terminal_status is TerminalStatus.COMPLETED
    assert services.calls.count(ResearchStage.NORMALIZE_REQUEST) == 1
    assert services.calls.count(ResearchStage.PLAN_RESEARCH) == 1
    assert resumed.state.step_count == 9


class RecoveringStageServices(DeterministicStageServices):
    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        if stage is ResearchStage.EXECUTE_POC:
            self.calls.append(stage)
            if self.calls.count(stage) == 1:
                failure = Failure(
                    failure_id="failure:harness-poc:0001",
                    code=FailureCode.DEPENDENCY_CONFLICT,
                    stage=FailureStage.POC_EXECUTION,
                    message="The deterministic PoC failed.",
                    recoverable=True,
                    recovery_action=RecoveryAction.PIN_VERSION_AND_RERUN_POC,
                    attempt=1,
                )
                return StageResult(
                    state=state.model_copy(update={"failures": (failure,)}),
                    tokens=100,
                )
            return StageResult(
                state=state.model_copy(
                    update={"poc_result_ids": ("poc-result:qdrant-recovered",)}
                ),
                tokens=100,
            )
        if stage is ResearchStage.VALIDATE:
            self.calls.append(stage)
            outcome = (
                GateOutcome.PASSED
                if state.poc_result_ids
                else GateOutcome.RECOVER
            )
            return StageResult(
                state=state.model_copy(update={"gate_outcome": outcome}),
                tokens=100,
            )
        return super().execute(stage, state, artifacts, deadline)


def test_recovery_repeats_only_the_failed_stage_once(tmp_path) -> None:
    services = RecoveringStageServices()

    with SQLiteCheckpointAdapter(tmp_path / "recovery.sqlite3") as checkpoints:
        result = _harness(services, checkpoints).run(_initial_state())

    assert result.state.terminal_status is TerminalStatus.COMPLETED
    assert result.state.recovery_count == 1
    assert result.recovery is not None
    assert result.recovery.original_failure_id == "failure:harness-poc:0001"
    assert result.recovery.checkpoint_id is not None
    assert services.calls.count(ResearchStage.EXECUTE_POC) == 2
    assert services.calls.count(ResearchStage.VALIDATE) == 2
    assert services.calls.count(ResearchStage.NORMALIZE_REQUEST) == 1
    assert services.calls.count(ResearchStage.PLAN_RESEARCH) == 1
    assert services.calls.count(ResearchStage.RESEARCH_CANDIDATES) == 1


def test_recovery_checkpoint_records_the_repeated_failed_stage(tmp_path) -> None:
    services = RecoveringStageServices()
    checkpoint_path = tmp_path / "recovery-stage.sqlite3"

    with SQLiteCheckpointAdapter(checkpoint_path) as checkpoints:
        recovered = _harness(services, checkpoints).run(
            _initial_state(),
            interrupt_after=ResearchStage.RECOVER_ONCE,
        )

    assert recovered.recovery is not None
    assert recovered.state.recovery_count == 1
    assert recovered.state.stage is ResearchStage.EXECUTE_POC
    assert recovered.state.checkpoint is not None
    assert recovered.state.checkpoint.stage is ResearchStage.EXECUTE_POC
    assert (
        recovered.state.checkpoint.parent_checkpoint_id
        == recovered.recovery.checkpoint_id
    )
    assert services.calls.count(ResearchStage.RESEARCH_CANDIDATES) == 1
    assert services.calls.count(ResearchStage.EXECUTE_POC) == 2

    with SQLiteCheckpointAdapter(checkpoint_path) as checkpoints:
        terminal = _harness(services, checkpoints).run(run_id=recovered.state.run_id)

    assert terminal.state.terminal_status is TerminalStatus.COMPLETED
    assert services.calls.count(ResearchStage.RESEARCH_CANDIDATES) == 1
    assert services.calls.count(ResearchStage.EXECUTE_POC) == 2


def test_recovery_policy_refuses_retry_without_a_checkpoint() -> None:
    failure = Failure(
        failure_id="failure:harness-poc:no-checkpoint",
        code=FailureCode.DEPENDENCY_CONFLICT,
        stage=FailureStage.POC_EXECUTION,
        message="Dependency conflict.",
        recoverable=True,
        recovery_action=RecoveryAction.PIN_VERSION_AND_RERUN_POC,
        attempt=1,
    )

    decision = RecoveryPolicy().decide(
        failure,
        recovery_count=0,
        checkpoint_id=None,
    )

    assert decision.should_recover is False
    assert decision.checkpoint_id is None


class FailureErasingRecoveryServices(RecoveringStageServices):
    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        if (
            stage is ResearchStage.EXECUTE_POC
            and self.calls.count(stage) == 1
        ):
            self.calls.append(stage)
            return StageResult(state=state.model_copy(update={"failures": ()}))
        return super().execute(stage, state, artifacts, deadline)


def test_recovery_cannot_erase_the_original_failure(tmp_path) -> None:
    services = FailureErasingRecoveryServices()

    with SQLiteCheckpointAdapter(
        tmp_path / "immutable-failure.sqlite3"
    ) as checkpoints:
        result = _harness(services, checkpoints).run(_initial_state())

    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.recovery_count == 1
    assert result.recovery is not None
    assert result.recovery.original_failure_id == "failure:harness-poc:0001"
    assert result.state.failures[0].failure_id == result.recovery.original_failure_id
    assert result.state.failures[-1].code is FailureCode.REPORT_SCHEMA_INVALID


class MalformedRecoveryServices(RecoveringStageServices):
    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        if (
            stage is ResearchStage.EXECUTE_POC
            and self.calls.count(stage) == 1
        ):
            self.calls.append(stage)
            raise ValueError("malformed recovery output")
        return super().execute(stage, state, artifacts, deadline)


def test_malformed_recovery_output_fails_safely(tmp_path) -> None:
    services = MalformedRecoveryServices()

    with SQLiteCheckpointAdapter(
        tmp_path / "malformed-recovery.sqlite3"
    ) as checkpoints:
        result = _harness(services, checkpoints).run(_initial_state())

    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.recovery_count == 1
    assert result.state.failures[-1].code is FailureCode.REPORT_SCHEMA_INVALID
    assert services.calls.count(ResearchStage.EXECUTE_POC) == 2


class ExhaustedRecoveryServices(DeterministicStageServices):
    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        if stage is ResearchStage.EXECUTE_POC:
            self.calls.append(stage)
            attempt = self.calls.count(stage)
            failure = Failure(
                failure_id=f"failure:harness-poc:{attempt:04d}",
                code=FailureCode.DEPENDENCY_CONFLICT,
                stage=FailureStage.POC_EXECUTION,
                message="The deterministic PoC failed.",
                recoverable=True,
                recovery_action=RecoveryAction.PIN_VERSION_AND_RERUN_POC,
                attempt=attempt,
            )
            return StageResult(
                state=state.model_copy(
                    update={"failures": (*state.failures, failure)}
                ),
                tokens=100,
            )
        if stage is ResearchStage.VALIDATE:
            self.calls.append(stage)
            return StageResult(
                state=state.model_copy(update={"gate_outcome": GateOutcome.RECOVER}),
                tokens=100,
            )
        return super().execute(stage, state, artifacts, deadline)


def test_exhausted_recovery_is_limited_and_never_retries_twice(tmp_path) -> None:
    services = ExhaustedRecoveryServices()

    with SQLiteCheckpointAdapter(
        tmp_path / "recovery-exhausted.sqlite3"
    ) as checkpoints:
        result = _harness(services, checkpoints).run(_initial_state())

    assert result.state.terminal_status is TerminalStatus.COMPLETED_WITH_LIMITATIONS
    assert result.state.recovery_count == 1
    assert services.calls.count(ResearchStage.EXECUTE_POC) == 2
    assert services.calls.count(ResearchStage.VALIDATE) == 2


def test_step_budget_exhaustion_terminates_without_starting_another_stage(
    tmp_path,
) -> None:
    services = DeterministicStageServices()

    with SQLiteCheckpointAdapter(tmp_path / "budget.sqlite3") as checkpoints:
        result = _harness(services, checkpoints).run(
            _initial_state(max_steps=2)
        )

    assert result.state.stage is ResearchStage.TERMINAL
    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.gate_outcome is GateOutcome.FAILED
    assert result.state.step_count == 2
    assert result.state.failures[-1].code is FailureCode.BUDGET_EXHAUSTED
    assert services.calls == [
        ResearchStage.NORMALIZE_REQUEST,
        ResearchStage.PLAN_RESEARCH,
    ]


class MalformedPlanningServices(DeterministicStageServices):
    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        if stage is ResearchStage.PLAN_RESEARCH:
            self.calls.append(stage)
            raise ValueError("malformed structured planning output")
        return super().execute(stage, state, artifacts, deadline)


def test_malformed_stage_output_terminates_as_a_typed_failure(tmp_path) -> None:
    services = MalformedPlanningServices()

    with SQLiteCheckpointAdapter(tmp_path / "malformed.sqlite3") as checkpoints:
        result = _harness(services, checkpoints).run(_initial_state())

    assert result.state.stage is ResearchStage.TERMINAL
    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.failures[-1].code is FailureCode.REPORT_SCHEMA_INVALID
    assert result.state.failures[-1].stage is FailureStage.PLANNING
    assert services.calls == [
        ResearchStage.NORMALIZE_REQUEST,
        ResearchStage.PLAN_RESEARCH,
    ]


class WrongRunIdServices(DeterministicStageServices):
    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        if stage is ResearchStage.PLAN_RESEARCH:
            self.calls.append(stage)
            return StageResult(
                state=state.model_copy(update={"run_id": "run:wrong"})
            )
        return super().execute(stage, state, artifacts, deadline)


def test_malformed_stage_result_fails_safely(tmp_path) -> None:
    services = WrongRunIdServices()

    with SQLiteCheckpointAdapter(
        tmp_path / "malformed-result.sqlite3"
    ) as checkpoints:
        result = _harness(services, checkpoints).run(_initial_state())

    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.failures[-1].code is FailureCode.REPORT_SCHEMA_INVALID


class ExcessiveUsageServices(DeterministicStageServices):
    def __init__(self, *, tool_calls: int, tokens: int) -> None:
        super().__init__()
        self._tool_calls = tool_calls
        self._tokens = tokens

    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        self.calls.append(stage)
        return StageResult(
            state=state,
            tool_calls=self._tool_calls,
            tokens=self._tokens,
        )


@pytest.mark.parametrize(
    ("tool_calls", "tokens"),
    ((13, 0), (0, 30_001)),
)
def test_stage_usage_cannot_overrun_tool_or_token_budget(
    tmp_path,
    tool_calls: int,
    tokens: int,
) -> None:
    services = ExcessiveUsageServices(tool_calls=tool_calls, tokens=tokens)

    with SQLiteCheckpointAdapter(
        tmp_path / f"usage-{tool_calls}-{tokens}.sqlite3"
    ) as checkpoints:
        result = _harness(services, checkpoints).run(_initial_state())

    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.failures[-1].code is FailureCode.BUDGET_EXHAUSTED
    assert services.calls == [ResearchStage.NORMALIZE_REQUEST]


def test_exact_tool_budget_is_allowed_when_later_stages_use_no_tools(
    tmp_path,
) -> None:
    services = DeterministicStageServices()
    initial = _initial_state()
    state = initial.model_copy(
        update={
            "budget": initial.budget.model_copy(update={"max_tool_calls": 1})
        }
    )

    with SQLiteCheckpointAdapter(tmp_path / "exact-tools.sqlite3") as checkpoints:
        result = _harness(services, checkpoints).run(state)

    assert result.state.terminal_status is TerminalStatus.COMPLETED
    assert result.state.tool_call_count == 1


def test_expired_whole_run_deadline_terminates_before_stage_execution(
    tmp_path,
) -> None:
    services = DeterministicStageServices()
    state = _initial_state().model_copy(
        update={
            "budget": _initial_state().budget.model_copy(
                update={"deadline_at": datetime(2026, 8, 9, tzinfo=timezone.utc)}
            )
        }
    )

    with SQLiteCheckpointAdapter(tmp_path / "deadline.sqlite3") as checkpoints:
        result = TechScoutHarness(
            services,
            checkpoints,
            now=lambda: datetime(2026, 8, 10, tzinfo=timezone.utc),
        ).run(state)

    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.failures[-1].code is FailureCode.DEADLINE_EXCEEDED
    assert result.state.step_count == 0
    assert services.calls == []


class DeadlineEnforcingStageServices(DeterministicStageServices):
    def __init__(self) -> None:
        super().__init__()
        self.deadline: StageDeadline | None = None

    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        self.calls.append(stage)
        self.deadline = deadline
        raise StageDeadlineExceeded


def test_stage_adapter_enforces_the_whole_run_deadline(tmp_path) -> None:
    services = DeadlineEnforcingStageServices()
    now = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)
    initial = _initial_state()
    state = initial.model_copy(
        update={
            "budget": initial.budget.model_copy(
                update={"deadline_at": now + timedelta(milliseconds=20)}
            )
        }
    )

    with SQLiteCheckpointAdapter(
        tmp_path / "enforced-deadline.sqlite3"
    ) as checkpoints:
        result = TechScoutHarness(
            services,
            checkpoints,
            now=lambda: now,
        ).run(state)

    assert services.deadline is not None
    assert services.deadline.deadline_at == state.budget.deadline_at
    assert services.deadline.timeout_seconds == pytest.approx(0.02)
    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.failures[-1].code is FailureCode.DEADLINE_EXCEEDED


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class DeadlineCrossingPublishServices(DeterministicStageServices):
    def __init__(self, clock: MutableClock) -> None:
        super().__init__()
        self._clock = clock

    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        result = super().execute(stage, state, artifacts, deadline)
        if stage is ResearchStage.PUBLISH:
            self._clock.current = state.budget.deadline_at
        return result


def test_publish_cannot_complete_after_the_run_deadline(tmp_path) -> None:
    clock = MutableClock(_NOW)
    services = DeadlineCrossingPublishServices(clock)

    with SQLiteCheckpointAdapter(
        tmp_path / "publish-deadline.sqlite3"
    ) as checkpoints:
        result = TechScoutHarness(services, checkpoints, now=clock).run(
            _initial_state()
        )

    assert result.state.terminal_status is TerminalStatus.FAILED
    assert result.state.failures[-1].code is FailureCode.DEADLINE_EXCEEDED
