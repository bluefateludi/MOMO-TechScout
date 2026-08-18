from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from paper_agent.config import Settings
from paper_agent.web.app import create_app
from paper_agent.web.api_models import CreateRunRequest
from paper_agent.web.techscout_fixtures import FIXTURE_NOTICE, SYNTHETIC_RUN_ID
from paper_agent.web.techscout_api_models import TechScoutIssueProjection


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(
        state_root=tmp_path / "state", output_root=tmp_path / "outputs",
        demo_root=None, web_dist=tmp_path / "missing",
        runner=lambda *args, **kwargs: None,
        settings_loader=lambda: Settings(dashscope_api_key="unused"),
    ))


def test_v2_fixture_projects_run_report_candidates_evidence_and_trace(tmp_path):
    with _client(tmp_path) as client:
        listing = client.get("/api/v2/runs")
        assert listing.status_code == 200
        assert listing.json()["items"][0]["id"] == SYNTHETIC_RUN_ID

        detail = client.get(f"/api/v2/runs/{SYNTHETIC_RUN_ID}").json()
        assert detail["synthetic"] is True
        assert detail["progress"]["completed_stages"] == ["plan", "research", "verify", "decide"]
        assert detail["approval"]["status"] == "not_required"

        report = client.get(f"/api/v2/runs/{SYNTHETIC_RUN_ID}/report").json()
        assert report["recommendation"] == "chroma"
        assert FIXTURE_NOTICE in report["limitations"]
        assert report["poc_results"][2]["status"] == "research_only"

        candidates = client.get(f"/api/v2/runs/{SYNTHETIC_RUN_ID}/candidates").json()["items"]
        assert candidates[2]["support_level"] == "research_only"
        candidate = client.get(f"/api/v2/runs/{SYNTHETIC_RUN_ID}/candidates/chroma")
        assert candidate.status_code == 200

        evidence = client.get(f"/api/v2/runs/{SYNTHETIC_RUN_ID}/evidence").json()["items"]
        assert all(item["source_url"] is None for item in evidence)

        first = client.get(f"/api/v2/runs/{SYNTHETIC_RUN_ID}/trace?limit=2").json()
        assert len(first["items"]) == 2 and first["next_cursor"]
        second = client.get(
            f"/api/v2/runs/{SYNTHETIC_RUN_ID}/trace",
            params={"limit": 2, "cursor": first["next_cursor"]},
        ).json()
        assert len(second["items"]) == 2 and second["next_cursor"] is None


def test_v2_create_enters_real_harness_queue(tmp_path):
    body = {
        "question": "Choose a local vector store",
        "project_context": "A Python local RAG application",
        "environment": {
            "python_version": "3.11", "operating_system": "Linux",
            "deployment": "single node",
        },
        "hard_constraints": ["local persistence"],
        "candidates": [{"name": "Chroma"}],
        "mode": "fast",
    }
    with _client(tmp_path) as client:
        response = client.post("/api/v2/runs", json=body)
        assert response.status_code == 202
        assert response.json()["status"] in {"queued", "running"}
        assert response.json()["synthetic"] is True


def test_v2_idempotency_context_health_and_readiness(tmp_path):
    body = {
        "question": "Choose a local vector store",
        "project_context": "A Python local RAG application",
        "environment": {
            "python_version": "3.11", "operating_system": "Linux",
            "deployment": "single node",
        },
        "hard_constraints": ["local persistence"],
        "candidates": [{"name": "Chroma"}],
        "mode": "fast",
    }
    with _client(tmp_path) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")
        assert live.json() == {"status": "ok"}
        assert ready.status_code == 200
        assert ready.json()["status"] == "ready"

        headers = {"Idempotency-Key": "submission-1", "X-Request-ID": "req-safe-1"}
        first = client.post("/api/v2/runs", json=body, headers=headers)
        repeated = client.post("/api/v2/runs", json=body, headers=headers)
        assert repeated.json()["id"] == first.json()["id"]
        assert first.headers["x-request-id"] == "req-safe-1"
        assert repeated.headers["x-request-id"] == "req-safe-1"

        changed = {**body, "question": "Choose another vector store"}
        conflict = client.post("/api/v2/runs", json=changed, headers=headers)
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency_conflict"
        assert conflict.headers["x-request-id"] == "req-safe-1"


def test_v2_trace_does_not_project_a_legacy_registry_run(tmp_path):
    with _client(tmp_path) as client:
        legacy_id = "00000000-0000-4000-8000-000000000099"
        client.app.state.run_service.registry.admit(
            legacy_id,
            CreateRunRequest.model_validate({
                "question": "Legacy paper run", "paper_limit": 1,
                "content_mode": "abstract_only",
                "retrieval": {
                    "mode": "lexical", "candidate_k": 2, "top_k": 1,
                    "rrf_k": 60, "analysis_evidence_per_paper": 1,
                },
            }),
            4,
        )
        response = client.get(f"/api/v2/runs/{legacy_id}/trace")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "run_not_found"


def test_v2_trace_rejects_invalid_cursor_and_oversized_limit(tmp_path):
    with _client(tmp_path) as client:
        invalid = client.get(
            f"/api/v2/runs/{SYNTHETIC_RUN_ID}/trace", params={"cursor": "bad"},
        )
        assert invalid.status_code == 422
        oversized = client.get(
            f"/api/v2/runs/{SYNTHETIC_RUN_ID}/trace", params={"limit": 101},
        )
        assert oversized.status_code == 422
        oversized_cursor = client.get(
            f"/api/v2/runs/{SYNTHETIC_RUN_ID}/trace", params={"cursor": "x" * 129},
        )
        assert oversized_cursor.status_code == 422


def test_issue_projection_rejects_raw_server_message_channel():
    with pytest.raises(ValidationError):
        TechScoutIssueProjection.model_validate({
            "stage": "verify", "code": "poc_timeout",
            "message": "secret-canary provider body C:\\private\\path",
            "retryable_by_new_run": True,
        })
