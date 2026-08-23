import hashlib
import json
from typing import Protocol

from paper_agent.techscout.planning.contracts import (
    CriteriaPlanningInput,
    EvaluationCriterion,
    HardConstraint,
    PlannerDraft,
    PocCheck,
    RequirementKind,
    ResearchQuestion,
    SelectionCriteriaContract,
    Unknown,
    UserRequirement,
)


class CriteriaDraftPlanner(Protocol):
    """Injectable boundary for model-backed or deterministic planning."""

    def plan(self, planning_input: CriteriaPlanningInput) -> PlannerDraft: ...


class StaticCriteriaDraftPlanner:
    """Small deterministic fake suitable for tests and offline composition."""

    def __init__(self, draft: PlannerDraft) -> None:
        self._draft = draft
        self.calls: list[CriteriaPlanningInput] = []

    def plan(self, planning_input: CriteriaPlanningInput) -> PlannerDraft:
        self.calls.append(planning_input)
        return self._draft


class SelectionCriteriaService:
    def __init__(self, planner: CriteriaDraftPlanner) -> None:
        self._planner = planner

    def create(
        self,
        planning_input: CriteriaPlanningInput,
    ) -> SelectionCriteriaContract:
        draft = self._planner.plan(planning_input)
        requirements = tuple(
            sorted(
                planning_input.requirements,
                key=lambda item: item.requirement_id,
            )
        )
        requirement_ids = {item.requirement_id for item in requirements}
        self._validate_draft_references(draft, requirement_ids)

        hard_constraints = tuple(
            self._hard_constraint(item)
            for item in requirements
            if item.kind is RequirementKind.HARD_CONSTRAINT
        )
        evaluation_criteria = tuple(
            self._evaluation_criterion(item)
            for item in requirements
            if item.kind is RequirementKind.EVALUATION_CRITERION
        )
        unknowns = tuple(
            self._unknown(item)
            for item in requirements
            if item.kind is RequirementKind.UNKNOWN
        )
        research_questions = tuple(
            sorted(
                (
                    ResearchQuestion(
                        item_id=_item_id(
                            "research-question",
                            item.question,
                            item.requirement_ids,
                        ),
                        question=item.question,
                        requirement_ids=tuple(sorted(item.requirement_ids)),
                    )
                    for item in draft.research_questions
                ),
                key=lambda item: item.item_id,
            )
        )
        poc_checks = tuple(
            sorted(
                (
                    PocCheck(
                        item_id=_item_id(
                            "poc-check",
                            item.check,
                            item.requirement_ids,
                        ),
                        check=item.check,
                        requirement_ids=tuple(sorted(item.requirement_ids)),
                    )
                    for item in draft.poc_checks
                ),
                key=lambda item: item.item_id,
            )
        )
        _reject_duplicate_ids(research_questions, "research questions")
        _reject_duplicate_ids(poc_checks, "PoC checks")

        contract_payload = {
            "run_id": planning_input.run_id,
            "requirements": [item.model_dump(mode="json") for item in requirements],
            "hard_constraints": [
                item.model_dump(mode="json") for item in hard_constraints
            ],
            "evaluation_criteria": [
                item.model_dump(mode="json") for item in evaluation_criteria
            ],
            "unknowns": [item.model_dump(mode="json") for item in unknowns],
            "research_questions": [
                item.model_dump(mode="json") for item in research_questions
            ],
            "poc_checks": [item.model_dump(mode="json") for item in poc_checks],
        }
        contract_id = "criteria-contract:" + _digest(contract_payload)
        return SelectionCriteriaContract(
            contract_id=contract_id,
            run_id=planning_input.run_id,
            requirements=requirements,
            hard_constraints=hard_constraints,
            evaluation_criteria=evaluation_criteria,
            unknowns=unknowns,
            research_questions=research_questions,
            poc_checks=poc_checks,
        )

    @staticmethod
    def _validate_draft_references(
        draft: PlannerDraft,
        requirement_ids: set[str],
    ) -> None:
        for item in (*draft.research_questions, *draft.poc_checks):
            missing = set(item.requirement_ids) - requirement_ids
            if missing:
                raise ValueError(
                    "planner item references unknown requirements: "
                    + ", ".join(sorted(missing))
                )

    @staticmethod
    def _hard_constraint(requirement: UserRequirement) -> HardConstraint:
        return HardConstraint(
            item_id=_item_id(
                "hard-constraint",
                requirement.statement,
                (requirement.requirement_id,),
            ),
            statement=requirement.statement,
            requirement_ids=(requirement.requirement_id,),
        )

    @staticmethod
    def _evaluation_criterion(
        requirement: UserRequirement,
    ) -> EvaluationCriterion:
        return EvaluationCriterion(
            item_id=_item_id(
                "evaluation-criterion",
                requirement.statement,
                (requirement.requirement_id,),
            ),
            statement=requirement.statement,
            requirement_ids=(requirement.requirement_id,),
        )

    @staticmethod
    def _unknown(requirement: UserRequirement) -> Unknown:
        return Unknown(
            item_id=_item_id(
                "unknown",
                requirement.statement,
                (requirement.requirement_id,),
            ),
            statement=requirement.statement,
            requirement_ids=(requirement.requirement_id,),
        )


def _item_id(prefix: str, content: str, requirement_ids: tuple[str, ...]) -> str:
    return f"{prefix}:{_digest({'content': content, 'requirements': sorted(requirement_ids)})}"


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _reject_duplicate_ids(items: tuple[object, ...], label: str) -> None:
    item_ids = [getattr(item, "item_id") for item in items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError(f"planner produced duplicate {label}")
