"""Recipe-run row records and the run state machine.

This module is the single home of the recipe-run lifecycle: the status
vocabulary, the legal transitions between statuses, and the plain dataclass
row records the repository reads/writes. Managers and routes import the state
machine from here so there is exactly one definition of "which move is legal".
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.platform.database.rows import dt_column

#: Who started a run, and therefore which steps it executes. An onboarding run
#: (the first-run wizard) runs the whole recipe; an admin run skips every step
#: marked `onboarding_only`.
MODE_ONBOARDING = "onboarding"
MODE_ADMIN = "admin"
VALID_MODES = (MODE_ONBOARDING, MODE_ADMIN)

#: `mode` rides inside the run's already-existing `safe_input` JSON column
#: rather than getting a column of its own, so recipes shipped without a
#: migration. `RecipeRunRepository` writes it on insert and lifts it back out
#: on read - it is never part of the recipe input a caller supplied.
MODE_KEY = "__mode"


class RecipeRunStatus(str, Enum):
    """Lifecycle of a whole recipe run.

    Non-terminal (a run in one of these "occupies" the single-active slot):
    ``PENDING``, ``RUNNING``, ``AWAITING_CONSENT``, ``PAUSED``. Terminal
    (immutable): ``COMPLETED``, ``FAILED``, ``CANCELLED``.
    """

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_CONSENT = "awaiting_consent"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecipeStepStatus(str, Enum):
    """Status of a single step attempt (one row in setup_step_attempts)."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ACTION_REQUIRED = "action_required"
    AWAITING_CONSENT = "awaiting_consent"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: Statuses in which a run is still "active" and holds the instance-wide lock
#: (mapped to active_marker = 1). Everything else is terminal (active_marker
#: NULL) and immutable.
ACTIVE_STATUSES = frozenset(
    {
        RecipeRunStatus.PENDING,
        RecipeRunStatus.RUNNING,
        RecipeRunStatus.AWAITING_CONSENT,
        RecipeRunStatus.PAUSED,
    }
)
TERMINAL_STATUSES = frozenset(
    {
        RecipeRunStatus.COMPLETED,
        RecipeRunStatus.FAILED,
        RecipeRunStatus.CANCELLED,
    }
)


#: Legal status transitions. A terminal status maps to the empty set, so any
#: mutation of a completed/failed/cancelled run is rejected. FAILED -> RUNNING
#: is the retry edge: a failed run reopens when a step is retried.
LEGAL_TRANSITIONS: Dict[RecipeRunStatus, frozenset] = {
    RecipeRunStatus.PENDING: frozenset(
        {
            RecipeRunStatus.RUNNING,
            RecipeRunStatus.PAUSED,
            RecipeRunStatus.AWAITING_CONSENT,
            RecipeRunStatus.FAILED,
            RecipeRunStatus.CANCELLED,
        }
    ),
    RecipeRunStatus.RUNNING: frozenset(
        {
            RecipeRunStatus.AWAITING_CONSENT,
            RecipeRunStatus.PAUSED,
            RecipeRunStatus.COMPLETED,
            RecipeRunStatus.FAILED,
            RecipeRunStatus.CANCELLED,
        }
    ),
    RecipeRunStatus.AWAITING_CONSENT: frozenset(
        {
            RecipeRunStatus.RUNNING,
            RecipeRunStatus.PAUSED,
            RecipeRunStatus.FAILED,
            RecipeRunStatus.CANCELLED,
        }
    ),
    RecipeRunStatus.PAUSED: frozenset(
        {
            RecipeRunStatus.RUNNING,
            RecipeRunStatus.FAILED,
            RecipeRunStatus.CANCELLED,
        }
    ),
    RecipeRunStatus.FAILED: frozenset({RecipeRunStatus.RUNNING}),
    RecipeRunStatus.COMPLETED: frozenset(),
    RecipeRunStatus.CANCELLED: frozenset(),
}


def is_legal_transition(src: RecipeRunStatus, dst: RecipeRunStatus) -> bool:
    """Whether a run may move from ``src`` to ``dst`` (identity is not a move)."""
    return dst in LEGAL_TRANSITIONS.get(src, frozenset())


@dataclass
class RecipeRun:
    """A row of ``setup_runs``. ``safe_input``/``safe_output`` are already-
    redacted plain dicts; the repository (de)serializes them as JSON."""

    id: str
    recipe_id: str
    recipe_version: int
    scope: str
    status: RecipeRunStatus
    current_step: Optional[str] = None
    safe_input: Optional[Dict[str, Any]] = None
    safe_output: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    safe_error_detail: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    mode: str = MODE_ONBOARDING

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @classmethod
    def from_row(cls, row) -> "RecipeRun":
        import json

        safe_input = json.loads(row["safe_input"]) if row["safe_input"] else None
        mode = MODE_ONBOARDING
        if isinstance(safe_input, dict):
            mode = safe_input.pop(MODE_KEY, MODE_ONBOARDING)
            if mode not in VALID_MODES:
                mode = MODE_ONBOARDING

        return cls(
            id=row["id"],
            recipe_id=row["recipe_id"],
            recipe_version=row["recipe_version"],
            scope=row["scope"],
            status=RecipeRunStatus(row["status"]),
            current_step=row["current_step"],
            safe_input=safe_input,
            safe_output=json.loads(row["safe_output"]) if row["safe_output"] else None,
            mode=mode,
            error_code=row["error_code"],
            safe_error_detail=row["safe_error_detail"],
            created_by=row["created_by"],
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
            completed_at=dt_column(row["completed_at"]),
        )


@dataclass
class RecipeStepAttempt:
    """A row of ``setup_step_attempts`` (append-only)."""

    id: str
    run_id: str
    step_key: str
    attempt: int
    status: RecipeStepStatus
    progress_current: Optional[int] = None
    progress_total: Optional[int] = None
    progress_unit: Optional[str] = None
    safe_input: Optional[Dict[str, Any]] = None
    safe_output: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    safe_error_detail: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "RecipeStepAttempt":
        import json

        return cls(
            id=row["id"],
            run_id=row["run_id"],
            step_key=row["step_key"],
            attempt=row["attempt"],
            status=RecipeStepStatus(row["status"]),
            progress_current=row["progress_current"],
            progress_total=row["progress_total"],
            progress_unit=row["progress_unit"],
            safe_input=json.loads(row["safe_input"]) if row["safe_input"] else None,
            safe_output=json.loads(row["safe_output"]) if row["safe_output"] else None,
            error_code=row["error_code"],
            safe_error_detail=row["safe_error_detail"],
            started_at=dt_column(row["started_at"]),
            finished_at=dt_column(row["finished_at"]),
        )
