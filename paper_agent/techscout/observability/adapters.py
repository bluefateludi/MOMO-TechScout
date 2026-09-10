from __future__ import annotations

from paper_agent.observability.sanitize import sanitize_bounded_event_data
from paper_agent.techscout.harness.stages import (
    StageArtifacts,
    StageDeadline,
    StageResult,
    StageServices,
)
from paper_agent.techscout.models import GateOutcome, SkillSelection, ToolCall, ToolResult, ToolStatus
from paper_agent.techscout.observability.recorder import TechScoutTraceRecorder
from paper_agent.techscout.observability.schema import TraceEventName
from paper_agent.techscout.recovery import RESEARCH_STAGE_BY_FAILURE_STAGE
from paper_agent.techscout.runtime_skills import SkillRegistry
from paper_agent.techscout.state import ResearchStage, ResearchState
from paper_agent.techscout.tools.runtime import ToolRuntime


class TracingSkillRouter:
    def __init__(self, delegate: SkillRegistry, trace: TechScoutTraceRecorder) -> None:
        self._delegate = delegate
        self._trace = trace

    def route(
        self,
        capability: str,
        stage: ResearchStage | str,
        *,
        selection_id: str,
        reason: str,
    ) -> SkillSelection:
        selection = self._delegate.route(
            capability,
            stage,
            selection_id=selection_id,
            reason=reason,
        )
        self._trace.record(
            TraceEventName.SKILL_SELECTED,
            status="ok",
            attributes={
                "skill_id": selection.skill_id,
                "stage": selection.stage,
                "reason_code": capability,
            },
        )
        return selection


class TracingToolRuntime:
    def __init__(self, delegate: ToolRuntime, trace: TechScoutTraceRecorder) -> None:
        self._delegate = delegate
        self._trace = trace

    async def discover_tools(self, skill_id: str | None = None) -> tuple[str, ...]:
        return await self._delegate.discover_tools(skill_id)

    async def invoke(self, call: ToolCall) -> ToolResult:
        safe_names = sorted(sanitize_bounded_event_data(call.arguments))
        started = {
            "tool_call_id": call.tool_call_id,
            "tool_name": call.tool_name,
            "skill_id": call.skill_id,
            "safe_parameter_names": safe_names,
            "parameter_count": len(safe_names),
        }
        self._trace.record(TraceEventName.MCP_TOOL_STARTED, status="started", attributes=started)
        self._trace.record(
            TraceEventName.TOOL_STARTED,
            status="started",
            attributes={key: started[key] for key in ("tool_call_id", "tool_name", "skill_id")},
        )
        result = await self._delegate.invoke(call)
        finished = {
            "tool_call_id": result.tool_call_id,
            "tool_name": call.tool_name,
            "latency_ms": result.latency_ms,
            "cache_status": result.cache_status.value,
            "error_code": result.error_code.value if result.error_code is not None else None,
        }
        status = "ok" if result.status is ToolStatus.SUCCEEDED else "error"
        self._trace.record(TraceEventName.TOOL_FINISHED, status=status, attributes=finished)
        self._trace.record(TraceEventName.MCP_TOOL_FINISHED, status=status, attributes=finished)
        return result


class TracingStageServices:
    def __init__(self, delegate: StageServices, trace: TechScoutTraceRecorder) -> None:
        self._delegate = delegate
        self._trace = trace

    def execute(
        self,
        stage: ResearchStage,
        state: ResearchState,
        artifacts: StageArtifacts,
        deadline: StageDeadline,
    ) -> StageResult:
        failure = state.failures[-1] if state.failures else None
        is_recovery = (
            state.gate_outcome is GateOutcome.RECOVER
            and failure is not None
            and RESEARCH_STAGE_BY_FAILURE_STAGE.get(failure.stage) is stage
        )
        if is_recovery:
            if state.checkpoint is None:
                raise ValueError("recovery trace requires a checkpoint link")
            self._trace.record(
                TraceEventName.RETRY_SCHEDULED,
                status="started",
                attributes={
                    "failure_id": failure.failure_id,
                    "stage": stage.value,
                    "attempt": failure.attempt + 1,
                },
            )
            self._trace.record(
                TraceEventName.RECOVERY_STARTED,
                status="started",
                attributes={
                    "failure_id": failure.failure_id,
                    "checkpoint_id": state.checkpoint.checkpoint_id,
                    "stage": stage.value,
                    "recovery_action": failure.recovery_action.value
                    if failure.recovery_action
                    else "fail_safely",
                },
            )
        try:
            result = self._delegate.execute(stage, state, artifacts, deadline)
        except Exception:
            if is_recovery:
                self._record_recovery_finished(stage, state, succeeded=False)
            raise
        self._trace.record(
            TraceEventName.STATE_TRANSITIONED,
            status="ok",
            attributes={"from_stage": stage.value, "to_stage": result.state.stage.value},
        )
        if state.plan is None and result.state.plan is not None:
            self._trace.record(
                TraceEventName.PLAN_CREATED,
                status="ok",
                attributes={
                    "plan_id": result.state.plan.plan_id,
                    "dimension_count": len(result.state.plan.investigation_dimensions),
                    "decision_code": "bounded_research_plan",
                },
            )
        if result.state.checkpoint is not None and result.state.checkpoint != state.checkpoint:
            checkpoint = result.state.checkpoint
            self._trace.record(
                TraceEventName.CHECKPOINT_CREATED,
                status="ok",
                attributes={
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "parent_checkpoint_id": checkpoint.parent_checkpoint_id,
                    "stage": checkpoint.stage.value,
                    "sequence": checkpoint.sequence,
                },
            )
        for failure in result.state.failures[len(state.failures) :]:
            self._trace.record(
                TraceEventName.ERROR_CLASSIFIED,
                status="error",
                attributes={
                    "failure_id": failure.failure_id,
                    "failure_code": failure.code.value,
                    "failure_stage": failure.stage.value,
                    "recoverable": failure.recoverable,
                    "attempt": failure.attempt,
                },
            )
        if stage is ResearchStage.VALIDATE and result.state.gate_outcome is not None:
            self._trace.record(
                TraceEventName.VALIDATION_COMPLETED,
                status="ok" if not result.state.failures else "error",
                attributes={
                    "gate_outcome": result.state.gate_outcome.value,
                    "checked_constraint_count": len(result.state.request.hard_constraints),
                    "failure_count": len(result.state.failures),
                },
            )
        if is_recovery:
            self._record_recovery_finished(
                stage,
                state,
                succeeded=len(result.state.failures) == len(state.failures),
            )
        return result

    def _record_recovery_finished(
        self,
        stage: ResearchStage,
        state: ResearchState,
        *,
        succeeded: bool,
    ) -> None:
        assert state.checkpoint is not None
        self._trace.record(
            TraceEventName.RECOVERY_FINISHED,
            status="ok" if succeeded else "error",
            attributes={
                "failure_id": state.failures[-1].failure_id,
                "checkpoint_id": state.checkpoint.checkpoint_id,
                "stage": stage.value,
                "succeeded": succeeded,
            },
        )
