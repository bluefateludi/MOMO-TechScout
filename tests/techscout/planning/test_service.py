import pytest
from pydantic import ValidationError

from paper_agent.techscout.planning import (
    CriteriaPlanningInput,
    PlannerDraft,
    PocCheckDraft,
    RequirementKind,
    ResearchQuestionDraft,
    SelectionCriteriaContract,
    SelectionCriteriaService,
    StaticCriteriaDraftPlanner,
    UserRequirement,
)


def _input() -> CriteriaPlanningInput:
    return CriteriaPlanningInput(
        run_id="run:criteria-001",
        requirements=(
            UserRequirement(
                requirement_id="requirement:persistence",
                kind=RequirementKind.HARD_CONSTRAINT,
                statement="Must persist data across local process restarts.",
            ),
            UserRequirement(
                requirement_id="requirement:operations",
                kind=RequirementKind.EVALUATION_CRITERION,
                statement="Prefer the lower operational burden.",
            ),
            UserRequirement(
                requirement_id="requirement:dataset-size",
                kind=RequirementKind.UNKNOWN,
                statement="Expected production dataset size is not known.",
            ),
        ),
    )


def _draft() -> PlannerDraft:
    return PlannerDraft(
        research_questions=(
            ResearchQuestionDraft(
                question="What services are required for local deployment?",
                requirement_ids=("requirement:operations",),
            ),
            ResearchQuestionDraft(
                question="Which persistence guarantees are documented?",
                requirement_ids=("requirement:persistence",),
            ),
        ),
        poc_checks=(
            PocCheckDraft(
                check="Restart the local process and read the stored fixture.",
                requirement_ids=("requirement:persistence",),
            ),
        ),
    )


def test_service_builds_five_distinct_auditable_categories() -> None:
    planning_input = _input()
    planner = StaticCriteriaDraftPlanner(_draft())

    contract = SelectionCriteriaService(planner).create(planning_input)

    assert planner.calls == [planning_input]
    assert [item.statement for item in contract.hard_constraints] == [
        "Must persist data across local process restarts."
    ]
    assert contract.hard_constraints[0].requirement_ids == ("requirement:persistence",)
    assert [item.statement for item in contract.evaluation_criteria] == [
        "Prefer the lower operational burden."
    ]
    assert contract.unknowns[0].requirement_ids == ("requirement:dataset-size",)
    assert len(contract.research_questions) == 2
    assert contract.poc_checks[0].requirement_ids == ("requirement:persistence",)
    assert contract.contract_id.startswith("criteria-contract:")
    assert (
        SelectionCriteriaContract.model_validate_json(contract.model_dump_json())
        == contract
    )


def test_user_owned_categories_and_statements_cannot_be_rewritten_by_planner() -> None:
    contract = SelectionCriteriaService(
        StaticCriteriaDraftPlanner(PlannerDraft())
    ).create(_input())

    assert contract.hard_constraints[0].statement == next(
        item.statement
        for item in _input().requirements
        if item.kind is RequirementKind.HARD_CONSTRAINT
    )
    assert contract.evaluation_criteria[0].statement == next(
        item.statement
        for item in _input().requirements
        if item.kind is RequirementKind.EVALUATION_CRITERION
    )
    assert contract.unknowns[0].statement == next(
        item.statement
        for item in _input().requirements
        if item.kind is RequirementKind.UNKNOWN
    )
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        PlannerDraft.model_validate(
            {
                "hard_constraints": [
                    {
                        "statement": "The planner says this is mandatory.",
                        "requirement_ids": ["requirement:operations"],
                    }
                ]
            }
        )


def test_planner_cannot_reference_a_requirement_that_the_user_did_not_supply() -> None:
    draft = PlannerDraft(
        research_questions=(
            ResearchQuestionDraft(
                question="Which option is cheapest?",
                requirement_ids=("requirement:invented-budget",),
            ),
        )
    )

    with pytest.raises(ValueError, match="unknown requirements"):
        SelectionCriteriaService(StaticCriteriaDraftPlanner(draft)).create(_input())


def test_serialized_contract_cannot_reclassify_or_rewrite_a_user_requirement() -> None:
    contract = SelectionCriteriaService(
        StaticCriteriaDraftPlanner(PlannerDraft())
    ).create(_input())
    payload = contract.model_dump()
    payload["hard_constraints"][0]["requirement_ids"] = ("requirement:operations",)
    with pytest.raises(ValidationError, match="wrong requirement kind"):
        SelectionCriteriaContract.model_validate(payload)

    payload = contract.model_dump()
    payload["hard_constraints"][0]["statement"] = "A model rewrite."
    with pytest.raises(ValidationError, match="preserve the user statement"):
        SelectionCriteriaContract.model_validate(payload)


def test_ids_are_stable_when_requirement_and_planner_order_change() -> None:
    first = SelectionCriteriaService(StaticCriteriaDraftPlanner(_draft())).create(
        _input()
    )
    reversed_draft = _draft().model_copy(
        update={"research_questions": tuple(reversed(_draft().research_questions))}
    )
    second = SelectionCriteriaService(
        StaticCriteriaDraftPlanner(reversed_draft)
    ).create(
        _input().model_copy(
            update={"requirements": tuple(reversed(_input().requirements))}
        )
    )

    assert first == second


def test_duplicate_requirements_and_duplicate_planner_items_fail_closed() -> None:
    requirement = _input().requirements[0]
    with pytest.raises(ValidationError, match="requirement identifiers must be unique"):
        CriteriaPlanningInput(
            run_id="run:criteria-001",
            requirements=(requirement, requirement),
        )

    question = ResearchQuestionDraft(
        question="Which persistence guarantees are documented?",
        requirement_ids=("requirement:persistence",),
    )
    with pytest.raises(ValueError, match="duplicate research questions"):
        SelectionCriteriaService(
            StaticCriteriaDraftPlanner(
                PlannerDraft(research_questions=(question, question))
            )
        ).create(_input())
