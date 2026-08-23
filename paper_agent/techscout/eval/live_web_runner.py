"""Real, bounded one-case smoke adapter over the existing Verified composition."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from paper_agent.config import Settings
from paper_agent.generation import GenerationProviderError
from paper_agent.techscout.eval.live_contracts import (
    DockerAuthority,
    LiveEvaluationCase,
    ResearchAuthority,
)
from paper_agent.techscout.eval.live_phase1 import (
    LiveInfrastructureError,
    LivePricingSnapshot,
    LiveProductObservation,
)
from paper_agent.web.registry import RunRegistry
from paper_agent.web.techscout_api_models import TechScoutCreateRunRequest
from paper_agent.web.techscout_execution import TechScoutRunEngine
from paper_agent.web.verified_composition import make_verified_services_factory


class VerifiedWebLiveCaseRunner:
    """Runs the no-fault smoke case; controlled-condition adapters stay evaluator-side."""

    supports_controlled_conditions = False

    def __init__(
        self,
        *,
        settings_loader,
        exact_model_revision: str,
        pricing: LivePricingSnapshot,
        maximum_prompt_tokens: int,
        maximum_completion_tokens: int,
    ) -> None:
        self._settings_loader = settings_loader
        self._exact_model_revision = exact_model_revision
        self._pricing = pricing
        self._maximum_prompt_tokens = maximum_prompt_tokens
        self._maximum_completion_tokens = maximum_completion_tokens

    def run(
        self,
        case: LiveEvaluationCase,
        *,
        repetition: int,
        timeout_seconds: int,
        run_directory: Path,
    ) -> LiveProductObservation:
        if (
            case.condition.research_authority is not ResearchAuthority.COLD_LIVE
            or case.condition.docker_authority is not DockerAuthority.REQUIRED
            or case.condition.injected_failure_code is not None
        ):
            raise LiveInfrastructureError(
                "the real smoke adapter cannot reinterpret evaluator-controlled conditions"
            )
        settings: Settings = self._settings_loader()
        if settings.dashscope_generation_model != self._exact_model_revision:
            raise LiveInfrastructureError("model revision drifted after preflight")
        run_directory.mkdir(parents=True, exist_ok=False)
        state_root = run_directory / "state"
        output_root = run_directory / "outputs"
        registry = RunRegistry(state_root / "registry.sqlite3")
        request = TechScoutCreateRunRequest.model_validate(
            {
                "question": case.request.question,
                "project_context": case.request.project_context,
                "environment": case.request.environment.model_dump(mode="json"),
                "hard_constraints": list(case.request.hard_constraints),
                "candidates": [
                    {
                        "name": item.name,
                        "package_name": item.package_name,
                        "requested_version": item.requested_version,
                    }
                    for item in case.request.candidates
                ],
                "mode": "verified",
            }
        )
        run_id = str(uuid5(NAMESPACE_URL, f"{case.case_id}:{repetition}"))
        registry.admit_techscout(run_id, request, capacity=1)
        row = registry.claim_oldest_techscout()
        if row is None:
            raise LiveInfrastructureError("live smoke run was not claimable")
        factory = make_verified_services_factory(
            output_root=output_root,
            state_root=state_root,
            settings_loader=self._settings_loader,
            generation_max_tokens=self._maximum_completion_tokens,
        )
        try:
            bundle, _ = TechScoutRunEngine(
                output_root,
                registry,
                verified_services_factory=factory,
                verified_timeout_seconds=timeout_seconds,
            ).run(row)
        except GenerationProviderError as error:
            raise LiveInfrastructureError(
                f"model provider infrastructure failed:{error.code}"
            ) from error
        if not bundle.evidence or any(
            item.acquisition_state != "live" for item in bundle.evidence
        ):
            raise LiveInfrastructureError("cold-live research authority was not produced")
        if bundle.report is None or not bundle.report.poc_results or any(
            not item.verified for item in bundle.report.poc_results
        ):
            raise LiveInfrastructureError("real Docker PoC authority was not produced")
        artifact_root = output_root / "techscout" / run_id
        terminal = next(
            item["attributes"]
            for item in reversed(
                [
                    json.loads(line)
                    for line in (artifact_root / "traces.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
            )
            if item.get("name") == "terminal.completed"
        )
        model_revision = terminal.get("model_revision")
        prompt_tokens = terminal.get("prompt_tokens")
        completion_tokens = terminal.get("completion_tokens")
        if model_revision != self._exact_model_revision:
            raise LiveInfrastructureError("provider did not return the exact model revision")
        if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
            raise LiveInfrastructureError("provider token usage was not returned")
        if (
            prompt_tokens > self._maximum_prompt_tokens
            or completion_tokens > self._maximum_completion_tokens
        ):
            raise LiveInfrastructureError("provider token usage exceeded authorization")
        estimated_cost = (
            prompt_tokens * self._pricing.input_usd_per_million_tokens
            + completion_tokens * self._pricing.output_usd_per_million_tokens
        ) / 1_000_000
        report = json.loads((artifact_root / "decision-report.json").read_text(encoding="utf-8"))
        manifest = json.loads((artifact_root / "run_manifest.json").read_text(encoding="utf-8"))
        return LiveProductObservation(
            case_id=case.case_id,
            repetition=repetition,
            product_status=bundle.detail.status,
            model_revision=model_revision,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=estimated_cost,
            report=report,
            manifest=manifest,
        )
