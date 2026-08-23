"""Closed, versioned registry for generic Experiment Recipes."""

from paper_agent.techscout.errors import StableId
from paper_agent.techscout.experiments.contracts import (
    ExperimentCheck,
    ExperimentRecipe,
    RecipeDisposition,
    ReviewedCommand,
)
from paper_agent.techscout.sandbox.types import DEFAULT_SANDBOX_IMAGE


OFFLINE_RECIPE_ID = "recipe:python-runtime-offline@1"
RESEARCH_ONLY_RECIPE_ID = "recipe:research-only@1"


def _default_recipes() -> tuple[ExperimentRecipe, ...]:
    return (
        ExperimentRecipe(
            recipe_id=RESEARCH_ONLY_RECIPE_ID,
            version="1.0.0",
            title="Research-only disposition",
            purpose="Record that no reviewed offline verification procedure is available.",
            disposition=RecipeDisposition.RESEARCH_ONLY,
            research_only_reason=(
                "No reviewed offline Recipe is available; evidence may be researched "
                "but no command may execute."
            ),
        ),
        ExperimentRecipe(
            recipe_id=OFFLINE_RECIPE_ID,
            version="1.0.0",
            title="Python runtime offline contract",
            purpose=(
                "Verify a Python runtime and deterministic standard-library JSON behavior "
                "without network access."
            ),
            disposition=RecipeDisposition.OFFLINE_EXECUTABLE,
            checks=(
                ExperimentCheck(
                    check_id="check:python-runtime-version",
                    title="Python runtime identity",
                    description="Capture the sandboxed Python runtime version.",
                    command=ReviewedCommand(
                        argv=("python", "--version"),
                        image=DEFAULT_SANDBOX_IMAGE,
                    ),
                ),
                ExperimentCheck(
                    check_id="check:stdlib-json-roundtrip",
                    title="Standard-library JSON round trip",
                    description="Verify deterministic JSON serialization and parsing.",
                    command=ReviewedCommand(
                        argv=(
                            "python",
                            "-I",
                            "-c",
                            (
                                "import json; payload={'ready':True}; "
                                "assert json.loads(json.dumps(payload)) == payload"
                            ),
                        ),
                        image=DEFAULT_SANDBOX_IMAGE,
                    ),
                ),
            ),
        ),
    )


class UnsupportedExperimentRecipeError(LookupError):
    """Raised when an identifier is absent from the reviewed registry."""


class ExperimentRecipeRegistry:
    def __init__(self, recipes: tuple[ExperimentRecipe, ...] | None = None) -> None:
        selected = recipes if recipes is not None else _default_recipes()
        self._recipes = {recipe.recipe_id: recipe for recipe in selected}
        if len(self._recipes) != len(selected):
            raise ValueError("Experiment Recipe identifiers must be unique")

    @property
    def recipe_ids(self) -> frozenset[StableId]:
        return frozenset(self._recipes)

    def get(self, recipe_id: StableId) -> ExperimentRecipe:
        try:
            return self._recipes[recipe_id]
        except KeyError as exc:
            raise UnsupportedExperimentRecipeError(
                "Recipe is not reviewed and cannot execute"
            ) from exc
