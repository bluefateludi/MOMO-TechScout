import hashlib
import json
import sqlite3

from paper_agent.web.registry import RunRegistry
from paper_agent.web.techscout_api_models import TechScoutCreateRunRequest


LEGACY_REQUEST = {
    "question": "Choose a local vector store",
    "project_context": "A Python local RAG service",
    "environment": {
        "python_version": "3.11",
        "operating_system": "Linux",
        "deployment": "single node",
    },
    "hard_constraints": ["local persistence"],
    "candidates": [{"name": "Chroma"}],
    "mode": "fast",
}


def test_registry_persists_and_reads_the_canonical_decision_context(tmp_path) -> None:
    path = tmp_path / "registry.sqlite3"
    registry = RunRegistry(path)
    request = TechScoutCreateRunRequest.model_validate({
        "decision_context": {
            "question": "Choose a vector store",
            "project_summary": "A local retrieval service",
            "current_stack": ["Python 3.11", "FastAPI"],
            "use_cases": ["semantic retrieval"],
            "deployment": {
                "python_version": "3.11",
                "operating_system": "Linux",
                "deployment": "single node",
            },
            "team_capabilities": ["Python operations"],
            "performance_requirements": ["p95 below 100 ms"],
            "budget_constraints": ["No managed service"],
            "security_requirements": ["Data stays local"],
            "license_requirements": ["Permissive license"],
            "must_haves": ["local persistence"],
            "preferences": ["low operational overhead"],
        },
        "candidates": [{"name": "Chroma"}],
        "mode": "fast",
    })
    run_id = "00000000-0000-4000-8000-000000000201"

    registry.admit_techscout(run_id, request, 4)

    with sqlite3.connect(path) as db:
        persisted = json.loads(db.execute(
            "SELECT request_json FROM techscout_runs WHERE id=?", (run_id,),
        ).fetchone()[0])
    restored = RunRegistry(path).get_techscout(run_id)
    assert "decision_context" in persisted
    assert "project_context" not in persisted
    assert restored.request == request
    assert restored.request.decision_context.current_stack == ("Python 3.11", "FastAPI")


def test_registry_reads_and_idempotently_replays_a_pre_decision_context_run(tmp_path) -> None:
    path = tmp_path / "registry.sqlite3"
    registry = RunRegistry(path)
    run_id = "00000000-0000-4000-8000-000000000202"
    request = TechScoutCreateRunRequest.model_validate(LEGACY_REQUEST)
    registry.admit_techscout_idempotent(
        run_id, request, capacity=4, idempotency_key="legacy-submission",
    )
    legacy_json = json.dumps(LEGACY_REQUEST, separators=(",", ":"))

    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE techscout_runs SET request_json=?,request_hash=? WHERE id=?",
            (legacy_json, hashlib.sha256(legacy_json.encode()).hexdigest(), run_id),
        )

    restored = RunRegistry(path).get_techscout(run_id)
    replayed, created = RunRegistry(path).admit_techscout_idempotent(
        "00000000-0000-4000-8000-000000000299",
        TechScoutCreateRunRequest.model_validate(LEGACY_REQUEST),
        capacity=4,
        idempotency_key="legacy-submission",
    )
    assert restored.request.question == LEGACY_REQUEST["question"]
    assert restored.request.decision_context.must_haves == ("local persistence",)
    assert restored.request.decision_context.current_stack == ()
    assert replayed.id == run_id
    assert created is False
