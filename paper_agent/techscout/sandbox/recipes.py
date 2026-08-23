"""Reviewed V1 smoke recipes.

The registry is intentionally closed: unsupported candidates remain research-only.
"""

from dataclasses import dataclass

from paper_agent.techscout.errors import StableId
from paper_agent.techscout.sandbox.types import NetworkAccess, PocStage


SANDBOX_IMAGE = "momo-techscout-sandbox:wave1"


@dataclass(frozen=True)
class RecipeCommand:
    argv: tuple[str, ...]
    network_access: NetworkAccess


@dataclass(frozen=True)
class ReviewedRecipe:
    recipe_id: StableId
    candidate_ids: frozenset[StableId]
    package_name: str
    package_version: str
    candidate_names: frozenset[str]
    checks: frozenset[str]
    commands: dict[PocStage, RecipeCommand]
    image: str = SANDBOX_IMAGE


class UnsupportedRecipeError(ValueError):
    """Raised when a plan cannot cross the reviewed PoC boundary."""


def _recipes() -> tuple[ReviewedRecipe, ...]:
    return (
        ReviewedRecipe(
            recipe_id="recipe:chroma-local@1",
            candidate_ids=frozenset({"candidate:chromadb", "candidate:chroma"}),
            package_name="chromadb",
            package_version="1.0.15",
            candidate_names=frozenset({"chroma", "chromadb"}),
            checks=frozenset(
                {"install", "import", "create", "persistence", "upsert", "query", "filter"}
            ),
            commands={
                PocStage.INSTALL: RecipeCommand(
                    argv=(
                        "python",
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--no-input",
                        "--target",
                        "/tmp/techscout-site",
                        "chromadb==1.0.15",
                    ),
                    network_access=NetworkAccess.INSTALL_ONLY,
                ),
                PocStage.TEST: RecipeCommand(
                    argv=("python", "/opt/techscout/recipes/chroma_local.py"),
                    network_access=NetworkAccess.NONE,
                ),
            },
        ),
        ReviewedRecipe(
            recipe_id="recipe:qdrant-local@1",
            candidate_ids=frozenset(
                {"candidate:qdrant-client", "candidate:qdrant-local"}
            ),
            package_name="qdrant-client",
            package_version="1.15.1",
            candidate_names=frozenset({"qdrant", "qdrant local", "qdrant-client"}),
            checks=frozenset(
                {"install", "import", "create", "persistence", "upsert", "query", "filter"}
            ),
            commands={
                PocStage.INSTALL: RecipeCommand(
                    argv=(
                        "python",
                        "-m",
                        "pip",
                        "install",
                        "--disable-pip-version-check",
                        "--no-input",
                        "--target",
                        "/tmp/techscout-site",
                        "qdrant-client==1.15.1",
                    ),
                    network_access=NetworkAccess.INSTALL_ONLY,
                ),
                PocStage.TEST: RecipeCommand(
                    argv=("python", "/opt/techscout/recipes/qdrant_local.py"),
                    network_access=NetworkAccess.NONE,
                ),
            },
        ),
    )


class RecipeRegistry:
    def __init__(self, recipes: tuple[ReviewedRecipe, ...] | None = None) -> None:
        selected = recipes if recipes is not None else _recipes()
        self._recipes = {recipe.recipe_id: recipe for recipe in selected}
        if len(self._recipes) != len(selected):
            raise ValueError("recipe identifiers must be unique")

    @property
    def trusted_recipe_ids(self) -> frozenset[StableId]:
        return frozenset(self._recipes)

    @property
    def trusted_recipe_versions(self) -> dict[StableId, str]:
        return {
            recipe_id: recipe.package_version
            for recipe_id, recipe in self._recipes.items()
        }

    def get(self, recipe_id: StableId | None) -> ReviewedRecipe:
        if recipe_id is None or recipe_id not in self._recipes:
            raise UnsupportedRecipeError(
                "recipe is not reviewed; candidate must remain research-only"
            )
        return self._recipes[recipe_id]


def version_matches(requested: str, actual: str) -> bool:
    """Match the exact and trailing-wildcard forms supported by Wave 1."""
    normalized = requested.strip()
    if normalized.endswith(".*"):
        return actual.startswith(normalized[:-1])
    return normalized == actual
