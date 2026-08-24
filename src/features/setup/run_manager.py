"""Durable setup-run orchestration.

Owns the lifecycle the wizard executes against: create a run (idempotent
under an existing active run), read it, record append-only step attempts, and
apply the pause/resume/cancel/retry/grant_consent actions - all validated
against the state machine in ``records``. Step *executors* (the code that
actually downloads a model, tests a backend, runs the smoke generation) plug
into the ``execute_current_step`` seam via a registered ``SetupExecutorRegistry``
(``register_executor_registry``); ``drive`` loops that seam forward until the
run needs the owner again (awaiting consent, paused, or terminal).

``drive`` is plain synchronous code and a route handler calling it inline blocks
the whole HTTP request for as long as the slowest step takes (a big
``models.index`` scan or a multi-GB download). ``drive_async`` (below) runs it
on a background ``threading.Thread`` and returns immediately, so a route can
respond with the run's current view and let the frontend's existing poll of
``GET /runs/{id}`` show progress.
"""

import logging
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from src.features.setup.records import (
    SetupRun,
    SetupRunStatus,
    SetupStepAttempt,
    SetupStepStatus,
    is_legal_transition,
)
from src.features.setup.run_dto import redact_safe_dict
from src.features.setup.run_repository import IntegrityError, SetupRunRepository

logger = logging.getLogger(__name__)


class SetupRunError(Exception):
    """Base for setup-run domain errors."""


class SetupRunNotFound(SetupRunError):
    pass


class IllegalSetupTransition(SetupRunError):
    """A requested status move is not permitted from the current status."""

    def __init__(self, src: SetupRunStatus, dst: SetupRunStatus):
        self.src = src
        self.dst = dst
        super().__init__(f"Illegal setup-run transition {src.value} -> {dst.value}")


class SetupExecutorNotConfigured(SetupRunError):
    """The step-executor registry has not been wired yet."""


# Which action drives which target status. `retry_step` is special (it reopens a
# failed run and records a new attempt) and handled outside this map.
_ACTION_TARGET: Dict[str, SetupRunStatus] = {
    "pause": SetupRunStatus.PAUSED,
    "resume": SetupRunStatus.RUNNING,
    "cancel": SetupRunStatus.CANCELLED,
}

# The statuses each action may be applied from. Tighter than raw transition
# legality: e.g. RUNNING is a legal target of `resume`, but retrying a step is
# only meaningful on a FAILED run, and resuming only on a not-yet-running one.
_ACTION_SOURCES: Dict[str, frozenset] = {
    "pause": frozenset(
        {
            SetupRunStatus.PENDING,
            SetupRunStatus.RUNNING,
            SetupRunStatus.AWAITING_CONSENT,
        }
    ),
    "resume": frozenset(
        {
            SetupRunStatus.PENDING,
            SetupRunStatus.PAUSED,
            SetupRunStatus.AWAITING_CONSENT,
        }
    ),
    "cancel": frozenset(
        {
            SetupRunStatus.PENDING,
            SetupRunStatus.RUNNING,
            SetupRunStatus.AWAITING_CONSENT,
            SetupRunStatus.PAUSED,
        }
    ),
    "retry_step": frozenset({SetupRunStatus.FAILED}),
}

#: `grant_consent` is not in `_ACTION_TARGET`/`_ACTION_SOURCES`: it targets one
#: specific *step* (via a required `step_key`), not "the run", so it gets its own
#: method (`SetupRunManager.grant_consent`) rather than the generic state-only
#: dispatch. It is still a recognized action, dispatched separately by the route.
VALID_ACTIONS: Tuple[str, ...] = ("pause", "resume", "cancel", "retry_step", "grant_consent")


class SetupRunManager:
    """State-machine-enforcing orchestrator over ``SetupRunRepository``."""

    def __init__(self, repository: Optional[SetupRunRepository] = None):
        self.repo = repository or SetupRunRepository()
        # Guards the create critical section within a process; the DB unique
        # index on active_marker is the cross-process guarantee.
        self._create_lock = threading.Lock()
        # Executor seam: an object exposing execute(run, step_key) -> attempt kw.
        self._executor_registry: Any = None
        # `drive_async` bookkeeping: which run_ids currently have a background
        # drive thread in flight, guarded by `_drive_lock` (see `drive_async`).
        self._driving: Set[str] = set()
        self._drive_lock = threading.Lock()

    # --- creation (idempotent) --------------------------------------------

    def create_run(
        self,
        recipe_id: str,
        *,
        recipe_version: int = 1,
        safe_input: Optional[Dict[str, Any]] = None,
        created_by: Optional[str] = None,
    ) -> SetupRun:
        """Start a run, or return the existing active one.

        Only one run may be active (pending/running/awaiting_consent/paused)
        instance-wide. A second create while one is active is a no-op that
        returns the current active run, so the endpoint is safely retryable.
        """
        clean_input = redact_safe_dict(safe_input)
        with self._create_lock:
            existing = self.repo.get_active_run()
            if existing is not None:
                return existing
            try:
                return self.repo.insert_run(
                    recipe_id=recipe_id,
                    recipe_version=recipe_version,
                    safe_input=clean_input,
                    created_by=created_by,
                )
            except IntegrityError:
                # Lost the race to another writer/process; its run is the one.
                active = self.repo.get_active_run()
                if active is not None:
                    return active
                raise

    # --- reads -------------------------------------------------------------

    def get_run(self, run_id: str) -> Optional[SetupRun]:
        return self.repo.get_run(run_id)

    def get_run_or_raise(self, run_id: str) -> SetupRun:
        run = self.repo.get_run(run_id)
        if run is None:
            raise SetupRunNotFound(run_id)
        return run

    def list_attempts(self, run_id: str) -> List[SetupStepAttempt]:
        return self.repo.list_attempts(run_id)

    def get_latest_completed_run(self, recipe_id: str) -> Optional[SetupRun]:
        """The most recent COMPLETED run for `recipe_id`, if any - see
        `SetupRunRepository.get_latest_completed_run`."""
        return self.repo.get_latest_completed_run(recipe_id)

    # --- transitions -------------------------------------------------------

    def transition(
        self,
        run_id: str,
        target: SetupRunStatus,
        *,
        current_step: Optional[str] = None,
        safe_output: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        safe_error_detail: Optional[str] = None,
        clear_error: bool = False,
    ) -> SetupRun:
        """Move a run to ``target`` if the state machine allows it.

        A terminal run (completed/failed/cancelled) has no legal moves, so any
        attempt to mutate it raises ``IllegalSetupTransition`` - completed runs
        are immutable.
        """
        run = self.get_run_or_raise(run_id)
        if not is_legal_transition(run.status, target):
            raise IllegalSetupTransition(run.status, target)
        return self.repo.update_run(
            run_id,
            status=target,
            current_step=current_step,
            safe_output=redact_safe_dict(safe_output) if safe_output else None,
            error_code=error_code,
            safe_error_detail=safe_error_detail,
            clear_error=clear_error,
        )

    def record_step_attempt(
        self,
        run_id: str,
        step_key: str,
        status: SetupStepStatus,
        *,
        attempt_id: Optional[str] = None,
        progress_current: Optional[int] = None,
        progress_total: Optional[int] = None,
        progress_unit: Optional[str] = None,
        safe_input: Optional[Dict[str, Any]] = None,
        safe_output: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        safe_error_detail: Optional[str] = None,
        finished: bool = False,
    ) -> SetupStepAttempt:
        """Append a step attempt, or - when ``attempt_id`` is given - update
        that existing row in place instead. Rejected on a terminal run (its
        history is frozen). Attempt numbers auto-increment per ``step_key``
        for a fresh insert, so a *retry* is always a new row - provenance is
        never overwritten; ``attempt_id`` is for progressively filling in the
        *same* logical attempt (interim progress ticks, then its terminal
        outcome), used by ``executors/registry.py``'s progress-report seam -
        see ``StepContext.report_progress`` in ``executors/base.py``.
        """
        run = self.get_run_or_raise(run_id)
        if run.is_terminal:
            raise IllegalSetupTransition(run.status, run.status)
        if attempt_id is not None:
            updated = self.repo.update_attempt(
                attempt_id,
                status=status,
                progress_current=progress_current,
                progress_total=progress_total,
                progress_unit=progress_unit,
                safe_output=redact_safe_dict(safe_output) if safe_output else None,
                error_code=error_code,
                safe_error_detail=safe_error_detail,
                finished=finished,
            )
            if updated is not None:
                return updated
            # The row vanished (shouldn't happen outside test surgery) - fall
            # through to a fresh insert rather than returning None.
        return self.repo.insert_attempt(
            run_id,
            step_key,
            status,
            progress_current=progress_current,
            progress_total=progress_total,
            progress_unit=progress_unit,
            safe_input=redact_safe_dict(safe_input) if safe_input else None,
            safe_output=redact_safe_dict(safe_output) if safe_output else None,
            error_code=error_code,
            safe_error_detail=safe_error_detail,
            finished=finished,
        )

    # --- actions (routes call this) ---------------------------------------

    def apply_action(self, run_id: str, action: str) -> SetupRun:
        """Apply pause/resume/cancel/retry_step, validated as a transition.

        This only moves the run's status (and, for ``retry_step``, opens a fresh
        attempt row); executing the real work of a step is separate."""
        if action not in VALID_ACTIONS:
            raise SetupRunError(f"Unknown setup action: {action}")
        run = self.get_run_or_raise(run_id)

        # Gate on the action's allowed source statuses first (tighter than raw
        # transition legality), so e.g. retry_step on a pending run is rejected.
        if run.status not in _ACTION_SOURCES[action]:
            target = (
                SetupRunStatus.RUNNING
                if action == "retry_step"
                else _ACTION_TARGET[action]
            )
            raise IllegalSetupTransition(run.status, target)

        if action == "retry_step":
            # Reopen a failed run and record a new attempt for the failed step.
            if run.current_step:
                self.repo.insert_attempt(
                    run_id, run.current_step, SetupStepStatus.RUNNING
                )
            return self.repo.update_run(
                run_id, status=SetupRunStatus.RUNNING, clear_error=True
            )

        target = _ACTION_TARGET[action]
        if not is_legal_transition(run.status, target):
            raise IllegalSetupTransition(run.status, target)
        return self.repo.update_run(run_id, status=target)

    # --- resume semantics --------------------------------------------------

    def resume_position(self, run_id: str) -> Optional[SetupRun]:
        """Reconstruct a run's position from the database alone.

        A restarted process holds no in-memory cursor: the run's persisted
        status + current_step + append-only attempts ARE the position. This is a
        plain re-read, named to make the resume contract explicit - the frontend
        renders whatever this returns.
        """
        return self.repo.get_run(run_id)

    # --- executor seam -----------------------------------------------------

    def register_executor_registry(self, registry: Any) -> None:
        """Wire the registry that knows how to run each step_key.

        Kept separate from construction so the persistence layer ships and is
        testable now, before any executor exists.
        """
        self._executor_registry = registry

    def execute_current_step(self, run_id: str) -> SetupRun:
        """Run ``current_step`` via the registered executor and record its
        attempt. Raises until an executor registry is wired, so the gap is loud
        rather than silent."""
        if self._executor_registry is None:
            raise SetupExecutorNotConfigured(
                "No setup step-executor registry is wired (Phase 3)."
            )
        run = self.get_run_or_raise(run_id)
        return self._executor_registry.execute(self, run)

    def drive(self, run_id: str, *, max_steps: int = 50) -> SetupRun:
        """Advance ``run_id`` by repeatedly calling ``execute_current_step``
        until it stops being auto-executable: awaiting consent, paused,
        terminal (completed/failed/cancelled), or ``max_steps`` reached (a
        circuit breaker against a pathological recipe, not a normal exit).

        `SetupExecutorRegistry.execute()` only ever advances one step per
        call and explicitly leaves "loop, and decide when to" to the caller
        (see its docstring) - this is that caller, used by the setup routes
        after any action that leaves a run PENDING/RUNNING (create, resume,
        retry_step, grant_consent) so the wizard runs every already-approved
        step without the frontend needing to poll-and-reissue.
        """
        run = self.get_run_or_raise(run_id)
        if self._executor_registry is None:
            # No executor registry wired (e.g. a minimal test container that
            # only exercises the state machine) - driving is opportunistic,
            # not a hard requirement of every action, so this is a no-op
            # rather than `execute_current_step`'s loud
            # `SetupExecutorNotConfigured`.
            return run
        steps_run = 0
        while run.status in (SetupRunStatus.PENDING, SetupRunStatus.RUNNING) and steps_run < max_steps:
            before = run.current_step
            run = self.execute_current_step(run_id)
            steps_run += 1
            # A step that neither changed status nor advanced current_step
            # would spin forever (defensive; execute() always does one or the
            # other today).
            if run.status == SetupRunStatus.RUNNING and run.current_step == before:
                break
        return run

    def drive_async(self, run_id: str, *, max_steps: int = 50) -> None:
        """Kick off ``drive(run_id)`` on a background thread and return
        immediately - the request-blocking fix (see module docstring). Callers
        (the setup routes) should re-read the run via
        `get_run`/`get_run_or_raise` right after this call to build their
        response - it will show whatever status the run is in *right now*
        (typically still pending/running), which is exactly what the
        frontend's poll loop is already built to render.

        A no-op if a drive for this ``run_id`` is already in flight - this is
        the double-POST guard: a retried/duplicate POST (create, or the same
        action called twice) must never spawn a second thread racing the
        first one through the same run's steps. The in-flight drive is left
        to do the work; nothing is lost.

        Also a no-op (same as `drive`) when no executor registry is wired -
        there is nothing to run in the background.
        """
        if self._executor_registry is None:
            return
        with self._drive_lock:
            if run_id in self._driving:
                return
            self._driving.add(run_id)

        def _run() -> None:
            try:
                self.drive(run_id, max_steps=max_steps)
            except Exception:
                # A step executor's own exceptions are already caught inside
                # `SetupExecutorRegistry.execute` and turned into a FAILED
                # transition. This is the outer safety net for anything that
                # escapes that (a plumbing bug), so a crashed drive marks the run
                # FAILED rather than leaving it stuck RUNNING forever.
                logger.exception("Background setup-run drive crashed for run '%s'", run_id)
                self._fail_run_after_crash(run_id)
            finally:
                with self._drive_lock:
                    self._driving.discard(run_id)

        threading.Thread(target=_run, name=f"setup-run-drive-{run_id}", daemon=True).start()

    def _fail_run_after_crash(self, run_id: str) -> None:
        """Best-effort: move `run_id` to FAILED after its background drive
        thread raised. Swallows its own errors - this already runs from
        inside an exception handler on a background thread, so there is
        nowhere further to report a failure here."""
        try:
            run = self.get_run(run_id)
            if run is not None and not run.is_terminal:
                self.transition(
                    run_id,
                    SetupRunStatus.FAILED,
                    error_code="SETUP_RUN_CRASHED",
                    safe_error_detail=(
                        "This step failed unexpectedly. You can retry it once the problem is fixed."
                    ),
                )
        except Exception:
            logger.exception("Failed to mark run '%s' failed after a crashed background drive", run_id)

    def grant_consent(self, run_id: str, step_key: str, *, granted_by: Optional[str] = None) -> SetupRun:
        """Approve the artifacts an ``awaiting_consent`` step is parked on.

        Only legal while ``run_id`` is AWAITING_CONSENT on exactly ``step_key``
        (a stale/wrong step_key from a client that polled an older view is
        rejected with a clear message rather than silently approving the
        current step). Records a fresh SUCCEEDED attempt for the step - the
        parked attempt's own ``consent_request`` becomes the audit trail of
        what was approved - then advances the run past it, exactly like a
        normal step success (see ``SetupExecutorRegistry.advance_past``).
        """
        run = self.get_run_or_raise(run_id)
        if run.status != SetupRunStatus.AWAITING_CONSENT:
            raise IllegalSetupTransition(run.status, SetupRunStatus.RUNNING)
        if run.current_step != step_key:
            raise SetupRunError(
                f"Step '{step_key}' is not the step awaiting consent "
                f"(the run is currently parked on '{run.current_step}')."
            )
        if self._executor_registry is None:
            raise SetupExecutorNotConfigured(
                "No setup step-executor registry is wired (Phase 3)."
            )

        parked_attempts = [a for a in self.repo.list_attempts(run_id) if a.step_key == step_key]
        parked = parked_attempts[-1] if parked_attempts else None
        consent_request = (parked.safe_output or {}).get("consent_request") if parked else None

        self.record_step_attempt(
            run_id,
            step_key,
            SetupStepStatus.SUCCEEDED,
            safe_output={"consent_granted": True, "granted_by": granted_by, "approved": consent_request},
            finished=True,
        )

        recipe = self._executor_registry.catalog.get_recipe(run.recipe_id, run.recipe_version)
        step = recipe.get_step(step_key) if recipe else None
        if step is None:
            return self.transition(
                run_id,
                SetupRunStatus.FAILED,
                error_code="RECIPE_NOT_FOUND",
                safe_error_detail=(
                    f"The setup recipe '{run.recipe_id}' (version {run.recipe_version}) "
                    "is no longer available on this installation."
                ),
            )
        run = self.transition(run_id, SetupRunStatus.RUNNING, current_step=step_key)
        return self._executor_registry.advance_past(self, run, step)
