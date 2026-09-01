"""Persistence for setup runs, step attempts, and per-user onboarding state.

Raw SQL only - the state machine (which transition is legal) lives in the
manager; this layer just reads and writes rows. The one invariant it does own is
keeping ``active_marker`` consistent with ``status`` (1 while active, NULL once
terminal), because that column is what the UNIQUE index uses to enforce a single
active run instance-wide.
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional

from src.platform.database.rows import now_iso
from src.platform.util.ids import generate_ulid
from src.features.setup.records import (
    ACTIVE_STATUSES,
    OnboardingStatus,
    SetupRun,
    SetupRunStatus,
    SetupStepAttempt,
    SetupStepStatus,
    UserOnboardingState,
)

# Raised so the manager can turn a lost create race into "return the existing
# active run" without importing sqlite3 itself.
IntegrityError = sqlite3.IntegrityError


def _active_marker(status: SetupRunStatus) -> Optional[int]:
    return 1 if status in ACTIVE_STATUSES else None


def _dumps(value: Optional[Dict[str, Any]]) -> Optional[str]:
    return json.dumps(value) if value else None


class SetupRunRepository:
    """Reads/writes ``setup_runs``, ``setup_step_attempts`` and
    ``user_onboarding_state``."""

    # --- runs --------------------------------------------------------------

    def insert_run(
        self,
        recipe_id: str,
        recipe_version: int,
        safe_input: Optional[Dict[str, Any]],
        created_by: Optional[str],
        scope: str = "instance",
    ) -> SetupRun:
        """Insert a fresh PENDING run holding the active slot.

        Raises ``sqlite3.IntegrityError`` if another active run already occupies
        the single-active index - the caller decides what to do with that.
        """
        run_id = generate_ulid()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO setup_runs (
                    id, recipe_id, recipe_version, scope, status,
                    safe_input, active_marker, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    run_id,
                    recipe_id,
                    recipe_version,
                    scope,
                    SetupRunStatus.PENDING.value,
                    _dumps(safe_input),
                    created_by,
                ),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> Optional[SetupRun]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM setup_runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
            return SetupRun.from_row(row) if row else None

    def get_active_run(self) -> Optional[SetupRun]:
        """The one non-terminal run, if any (active_marker = 1)."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute("SELECT * FROM setup_runs WHERE active_marker = 1")
            row = cursor.fetchone()
            return SetupRun.from_row(row) if row else None

    def get_latest_completed_run(self, recipe_id: str) -> Optional[SetupRun]:
        """The most recent COMPLETED run for `recipe_id`, if any - lets the
        recipe catalog mark a recipe "Installed" instead of re-offering
        "Start" on one a run already finished."""
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM setup_runs WHERE recipe_id = ? AND status = ? "
                "ORDER BY completed_at DESC LIMIT 1",
                (recipe_id, SetupRunStatus.COMPLETED.value),
            )
            row = cursor.fetchone()
            return SetupRun.from_row(row) if row else None

    def update_run(
        self,
        run_id: str,
        *,
        status: Optional[SetupRunStatus] = None,
        current_step: Optional[str] = None,
        safe_output: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        safe_error_detail: Optional[str] = None,
        clear_error: bool = False,
    ) -> Optional[SetupRun]:
        """Apply a partial update. When ``status`` is given, ``active_marker``
        and ``completed_at`` are kept consistent with it. Only fields explicitly
        passed are touched (``current_step`` cannot be cleared here - it is only
        ever moved forward)."""
        sets: List[str] = ["updated_at = ?"]
        params: List[Any] = [now_iso()]
        from src.platform.database.database import db

        if status is not None:
            sets.append("status = ?")
            params.append(status.value)
            sets.append("active_marker = ?")
            params.append(_active_marker(status))
            sets.append("completed_at = ?")
            params.append(
                now_iso() if status in {SetupRunStatus.COMPLETED} else None
            )
        if current_step is not None:
            sets.append("current_step = ?")
            params.append(current_step)
        if safe_output is not None:
            sets.append("safe_output = ?")
            params.append(_dumps(safe_output))
        if clear_error:
            sets.append("error_code = ?")
            params.append(None)
            sets.append("safe_error_detail = ?")
            params.append(None)
        else:
            if error_code is not None:
                sets.append("error_code = ?")
                params.append(error_code)
            if safe_error_detail is not None:
                sets.append("safe_error_detail = ?")
                params.append(safe_error_detail)

        params.append(run_id)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"UPDATE setup_runs SET {', '.join(sets)} WHERE id = ?", params
            )
        return self.get_run(run_id)

    # --- step attempts -----------------------------------------------------

    def next_attempt_number(self, run_id: str, step_key: str) -> int:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(attempt), 0) AS n FROM setup_step_attempts "
                "WHERE run_id = ? AND step_key = ?",
                (run_id, step_key),
            )
            return cursor.fetchone()["n"] + 1

    def insert_attempt(
        self,
        run_id: str,
        step_key: str,
        status: SetupStepStatus,
        *,
        attempt: Optional[int] = None,
        progress_current: Optional[int] = None,
        progress_total: Optional[int] = None,
        progress_unit: Optional[str] = None,
        safe_input: Optional[Dict[str, Any]] = None,
        safe_output: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        safe_error_detail: Optional[str] = None,
        finished: bool = False,
    ) -> SetupStepAttempt:
        if attempt is None:
            attempt = self.next_attempt_number(run_id, step_key)
        attempt_id = generate_ulid()
        finished_at = now_iso() if finished else None
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO setup_step_attempts (
                    id, run_id, step_key, attempt, status,
                    progress_current, progress_total, progress_unit,
                    safe_input, safe_output, error_code, safe_error_detail,
                    finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt_id,
                    run_id,
                    step_key,
                    attempt,
                    status.value,
                    progress_current,
                    progress_total,
                    progress_unit,
                    _dumps(safe_input),
                    _dumps(safe_output),
                    error_code,
                    safe_error_detail,
                    finished_at,
                ),
            )
            cursor.execute(
                "SELECT * FROM setup_step_attempts WHERE id = ?", (attempt_id,)
            )
            return SetupStepAttempt.from_row(cursor.fetchone())

    def update_attempt(
        self,
        attempt_id: str,
        *,
        status: Optional[SetupStepStatus] = None,
        progress_current: Optional[int] = None,
        progress_total: Optional[int] = None,
        progress_unit: Optional[str] = None,
        safe_output: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        safe_error_detail: Optional[str] = None,
        finished: bool = False,
    ) -> Optional[SetupStepAttempt]:
        """Update an existing attempt row in place, rather than appending a
        new one - used for interim progress ticks and for the terminal write
        that follows them (see ``SetupRunner.record_step_attempt``'s
        ``attempt_id`` param and ``executors/registry.py``'s progress-report
        seam). Only the fields explicitly passed are touched; ``finished``
        stamps ``finished_at`` the same way ``insert_attempt`` does. This is
        the one place ``setup_step_attempts`` is mutated after insert - the
        row's ``attempt`` number never changes, so history (one row per
        logical attempt) is preserved even though its content is written
        incrementally.
        """
        sets: List[str] = []
        params: List[Any] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status.value)
        if progress_current is not None:
            sets.append("progress_current = ?")
            params.append(progress_current)
        if progress_total is not None:
            sets.append("progress_total = ?")
            params.append(progress_total)
        if progress_unit is not None:
            sets.append("progress_unit = ?")
            params.append(progress_unit)
        if safe_output is not None:
            sets.append("safe_output = ?")
            params.append(_dumps(safe_output))
        if error_code is not None:
            sets.append("error_code = ?")
            params.append(error_code)
        if safe_error_detail is not None:
            sets.append("safe_error_detail = ?")
            params.append(safe_error_detail)
        if finished:
            sets.append("finished_at = ?")
            params.append(now_iso())

        from src.platform.database.database import db
        if not sets:
            with db.get_cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM setup_step_attempts WHERE id = ?", (attempt_id,)
                )
                row = cursor.fetchone()
                return SetupStepAttempt.from_row(row) if row else None

        params.append(attempt_id)
        with db.get_cursor() as cursor:
            cursor.execute(
                f"UPDATE setup_step_attempts SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            cursor.execute(
                "SELECT * FROM setup_step_attempts WHERE id = ?", (attempt_id,)
            )
            row = cursor.fetchone()
            return SetupStepAttempt.from_row(row) if row else None

    def list_attempts(self, run_id: str) -> List[SetupStepAttempt]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM setup_step_attempts WHERE run_id = ? "
                "ORDER BY step_key ASC, attempt ASC",
                (run_id,),
            )
            return [SetupStepAttempt.from_row(r) for r in cursor.fetchall()]

    # --- per-user onboarding ----------------------------------------------

    def get_onboarding_state(self, user_id: str) -> Optional[UserOnboardingState]:
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            cursor.execute(
                "SELECT * FROM user_onboarding_state WHERE user_id = ?", (user_id,)
            )
            row = cursor.fetchone()
            return UserOnboardingState.from_row(row) if row else None

    def upsert_onboarding_state(
        self,
        user_id: str,
        *,
        status: Optional[OnboardingStatus] = None,
        first_generation_id: Optional[str] = None,
        dismissed: bool = False,
    ) -> UserOnboardingState:
        existing = self.get_onboarding_state(user_id)
        now = now_iso()
        from src.platform.database.database import db
        with db.get_cursor() as cursor:
            if existing is None:
                cursor.execute(
                    """
                    INSERT INTO user_onboarding_state (
                        user_id, status, first_generation_id,
                        dismissed_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        (status or OnboardingStatus.PENDING).value,
                        first_generation_id,
                        now if dismissed else None,
                        now if status == OnboardingStatus.COMPLETED else None,
                    ),
                )
            else:
                sets = ["updated_at = ?"]
                params: List[Any] = [now]
                if status is not None:
                    sets.append("status = ?")
                    params.append(status.value)
                    if status == OnboardingStatus.COMPLETED:
                        sets.append("completed_at = ?")
                        params.append(now)
                if first_generation_id is not None:
                    sets.append("first_generation_id = ?")
                    params.append(first_generation_id)
                if dismissed:
                    sets.append("dismissed_at = ?")
                    params.append(now)
                params.append(user_id)
                cursor.execute(
                    f"UPDATE user_onboarding_state SET {', '.join(sets)} "
                    "WHERE user_id = ?",
                    params,
                )
        return self.get_onboarding_state(user_id)
