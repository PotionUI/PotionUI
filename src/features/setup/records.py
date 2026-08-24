"""Setup-run row records and the run state machine.

This module is the single home of the setup-run lifecycle: the status
vocabulary, the legal transitions between statuses, and the plain dataclass
row records the repository reads/writes. Managers and routes import the state
machine from here so there is exactly one definition of "which move is legal".
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.platform.database.rows import dt_column


class SetupRunStatus(str, Enum):
    """Lifecycle of a whole setup run.

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


class SetupStepStatus(str, Enum):
    """Status of a single step attempt (one row in setup_step_attempts)."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ACTION_REQUIRED = "action_required"
    AWAITING_CONSENT = "awaiting_consent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OnboardingStatus(str, Enum):
    """Per-user onboarding progress."""

    PENDING = "pending"
    COMPLETED = "completed"
    DISMISSED = "dismissed"


#: Statuses in which a run is still "active" and holds the instance-wide lock
#: (mapped to active_marker = 1). Everything else is terminal (active_marker
#: NULL) and immutable.
ACTIVE_STATUSES = frozenset(
    {
        SetupRunStatus.PENDING,
        SetupRunStatus.RUNNING,
        SetupRunStatus.AWAITING_CONSENT,
        SetupRunStatus.PAUSED,
    }
)
TERMINAL_STATUSES = frozenset(
    {
        SetupRunStatus.COMPLETED,
        SetupRunStatus.FAILED,
        SetupRunStatus.CANCELLED,
    }
)


#: Legal status transitions. A terminal status maps to the empty set, so any
#: mutation of a completed/failed/cancelled run is rejected. FAILED -> RUNNING
#: is the retry edge: a failed run reopens when a step is retried.
LEGAL_TRANSITIONS: Dict[SetupRunStatus, frozenset] = {
    SetupRunStatus.PENDING: frozenset(
        {
            SetupRunStatus.RUNNING,
            SetupRunStatus.PAUSED,
            SetupRunStatus.AWAITING_CONSENT,
            SetupRunStatus.FAILED,
            SetupRunStatus.CANCELLED,
        }
    ),
    SetupRunStatus.RUNNING: frozenset(
        {
            SetupRunStatus.AWAITING_CONSENT,
            SetupRunStatus.PAUSED,
            SetupRunStatus.COMPLETED,
            SetupRunStatus.FAILED,
            SetupRunStatus.CANCELLED,
        }
    ),
    SetupRunStatus.AWAITING_CONSENT: frozenset(
        {
            SetupRunStatus.RUNNING,
            SetupRunStatus.PAUSED,
            SetupRunStatus.FAILED,
            SetupRunStatus.CANCELLED,
        }
    ),
    SetupRunStatus.PAUSED: frozenset(
        {
            SetupRunStatus.RUNNING,
            SetupRunStatus.FAILED,
            SetupRunStatus.CANCELLED,
        }
    ),
    SetupRunStatus.FAILED: frozenset({SetupRunStatus.RUNNING}),
    SetupRunStatus.COMPLETED: frozenset(),
    SetupRunStatus.CANCELLED: frozenset(),
}


def is_legal_transition(src: SetupRunStatus, dst: SetupRunStatus) -> bool:
    """Whether a run may move from ``src`` to ``dst`` (identity is not a move)."""
    return dst in LEGAL_TRANSITIONS.get(src, frozenset())


@dataclass
class SetupRun:
    """A row of ``setup_runs``. ``safe_input``/``safe_output`` are already-
    redacted plain dicts; the repository (de)serializes them as JSON."""

    id: str
    recipe_id: str
    recipe_version: int
    scope: str
    status: SetupRunStatus
    current_step: Optional[str] = None
    safe_input: Optional[Dict[str, Any]] = None
    safe_output: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None
    safe_error_detail: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @classmethod
    def from_row(cls, row) -> "SetupRun":
        import json

        return cls(
            id=row["id"],
            recipe_id=row["recipe_id"],
            recipe_version=row["recipe_version"],
            scope=row["scope"],
            status=SetupRunStatus(row["status"]),
            current_step=row["current_step"],
            safe_input=json.loads(row["safe_input"]) if row["safe_input"] else None,
            safe_output=json.loads(row["safe_output"]) if row["safe_output"] else None,
            error_code=row["error_code"],
            safe_error_detail=row["safe_error_detail"],
            created_by=row["created_by"],
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
            completed_at=dt_column(row["completed_at"]),
        )


@dataclass
class SetupStepAttempt:
    """A row of ``setup_step_attempts`` (append-only)."""

    id: str
    run_id: str
    step_key: str
    attempt: int
    status: SetupStepStatus
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
    def from_row(cls, row) -> "SetupStepAttempt":
        import json

        return cls(
            id=row["id"],
            run_id=row["run_id"],
            step_key=row["step_key"],
            attempt=row["attempt"],
            status=SetupStepStatus(row["status"]),
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


@dataclass
class UserOnboardingState:
    """A row of ``user_onboarding_state``."""

    user_id: str
    version: int
    status: OnboardingStatus
    dismissed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    first_generation_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_row(cls, row) -> "UserOnboardingState":
        return cls(
            user_id=row["user_id"],
            version=row["version"],
            status=OnboardingStatus(row["status"]),
            dismissed_at=dt_column(row["dismissed_at"]),
            completed_at=dt_column(row["completed_at"]),
            first_generation_id=row["first_generation_id"],
            created_at=dt_column(row["created_at"]),
            updated_at=dt_column(row["updated_at"]),
        )
