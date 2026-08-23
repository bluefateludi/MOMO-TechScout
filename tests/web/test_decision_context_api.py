from pathlib import Path

from fastapi.testclient import TestClient

from paper_agent.config import Settings
from paper_agent.web.app import create_app


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(
        state_root=tmp_path / "state",
        output_root=tmp_path / "outputs",
        demo_root=None,
        web_dist=tmp_path / "missing",
        runner=lambda *args, **kwargs: None,
        settings_loader=lambda: Settings(dashscope_api_key="unused"),
    ))


def _canonical_body() -> dict[str, object]:
    return {
        "decision_context": {
            "question": "Choose a vector store for this service",
            "project_summary": "A security-sensitive retrieval service",
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
    }


def test_api_accepts_and_projects_the_canonical_decision_context(tmp_path) -> None:
    with _client(tmp_path) as client:
        created = client.post("/api/v2/runs", json=_canonical_body())
        assert created.status_code == 202

        response = client.get(
            f"/api/v2/runs/{created.json()['id']}/decision-context",
        )

    assert response.status_code == 200
    assert response.json() == _canonical_body()["decision_context"]


def test_api_normalizes_a_legacy_run_request_at_the_boundary(tmp_path) -> None:
    body = {
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
    with _client(tmp_path) as client:
        created = client.post("/api/v2/runs", json=body)
        assert created.status_code == 202
        context = client.get(
            f"/api/v2/runs/{created.json()['id']}/decision-context",
        ).json()

    assert context["question"] == body["question"]
    assert context["project_summary"] == body["project_context"]
    assert context["deployment"] == body["environment"]
    assert context["must_haves"] == body["hard_constraints"]
    assert context["current_stack"] == []
    assert context["preferences"] == []


def test_api_rejects_mixed_or_invalid_decision_contexts(tmp_path) -> None:
    mixed = {**_canonical_body(), "question": "Conflicting legacy question"}
    invalid = _canonical_body()
    invalid["decision_context"] = {
        **invalid["decision_context"],
        "preferences": ["local persistence"],
    }
    with _client(tmp_path) as client:
        mixed_response = client.post("/api/v2/runs", json=mixed)
        invalid_response = client.post("/api/v2/runs", json=invalid)

    assert mixed_response.status_code == 422
    assert invalid_response.status_code == 422
