"""`workspace.activate` - the recipe's last step: mark onboarding complete for
the owner who ran it. Writes `user_onboarding_state` (migration 090) via the
repository method that already exists for this
(`SetupRunRepository.upsert_onboarding_state`) - nothing new to persist.

`first_generation_id` is threaded from `generation.smoke`'s own recorded
output (see `generation_smoke.py`) when present, so onboarding's "first real
generation" pointer is the smoke run itself rather than nothing.
"""

from __future__ import annotations

from typing import Optional

from src.features.setup.executors.base import StepContext, StepResult
from src.features.setup.records import OnboardingStatus
from src.features.setup.run_repository import SetupRunRepository


class WorkspaceActivateExecutor:
    def __init__(self, run_repository: Optional[SetupRunRepository] = None):
        self.run_repository = run_repository or SetupRunRepository()

    def execute(self, context: StepContext) -> StepResult:
        owner_id = context.owner_user_id
        if not owner_id:
            return StepResult.fail(
                "OWNER_NOT_FOUND",
                "We couldn't find the account that started setup, so onboarding can't be marked complete automatically.",
            )

        first_generation_id = self._smoke_generation_id(context)

        self.run_repository.upsert_onboarding_state(
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
