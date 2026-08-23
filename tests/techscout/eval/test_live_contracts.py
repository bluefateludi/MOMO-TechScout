from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from paper_agent.techscout.eval.live_contracts import (
    DockerAuthority,
    LiveAuthorityRequirements,
    LiveCaseCategory,
    LiveEvaluationCase,
    LiveEvaluationPolicy,
    LiveEvaluationRegistration,
    LiveEvaluationRubric,
    LiveExpectedOutcome,
    LiveRubricDimension,
    LiveRunCondition,
    ResearchAuthority,
    load_live_evaluation_registration,
)
from paper_agent.techscout.eval.live_phase1 import (
    LiveExecutionAuthorization,
    LiveInfrastructureError,
    LivePreflightError,
    LivePricingSnapshot,
    LiveProductObservation,
    Phase1LiveExecutor,
    run_live_preflight,
    verify_live_authority,
)
from paper_agent.techscout.sandbox.recipes import SANDBOX_IMAGE
from paper_agent.techscout.errors import FailureCode
from paper_agent.techscout.models import (
    Candidate,
    EnvironmentSpec,
    ResearchRequest,
    RunMode,
    TerminalStatus,
    Verdict,
)


def _candidate(candidate_id: str) -> Candidate:
    names = {
        "candidate:chroma": ("Chroma", "chromadb"),
        "candidate:qdrant-local": ("Qdrant Local", "qdrant-client"),
        "candidate:pgvector": ("pgvector", "pgvector"),
    }
    name, package = names[candidate_id]
    return Candidate(candidate_id=candidate_id, name=name, package_name=package)


def _request(index: int, candidate_ids: tuple[str, ...]) -> ResearchRequest:
    return ResearchRequest(
        run_id=f"run:live-v1-{index:02d}",
        question="Choose a safe local vector store for this bounded project.",
        project_context="A Python RAG prototype with explicit verification requirements.",
        environment=EnvironmentSpec(
            python_version="3.11",
            operating_system="linux",
            deployment="single-host local process",
        ),
        hard_constraints=("Only a verified eligible candidate may be recommended.",),
        candidates=tuple(_candidate(candidate_id) for candidate_id in candidate_ids),
        mode=RunMode.VERIFIED,
    )


def _case(index: int, category: LiveCaseCategory) -> LiveEvaluationCase:
    if category is LiveCaseCategory.SUPPORTED_RECOMMENDATION:
        candidate_ids = ("candidate:chroma", "candidate:pgvector")
        condition = LiveRunCondition(
            research_authority=ResearchAuthority.COLD_LIVE,
            docker_authority=DockerAuthority.REQUIRED,
        )
        expected = LiveExpectedOutcome(
            allowed_terminal_statuses=(TerminalStatus.COMPLETED,),
            allowed_verdicts=(Verdict.RECOMMENDED,),
            eligible_recommendations=("candidate:chroma",),
            prohibited_recommendations=("candidate:pgvector",),
        )
    elif category is LiveCaseCategory.SAFE_BOUNDARY:
        candidate_ids = ("candidate:pgvector",)
        condition = LiveRunCondition(
            research_authority=ResearchAuthority.COLD_LIVE,
            docker_authority=DockerAuthority.FORCED_UNAVAILABLE,
        )
        expected = LiveExpectedOutcome(
            allowed_terminal_statuses=(TerminalStatus.COMPLETED_WITH_LIMITATIONS,),
            allowed_verdicts=(Verdict.INSUFFICIENT_EVIDENCE,),
            prohibited_recommendations=("candidate:pgvector",),
            required_limitation_codes=("no-verified-eligible-candidate",),
        )
    else:
        candidate_ids = ("candidate:chroma",)
        recovery_succeeds = index == 11
        condition = LiveRunCondition(
            research_authority=ResearchAuthority.COLD_LIVE,
            docker_authority=DockerAuthority.REQUIRED,
            injected_failure_code=(
                FailureCode.DEPENDENCY_CONFLICT
                if recovery_succeeds
                else FailureCode.POC_TIMEOUT
            ),
            maximum_recovery_attempts=1,
        )
        expected = LiveExpectedOutcome(
            allowed_terminal_statuses=(
                TerminalStatus.COMPLETED
                if recovery_succeeds
                else TerminalStatus.COMPLETED_WITH_LIMITATIONS,
            ),
            allowed_verdicts=(
                Verdict.RECOMMENDED
                if recovery_succeeds
                else Verdict.INSUFFICIENT_EVIDENCE,
            ),
            eligible_recommendations=("candidate:chroma",) if recovery_succeeds else (),
            prohibited_recommendations=() if recovery_succeeds else ("candidate:chroma",),
            required_limitation_codes=() if recovery_succeeds else ("recovery-exhausted",),
            recovery_required=True,
            recovery_must_succeed=recovery_succeeds,
        )
    return LiveEvaluationCase(
        schema_version="techscout-live-eval-case-v1",
        fixture_kind="live_preregistered_evaluation",
        case_id=f"case:live-v1-{index:02d}",
        category=category,
        request=_request(index, candidate_ids),
        condition=condition,
        expected_outcome=expected,
        forbidden_claims=("Do not claim unobserved production performance.",),
        reviewer_rationale="The oracle checks the current V1 support and safety boundary.",
    )


def _registration() -> LiveEvaluationRegistration:
    categories = (
        (LiveCaseCategory.SUPPORTED_RECOMMENDATION,) * 6
        + (LiveCaseCategory.SAFE_BOUNDARY,) * 4
        + (LiveCaseCategory.CONTROLLED_RECOVERY,) * 2
    )
    rubric = LiveEvaluationRubric(
        schema_version="techscout-live-eval-rubric-v1",
        dimensions=(
            LiveRubricDimension(
                dimension_id="outcome",
                weight=0.30,
                maximum_points=4,
                pass_description="Terminal status and verdict satisfy the oracle.",
                fail_description="The run crashes or violates the outcome contract.",
            ),
            LiveRubricDimension(
                dimension_id="constraints",
                weight=0.25,
                maximum_points=4,
                pass_description="Every hard constraint is explicitly addressed.",
                fail_description="A hard constraint is ignored or violated.",
            ),
            LiveRubricDimension(
                dimension_id="evidence",
                weight=0.20,
                maximum_points=4,
                pass_description="Critical claims have run-scoped evidence.",
                fail_description="A critical claim is unsupported or fabricated.",
            ),
            LiveRubricDimension(
                dimension_id="poc-authority",
                weight=0.15,
                maximum_points=4,
                pass_description="Required behavior is verified by an authorized PoC.",
                fail_description="The report exceeds the observed PoC authority.",
            ),
            LiveRubricDimension(
                dimension_id="recovery-honesty",
                weight=0.10,
                maximum_points=4,
                pass_description="Recovery is bounded and limitations remain visible.",
                fail_description="Recovery loops or success is fabricated.",
            ),
        ),
        passing_weighted_score=0.80,
    )
    return LiveEvaluationRegistration(
        schema_version="techscout-live-eval-registration-v1",
        suite_id="suite:techscout-live-v1-draft",
        status="draft_preregistration",
        cases=tuple(_case(index, category) for index, category in enumerate(categories, 1)),
        rubric=rubric,
        policy=LiveEvaluationPolicy(
            per_run_timeout_seconds=600,
            total_run_budget_seconds=14400,
        ),
        required_authorities=LiveAuthorityRequirements(),
        authority_notice="Draft only; no model, network, Docker, or spend is authorized.",
    )


def test_registration_freezes_counts_and_denies_execution() -> None:
    registration = _registration()

    assert len(registration.cases) == 12
    assert registration.policy.repetitions_per_case == 2
    assert registration.policy.execution_authorized is False
    assert registration.policy.maximum_approved_cost_usd == 0.0
    assert registration.required_authorities.model_backed_reasoning is True


def test_registration_rejects_wrong_category_counts() -> None:
    registration = _registration()

    with pytest.raises(ValidationError, match="category counts"):
        LiveEvaluationRegistration.model_validate(
            {
                **registration.model_dump(mode="python"),
                "cases": tuple(
                    _case(index, LiveCaseCategory.SAFE_BOUNDARY)
                    for index in range(1, 13)
                ),
            }
        )


def test_loader_returns_payload_hash(tmp_path) -> None:
    registration = _registration()
    path = tmp_path / "registration.json"
    payload = registration.model_dump_json(indent=2).encode("utf-8")
    path.write_bytes(payload)

    loaded, sha256 = load_live_evaluation_registration(path)

    assert loaded == registration
    assert sha256 == hashlib.sha256(payload).hexdigest()


def _authorization(registration: LiveEvaluationRegistration, payload: bytes) -> LiveExecutionAuthorization:
    return LiveExecutionAuthorization(
        schema_version="techscout-live-eval-authorization-v1",
        execution_authorized=True,
        suite_id=registration.suite_id,
        registration_sha256=hashlib.sha256(payload).hexdigest(),
        baseline_git_commit="a" * 40,
        provider="dashscope",
        model_wiring="verified_stage_services:techscout_decision_report:v1",
        exact_model_revision="qwen-exact-revision",
        model_revision_is_immutable=True,
        pricing=LivePricingSnapshot(
            input_usd_per_million_tokens=1.0,
            output_usd_per_million_tokens=2.0,
            captured_at=datetime(2026, 8, 17, tzinfo=timezone.utc),
            source="Frozen provider pricing snapshot.",
        ),
        docker_image_ref=SANDBOX_IMAGE,
        docker_image_id="sha256:" + "b" * 64,
        maximum_approved_cost_usd=0.01,
        execution_scope="formal",
        maximum_prompt_tokens_per_run=100,
        maximum_completion_tokens_per_run=50,
        per_run_timeout_seconds=120,
        total_timeout_seconds=720,
        output_directory="authority",
    )


def _write_preflight_inputs(tmp_path: Path):
    registration = _registration().model_copy(update={"baseline_git_commit": "a" * 40})
    payload = registration.model_dump_json(indent=2).encode()
    registration_path = tmp_path / "registration.json"
    registration_path.write_bytes(payload)
    authorization = _authorization(registration, payload)
    authorization_path = tmp_path / "authorization.json"
    authorization_path.write_text(authorization.model_dump_json(indent=2), encoding="utf-8")
    return registration_path, authorization_path, authorization


def _commands(authorization: LiveExecutionAuthorization):
    def run(command, cwd):
        del cwd
        if command[:3] == ("git", "rev-parse", "HEAD"):
            return subprocess.CompletedProcess(command, 0, authorization.baseline_git_commit + "\n", "")
        if command[:2] == ("git", "status"):
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ("docker", "version"):
            return subprocess.CompletedProcess(command, 0, "27.0\n", "")
        if command[:3] == ("docker", "image", "inspect"):
            return subprocess.CompletedProcess(command, 0, authorization.docker_image_id + "\n", "")
        raise AssertionError(command)
    return run


def test_preflight_denies_before_output_when_authority_is_missing(tmp_path: Path) -> None:
    registration_path, authorization_path, authorization = _write_preflight_inputs(tmp_path)
    output = tmp_path / "authority"
    with pytest.raises(LivePreflightError) as caught:
        run_live_preflight(
            registration_path=registration_path,
            authorization_path=authorization_path,
            output_directory=output,
            repository_root=tmp_path,
            environment={},
            command_runner=_commands(authorization),
        )
    assert "live_research_credential_missing:TAVILY_API_KEY" in caught.value.blockers
    assert "model_credential_missing:DASHSCOPE_API_KEY" in caught.value.blockers
    assert not output.exists()


def test_preflight_rejects_cost_below_frozen_token_ceiling(tmp_path: Path) -> None:
    registration_path, authorization_path, authorization = _write_preflight_inputs(tmp_path)
    underfunded = authorization.model_copy(update={"maximum_approved_cost_usd": 0.000001})
    authorization_path.write_text(underfunded.model_dump_json(indent=2), encoding="utf-8")
    with pytest.raises(LivePreflightError) as caught:
        run_live_preflight(
            registration_path=registration_path,
            authorization_path=authorization_path,
            output_directory=tmp_path / "authority",
            repository_root=tmp_path,
            environment={
                "TAVILY_API_KEY": "present",
                "DASHSCOPE_API_KEY": "present",
                "DASHSCOPE_GENERATION_MODEL": authorization.exact_model_revision,
                "TECHSCOUT_DOCKER_INSTALL_NETWORK": "techscout-egress",
                "TECHSCOUT_DOCKER_EGRESS_ALLOWLIST_ENFORCED": "true",
            },
            command_runner=_commands(underfunded),
        )
    assert "approved_cost_below_frozen_token_ceiling" in caught.value.blockers


class _Runner:
    def __init__(self) -> None:
        self.keys = []

    def run(self, case, *, repetition, timeout_seconds, run_directory):
        del timeout_seconds, run_directory
        self.keys.append((case.case_id, repetition))
        if len(self.keys) == 1:
            raise ValueError("product failure")
        return LiveProductObservation(
            case_id=case.case_id,
            repetition=repetition,
            product_status="completed_with_limitations",
            model_revision="qwen-exact-revision",
            prompt_tokens=10,
            completion_tokens=5,
            estimated_cost_usd=0.00002,
            report={"verdict": "insufficient_evidence"},
            manifest={"terminal_status": "completed_with_limitations"},
        )


def test_phase1_executor_continues_product_failure_and_seals_unique_authority(tmp_path: Path) -> None:
    registration_path, authorization_path, authorization = _write_preflight_inputs(tmp_path)
    output = tmp_path / "authority"
    registration, _, attestation = run_live_preflight(
        registration_path=registration_path,
        authorization_path=authorization_path,
        output_directory=output,
        repository_root=tmp_path,
        environment={
            "TAVILY_API_KEY": "present",
            "DASHSCOPE_API_KEY": "present",
            "DASHSCOPE_GENERATION_MODEL": authorization.exact_model_revision,
            "TECHSCOUT_DOCKER_INSTALL_NETWORK": "techscout-egress",
            "TECHSCOUT_DOCKER_EGRESS_ALLOWLIST_ENFORCED": "true",
        },
        command_runner=_commands(authorization),
    )
    runner = _Runner()
    observations = Phase1LiveExecutor(runner).execute(
        registration=registration,
        authorization=authorization,
        attestation=attestation,
        output_directory=output,
    )
    assert len(observations) == 24
    assert observations[0].product_status == "failed"
    assert len(set(runner.keys)) == 24
    assert verify_live_authority(output)
    with pytest.raises(LiveInfrastructureError, match="already exists"):
        Phase1LiveExecutor(runner).execute(
            registration=registration,
            authorization=authorization,
            attestation=attestation,
            output_directory=output,
        )
