from enum import Enum

from pydantic import Field, model_validator
from typing_extensions import Self

from paper_agent.techscout.errors import StableId
from paper_agent.techscout.models import NonEmptyStr, TechScoutModel


class RequirementKind(str, Enum):
    HARD_CONSTRAINT = "hard_constraint"
    EVALUATION_CRITERION = "evaluation_criterion"
    UNKNOWN = "unknown"


class UserRequirement(TechScoutModel):
    """An upstream, user-owned atomic requirement.

    Direction-one integration point: intake may construct this value from its
    Decision Context without this module depending on persistence or Web types.
    """

    requirement_id: StableId
    kind: RequirementKind
    statement: NonEmptyStr


class CriteriaPlanningInput(TechScoutModel):
    run_id: StableId
    requirements: tuple[UserRequirement, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def requirement_ids_are_unique(self) -> Self:
        identifiers = [item.requirement_id for item in self.requirements]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("requirement identifiers must be unique")
        return self


class _PlannerItemDraft(TechScoutModel):
    requirement_ids: tuple[StableId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def requirement_ids_are_unique(self) -> Self:
        if len(self.requirement_ids) != len(set(self.requirement_ids)):
            raise ValueError("planner item requirement identifiers must be unique")
        return self


class ResearchQuestionDraft(_PlannerItemDraft):
    question: NonEmptyStr


class PocCheckDraft(_PlannerItemDraft):
    check: NonEmptyStr


class PlannerDraft(TechScoutModel):
    """The only output an injected model planner is allowed to propose.

    Deliberately absent are hard constraints, criteria, weights, thresholds,
    and unknown dispositions. Those remain owned by the user input contract.
    """

    research_questions: tuple[ResearchQuestionDraft, ...] = ()
    poc_checks: tuple[PocCheckDraft, ...] = ()


class _TraceableItem(TechScoutModel):
    item_id: StableId
    requirement_ids: tuple[StableId, ...] = Field(min_length=1)


class HardConstraint(_TraceableItem):
    statement: NonEmptyStr


class EvaluationCriterion(_TraceableItem):
    statement: NonEmptyStr


class Unknown(_TraceableItem):
    statement: NonEmptyStr


class ResearchQuestion(_TraceableItem):
    question: NonEmptyStr


class PocCheck(_TraceableItem):
    check: NonEmptyStr


class SelectionCriteriaContract(TechScoutModel):
    contract_id: StableId
    run_id: StableId
    requirements: tuple[UserRequirement, ...] = Field(min_length=1)
    hard_constraints: tuple[HardConstraint, ...]
    evaluation_criteria: tuple[EvaluationCriterion, ...]
    unknowns: tuple[Unknown, ...]
    research_questions: tuple[ResearchQuestion, ...]
    poc_checks: tuple[PocCheck, ...]

    @model_validator(mode="after")
    def traceability_is_complete_and_category_safe(self) -> Self:
        requirements = {item.requirement_id: item for item in self.requirements}
        if len(requirements) != len(self.requirements):
            raise ValueError("requirement identifiers must be unique")

        collections = (
            self.hard_constraints,
            self.evaluation_criteria,
            self.unknowns,
            self.research_questions,
            self.poc_checks,
        )
        item_ids = [item.item_id for items in collections for item in items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("selection criteria item identifiers must be unique")

        for items in collections:
            for item in items:
                if len(item.requirement_ids) != len(set(item.requirement_ids)):
                    raise ValueError("item requirement identifiers must be unique")
                missing = set(item.requirement_ids) - requirements.keys()
                if missing:
                    raise ValueError(
                        "selection criteria item references unknown requirements: "
                        + ", ".join(sorted(missing))
                    )

        primary_collections = {
            RequirementKind.HARD_CONSTRAINT: self.hard_constraints,
            RequirementKind.EVALUATION_CRITERION: self.evaluation_criteria,
            RequirementKind.UNKNOWN: self.unknowns,
        }
        for kind, items in primary_collections.items():
            for item in items:
                if len(item.requirement_ids) != 1:
                    raise ValueError(f"{kind.value} must map to one requirement")
                requirement = requirements[item.requirement_ids[0]]
                if requirement.kind is not kind:
                    raise ValueError(f"{kind.value} maps to the wrong requirement kind")
                if item.statement != requirement.statement:
                    raise ValueError(f"{kind.value} must preserve the user statement")
            identifiers = {item.requirement_ids[0] for item in items}
            required = {
                item.requirement_id for item in self.requirements if item.kind is kind
            }
            if identifiers != required:
                raise ValueError(f"{kind.value} requirements must map one-to-one")
        return self
