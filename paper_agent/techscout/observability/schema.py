from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from pydantic import Field, JsonValue, field_validator, model_validator

from paper_agent.techscout.models import TechScoutModel


class TraceEventName(str, Enum):
    PLAN_CREATED = "plan.created"
    SKILL_SELECTED = "skill.selected"
    MCP_TOOL_STARTED = "mcp.tool.started"
    MCP_TOOL_FINISHED = "mcp.tool.finished"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    CHECKPOINT_CREATED = "checkpoint.created"
    STATE_TRANSITIONED = "state.transitioned"
    VALIDATION_COMPLETED = "validation.completed"
    ERROR_CLASSIFIED = "error.classified"
    RETRY_SCHEDULED = "retry.scheduled"
    RECOVERY_STARTED = "recovery.started"
    RECOVERY_FINISHED = "recovery.finished"
    TERMINAL_COMPLETED = "terminal.completed"


_COMMON_ATTRIBUTES = {"case_id", "harness_variant"}
_ALLOWED_ATTRIBUTES = {
    TraceEventName.PLAN_CREATED: {"plan_id", "dimension_count", "decision_code"},
    TraceEventName.SKILL_SELECTED: {"skill_id", "stage", "reason_code"},
    TraceEventName.MCP_TOOL_STARTED: {
        "tool_call_id", "tool_name", "skill_id", "safe_parameter_names", "parameter_count"
    },
    TraceEventName.MCP_TOOL_FINISHED: {
        "tool_call_id", "tool_name", "latency_ms", "cache_status", "error_code",
        "prompt_tokens", "completion_tokens", "estimated_cost"
    },
    TraceEventName.TOOL_STARTED: {"tool_call_id", "tool_name", "skill_id"},
    TraceEventName.TOOL_FINISHED: {
        "tool_call_id", "tool_name", "latency_ms", "cache_status", "error_code"
    },
    TraceEventName.CHECKPOINT_CREATED: {
        "checkpoint_id", "parent_checkpoint_id", "stage", "sequence"
    },
    TraceEventName.STATE_TRANSITIONED: {"from_stage", "to_stage"},
    TraceEventName.VALIDATION_COMPLETED: {
        "gate_outcome", "checked_constraint_count", "failure_count"
    },
    TraceEventName.ERROR_CLASSIFIED: {
        "failure_id", "failure_code", "failure_stage", "recoverable", "attempt"
    },
    TraceEventName.RETRY_SCHEDULED: {"failure_id", "stage", "attempt"},
    TraceEventName.RECOVERY_STARTED: {
        "failure_id", "checkpoint_id", "stage", "recovery_action"
    },
    TraceEventName.RECOVERY_FINISHED: {
        "failure_id", "checkpoint_id", "stage", "succeeded"
    },
    TraceEventName.TERMINAL_COMPLETED: {
        "terminal_status", "gate_outcome", "latency_ms", "prompt_tokens",
        "completion_tokens", "total_tokens", "retry_count", "recovery_count",
        "report_sha256", "manifest_sha256", "model_revision",
        "provider_usage_reported",
    },
}
_REQUIRED_ATTRIBUTES = {
    TraceEventName.TERMINAL_COMPLETED: {
        "terminal_status",
        "gate_outcome",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "retry_count",
        "recovery_count",
        "report_sha256",
        "manifest_sha256",
    }
}


class TraceEvent(TechScoutModel):
    schema_version: str = "1.0"
    record_type: str = "event"
    sequence: int = Field(ge=1)
    timestamp: datetime
    run_id: str = Field(min_length=1)
    name: TraceEventName
    status: str = Field(pattern=r"^(started|ok|degraded|error)$")
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("trace timestamp must be UTC-aware")
        return value

    @model_validator(mode="after")
    def attributes_are_allowlisted(self) -> "TraceEvent":
        unknown = set(self.attributes) - (_ALLOWED_ATTRIBUTES[self.name] | _COMMON_ATTRIBUTES)
        if unknown:
            raise ValueError("trace event attribute is not allowlisted")
        missing = _REQUIRED_ATTRIBUTES.get(self.name, set()) - set(self.attributes)
        if missing:
            raise ValueError("trace event is missing required attributes")
        if self.name in {TraceEventName.RECOVERY_STARTED, TraceEventName.RECOVERY_FINISHED}:
            checkpoint_id = self.attributes.get("checkpoint_id")
            if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
                raise ValueError("recovery event requires a checkpoint link")
        return self


def validate_event(
    *,
    sequence: int,
    timestamp: datetime,
    run_id: str,
    name: TraceEventName,
    status: str,
    attributes: dict[str, Any],
) -> TraceEvent:
    return TraceEvent.model_validate(
        {
            "sequence": sequence,
            "timestamp": timestamp,
            "run_id": run_id,
            "name": name,
            "status": status,
            "attributes": attributes,
        }
    )
