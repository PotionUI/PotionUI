"""Wave-2 step kinds the schema already recognizes (`artifacts.plan`,
`artifacts.fetch`, `generation.smoke` - see `recipe_schema.DEFERRED_STEP_KINDS`)
so a recipe referencing them lints fine today. Executing one now reports a
clear, honest "not yet" rather than crashing or silently skipping - real
downloading and the real smoke generation ship in T3.3.
"""

from __future__ import annotations

from src.features.setup.executors.base import StepContext, StepResult


class DeferredStepExecutor:
    def __init__(self, kind: str):
        self.kind = kind

    def execute(self, context: StepContext) -> StepResult:
        return StepResult.fail(
            "STEP_NOT_IMPLEMENTED",
            f"'{context.step.title or context.step.key}' isn't available yet - "
            f"support for '{self.kind}' steps is coming in a later update.",
        )
