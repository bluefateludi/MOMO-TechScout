from datetime import datetime, timezone

import pytest

from paper_agent.techscout.decision_context import DecisionContext, EnvironmentSpec
from paper_agent.techscout.planning import (
    PlannerDraft,
    PocCheckDraft,
    RequirementKind,
    ResearchQuestionDraft,
    StaticCriteriaDraftPlanner,
    UserRequirement,
)
from paper_agent.techscout.workflow import (
    DecisionWorkflowService,
    ResearchNotReadyError,
    SqliteDecisionWorkflowStore,
    WorkflowCommandConflictError,
    WorkflowState,
    WorkflowTransitionError,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _context() -> DecisionContext:
    return DecisionContext(
        question="Which local vector store should we adopt?",
        project_summary="A Python retrieval service.",
        deployment=EnvironmentSpec(
            python_version="3.11",
            operating_system="Linux",
            deployment="single node",
        ),
        must_haves=("Persist data across restarts.",),
        preferences=("Prefer low operational burden.",),
    )


def _requirements() -> tuple[UserRequirement, ...]:
    return (
        UserRequirement(
            requirement_id="requirement:persistence",
            kind=RequirementKind.HARD_CONSTRAINT,
            statement="Persist data across restarts.",
        ),
        UserRequirement(
            requirement_id="requirement:operations",
            kind=RequirementKind.EVALUATION_CRITERION,
            statement="Prefer low operational burden.",
        ),
    )


def _workflow(tmp_path) -> DecisionWorkflowService:
    planner = StaticCriteriaDraftPlanner(PlannerDraft(
        research_questions=(ResearchQuestionDraft(
            question="Which persistence guarantees are documented?",
            requirement_ids=("requirement:persistence",),
        ),),
        poc_checks=(PocCheckDraft(
            check="Restart the process and read the stored fixture.",
            requirement_ids=("requirement:persistence",),
        ),),
    ))
    return DecisionWorkflowService(
        SqliteDecisionWorkflowStore(tmp_path / "workflow.sqlite3"),
        planner=planner,
        clock=lambda: NOW,
    )


def test_confirmed_workflow_becomes_research_ready_and_replays_commands(tmp_path) -> None:
    workflow = _workflow(tmp_path)
    created = workflow.create("run:workflow-001", _context())

    review = workflow.review_requirements(
        created.run_id,
        command_id="command:review",
        requirements=_requirements(),
    )
    replay = workflow.review_requirements(
        created.run_id,
        command_id="command:review",
        requirements=_requirements(),
    )
    criteria = workflow.confirm_requirements(
        created.run_id,
        command_id="command:confirm-requirements",
    )
    ready = workflow.confirm_criteria(
        created.run_id,
        command_id="command:confirm-criteria",
        contract_id=criteria.selection_criteria.contract_id,
    )

    assert created.state is WorkflowState.DRAFT_CONTEXT
    assert review == replay
    assert criteria.state is WorkflowState.CRITERIA_CONFIRMATION
    assert criteria.selection_criteria.hard_constraints[0].statement == _requirements()[0].statement
    assert criteria.selection_criteria.evaluation_criteria[0].statement == _requirements()[1].statement
    assert ready.state is WorkflowState.RESEARCH_READY
    assert workflow.research_plan(ready.run_id) == ready.research_plan
    assert ready.research_plan.criteria_contract_id == ready.selection_criteria.contract_id
    assert [event.event_type for event in workflow.events(ready.run_id)] == [
        "workflow.created",
        "requirements.review_started",
        "requirements.confirmed",
        "criteria.confirmed",
    ]


def test_workflow_rejects_illegal_research_and_idempotency_conflicts(tmp_path) -> None:
    workflow = _workflow(tmp_path)
    workflow.create("run:workflow-002", _context())

    with pytest.raises(ResearchNotReadyError):
        workflow.research_plan("run:workflow-002")
    with pytest.raises(WorkflowTransitionError):
        workflow.confirm_requirements(
            "run:workflow-002",
            command_id="command:too-early",
        )

    workflow.review_requirements(
        "run:workflow-002",
        command_id="command:review",
        requirements=_requirements(),
    )
    changed = _requirements()[0].model_copy(update={"statement": "A changed requirement."})
    with pytest.raises(WorkflowCommandConflictError):
        workflow.review_requirements(
            "run:workflow-002",
            command_id="command:review",
            requirements=(changed, _requirements()[1]),
        )


def test_workflow_state_receipts_and_events_survive_service_restart(tmp_path) -> None:
    path = tmp_path / "workflow.sqlite3"
    first = _workflow(tmp_path)
    first.create("run:workflow-003", _context())
    reviewed = first.review_requirements(
        "run:workflow-003",
        command_id="command:review",
        requirements=_requirements(),
    )

    restarted = DecisionWorkflowService(
        SqliteDecisionWorkflowStore(path),
        planner=StaticCriteriaDraftPlanner(PlannerDraft()),
        clock=lambda: NOW,
    )

    assert restarted.get("run:workflow-003") == reviewed
    assert restarted.review_requirements(
        "run:workflow-003",
        command_id="command:review",
        requirements=_requirements(),
    ) == reviewed
    assert [event.event_type for event in restarted.events("run:workflow-003")] == [
        "workflow.created",
        "requirements.review_started",
    ]


def test_requirements_can_be_revised_during_review_but_freeze_on_confirmation(tmp_path) -> None:
    workflow = _workflow(tmp_path)
    workflow.create("run:workflow-004", _context())
    workflow.review_requirements(
        "run:workflow-004",
        command_id="command:review",
        requirements=_requirements(),
    )
    revised = _requirements()[1].model_copy(update={
        "kind": RequirementKind.UNKNOWN,
        "statement": "Operational burden is not yet known.",
    })

    review = workflow.review_requirements(
        "run:workflow-004",
        command_id="command:revise",
        requirements=(_requirements()[0], revised),
    )
    confirmed = workflow.confirm_requirements(
        "run:workflow-004", command_id="command:confirm",
    )

    assert review.requirements[0] == revised
    assert confirmed.selection_criteria.unknowns[0].statement == revised.statement
    assert [event.event_type for event in workflow.events("run:workflow-004")] == [
        "workflow.created",
        "requirements.review_started",
        "requirements.review_revised",
        "requirements.confirmed",
    ]
    with pytest.raises(WorkflowTransitionError):
        workflow.review_requirements(
            "run:workflow-004",
            command_id="command:late-revision",
            requirements=_requirements(),
        )
