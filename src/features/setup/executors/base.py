"""Shared contract for Phase-3 setup-run step executors.

Each executor implements `StepExecutor.execute(context) -> StepResult`. The
registry (`registry.py`) dispatches by `RecipeStep.kind` and turns the result
into exactly one `setup_step_attempts` row via
`SetupRunner.record_step_attempt`. Executors receive their collaborators
through the constructor (composition root wiring, see
`src/bootstrap/container.py`'s `build_default_executor_registry` call) -
never a service locator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol

from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRun


def _noop_progress(
    progress_current: Optional[int] = None,
    progress_total: Optional[int] = None,
    progress_unit: Optional[str] = None,
) -> None:
    """Default `StepContext.report_progress` for executors that never report
    interim progress - a plain no-op so nothing has to guard against a `None`
    callback."""


@dataclass
class StepContext:
    """Everything an executor needs to run one step of one run.

    `report_progress` is the interim-progress seam (setup-run T3.7 follow-up):
    a long-running executor (e.g. `artifacts.fetch` polling a download) calls
    it from inside its own poll loop to push `progress_current`/
    `progress_total`/`progress_unit` onto the in-flight `setup_step_attempts`
    row *before* the step finishes, so `GET /api/setup/runs/{id}` (polled by
    the frontend while this call is still in flight - see `registry.py`,
    which runs synchronously in a threadpool worker) can render it. The
    registry wires the real callback per-step; the default here is a no-op so
    executors that never call it (most of them) and tests that construct a
    bare `StepContext` need no changes. Callers should throttle themselves
    (roughly once a second, or on a meaningful delta) - this is a plain DB
    write per call, not a queue.
    """

    run: SetupRun
    recipe: Recipe
    step: RecipeStep
    report_progress: Callable[..., None] = field(default=_noop_progress)

    @property
    def owner_user_id(self) -> Optional[str]:
        """The admin who started this run - the "owner" a recipe installs
        and assigns content for."""
        return self.run.created_by


@dataclass
class StepResult:
    """What an executor reports back.

    `safe_output`/`safe_error_detail` pass through the same redaction gate as
    everything else in `setup_step_attempts` (see `run_dto.redact_safe_dict`)
    once handed to `SetupRunner.record_step_attempt` - executors don't
    need to redact themselves, but should still never put a secret in either
    field on principle. Failure messages must read as a plain sentence a
    non-technical owner can understand; `suggested_repair` is the one place a
    concrete, possibly admin-shaped fix (e.g. "Open Administration ->
    Backends") belongs.

    A third outcome besides ok/fail: `awaiting_consent`. A step that would
    otherwise succeed but needs an explicit go-ahead first (e.g. `artifacts.plan`
    finding artifacts to download) reports this instead of `ok`, carrying a
    `consent_request` describing exactly what would happen. The registry parks
    the run in `awaiting_consent` rather than advancing; `SetupRunner.
    grant_consent` is what lets it proceed (see `runner.py`).
    """

    success: bool
    safe_output: Dict[str, Any] = field(default_factory=dict)
    error_code: Optional[str] = None
    safe_error_detail: Optional[str] = None
    suggested_repair: Optional[str] = None
    awaiting_consent: bool = False
    consent_request: Optional[Dict[str, Any]] = None

    @classmethod
    def ok(cls, safe_output: Optional[Dict[str, Any]] = None) -> "StepResult":
        return cls(success=True, safe_output=dict(safe_output or {}))

    @classmethod
    def fail(
        cls,
        error_code: str,
        detail: str,
        suggested_repair: Optional[str] = None,
    ) -> "StepResult":
        return cls(
            success=False,
            error_code=error_code,
            safe_error_detail=detail,
            suggested_repair=suggested_repair,
        )

    @classmethod
    def awaiting(
        cls,
        consent_request: Dict[str, Any],
        safe_output: Optional[Dict[str, Any]] = None,
    ) -> "StepResult":
        """The step is parked pending an explicit owner go-ahead.
        `consent_request` is `{"artifacts": [{"id", "display_name",
        "size_bytes", "kind"}], "total_bytes", "providers"?}` (pinned contract
        #3; `providers` is optional - see `artifacts_plan.
        ArtifactsPlanExecutor`) - what the UI shows before the owner
        approves."""
        return cls(
            success=False,
            safe_output=dict(safe_output or {}),
            awaiting_consent=True,
            consent_request=dict(consent_request),
        )

    def to_safe_output(self) -> Dict[str, Any]:
        """`safe_output` plus `suggested_repair`/`consent_request` under
        well-known keys, so they survive into the persisted attempt row (there
        is no separate column for either) - `run_dto.SetupStepAttemptView`
        promotes both back out into their own fields on the wire."""
        out = dict(self.safe_output)
        if self.suggested_repair:
            out["suggested_repair"] = self.suggested_repair
        if self.consent_request is not None:
            out["consent_request"] = self.consent_request
        return out


class StepExecutor(Protocol):
    """A step executor: one `kind` -> one callable."""

    def execute(self, context: StepContext) -> StepResult:
        ...
