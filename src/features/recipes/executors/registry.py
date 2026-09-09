"""Dispatches a recipe-run step to its executor by `kind` and records the
outcome as one `setup_step_attempts` row.

This is the concrete object `RecipeRunner.register_executor_registry()`
expects: anything exposing `.execute(runner, run) -> RecipeRun` (see
`runner.execute_current_step`, which calls
`self._executor_registry.execute(self, run)`).

One call to `execute()` drives the run forward by exactly one step: it runs
whichever step is "current" (or the first step, if none has started yet),
records its outcome, and advances `current_step` on success (or moves the run
to FAILED/COMPLETED). A caller wanting to run a whole recipe end-to-end calls
this repeatedly - that looping, and deciding *when* to call it, is the
caller's job (the routes that drive a run), not this registry's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.features.recipes.executors.base import StepContext, StepExecutor, StepResult
from src.features.recipes.catalog import RecipeCatalog
from src.features.recipes.records import RecipeRun, RecipeRunStatus, RecipeStepStatus
from src.features.recipes.runner import RecipeRunError, RecipeRunner

logger = logging.getLogger(__name__)

#: Statuses `execute()` is willing to act on. AWAITING_CONSENT/PAUSED runs
#: need an explicit resume/`grant_consent` first (see `RecipeRunner.
#: grant_consent`, which advances past a parked step without calling this
#: method again); a terminal run is already rejected by `record_step_attempt`
#: itself, but rejecting it here too gives a clearer message than "illegal
#: transition".
_EXECUTABLE_STATUSES = frozenset({RecipeRunStatus.PENDING, RecipeRunStatus.RUNNING})


@dataclass(frozen=True)
class StepKind:
    """One runnable step kind, and where it came from - what
    `GET /api/recipes/step-kinds` reports."""

    kind: str
    source: str  # "core" | "plugin"
    plugin_id: Optional[str] = None


class RecipeExecutorRegistry:
    """Maps a recipe step's `kind` to the `StepExecutor` that runs it.

    Core's own kinds are held here. Plugin-contributed kinds live on the
    shared `RecipeStepKindRegistry` (`src.platform.plugins.recipe_steps`),
    which enable/disable adds to and removes from at runtime - this registry
    consults it as a fallback so a plugin kind becomes runnable the moment its
    plugin is enabled, with nothing to re-wire.
    """

    def __init__(
        self,
        catalog: RecipeCatalog,
        executors: Dict[str, StepExecutor],
        step_kind_registry=None,
    ):
        self.catalog = catalog
        self._executors: Dict[str, StepExecutor] = dict(executors)
        self.step_kind_registry = step_kind_registry

    def register(self, kind: str, executor: StepExecutor) -> None:
        """Register (or override) the executor for one step kind."""
        self._executors[kind] = executor

    def get_executor(self, kind: str) -> Optional[StepExecutor]:
        """The executor for `kind`: core's, else whatever a plugin registered."""
        executor = self._executors.get(kind)
        if executor is not None:
            return executor
        if self.step_kind_registry is None:
            return None
        registration = self.step_kind_registry.get(kind)
        return registration.executor if registration else None

    def list_kinds(self) -> List[StepKind]:
        """Every runnable step kind, core's first, then plugin-contributed."""
        kinds = [StepKind(kind=k, source="core") for k in sorted(self._executors)]
        if self.step_kind_registry is not None:
            kinds.extend(
                StepKind(kind=reg.kind, source="plugin", plugin_id=reg.source)
                for reg in sorted(self.step_kind_registry.all(), key=lambda r: r.kind)
                if reg.kind not in self._executors
            )
        return kinds

    def execute(self, runner: RecipeRunner, run: RecipeRun) -> RecipeRun:
        if run.status not in _EXECUTABLE_STATUSES:
            raise RecipeRunError(
                f"Cannot execute a step while the run is '{run.status.value}' "
                "(resume it first)."
            )

        recipe = self.catalog.get_recipe(run.recipe_id, run.recipe_version)
        if recipe is None:
            return runner.transition(
                run.id,
                RecipeRunStatus.FAILED,
                error_code="RECIPE_NOT_FOUND",
                safe_error_detail=(
                    f"The recipe '{run.recipe_id}' (version {run.recipe_version}) "
                    "is no longer available on this installation."
                ),
            )

        if run.status == RecipeRunStatus.PENDING:
            # Flip to RUNNING first (a legal move from any non-terminal
            # status a step could plausibly complete to, including
            # COMPLETED below for a steps-less recipe) - PENDING cannot
            # transition directly to COMPLETED.
            run = runner.transition(run.id, RecipeRunStatus.RUNNING)

        plan = recipe.steps_for_mode(run.mode)
        step = recipe.get_step(run.current_step) if run.current_step else None
        if step is None or step not in plan:
            if not plan:
                return runner.transition(run.id, RecipeRunStatus.COMPLETED)
            step = plan[0]

        if run.current_step != step.key:
            # Set the pointer without touching the status transition gate,
            # which only governs `status` moves - covers both "just flipped
            # to RUNNING above" and "already RUNNING but current_step was
            # never recorded" (e.g. the run was only ever `resume`d, which
            # moves status without touching current_step).
            run = runner.repo.update_run(run.id, current_step=step.key)

        # Progress-report seam: a long-running executor calls
        # `context.report_progress(...)` from its poll loop. The first call
        # lazily inserts a RUNNING attempt row (so the frontend can poll it
        # mid-flight); every call after updates the SAME row in place via
        # `attempt_id`. If the executor never reports progress (the common case),
        # `_attempt_id` stays `None` and the terminal write inserts one row.
        _attempt_id: Dict[str, Optional[str]] = {"value": None}

        def _report_progress(
            progress_current=None, progress_total=None, progress_unit=None
        ) -> None:
            try:
                attempt = runner.record_step_attempt(
                    run.id,
                    step.key,
                    RecipeStepStatus.RUNNING,
                    attempt_id=_attempt_id["value"],
                    progress_current=progress_current,
                    progress_total=progress_total,
                    progress_unit=progress_unit,
                    finished=False,
                )
                _attempt_id["value"] = attempt.id
            except Exception:  # a broken progress report must never fail the step
                logger.exception(
                    "Failed to record progress for recipe step '%s' (%s)", step.key, step.kind
                )

        executor = self.get_executor(step.kind)
        if executor is None:
            result = StepResult.fail(
                "STEP_NOT_IMPLEMENTED",
                f"'{step.title or step.key}' isn't available yet - support for "
                f"'{step.kind}' steps is coming in a later update.",
            )
        else:
            try:
                result = executor.execute(
                    StepContext(run=run, recipe=recipe, step=step, report_progress=_report_progress)
                )
            except Exception as exc:  # an executor must never crash the run silently
                logger.exception("Setup step '%s' (%s) raised", step.key, step.kind)
                result = StepResult.fail(
                    "STEP_EXECUTOR_ERROR",
                    f"'{step.title or step.key}' failed unexpectedly: {exc}",
                )

        if result.awaiting_consent:
            # Parked, not finished: no terminal `finished_at` on this attempt -
            # `grant_consent` records the actual outcome (a fresh attempt) once
            # the owner approves, per `RecipeRunner.grant_consent`.
            runner.record_step_attempt(
                run.id,
                step.key,
                RecipeStepStatus.AWAITING_CONSENT,
                attempt_id=_attempt_id["value"],
                safe_output=result.to_safe_output(),
                finished=False,
            )
            return runner.transition(
                run.id, RecipeRunStatus.AWAITING_CONSENT, current_step=step.key
            )

        runner.record_step_attempt(
            run.id,
            step.key,
            RecipeStepStatus.SUCCEEDED if result.success else RecipeStepStatus.FAILED,
            attempt_id=_attempt_id["value"],
            safe_output=result.to_safe_output(),
            error_code=result.error_code,
            safe_error_detail=result.safe_error_detail,
            finished=True,
        )

        if not result.success:
            return runner.transition(
                run.id,
                RecipeRunStatus.FAILED,
                current_step=step.key,
                error_code=result.error_code,
                safe_error_detail=result.safe_error_detail,
            )

        return self.advance_past(runner, run, step)

    def advance_past(self, runner: RecipeRunner, run: RecipeRun, step) -> RecipeRun:
        """Move `run` on to whatever follows `step` in its recipe (or complete
        it, if `step` was last). Shared by `execute()`'s on-success path and
        `RecipeRunner.grant_consent` (which records its own attempt for the
        just-approved step, then needs this same "what's next" logic without
        re-invoking an executor)."""
        recipe = self.catalog.get_recipe(run.recipe_id, run.recipe_version)
        next_step = recipe.next_step_after(step.key, run.mode) if recipe else None
        if next_step is None:
            return runner.transition(run.id, RecipeRunStatus.COMPLETED, current_step=step.key)
        return runner.repo.update_run(run.id, current_step=next_step.key)
