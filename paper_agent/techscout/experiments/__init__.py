"""Generic, auditable Experiment Recipe execution."""

from paper_agent.techscout.experiments.adapter import SandboxExperimentAdapter
from paper_agent.techscout.experiments.contracts import (
    CheckResult,
    CheckStatus,
    ExecutionBudget,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionTerminalStatus,
    ExperimentArtifact,
    ExperimentCheck,
    ExperimentRecipe,
    ExperimentResult,
    Measurement,
    RecipeDisposition,
    ReviewedCommand,
    SealedExecution,
)
from paper_agent.techscout.experiments.engine import (
    CancellationToken,
    ExecutionInProgressError,
    ExperimentEngine,
    IdempotencyConflictError,
    InvalidExecutionSealError,
)
from paper_agent.techscout.experiments.registry import (
    OFFLINE_RECIPE_ID,
    RESEARCH_ONLY_RECIPE_ID,
    ExperimentRecipeRegistry,
    UnsupportedExperimentRecipeError,
)

__all__ = [
    "CancellationToken",
    "CheckResult",
    "CheckStatus",
    "ExecutionBudget",
    "ExecutionFailure",
    "ExecutionInProgressError",
    "ExecutionRequest",
    "ExecutionTerminalStatus",
    "ExperimentArtifact",
    "ExperimentCheck",
    "ExperimentEngine",
    "ExperimentRecipe",
    "ExperimentRecipeRegistry",
    "ExperimentResult",
    "IdempotencyConflictError",
    "InvalidExecutionSealError",
    "Measurement",
    "OFFLINE_RECIPE_ID",
    "RESEARCH_ONLY_RECIPE_ID",
    "RecipeDisposition",
    "ReviewedCommand",
    "SandboxExperimentAdapter",
    "SealedExecution",
    "UnsupportedExperimentRecipeError",
]
