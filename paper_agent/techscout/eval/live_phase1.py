"""Deny-by-default control plane for the independent TechScout Live Eval V1."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator
from typing_extensions import Self

from paper_agent.techscout.eval.live_contracts import (
    LiveEvaluationCase,
    LiveEvaluationRegistration,
    load_live_evaluation_registration,
)
from paper_agent.techscout.models import NonEmptyStr, TechScoutModel
from paper_agent.techscout.sandbox.recipes import SANDBOX_IMAGE


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LivePricingSnapshot(TechScoutModel):
    currency: Literal["USD"] = "USD"
    input_usd_per_million_tokens: float = Field(ge=0)
    output_usd_per_million_tokens: float = Field(ge=0)
    captured_at: datetime
    source: NonEmptyStr

    @property
    def sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="json"))


class LiveExecutionAuthorization(TechScoutModel):
    schema_version: Literal["techscout-live-eval-authorization-v1"]
    execution_authorized: Literal[True]
    suite_id: NonEmptyStr
    registration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_git_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    provider: Literal["dashscope"]
    model_wiring: Literal["verified_stage_services:techscout_decision_report:v1"]
    exact_model_revision: NonEmptyStr
    model_revision_is_immutable: Literal[True]
    provider_token_usage_required: Literal[True] = True
    pricing: LivePricingSnapshot
    docker_image_ref: Literal[SANDBOX_IMAGE]
    docker_image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    maximum_approved_cost_usd: float = Field(gt=0)
    execution_scope: Literal["smoke", "formal"]
    maximum_prompt_tokens_per_run: int = Field(ge=1, le=100_000)
    maximum_completion_tokens_per_run: int = Field(ge=1, le=8_192)
    per_run_timeout_seconds: int = Field(ge=60, le=1800)
    total_timeout_seconds: int = Field(ge=60, le=43200)
    output_directory: NonEmptyStr
    smoke_case_limit: Literal[1] = 1
    smoke_repetitions: Literal[1] = 1

    @model_validator(mode="after")
    def validate_time_budget(self) -> Self:
        if self.total_timeout_seconds < self.per_run_timeout_seconds:
            raise ValueError("total timeout must cover at least one run")
        return self


class LivePreflightAttestation(TechScoutModel):
    schema_version: Literal["techscout-live-eval-preflight-v1"]
    registration_sha256: str
    authorization_sha256: str
    baseline_git_commit: str
    output_directory: str
    exact_model_revision: str
    model_revision_is_immutable: Literal[True]
    model_wiring: str
    docker_image_ref: str
    docker_image_id: str
    pricing_sha256: str
    maximum_approved_cost_usd: float
    execution_scope: Literal["smoke", "formal"]
    maximum_prompt_tokens_per_run: int
    maximum_completion_tokens_per_run: int
    per_run_timeout_seconds: int
    total_timeout_seconds: int
    live_research_ready: Literal[True]
    real_docker_ready: Literal[True]
    model_backed_reasoning_ready: Literal[True]
    provider_token_usage_ready: Literal[True]
    clean_commit: Literal[True]


class LivePreflightError(RuntimeError):
    def __init__(self, blockers: tuple[str, ...]) -> None:
        self.blockers = blockers
        super().__init__("live eval preflight denied: " + "; ".join(blockers))


class LiveProductObservation(TechScoutModel):
    case_id: NonEmptyStr
    repetition: int = Field(ge=1)
    product_status: Literal["completed", "completed_with_limitations", "failed"]
    model_revision: NonEmptyStr
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    estimated_cost_usd: float = Field(ge=0)
    report: dict[str, object] | None = None
    manifest: dict[str, object] | None = None
    product_failure_code: str | None = None


class LiveCaseRunner(Protocol):
    def run(
        self,
        case: LiveEvaluationCase,
        *,
        repetition: int,
        timeout_seconds: int,
        run_directory: Path,
    ) -> LiveProductObservation: ...


class LiveInfrastructureError(RuntimeError):
    """Only this class, total budget, or total timeout stops later run keys."""


def _default_command(command: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, check=False, capture_output=True, text=True, timeout=15
    )


def run_live_preflight(
    *,
    registration_path: Path,
    authorization_path: Path,
    output_directory: Path,
    repository_root: Path,
    environment: dict[str, str] | None = None,
    command_runner: Callable[[tuple[str, ...], Path], subprocess.CompletedProcess[str]] = _default_command,
) -> tuple[LiveEvaluationRegistration, LiveExecutionAuthorization, LivePreflightAttestation]:
    """Prove all authority before any evaluation output, Trace, or worker exists."""

    blockers: list[str] = []
    registration = None
    authorization = None
    registration_sha256 = ""
    authorization_bytes = b""
    try:
        registration, registration_sha256 = load_live_evaluation_registration(
            registration_path
        )
    except (OSError, ValueError) as error:
        blockers.append(f"registration_unavailable:{type(error).__name__}")
    try:
        authorization_bytes = authorization_path.read_bytes()
        authorization = LiveExecutionAuthorization.model_validate_json(
            authorization_bytes
        )
    except (OSError, ValueError) as error:
        blockers.append(f"authorization_unavailable:{type(error).__name__}")
    if blockers:
        raise LivePreflightError(tuple(blockers))
    assert registration is not None and authorization is not None

    requested_output = output_directory.resolve()
    authorized_output = (repository_root / authorization.output_directory).resolve()
    legacy_authority = (repository_root / "docs" / "evaluations" / "artifacts").resolve()
    if requested_output != authorized_output:
        blockers.append("output_directory_mismatch")
    if requested_output == legacy_authority or legacy_authority in requested_output.parents:
        blockers.append("synthetic_authority_overlap")
    if output_directory.exists():
        blockers.append("output_directory_already_exists")
    if authorization.registration_sha256 != registration_sha256:
        blockers.append("registration_hash_mismatch")
    if authorization.suite_id != registration.suite_id:
        blockers.append("suite_id_mismatch")
    if registration.baseline_git_commit != authorization.baseline_git_commit:
        blockers.append("registration_baseline_unfrozen_or_mismatched")
    if authorization.per_run_timeout_seconds > registration.policy.per_run_timeout_seconds:
        blockers.append("per_run_timeout_exceeds_preregistration")
    if authorization.total_timeout_seconds > registration.policy.total_run_budget_seconds:
        blockers.append("total_timeout_exceeds_preregistration")
    planned_runs = (
        1
        if authorization.execution_scope == "smoke"
        else len(registration.cases) * registration.policy.repetitions_per_case
    )
    worst_case_cost = planned_runs * (
        authorization.maximum_prompt_tokens_per_run
        * authorization.pricing.input_usd_per_million_tokens
        + authorization.maximum_completion_tokens_per_run
        * authorization.pricing.output_usd_per_million_tokens
    ) / 1_000_000
    if worst_case_cost > authorization.maximum_approved_cost_usd:
        blockers.append("approved_cost_below_frozen_token_ceiling")

    env = os.environ if environment is None else environment
    if not env.get("TAVILY_API_KEY", "").strip():
        blockers.append("live_research_credential_missing:TAVILY_API_KEY")
    if not env.get("DASHSCOPE_API_KEY", "").strip():
        blockers.append("model_credential_missing:DASHSCOPE_API_KEY")
    if not env.get("TECHSCOUT_DOCKER_INSTALL_NETWORK", "").strip():
        blockers.append("docker_install_network_missing")
    if env.get("TECHSCOUT_DOCKER_EGRESS_ALLOWLIST_ENFORCED", "").strip().casefold() != "true":
        blockers.append("docker_egress_allowlist_not_attested")
    configured_model = env.get("DASHSCOPE_GENERATION_MODEL", "qwen3.7-plus").strip()
    if configured_model != authorization.exact_model_revision:
        blockers.append("exact_model_revision_mismatch")

    head = command_runner(("git", "rev-parse", "HEAD"), repository_root)
    status = command_runner(
        ("git", "status", "--porcelain", "--untracked-files=all"), repository_root
    )
    head_commit = head.stdout.strip() if head.returncode == 0 else ""
    if not head_commit or head_commit != authorization.baseline_git_commit:
        blockers.append("baseline_git_commit_mismatch")
    if status.returncode != 0 or status.stdout.strip():
        blockers.append("working_tree_not_clean")
    docker = command_runner(
        ("docker", "version", "--format", "{{.Server.Version}}"), repository_root
    )
    if docker.returncode != 0 or not docker.stdout.strip():
        blockers.append("real_docker_unavailable")
    image = command_runner(
        ("docker", "image", "inspect", authorization.docker_image_ref, "--format", "{{.Id}}"),
        repository_root,
    )
    if image.returncode != 0 or image.stdout.strip() != authorization.docker_image_id:
        blockers.append("docker_image_identity_mismatch")
    if blockers:
        raise LivePreflightError(tuple(blockers))

    return registration, authorization, LivePreflightAttestation(
        schema_version="techscout-live-eval-preflight-v1",
        registration_sha256=registration_sha256,
        authorization_sha256=hashlib.sha256(authorization_bytes).hexdigest(),
        baseline_git_commit=head_commit,
        output_directory=str(requested_output),
        exact_model_revision=authorization.exact_model_revision,
        model_revision_is_immutable=True,
        model_wiring=authorization.model_wiring,
        docker_image_ref=authorization.docker_image_ref,
        docker_image_id=authorization.docker_image_id,
        pricing_sha256=authorization.pricing.sha256,
        maximum_approved_cost_usd=authorization.maximum_approved_cost_usd,
        execution_scope=authorization.execution_scope,
        maximum_prompt_tokens_per_run=authorization.maximum_prompt_tokens_per_run,
        maximum_completion_tokens_per_run=authorization.maximum_completion_tokens_per_run,
        per_run_timeout_seconds=authorization.per_run_timeout_seconds,
        total_timeout_seconds=authorization.total_timeout_seconds,
        live_research_ready=True,
        real_docker_ready=True,
        model_backed_reasoning_ready=True,
        provider_token_usage_ready=True,
        clean_commit=True,
    )


class Phase1LiveExecutor:
    """Sequential, run-keyed executor isolated from sealed synthetic authority."""

    def __init__(self, runner: LiveCaseRunner, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._runner = runner
        self._monotonic = monotonic

    def execute(
        self,
        *,
        registration: LiveEvaluationRegistration,
        authorization: LiveExecutionAuthorization,
        attestation: LivePreflightAttestation,
        output_directory: Path,
        smoke: bool = False,
    ) -> tuple[LiveProductObservation, ...]:
        if (
            Path(attestation.output_directory).resolve() != output_directory.resolve()
            or attestation.exact_model_revision != authorization.exact_model_revision
            or attestation.pricing_sha256 != authorization.pricing.sha256
            or attestation.baseline_git_commit != authorization.baseline_git_commit
            or attestation.execution_scope != authorization.execution_scope
        ):
            raise LiveInfrastructureError("preflight attestation does not match authorization")
        if smoke != (authorization.execution_scope == "smoke"):
            raise LiveInfrastructureError("authorization execution scope mismatch")
        if not smoke and not getattr(self._runner, "supports_controlled_conditions", True):
            raise LiveInfrastructureError(
                "runner lacks evaluator-controlled condition authority"
            )
        if output_directory.exists():
            raise LiveInfrastructureError("authority output already exists")
        output_directory.mkdir(parents=True, exist_ok=False)
        started = self._monotonic()
        observations: list[LiveProductObservation] = []
        stopped_reason: str | None = None
        cases = registration.cases[:1] if smoke else registration.cases
        repetitions = 1 if smoke else registration.policy.repetitions_per_case
        try:
            (output_directory / "preflight.json").write_text(
                attestation.model_dump_json(indent=2), encoding="utf-8"
            )
            for case in cases:
                for repetition in range(1, repetitions + 1):
                    elapsed = self._monotonic() - started
                    if elapsed >= authorization.total_timeout_seconds:
                        stopped_reason = "total_timeout_exceeded"
                        raise LiveInfrastructureError(stopped_reason)
                    run_key = f"{case.case_id.replace(':', '_')}--r{repetition}"
                    run_directory = output_directory / "runs" / run_key
                    try:
                        observation = self._runner.run(
                            case,
                            repetition=repetition,
                            timeout_seconds=authorization.per_run_timeout_seconds,
                            run_directory=run_directory,
                        )
                    except LiveInfrastructureError:
                        stopped_reason = "infrastructure_failure"
                        raise
                    except Exception as error:  # product failures are observations
                        observation = LiveProductObservation(
                            case_id=case.case_id,
                            repetition=repetition,
                            product_status="failed",
                            model_revision=authorization.exact_model_revision,
                            prompt_tokens=0,
                            completion_tokens=0,
                            estimated_cost_usd=0,
                            product_failure_code=f"unhandled_product_failure:{type(error).__name__}",
                        )
                    if observation.case_id != case.case_id or observation.repetition != repetition:
                        stopped_reason = "run_key_mismatch"
                        raise LiveInfrastructureError(stopped_reason)
                    if observation.model_revision != authorization.exact_model_revision:
                        stopped_reason = "model_revision_drift"
                        raise LiveInfrastructureError(stopped_reason)
                    if (
                        observation.prompt_tokens
                        > authorization.maximum_prompt_tokens_per_run
                        or observation.completion_tokens
                        > authorization.maximum_completion_tokens_per_run
                    ):
                        stopped_reason = "provider_token_ceiling_exceeded"
                        raise LiveInfrastructureError(stopped_reason)
                    if (
                        observation.product_status != "failed"
                        and observation.prompt_tokens + observation.completion_tokens <= 0
                    ):
                        stopped_reason = "provider_token_usage_missing"
                        raise LiveInfrastructureError(stopped_reason)
                    expected_cost = (
                        observation.prompt_tokens
                        * authorization.pricing.input_usd_per_million_tokens
                        + observation.completion_tokens
                        * authorization.pricing.output_usd_per_million_tokens
                    ) / 1_000_000
                    if abs(observation.estimated_cost_usd - expected_cost) > 1e-9:
                        stopped_reason = "provider_cost_mismatch"
                        raise LiveInfrastructureError(stopped_reason)
                    observations.append(observation)
                    if sum(item.estimated_cost_usd for item in observations) > authorization.maximum_approved_cost_usd:
                        stopped_reason = "total_cost_budget_exceeded"
                        raise LiveInfrastructureError(stopped_reason)
                    if self._monotonic() - started > authorization.total_timeout_seconds:
                        stopped_reason = "total_timeout_exceeded"
                        raise LiveInfrastructureError(stopped_reason)
            return tuple(observations)
        finally:
            self._seal(
                output_directory,
                observations=tuple(observations),
                smoke=smoke,
                stopped_reason=stopped_reason,
            )

    @staticmethod
    def _seal(
        output_directory: Path,
        *,
        observations: tuple[LiveProductObservation, ...],
        smoke: bool,
        stopped_reason: str | None,
    ) -> None:
        results = output_directory / "run-observations.jsonl"
        results.write_text(
            "".join(item.model_dump_json() + "\n" for item in observations),
            encoding="utf-8",
        )
        files = sorted(
            path for path in output_directory.rglob("*")
            if path.is_file() and path.name != "sealed-manifest.json"
        )
        manifest = {
            "schema_version": "techscout-live-eval-sealed-manifest-v1",
            "authority_kind": "bounded_live_smoke" if smoke else "phase1_live_eval",
            "sealed_at": datetime.now(timezone.utc).isoformat(),
            "run_count": len(observations),
            "stopped_reason": stopped_reason,
            "files": {
                str(path.relative_to(output_directory)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in files
            },
        }
        (output_directory / "sealed-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )


def verify_live_authority(output_directory: Path) -> bool:
    manifest = json.loads(
        (output_directory / "sealed-manifest.json").read_text(encoding="utf-8")
    )
    return all(
        hashlib.sha256((output_directory / name).read_bytes()).hexdigest() == digest
        for name, digest in manifest["files"].items()
    )
