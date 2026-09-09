"""Recipes: an ordered list of steps that takes an instance from "nothing
installed" to "this preset generates".

A recipe is a versioned YAML document under `content/recipes/` (or a plugin's
own `recipes:` root). `RecipeCatalog` discovers and validates them,
`RecipeRunner` executes one as a durable run, and each step's `kind:` is
dispatched to a `StepExecutor` (see `executors/`). Plugins add step kinds
through the manifest `recipe_steps:` section.

The first-run wizard (`src.features.setup`) is one caller among two: the admin
Recipes page runs the same recipes, skipping the steps recipes mark
`onboarding_only`.
"""

from src.features.recipes.catalog import RecipeCatalog
from src.features.recipes.run_repository import RecipeRunRepository
from src.features.recipes.runner import RecipeRunner

__all__ = [
    "RecipeCatalog",
    "RecipeRunRepository",
    "RecipeRunner",
]
