"""Recipe step-kind registry.

A recipe is an ordered list of steps, each with a `kind:` that names the code
which runs it (see docs/plugin-api.md "Contributing a recipe"). Core ships the
built-in kinds (`plugins.ensure`, `artifacts.fetch`, `generation.smoke`, ...);
a plugin adds its own - say `collections.ensure` - by declaring
`recipe_steps:` in its `manifest.yml` without touching core.

This module holds only the registration bookkeeping. The executor contract
itself (`StepExecutor`, `StepContext`, `StepResult`) lives in
`src.features.recipes.executors.base` (re-exported by
`src.plugin_api.recipes`), because it carries feature-shaped context (the
parsed recipe, the run record) that `src.platform` must not import. Executor
objects are stored here `Any`-typed for the same reason - mirrors
`RequirementCheckerRegistry`.

A kind registered here is both runnable (the recipes executor registry falls
back to this registry when a kind is not one of core's) and *lintable*: the
recipe catalog feeds these kind names to `validate_recipe_dict` so a recipe
using a plugin kind validates, while an unregistered kind stays an error.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class DuplicateRecipeStepKindError(ValueError):
    """Raised when registering a step kind that is already registered."""


@dataclass(frozen=True)
class RecipeStepKindRegistration:
    """One registered recipe step kind."""

    kind: str
    # A `src.plugin_api.recipes.StepExecutor` instance - typed `Any` here
    # because `src.platform` must not import `src.features`/`src.plugin_api`.
    executor: Any
    source: str = "core"


class RecipeStepKindRegistry:
    """Registry mapping a recipe step `kind:` -> `RecipeStepKindRegistration`."""

    def __init__(self):
        self._by_kind: Dict[str, RecipeStepKindRegistration] = {}

    def register(self, registration: RecipeStepKindRegistration) -> None:
        """Register a step kind. Raises `DuplicateRecipeStepKindError` on collision."""
        if registration.kind in self._by_kind:
            raise DuplicateRecipeStepKindError(
                f"Recipe step kind already registered: '{registration.kind}'"
            )
        self._by_kind[registration.kind] = registration

    def unregister_source(self, source: str) -> None:
        """Remove every kind registered by `source` (e.g. a plugin id)."""
        for kind in [k for k, reg in self._by_kind.items() if reg.source == source]:
            del self._by_kind[kind]

    def get(self, kind: str) -> Optional[RecipeStepKindRegistration]:
        """Look up a registration by step kind, or `None`."""
        return self._by_kind.get(kind)

    def all(self) -> List[RecipeStepKindRegistration]:
        """Every registered step kind."""
        return list(self._by_kind.values())


# Module-level singleton shared by the plugin enable/disable path and the
# recipes executor registry - mirrors `requirement_checker_registry`.
recipe_step_kind_registry = RecipeStepKindRegistry()
