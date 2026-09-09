"""Recipes: shipping one, and contributing a step kind.

A recipe is a versioned YAML document describing the ordered journey from
"nothing installed" to "this preset generates" - which plugins to enable, which
model files to fetch, which preset to install, and a smoke generation to prove
it. Core scans `content/recipes/{marketplace,local}/*.yml`; a plugin ships its
own by declaring a `recipes:` root in `manifest.yml`:

    recipes:
      - path: recipes

Each step names a `kind:`, and a plugin can add kinds of its own. Declare them
under `recipe_steps:`, pointing `backend` at a class implementing
`StepExecutor` below:

    recipe_steps:
      - kind: collections.ensure
        backend: steps:EnsureCollections

A registered kind is both runnable and lintable: `scripts/recipe_lint.py` and
the running catalog accept recipes that use it, while an unregistered kind
stays an unknown-kind error.

`StepExecutor.execute()` receives a `StepContext` (the run, the parsed recipe,
the step being run, and a `report_progress` callback for long work) and returns
a `StepResult`. Three outcomes: `StepResult.ok(...)`, `StepResult.fail(code,
detail, suggested_repair)`, and `StepResult.awaiting(consent_request)` for a
step that must not proceed without an explicit go-ahead (a multi-GB download,
say). Failure messages are read by a non-technical owner mid-setup, so write
them as a plain sentence and put any admin-shaped fix in `suggested_repair`.

Mark a step `onboarding_only: true` in the recipe YAML when it only makes
sense during the first-run wizard - a run started from Admin -> Recipes skips
those.

See docs/plugin-api.md.
"""

from src.features.recipes.executors.base import StepContext, StepExecutor, StepResult
from src.features.recipes.records import RecipeRun, RecipeRunStatus, RecipeStepStatus
from src.features.recipes.schema import (
    Recipe,
    RecipeArtifact,
    RecipePresetRef,
    RecipeSmokeRef,
    RecipeStep,
)

__all__ = [
    "Recipe",
    "RecipeArtifact",
    "RecipePresetRef",
    "RecipeRun",
    "RecipeRunStatus",
    "RecipeSmokeRef",
    "RecipeStep",
    "RecipeStepStatus",
    "StepContext",
    "StepExecutor",
    "StepResult",
]
