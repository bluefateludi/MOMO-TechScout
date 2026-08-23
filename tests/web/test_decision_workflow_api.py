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


def _run_body() -> dict[str, object]:
    return {
        "decision_context": {
            "question": "Choose a local vector store",
            "project_summary": "A Python local RAG service",
            "deployment": {
                "python_version": "3.11",
                "operating_system": "Linux",
                "deployment": "single node",
            },
            "must_haves": ["Persist data across restarts."],
            "preferences": ["Prefer low operational burden."],
        },
        "candidates": [{"name": "Chroma"}],
        "mode": "fast",
    }


def _requirements_body() -> dict[str, object]:
    return {"requirements": [
        {
            "requirement_id": "requirement:persistence",
            "kind": "hard_constraint",
            "statement": "Persist data across restarts.",
        },
        {
            "requirement_id": "requirement:operations",
            "kind": "evaluation_criterion",
            "statement": "Prefer low operational burden.",
        },
    ]}


def test_http_workflow_gates_research_until_both_confirmations(tmp_path) -> None:
    with _client(tmp_path) as client:
        created = client.post("/api/v2/runs", json=_run_body())
        run_id = created.json()["id"]

        draft = client.get(f"/api/v2/runs/{run_id}/workflow")
        blocked = client.get(f"/api/v2/runs/{run_id}/workflow/research-plan")
        review = client.post(
            f"/api/v2/runs/{run_id}/workflow/requirements-review",
            json=_requirements_body(),
            headers={"Idempotency-Key": "review-1"},
        )
        confirmed = client.post(
            f"/api/v2/runs/{run_id}/workflow/confirm-requirements",
            headers={"Idempotency-Key": "requirements-1"},
        )
        still_blocked = client.get(f"/api/v2/runs/{run_id}/workflow/research-plan")
        ready = client.post(
            f"/api/v2/runs/{run_id}/workflow/confirm-criteria",
            json={"contract_id": confirmed.json()["selection_criteria"]["contract_id"]},
            headers={"Idempotency-Key": "criteria-1"},
        )
        plan = client.get(f"/api/v2/runs/{run_id}/workflow/research-plan")
        events = client.get(f"/api/v2/runs/{run_id}/workflow/events")

    assert created.status_code == 202
    assert draft.json()["state"] == "draft_context"
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "research_not_ready"
    assert review.json()["state"] == "requirements_review"
    assert confirmed.json()["state"] == "criteria_confirmation"
    assert still_blocked.status_code == 409
    assert ready.json()["state"] == "research_ready"
    assert plan.status_code == 200
    assert plan.json()["criteria_contract_id"] == confirmed.json()["selection_criteria"]["contract_id"]
    assert [item["event_type"] for item in events.json()["items"]] == [
        "workflow.created",
        "requirements.review_started",
        "requirements.confirmed",
        "criteria.confirmed",
    ]


def test_http_workflow_reports_transition_and_idempotency_errors(tmp_path) -> None:
    with _client(tmp_path) as client:
        run_id = client.post("/api/v2/runs", json=_run_body()).json()["id"]
        early = client.post(
            f"/api/v2/runs/{run_id}/workflow/confirm-requirements",
            headers={"Idempotency-Key": "too-early"},
        )
        first = client.post(
            f"/api/v2/runs/{run_id}/workflow/requirements-review",
            json=_requirements_body(),
            headers={"Idempotency-Key": "review-command"},
        )
        replay = client.post(
            f"/api/v2/runs/{run_id}/workflow/requirements-review",
            json=_requirements_body(),
            headers={"Idempotency-Key": "review-command"},
        )
        changed = _requirements_body()
        changed["requirements"][0]["statement"] = "A changed requirement."
        conflict = client.post(
            f"/api/v2/runs/{run_id}/workflow/requirements-review",
            json=changed,
            headers={"Idempotency-Key": "review-command"},
        )

    assert early.status_code == 409
    assert early.json()["error"]["code"] == "invalid_workflow_transition"
    assert first.json() == replay.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "workflow_command_conflict"
