"""Canonical input contract for a technology-selection decision.

The run request remains the execution envelope. This module owns the decision
facts inside that envelope and the single compatibility adapter for pre-context
run payloads.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from typing_extensions import Self


DecisionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
QuestionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=1000)]
SummaryText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]
PythonVersion = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
OperatingSystem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
DeploymentDescription = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class _DecisionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class EnvironmentSpec(_DecisionModel):
    """Runtime and deployment environment relevant to a decision."""

    python_version: PythonVersion
    operating_system: OperatingSystem
    deployment: DeploymentDescription


class DecisionContext(_DecisionModel):
    """The user-owned facts and criteria that frame one technology decision."""

    question: QuestionText
    project_summary: SummaryText
    current_stack: tuple[DecisionText, ...] = Field(default=(), max_length=20)
    use_cases: tuple[DecisionText, ...] = Field(default=(), max_length=12)
    deployment: EnvironmentSpec
    team_capabilities: tuple[DecisionText, ...] = Field(default=(), max_length=12)
    performance_requirements: tuple[DecisionText, ...] = Field(default=(), max_length=12)
    budget_constraints: tuple[DecisionText, ...] = Field(default=(), max_length=12)
    security_requirements: tuple[DecisionText, ...] = Field(default=(), max_length=12)
    license_requirements: tuple[DecisionText, ...] = Field(default=(), max_length=12)
    must_haves: tuple[DecisionText, ...] = Field(min_length=1, max_length=5)
    preferences: tuple[DecisionText, ...] = Field(default=(), max_length=12)

    @field_validator(
        "current_stack",
        "use_cases",
        "team_capabilities",
        "performance_requirements",
        "budget_constraints",
        "security_requirements",
        "license_requirements",
        "must_haves",
        "preferences",
        mode="before",
    )
    @classmethod
    def normalize_items(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[object] = []
        for item in value:
            if isinstance(item, str):
                item = item.strip()
                if not item:
                    raise ValueError("decision context lists must not contain blank items")
            normalized.append(item)
        return tuple(normalized)

    @model_validator(mode="after")
    def criteria_are_unambiguous(self) -> Self:
        collection_names = (
            "current_stack",
            "use_cases",
            "team_capabilities",
            "performance_requirements",
            "budget_constraints",
            "security_requirements",
            "license_requirements",
            "must_haves",
            "preferences",
        )
        for name in collection_names:
            values = getattr(self, name)
            normalized = [value.casefold() for value in values]
            if len(normalized) != len(set(normalized)):
                raise ValueError(f"{name} entries must be unique")
        must_haves = {value.casefold() for value in self.must_haves}
        preferences = {value.casefold() for value in self.preferences}
        if must_haves & preferences:
            raise ValueError("must-haves and preferences must be disjoint")
        return self


_LEGACY_CONTEXT_KEYS = frozenset({
    "question",
    "project_context",
    "environment",
    "hard_constraints",
    "current_stack",
    "use_cases",
    "team_capabilities",
    "performance_requirements",
    "budget_constraints",
    "security_requirements",
    "license_requirements",
    "preferences",
})
_DECISION_COLLECTION_KEYS = (
    "current_stack",
    "use_cases",
    "team_capabilities",
    "performance_requirements",
    "budget_constraints",
    "security_requirements",
    "license_requirements",
    "preferences",
)


def normalize_decision_request(value: Any) -> Any:
    """Normalize an old flat request into the canonical nested representation."""

    if not isinstance(value, Mapping):
        return value
    payload = dict(value)
    present_legacy = _LEGACY_CONTEXT_KEYS.intersection(payload)
    if "decision_context" in payload:
        if present_legacy:
            raise ValueError("cannot mix decision_context with legacy context fields")
        return payload
    context = {
        "question": payload.pop("question", None),
        "project_summary": payload.pop("project_context", None),
        "deployment": payload.pop("environment", None),
        "must_haves": payload.pop("hard_constraints", None),
    }
    payload["decision_context"] = context
    return payload


def flatten_decision_request(value: Any) -> Any:
    """Project canonical input onto the legacy-compatible HTTP transport shape."""

    normalized = normalize_decision_request(value)
    if not isinstance(normalized, dict):
        return normalized
    context = normalized.pop("decision_context")
    if isinstance(context, DecisionContext):
        context = context.model_dump(mode="python")
    if not isinstance(context, Mapping):
        return normalized
    normalized.update({
        "question": context.get("question"),
        "project_context": context.get("project_summary"),
        "environment": context.get("deployment"),
        "hard_constraints": context.get("must_haves"),
    })
    for key in _DECISION_COLLECTION_KEYS:
        normalized[key] = context.get(key, normalized.get(key, []))
    return normalized
