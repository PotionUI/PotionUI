"""Dispatches a setup-run step to its executor by `kind` and records the
outcome as one `setup_step_attempts` row.

This is the concrete object `SetupRunManager.register_executor_registry()`
expects: anything exposing `.execute(run_manager, run) -> SetupRun` (see
`run_manager.execute_current_step`, which calls
`self._executor_registry.execute(self, run)`).

One call to `execute()` drives the run forward by exactly one step: it runs
whichever step is "current" (or the first step, if none has started yet),
records its outcome, and advances `current_step` on success (or moves the run
to FAILED/COMPLETED). A caller wanting to run a whole recipe end-to-end calls
this repeatedly - that looping, and deciding *when* to call it, is the setup
route/UI's job, not this registry's.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

from src.features.setup.executors.base import StepContext, StepExecutor, StepResult
from src.features.setup.recipe_catalog import RecipeCatalog
from src.features.setup.records import SetupRun, SetupRunStatus, SetupStepStatus
from src.features.setup.run_manager import SetupRunError, SetupRunManager

logger = logging.getLogger(__name__)

#: Statuses `execute()` is willing to act on. AWAITING_CONSENT/PAUSED runs
#: need an explicit resume/`grant_consent` first (see `SetupRunManager.
#: grant_consent`, which advances past a parked step without calling this
#: method again); a terminal run is already rejected by `record_step_attempt`
#: itself, but rejecting it here too gives a clearer message than "illegal
#: transition".
_EXECUTABLE_STATUSES = frozenset({SetupRunStatus.PENDING, SetupRunStatus.RUNNING})


class SetupExecutorRegistry:
    """Maps a recipe step's `kind` to the `StepExecutor` that runs it."""

    def __init__(self, catalog: RecipeCatalog, executors: Dict[str, StepExecutor]):
        self.catalog = catalog
        self._executors: Dict[str, StepExecutor] = dict(executors)

    def register(self, kind: str, executor: StepExecutor) -> None:
        """Register (or override) the executor for one step kind."""
        self._executors[kind] = executor

    def execute(self, run_manager: SetupRunManager, run: SetupRun) -> SetupRun:
        if run.status not in _EXECUTABLE_STATUSES:
            raise SetupRunError(
                f"Cannot execute a step while the run is '{run.status.value}' "
                "(resume it first)."
            )

        recipe = self.catalog.get_recipe(run.recipe_id, run.recipe_version)
        if recipe is None:
            return run_manager.transition(
                run.id,
                SetupRunStatus.FAILED,
                error_code="RECIPE_NOT_FOUND",
                safe_error_detail=(
                    f"The setup recipe '{run.recipe_id}' (version {run.recipe_version}) "
                    "is no longer available on this installation."
                ),
            )

        if run.status == SetupRunStatus.PENDING:
            # Flip to RUNNING first (a legal move from any non-terminal
            # status a step could plausibly complete to, including
            # COMPLETED below for a steps-less recipe) - PENDING cannot
            # transition directly to COMPLETED.
            run = run_manager.transition(run.id, SetupRunStatus.RUNNING)

        step = recipe.get_step(run.current_step) if run.current_step else None
        if step is None:
            if not recipe.steps:
                return run_manager.transition(run.id, SetupRunStatus.COMPLETED)
            step = recipe.steps[0]

        if run.current_step != step.key:
            # Set the pointer without touching the status transition gate,
            # which only governs `status` moves - covers both "just flipped
            # to RUNNING above" and "already RUNNING but current_step was
            # never recorded" (e.g. the run was only ever `resume`d, which
            # moves status without touching current_step).
            run = run_manager.repo.update_run(run.id, current_step=step.key)

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
                attempt = run_manager.record_step_attempt(
                    run.id,
                    step.key,
                    SetupStepStatus.RUNNING,
                    attempt_id=_attempt_id["value"],
                    progress_current=progress_current,
                    progress_total=progress_total,
                    progress_unit=progress_unit,
                    finished=False,
                )
                _attempt_id["value"] = attempt.id
            except Exception:  # a broken progress report must never fail the step
                logger.exception(
                    "Failed to record progress for setup step '%s' (%s)", step.key, step.kind
                )

        executor = self._executors.get(step.kind)
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
            # the owner approves, per `SetupRunManager.grant_consent`.
            run_manager.record_step_attempt(
                run.id,
                step.key,
                SetupStepStatus.AWAITING_CONSENT,
                attempt_id=_attempt_id["value"],
                safe_output=result.to_safe_output(),
                finished=False,
            )
            return run_manager.transition(
                run.id, SetupRunStatus.AWAITING_CONSENT, current_step=step.key
            )

        run_manager.record_step_attempt(
            run.id,
            step.key,
            SetupStepStatus.SUCCEEDED if result.success else SetupStepStatus.FAILED,
            attempt_id=_attempt_id["value"],
            safe_output=result.to_safe_output(),
            error_code=result.error_code,
            safe_error_detail=result.safe_error_detail,
            finished=True,
        )

        if not result.success:
            return run_manager.transition(
                run.id,
                SetupRunStatus.FAILED,
                current_step=step.key,
                error_code=result.error_code,
                safe_error_detail=result.safe_error_detail,
            )

        return self.advance_past(run_manager, run, step)

    def advance_past(self, run_manager: SetupRunManager, run: SetupRun, step) -> SetupRun:
        """Move `run` on to whatever follows `step` in its recipe (or complete
        it, if `step` was last). Shared by `execute()`'s on-success path and
        `SetupRunManager.grant_consent` (which records its own attempt for the
        just-approved step, then needs this same "what's next" logic without
        re-invoking an executor)."""
        recipe = self.catalog.get_recipe(run.recipe_id, run.recipe_version)
        next_step = recipe.next_step_after(step.key) if recipe else None
        if next_step is None:
            return run_manager.transition(run.id, SetupRunStatus.COMPLETED, current_step=step.key)
        return run_manager.repo.update_run(run.id, current_step=next_step.key)
