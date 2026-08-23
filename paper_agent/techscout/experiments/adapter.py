"""Adapter from generic Experiment Checks to the existing sandbox runner seam."""

from collections.abc import Callable
from pathlib import Path

from paper_agent.techscout.experiments.contracts import (
    ExecutionBudget,
    ExecutionRequest,
    ExperimentCheck,
    ExperimentRecipe,
)
from paper_agent.techscout.sandbox.runner import SandboxRunner
from paper_agent.techscout.sandbox.types import (
    CompiledCommand,
    NetworkAccess,
    PocStage,
    SandboxLimits,
    SandboxResult,
)


class SandboxExperimentAdapter:
    """Project reviewed generic Checks into the established Docker/fake runner seam."""

    def __init__(
        self, runner: SandboxRunner, *, limits: SandboxLimits | None = None
    ) -> None:
        self._runner = runner
        self._limits = limits or SandboxLimits()

    def run_check(
        self,
        recipe: ExperimentRecipe,
        check: ExperimentCheck,
        request: ExecutionRequest,
        *,
        run_workspace: Path,
        timeout_seconds: float,
        cancel_requested: Callable[[], bool],
    ) -> SandboxResult:
        self._require_budget_compatibility(request.budget)
        if check.command.network_access is not NetworkAccess.NONE:
            raise PermissionError(
                "generic offline Experiment Check requested network access"
            )
        command = CompiledCommand(
            poc_plan_id=request.execution_id,
            candidate_id=request.subject_id,
            recipe_id=recipe.recipe_id,
            stage=PocStage.TEST,
            argv=check.command.argv,
            image=check.command.image,
            network_access=NetworkAccess.NONE,
        )
        return self._runner.run(
            command,
            run_workspace,
            timeout_seconds=min(timeout_seconds, self._limits.timeout_seconds),
            cancel_requested=cancel_requested,
        )

    def _require_budget_compatibility(self, budget: ExecutionBudget) -> None:
        if budget.resources != self._limits:
            raise ValueError(
                "Execution Budget resources must match the configured sandbox limits"
            )
