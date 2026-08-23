import json

import pytest
from pydantic import ValidationError

from paper_agent.techscout.decision_context import DecisionContext, EnvironmentSpec
from paper_agent.techscout.models import Candidate, ResearchRequest


def _context_payload() -> dict[str, object]:
    return {
        "question": "Which vector store should power this service?",
        "project_summary": "A multi-tenant retrieval service.",
        "current_stack": ["Python 3.11", "FastAPI", "PostgreSQL"],
        "use_cases": ["semantic search", "metadata-filtered retrieval"],
        "deployment": {
            "python_version": "3.11",
            "operating_system": "Linux",
            "deployment": "Kubernetes",
        },
        "team_capabilities": ["Python operations", "PostgreSQL operations"],
        "performance_requirements": ["p95 query latency below 100 ms"],
        "budget_constraints": ["No new managed service"],
        "security_requirements": ["Tenant isolation"],
        "license_requirements": ["Apache-2.0 compatible"],
        "must_haves": ["metadata filtering", "durable persistence"],
        "preferences": ["low operational overhead"],
    }


def test_decision_context_is_strict_bounded_and_json_round_trippable() -> None:
    context = DecisionContext.model_validate(_context_payload())

    restored = DecisionContext.model_validate_json(context.model_dump_json())

    assert restored == context
    assert restored.must_haves == ("metadata filtering", "durable persistence")
    assert json.loads(context.model_dump_json())["deployment"]["python_version"] == "3.11"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DecisionContext.model_validate({**_context_payload(), "vendor_score": 99})


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"current_stack": ["Python", " python "]}, "must be unique"),
        ({"must_haves": ["Self hosted"], "preferences": ["self HOSTED"]}, "must-haves and preferences"),
        ({"security_requirements": [" "]}, "must not contain blank"),
        ({"must_haves": []}, "at least 1 item"),
    ],
)
def test_decision_context_rejects_ambiguous_or_empty_criteria(
    update: dict[str, object], message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        DecisionContext.model_validate({**_context_payload(), **update})


def test_research_request_owns_one_canonical_decision_context() -> None:
    request = ResearchRequest(
        run_id="run:decision-context",
        decision_context=DecisionContext.model_validate(_context_payload()),
        candidates=(Candidate(candidate_id="candidate:qdrant", name="Qdrant"),),
    )

    payload = request.model_dump(mode="json")

    assert payload["decision_context"] == _context_payload()
    assert "question" not in payload
    assert request.question == request.decision_context.question
    assert request.project_context == request.decision_context.project_summary
    assert request.environment == request.decision_context.deployment
    assert request.hard_constraints == request.decision_context.must_haves


def test_legacy_research_request_is_normalized_without_inventing_rich_fields() -> None:
    request = ResearchRequest.model_validate_json(json.dumps({
        "run_id": "run:legacy",
        "question": "Choose a local vector store",
        "project_context": "A local Python RAG service",
        "environment": {
            "python_version": "3.11",
            "operating_system": "Linux",
            "deployment": "single node",
        },
        "hard_constraints": ["local persistence"],
        "candidates": [],
        "mode": "fast",
    }))

    assert request.decision_context == DecisionContext(
        question="Choose a local vector store",
        project_summary="A local Python RAG service",
        deployment=EnvironmentSpec(
            python_version="3.11",
            operating_system="Linux",
            deployment="single node",
        ),
        must_haves=("local persistence",),
    )
    assert request.decision_context.current_stack == ()
    assert request.decision_context.preferences == ()


def test_request_rejects_mixed_canonical_and_legacy_context_shapes() -> None:
    with pytest.raises(ValidationError, match="cannot mix decision_context"):
        ResearchRequest.model_validate({
            "run_id": "run:mixed",
            "decision_context": _context_payload(),
            "question": "Conflicting question",
            "candidates": [],
            "mode": "fast",
        })
