from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator, model_validator
from typing_extensions import Self

from paper_agent.techscout.decision_context import DecisionContext
from paper_agent.techscout.models import NonEmptyStr, ResearchPlan, TechScoutModel
from paper_agent.techscout.planning import SelectionCriteriaContract, UserRequirement


class WorkflowState(str, Enum):
    DRAFT_CONTEXT = "draft_context"
    REQUIREMENTS_REVIEW = "requirements_review"
    CRITERIA_CONFIRMATION = "criteria_confirmation"
    RESEARCH_READY = "research_ready"


class WorkflowEventType(str, Enum):
    WORKFLOW_CREATED = "workflow.created"
    REQUIREMENTS_REVIEW_STARTED = "requirements.review_started"
    REQUIREMENTS_REVIEW_REVISED = "requirements.review_revised"
    REQUIREMENTS_CONFIRMED = "requirements.confirmed"
    CRITERIA_CONFIRMED = "criteria.confirmed"


class DecisionWorkflow(TechScoutModel):
    run_id: NonEmptyStr
    state: WorkflowState
    version: int = Field(ge=1)
    decision_context: DecisionContext
    requirements: tuple[UserRequirement, ...] = ()
    requirements_confirmed: bool = False
    selection_criteria: SelectionCriteriaContract | None = None
    research_plan: ResearchPlan | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("workflow timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def state_contract_is_complete(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("workflow update cannot precede creation")
        if self.state is WorkflowState.DRAFT_CONTEXT:
            if self.requirements or self.requirements_confirmed:
                raise ValueError("draft context cannot contain reviewed requirements")
        else:
            if not self.requirements:
                raise ValueError("requirements review requires user requirements")
        if self.state in {
            WorkflowState.CRITERIA_CONFIRMATION,
            WorkflowState.RESEARCH_READY,
        }:
            if not self.requirements_confirmed:
                raise ValueError("criteria require confirmed requirements")
            if self.selection_criteria is None or self.research_plan is None:
                raise ValueError("criteria confirmation requires criteria and a research plan")
            if self.selection_criteria.run_id != self.run_id:
                raise ValueError("selection criteria belong to another run")
            if self.selection_criteria.requirements != self.requirements:
                raise ValueError("selection criteria must preserve confirmed requirements")
            if self.research_plan.criteria_contract_id != self.selection_criteria.contract_id:
                raise ValueError("research plan must reference the selection criteria")
        elif self.selection_criteria is not None or self.research_plan is not None:
            raise ValueError("unconfirmed requirements cannot have criteria or a research plan")
        return self


class WorkflowEvent(TechScoutModel):
    sequence: int = Field(ge=1)
    run_id: NonEmptyStr
    event_type: WorkflowEventType
    command_id: NonEmptyStr | None = None
    from_state: WorkflowState | None = None
    to_state: WorkflowState
    workflow_version: int = Field(ge=1)
    occurred_at: datetime


class WorkflowEventList(TechScoutModel):
    items: tuple[WorkflowEvent, ...]
