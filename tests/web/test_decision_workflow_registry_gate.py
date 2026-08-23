from datetime import datetime, timedelta, timezone

from paper_agent.techscout.decision_context import DecisionContext, EnvironmentSpec
from paper_agent.techscout.planning import (
    PlannerDraft,
    RequirementKind,
    StaticCriteriaDraftPlanner,
    UserRequirement,
)
from paper_agent.techscout.workflow import DecisionWorkflowService, SqliteDecisionWorkflowStore
from paper_agent.web.registry import RunRegistry
from paper_agent.web.techscout_api_models import TechScoutCreateRunRequest


def test_registry_exposes_a_workflow_run_to_execution_only_after_research_ready(tmp_path) -> None:
    path = tmp_path / "registry.sqlite3"
    registry = RunRegistry(path)
    workflow = DecisionWorkflowService(
        SqliteDecisionWorkflowStore(path),
        planner=StaticCriteriaDraftPlanner(PlannerDraft()),
    )
    context = DecisionContext(
        question="Choose a local vector store",
        project_summary="A Python retrieval service",
        deployment=EnvironmentSpec(
            python_version="3.11", operating_system="Linux", deployment="single node",
        ),
        must_haves=("Persist data across restarts.",),
    )
    request = TechScoutCreateRunRequest.model_validate({
        "decision_context": context.model_dump(mode="json"),
        "candidates": [{"name": "Chroma"}],
        "mode": "fast",
    })
    run_id = "00000000-0000-4000-8000-000000000401"
    registry.admit_techscout_idempotent(
        run_id,
        request,
        capacity=4,
        workflow_required=True,
    )
    workflow.create(run_id, context)

    assert registry.active_techscout() == []
    workflow.review_requirements(
        run_id,
        command_id="review-1",
        requirements=(UserRequirement(
            requirement_id="requirement:persistence",
            kind=RequirementKind.HARD_CONSTRAINT,
            statement="Persist data across restarts.",
        ),),
    )
    criteria = workflow.confirm_requirements(run_id, command_id="requirements-1")
    assert registry.active_techscout() == []

    workflow.confirm_criteria(
        run_id,
        command_id="criteria-1",
        contract_id=criteria.selection_criteria.contract_id,
    )

    assert [row.id for row in registry.active_techscout()] == [run_id]


def test_workflow_review_time_does_not_consume_the_execution_deadline(
    tmp_path, monkeypatch,
) -> None:
    import paper_agent.web.registry as registry_module

    admitted_at = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(registry_module, "utc_now", lambda: admitted_at)
    path = tmp_path / "registry.sqlite3"
    registry = RunRegistry(path)
    workflow = DecisionWorkflowService(
        SqliteDecisionWorkflowStore(path),
        planner=StaticCriteriaDraftPlanner(PlannerDraft()),
    )
    context = DecisionContext(
        question="Choose a local vector store",
        project_summary="A Python retrieval service",
        deployment=EnvironmentSpec(
            python_version="3.11", operating_system="Linux", deployment="single node",
        ),
        must_haves=("Persist data across restarts.",),
    )
    request = TechScoutCreateRunRequest.model_validate({
        "decision_context": context.model_dump(mode="json"),
        "candidates": [{"name": "Chroma"}],
        "mode": "fast",
    })
    run_id = "00000000-0000-4000-8000-000000000402"
    registry.admit_techscout_idempotent(
        run_id,
        request,
        capacity=4,
        deadline_seconds=1,
        workflow_required=True,
    )
    workflow.create(run_id, context)
    workflow.review_requirements(
        run_id,
        command_id="review-after-delay",
        requirements=(UserRequirement(
            requirement_id="requirement:persistence",
            kind=RequirementKind.HARD_CONSTRAINT,
            statement="Persist data across restarts.",
        ),),
    )
    criteria = workflow.confirm_requirements(
        run_id, command_id="requirements-after-delay",
    )
    workflow.confirm_criteria(
        run_id,
        command_id="criteria-after-delay",
        contract_id=criteria.selection_criteria.contract_id,
    )
    execution_started_at = admitted_at + timedelta(seconds=2)
    monkeypatch.setattr(
        registry_module, "utc_now", lambda: execution_started_at,
    )
    registry = RunRegistry(path)

    claimed = registry.claim_techscout(run_id, worker_id="worker-test")

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.deadline_at == execution_started_at + timedelta(seconds=1)
