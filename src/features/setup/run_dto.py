"""Setup-run request/response DTOs and the redaction gate.

Everything that lands in ``safe_input`` / ``safe_output`` passes through
``redact_safe_payload`` first. The rule is deliberately paranoid: setup runs
touch tokens, provider credentials, and download URLs, and none of that may be
persisted or returned. Redaction is a *whitelist of plain fields* - only JSON
scalars (and nested dicts/lists of them) survive - combined with a blocklist of
secret-looking key names that are dropped outright even when their value looks
plain.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from src.features.setup.records import (
    SetupRun,
    SetupStepAttempt,
)

# Key names whose value is never persisted, matched case-insensitively as a
# substring so `api_key`, `X-Auth-Token`, `claim_token`, `db_password`, etc. are
# all caught.
_SECRET_KEY_PATTERNS = re.compile(
    r"(token|secret|password|passwd|credential|api[_-]?key|apikey|"
    r"authorization|auth|private[_-]?key|access[_-]?key|session|cookie|"
    r"bearer|signature)",
    re.IGNORECASE,
)

# The only value types allowed to survive into a safe payload.
_PLAIN_SCALARS = (str, int, float, bool)

_MAX_DEPTH = 6


def _is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_PATTERNS.search(key))


def redact_safe_payload(value: Any, _depth: int = 0) -> Any:
    """Return a copy of ``value`` safe to persist/return.

    Drops secret-looking keys, keeps only plain scalars and nested plain
    containers, and coerces anything else (service objects, callables, unknown
    types) out. Returns ``None`` for a non-plain top-level value so a caller can
    never smuggle a serialized object into ``safe_input``.
    """
    if _depth > _MAX_DEPTH:
        return None
    if value is None or isinstance(value, bool):
        # bool must be checked before int (bool is an int subclass).
        return value
    if isinstance(value, _PLAIN_SCALARS):
        return value
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for raw_key, raw_val in value.items():
            key = str(raw_key)
            if _is_secret_key(key):
                continue
            cleaned = redact_safe_payload(raw_val, _depth + 1)
            if cleaned is not None or raw_val is None:
                out[key] = cleaned
        return out
    if isinstance(value, (list, tuple)):
        return [
            redact_safe_payload(item, _depth + 1)
            for item in value
            if redact_safe_payload(item, _depth + 1) is not None or item is None
        ]
    # Anything else (objects, callables, bytes, sets, ...) is dropped.
    return None


def redact_safe_dict(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Redact and guarantee a dict result for the *_input / *_output columns."""
    if not value:
        return {}
    cleaned = redact_safe_payload(value)
    return cleaned if isinstance(cleaned, dict) else {}


# --- request DTOs ----------------------------------------------------------


class CreateSetupRunRequest(BaseModel):
    """Body of ``POST /api/setup/runs``. ``safe_input`` is redacted server-side
    regardless of what the client sends."""

    recipe_id: str = Field(..., min_length=1)
    recipe_version: int = 1
    safe_input: Dict[str, Any] = Field(default_factory=dict)


class SetupRunActionRequest(BaseModel):
    """Body of ``POST /api/setup/runs/{run_id}/actions/{action}``. Only
    ``grant_consent`` uses ``step_key`` today; the other actions (pause/
    resume/cancel/retry_step) ignore it, so a client may always POST this
    shape without branching on which action it's calling."""

    step_key: Optional[str] = None


# --- response DTOs ---------------------------------------------------------


class SetupStepAttemptView(BaseModel):
    step_key: str
    attempt: int
    status: str
    progress_current: Optional[int] = None
    progress_total: Optional[int] = None
    progress_unit: Optional[str] = None
    safe_output: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    safe_error_detail: Optional[str] = None
    # A step executor's repair hint (`StepResult.suggested_repair`), split out
    # of `safe_output` into its own field on the wire - mirrors
    # `ReadinessCheck`'s message/action split, so the UI never has to fish a
    # magic key out of a free-form output dict.
    safe_suggested_action: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @classmethod
    def from_record(cls, attempt: SetupStepAttempt) -> "SetupStepAttemptView":
        safe_output = dict(attempt.safe_output) if attempt.safe_output else {}
        # See executors/base.py `StepResult.to_safe_output`: it merges
        # `suggested_repair` into `safe_output` so the hint survives the
        # append-only attempt row even on failure. Promote it back out here.
        # `consent_request` (see `StepResult.awaiting`) is deliberately left
        # in place inside `safe_output` rather than promoted - the frontend
        # reads it at `attempt.safe_output.consent_request`.
        suggested_action = safe_output.pop("suggested_repair", None)
        return cls(
            step_key=attempt.step_key,
            attempt=attempt.attempt,
            status=attempt.status.value,
            progress_current=attempt.progress_current,
            progress_total=attempt.progress_total,
            progress_unit=attempt.progress_unit,
            safe_output=safe_output or None,
            error_code=attempt.error_code,
            safe_error_detail=attempt.safe_error_detail,
            safe_suggested_action=suggested_action,
            started_at=attempt.started_at,
            finished_at=attempt.finished_at,
        )


class SetupRunStepView(BaseModel):
    """One step of the run's ordered execution plan (from the recipe), whether
    or not it has been attempted yet - so the UI can render not-yet-started
    steps as "pending" instead of them simply being absent."""

    step_key: str
    title: str
    kind: str
    ordinal: int
    status: str  # "pending", or the latest attempt's status
    attempts: List[SetupStepAttemptView] = Field(default_factory=list)


class SetupRunView(BaseModel):
    """The durable run as the admin UI renders it. Only safe fields; the run's
    ``safe_output`` (never raw service output) and redacted attempts."""

    id: str
    recipe_id: str
    recipe_version: int
    scope: str
    status: str
    current_step: Optional[str] = None
    safe_input: Optional[Dict[str, Any]] = None
    safe_output: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    safe_error_detail: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # The recipe's ordered execution plan, each entry carrying whatever
    # attempts exist for it (possibly none - "pending"). Empty when the run's
    # recipe can no longer be resolved (e.g. it was removed from disk); in
    # that case `attempts` below is still the full, if unordered, history.
    steps: List[SetupRunStepView] = Field(default_factory=list)
    # Flat attempt history. Ordered by recipe execution order (then attempt
    # number) whenever `steps` is populated; falls back to whatever order the
    # repository returned otherwise.
    attempts: List[SetupStepAttemptView] = Field(default_factory=list)

    @classmethod
    def from_record(
        cls,
        run: SetupRun,
        attempts: Optional[List[SetupStepAttempt]] = None,
        recipe_steps: Optional[List[Tuple[str, str, str]]] = None,
    ) -> "SetupRunView":
        """``recipe_steps`` is the run's recipe's ordered ``(step_key, kind,
        title)`` list (see ``Recipe.steps``) when the recipe could be
        resolved. Without it, this degrades to the old flat, repository-order
        ``attempts``-only shape (``steps`` stays empty).
        """
        attempts = attempts or []
        by_step: Dict[str, List[SetupStepAttempt]] = {}
        for a in attempts:
            by_step.setdefault(a.step_key, []).append(a)
        for step_attempts in by_step.values():
            step_attempts.sort(key=lambda a: a.attempt)

        steps: List[SetupRunStepView] = []
        ordered_attempt_views: List[SetupStepAttemptView] = []

        if recipe_steps:
            for ordinal, (step_key, kind, title) in enumerate(recipe_steps):
                step_attempts = by_step.pop(step_key, [])
                views = [SetupStepAttemptView.from_record(a) for a in step_attempts]
                ordered_attempt_views.extend(views)
                status = views[-1].status if views else "pending"
                steps.append(
                    SetupRunStepView(
                        step_key=step_key,
                        title=title,
                        kind=kind,
                        ordinal=ordinal,
                        status=status,
                        attempts=views,
                    )
                )
            # Attempts for a step_key the recipe no longer declares (e.g. it
            # was edited after this run started) still surface, appended
            # after the known steps - history is never silently dropped.
            for step_key, step_attempts in by_step.items():
                ordered_attempt_views.extend(SetupStepAttemptView.from_record(a) for a in step_attempts)
        else:
            ordered_attempt_views = [SetupStepAttemptView.from_record(a) for a in attempts]

        return cls(
            id=run.id,
            recipe_id=run.recipe_id,
            recipe_version=run.recipe_version,
            scope=run.scope,
            status=run.status.value,
            current_step=run.current_step,
            safe_input=run.safe_input,
            safe_output=run.safe_output,
            error_code=run.error_code,
            safe_error_detail=run.safe_error_detail,
            created_at=run.created_at,
            updated_at=run.updated_at,
            completed_at=run.completed_at,
            steps=steps,
            attempts=ordered_attempt_views,
        )
