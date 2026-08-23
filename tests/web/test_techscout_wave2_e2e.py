from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from paper_agent.web.app import create_app
from paper_agent.observability.sealed_jsonl import verify_sealed_jsonl
from paper_agent.techscout.harness import SQLiteCheckpointAdapter, TechScoutHarness
from paper_agent.techscout.state import ResearchStage
from paper_agent.techscout.validation import REQUIRED_TERMINAL_ARTIFACTS
from paper_agent.web.registry import RunRegistry
from paper_agent.web.techscout_api_models import TechScoutCreateRunRequest
from paper_agent.web.techscout_execution import (
    DeterministicStageServices,
    TechScoutRunEngine,
    TechScoutSingleRunExecutor,
    demo_mcp_environment,
)


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "techscout"
WEB_FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _body(name: str) -> tuple[dict[str, object], dict[str, object]]:
    path = WEB_FIXTURE_ROOT / name if name.startswith("techscout-") else FIXTURE_ROOT / name
    fixture = json.loads(path.read_text(encoding="utf-8"))
    request = fixture["request"]
    body = {
        "question": request["question"],
        "project_context": request["project_context"],
        "environment": {
            "python_version": request["environment"]["python"],
            "operating_system": request["environment"]["os"],
            "deployment": request["environment"]["deployment"],
        },
        "hard_constraints": request["hard_constraints"],
        "candidates": [
            {
                "name": candidate["display_name"],
                "package_name": candidate.get("package_name"),
            }
            for candidate in request["candidates"]
        ],
        "mode": request["mode"],
    }
    return body, fixture


def _wait_terminal(client: TestClient, run_id: str, hard_bound: float = 120) -> dict[str, object]:
    started = time.monotonic()
    while time.monotonic() - started < hard_bound:
        response = client.get(f"/api/v2/runs/{run_id}")
        assert response.status_code == 200
        detail = response.json()
        if detail["status"] not in {"queued", "running"}:
            assert time.monotonic() - started < hard_bound
            return detail
        time.sleep(0.05)
    pytest.fail(f"TechScout run exceeded {hard_bound}s hard bound")


def _confirm_workflow(client: TestClient, run_id: str, body: dict[str, object]) -> None:
    requirements = [
        {
            "requirement_id": f"requirement:must-have-{index}",
            "kind": "hard_constraint",
            "statement": statement,
        }
        for index, statement in enumerate(body["hard_constraints"])
    ]
    assert client.post(
        f"/api/v2/runs/{run_id}/workflow/requirements-review",
        json={"requirements": requirements},
        headers={"Idempotency-Key": "e2e-review"},
    ).status_code == 200
    criteria = client.post(
        f"/api/v2/runs/{run_id}/workflow/confirm-requirements",
        headers={"Idempotency-Key": "e2e-requirements"},
    )
    assert criteria.status_code == 200
    assert client.post(
        f"/api/v2/runs/{run_id}/workflow/confirm-criteria",
        json={"contract_id": criteria.json()["selection_criteria"]["contract_id"]},
        headers={"Idempotency-Key": "e2e-criteria"},
    ).status_code == 200


@pytest.mark.parametrize(
    ("fixture_name", "expected_status", "expected_verdict", "recovery_attempted"),
    [
        ("happy-path.json", "completed", "recommended", False),
        ("techscout-cached-degradation.json", "completed_with_limitations", "no_safe_winner", False),
        ("bounded-failure-recovery.json", "completed", "recommended", True),
    ],
)
def test_frozen_wave2_create_poll_trace_and_artifacts(
    tmp_path: Path,
    fixture_name: str,
    expected_status: str,
    expected_verdict: str,
    recovery_attempted: bool,
) -> None:
    body, fixture = _body(fixture_name)
    app = create_app(
        state_root=tmp_path / "state",
        output_root=tmp_path / "outputs",
        demo_root=None,
        web_dist=tmp_path / "missing-web",
    )
    with TestClient(app) as client:
        created = client.post("/api/v2/runs", json=body)
        assert created.status_code == 202
        run_id = created.json()["id"]
        _confirm_workflow(client, run_id, body)

        detail = _wait_terminal(client, run_id)
        assert detail["status"] == expected_status
        assert detail["synthetic"] is True
        assert detail["recovery"]["attempted"] is recovery_attempted
        if recovery_attempted:
            assert detail["recovery"]["attempts_used"] == 1
            assert detail["recovery"]["outcome"] == "recovered"

        report_response = client.get(f"/api/v2/runs/{run_id}/report")
        assert report_response.status_code == 200
        report = report_response.json()
        assert report["verdict"] == expected_verdict
        assert report["synthetic"] is True
        assert any("real local MCP" in item for item in report["limitations"])
        if fixture_name == "happy-path.json":
            assert {item["candidate_id"] for item in report["poc_results"]} == {
                "chroma", "qdrant-local", "pgvector",
            }

        assert client.get(f"/api/v2/runs/{run_id}/candidates").status_code == 200
        evidence = client.get(f"/api/v2/runs/{run_id}/evidence")
        assert evidence.status_code == 200
        assert evidence.json()["items"]

        trace = client.get(f"/api/v2/runs/{run_id}/trace?limit=100").json()["items"]
        assert any(item["skill"] == "skill:official-doc-research@1" for item in trace)
        assert any(item["tool"] == "web.search" for item in trace)
        assert any(item["tool"] == "sandbox.run_smoke_test" for item in trace)
        if recovery_attempted:
            assert any(item["status"] == "failed" and "dependency_conflict" in item["label"] for item in trace)
            assert any(item["event_type"] == "recovery" and "checkpoint=" in item["label"] for item in trace)
            assert any(item["event_type"] == "recovery" and item["status"] == "completed" for item in trace)

    run_dir = tmp_path / "outputs" / "techscout" / run_id
    assert REQUIRED_TERMINAL_ARTIFACTS.issubset({path.name for path in run_dir.iterdir()})
    assert (run_dir / "harness-checkpoints.sqlite3").is_file()
    sealed_trace = [json.loads(line) for line in (run_dir / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    trace_names = {item.get("name") for item in sealed_trace}
    assert {"skill.selected", "mcp.tool.started", "state.transitioned", "terminal.completed"} <= trace_names
    assert sealed_trace[-1]["record_type"] == "trace_seal"
    assert verify_sealed_jsonl(run_dir / "traces.jsonl")["sealed"] is True
    if recovery_attempted:
        poc_history = json.loads((run_dir / "poc-results.json").read_text(encoding="utf-8"))
        assert [item["status"] for item in poc_history] == ["failed", "passed", "passed"]
        assert {"error.classified", "recovery.started", "recovery.finished"} <= trace_names
    assert fixture["planning_targets"]["fast_terminal_seconds"] == 120


def test_refresh_requeues_and_resumes_from_harness_checkpoint(tmp_path: Path) -> None:
    body, _ = _body("happy-path.json")
    request = TechScoutCreateRunRequest.model_validate(body)
    registry = RunRegistry(tmp_path / "state" / "run-registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000501"
    registry.admit_techscout(run_id, request, 4)
    row = registry.claim_oldest_techscout()
    assert row is not None

    output_root = tmp_path / "outputs"
    run_dir = output_root / "techscout" / run_id
    services = DeterministicStageServices(
        run_dir=run_dir,
        scenario="happy",
        progress_sink=lambda *args: None,
    )
    state = TechScoutRunEngine._initial_state(f"run:{run_id}", request)
    with SQLiteCheckpointAdapter(run_dir / "harness-checkpoints.sqlite3") as checkpoints:
        interrupted = TechScoutHarness(services, checkpoints).run(
            state, interrupt_after=ResearchStage.PLAN_RESEARCH,
        )
    assert interrupted.state.stage is ResearchStage.PLAN_RESEARCH

    executor = TechScoutSingleRunExecutor(registry, output_root)
    executor.start()
    try:
        started = time.monotonic()
        while time.monotonic() - started < 120:
            resumed = registry.get_techscout(run_id)
            if resumed.status not in {"queued", "running"}:
                break
            time.sleep(0.05)
        assert resumed.status == "completed"
        bundle_path = output_root / (resumed.projection_path or "missing")
        assert bundle_path.is_file()
    finally:
        executor.close()


def test_fast_and_verified_use_disjoint_stage_service_factories(tmp_path: Path) -> None:
    body, _ = _body("happy-path.json")
    calls: list[str] = []

    class FakeVerifiedServices(DeterministicStageServices):
        synthetic = False

    def verified_factory(**kwargs):
        calls.append("verified")
        return FakeVerifiedServices(scenario="happy", **kwargs)

    app = create_app(
        state_root=tmp_path / "state",
        output_root=tmp_path / "outputs",
        demo_root=None,
        web_dist=tmp_path / "missing-web",
        verified_services_factory=verified_factory,
    )
    with TestClient(app) as client:
        fast = client.post("/api/v2/runs", json=body)
        _confirm_workflow(client, fast.json()["id"], body)
        assert _wait_terminal(client, fast.json()["id"])["synthetic"] is True
        assert calls == []

        body["mode"] = "verified"
        verified = client.post("/api/v2/runs", json=body)
        _confirm_workflow(client, verified.json()["id"], body)
        detail = _wait_terminal(client, verified.json()["id"])
        assert calls == ["verified"]
        assert detail["synthetic"] is False


def test_unsupported_candidate_remains_research_only_without_borrowed_recipe(
    tmp_path: Path,
) -> None:
    body, _ = _body("no-safe-winner-research-only.json")
    app = create_app(
        state_root=tmp_path / "state",
        output_root=tmp_path / "outputs",
        demo_root=None,
        web_dist=tmp_path / "missing-web",
    )
    with TestClient(app) as client:
        created = client.post("/api/v2/runs", json=body)
        run_id = created.json()["id"]
        _confirm_workflow(client, run_id, body)
        detail = _wait_terminal(client, run_id)
        assert detail["status"] == "completed_with_limitations"
        assert detail["candidates"][0]["support_level"] == "research_only"
        report = client.get(f"/api/v2/runs/{run_id}/report").json()
        assert report["verdict"] == "no_safe_winner"
        assert report["poc_results"][0]["status"] == "research_only"
        trace = client.get(f"/api/v2/runs/{run_id}/trace?limit=100").json()["items"]
        assert not any(item["tool"] == "sandbox.run_smoke_test" for item in trace)


def test_shortlist_tie_break_selects_first_eligible_not_absolute_first(
    tmp_path: Path,
) -> None:
    body, _ = _body("happy-path.json")
    body["candidates"] = [{"name": "pgvector"}, {"name": "Chroma"}]
    app = create_app(
        state_root=tmp_path / "state", output_root=tmp_path / "outputs",
        demo_root=None, web_dist=tmp_path / "missing-web",
    )
    with TestClient(app) as client:
        created = client.post("/api/v2/runs", json=body)
        run_id = created.json()["id"]
        _confirm_workflow(client, run_id, body)
        detail = _wait_terminal(client, run_id)
        assert detail["status"] == "completed"
        report = client.get(f"/api/v2/runs/{run_id}/report").json()
        assert report["recommendation"] == "chroma"


def test_executor_exception_always_publishes_failed_terminal_projection(
    tmp_path: Path, caplog,
) -> None:
    body, _ = _body("happy-path.json")
    request = TechScoutCreateRunRequest.model_validate(body)
    registry = RunRegistry(tmp_path / "state" / "run-registry.sqlite3")
    run_id = "00000000-0000-4000-8000-000000000502"
    registry.admit_techscout(run_id, request, 4)
    row = registry.claim_oldest_techscout()
    assert row is not None
    executor = TechScoutSingleRunExecutor(registry, tmp_path / "outputs")

    def fail(_row):
        raise RuntimeError("secret-canary raw provider exception")

    executor.engine.run = fail  # type: ignore[method-assign]
    executor._execute(row)
    terminal = registry.get_techscout(run_id)
    assert terminal.status == "failed"
    projection = json.loads(
        (tmp_path / "outputs" / (terminal.projection_path or "missing")).read_text(encoding="utf-8")
    )
    assert projection["detail"]["issues"] == [{
        "stage": "orchestration",
        "code": "execution_initialization_failed",
        "retryable_by_new_run": True,
    }]
    assert "secret-canary" not in json.dumps(projection)
    assert "secret-canary" not in caplog.text
    run_dir = tmp_path / "outputs" / "techscout" / run_id
    artifact_names = {path.name for path in run_dir.iterdir()}
    assert "decision-report.json" not in artifact_names
    assert "decision-report.md" not in artifact_names
    assert REQUIRED_TERMINAL_ARTIFACTS - {
        "decision-report.json", "decision-report.md",
    } <= artifact_names
    trace_lines = [json.loads(line) for line in (run_dir / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    assert trace_lines[-2]["name"] == "terminal.completed"
    assert trace_lines[-2]["attributes"]["terminal_status"] == "failed"
    assert trace_lines[-1]["record_type"] == "trace_seal"
    assert verify_sealed_jsonl(run_dir / "traces.jsonl")["sealed"] is True


def test_demo_mcp_environment_does_not_inherit_parent_credentials(monkeypatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "secret-canary")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-canary")
    environment = demo_mcp_environment("happy")
    assert environment["TECHSCOUT_DEMO_SCENARIO"] == "happy"
    assert "TAVILY_API_KEY" not in environment
    assert "GITHUB_TOKEN" not in environment
    assert "secret-canary" not in json.dumps(environment)
