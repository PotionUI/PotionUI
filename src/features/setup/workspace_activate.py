"""`workspace.activate` - the first-run wizard's last step: mark onboarding
complete for the owner who ran it. Writes `user_onboarding_state` (migration
090) through `OnboardingRepository` - nothing new to persist.

This is the one step kind that belongs to setup rather than to recipes: it
only means anything during onboarding, so recipes mark it `onboarding_only`
and a run started from the admin Recipes page skips it. Bootstrap registers it
onto the recipes executor registry (see `src/bootstrap/container.py`).

`first_generation_id` is threaded from `generation.smoke`'s own recorded
output (see `src/features/recipes/executors/generation_smoke.py`) when
present, so onboarding's "first real generation" pointer is the smoke run
itself rather than nothing.
"""

from __future__ import annotations

from typing import Optional

from src.features.recipes.executors.base import StepContext, StepResult
from src.features.recipes.run_repository import RecipeRunRepository
from src.features.setup.onboarding_repository import OnboardingRepository
from src.features.setup.records import OnboardingStatus


class WorkspaceActivateExecutor:
    def __init__(
        self,
        onboarding_repository: Optional[OnboardingRepository] = None,
        run_repository: Optional[RecipeRunRepository] = None,
    ):
        self.onboarding_repository = onboarding_repository or OnboardingRepository()
        self.run_repository = run_repository or RecipeRunRepository()

    def execute(self, context: StepContext) -> StepResult:
        owner_id = context.owner_user_id
        if not owner_id:
            return StepResult.fail(
                "OWNER_NOT_FOUND",
                "We couldn't find the account that started setup, so onboarding can't be marked complete automatically.",
            )

        first_generation_id = self._smoke_generation_id(context)

        self.onboarding_repository.upsert_onboarding_state(
            owner_id,
            status=OnboardingStatus.COMPLETED,
            first_generation_id=first_generation_id,
        )

        return StepResult.ok(
            {
                "recipe_id": context.recipe.id,
                "onboarding_status": OnboardingStatus.COMPLETED.value,
                "first_generation_id": first_generation_id,
            }
        )

    def _smoke_generation_id(self, context: StepContext) -> Optional[str]:
        smoke_step = next((s for s in context.recipe.steps if s.kind == "generation.smoke"), None)
        if smoke_step is None:
            return None
        attempts = [
            a
            for a in self.run_repository.list_attempts(context.run.id)
            if a.step_key == smoke_step.key and a.status.value == "succeeded"
        ]
        if not attempts:
            return None
        return (attempts[-1].safe_output or {}).get("generation_id")
