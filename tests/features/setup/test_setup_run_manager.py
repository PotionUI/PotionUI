"""Durable setup-run lifecycle against the real file-backed SQLite wrapper.

Mirrors `test_instance_claim`'s `file_db` pattern: point the global DB singleton
at a temp file, migrate it, and exercise the production connection path. These
tests cover the state machine, single-active-run idempotency, append-only
attempts, resume-after-restart, and the migration itself.
"""

import importlib.util
import threading
import time
from pathlib import Path

import pytest

from src.platform.database.database import db as global_db
from src.platform.database.migration_runner import MigrationManager
from src.platform.security.user import AccountType
from src.features.users.repository import UserRepository
from src.features.setup.records import (
    OnboardingStatus,
    SetupRunStatus,
    SetupStepStatus,
)
from src.features.setup.run_manager import (
    IllegalSetupTransition,
    SetupRunManager,
    SetupRunNotFound,
    SetupExecutorNotConfigured,
)
from src.features.setup.run_repository import SetupRunRepository


@pytest.fixture
def file_db(tmp_path):
    """Redirect the shared DB singleton at a fresh migrated temp file."""
    original_path = global_db.db_path
    global_db.db_path = tmp_path / "setup_runs.db"
    try:
        MigrationManager().run_migrations()
        yield global_db
    finally:
        global_db.db_path = original_path


def _manager() -> SetupRunManager:
    return SetupRunManager()


# --- migration -------------------------------------------------------------


def test_migration_creates_tables_and_single_active_index(file_db):
    with global_db.get_cursor() as cursor:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('setup_runs','setup_step_attempts','user_onboarding_state')"
        )
        tables = {r[0] for r in cursor.fetchall()}
        assert tables == {
            "setup_runs",
            "setup_step_attempts",
            "user_onboarding_state",
        }
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='uq_setup_runs_single_active'"
        )
        assert cursor.fetchone() is not None


# --- create idempotency ----------------------------------------------------


def test_create_run_starts_pending(file_db):
    run = _manager().create_run("native-image-starter", created_by=None)
    assert run.status == SetupRunStatus.PENDING
    assert run.recipe_id == "native-image-starter"
    assert run.recipe_version == 1


def test_second_create_returns_existing_active_run(file_db):
    mgr = _manager()
    first = mgr.create_run("native-image-starter")
    second = mgr.create_run("some-other-recipe")
    # One active run at a time: the second create is a no-op returning the first.
    assert second.id == first.id
    assert second.recipe_id == "native-image-starter"


def test_create_after_terminal_makes_new_run(file_db):
    mgr = _manager()
    first = mgr.create_run("native-image-starter")
    mgr.transition(first.id, SetupRunStatus.RUNNING)
    mgr.transition(first.id, SetupRunStatus.COMPLETED)
    # The prior run is terminal, so a fresh create is allowed.
    second = mgr.create_run("native-image-starter")
    assert second.id != first.id
    assert second.status == SetupRunStatus.PENDING


def test_single_active_index_blocks_a_second_active_row(file_db):
    """The DB unique index is the cross-process guarantee behind idempotency."""
    repo = SetupRunRepository()
    repo.insert_run("r", 1, None, None)
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        repo.insert_run("r2", 1, None, None)


# --- transitions -----------------------------------------------------------


def test_legal_transition_chain(file_db):
    mgr = _manager()
    run = mgr.create_run("r")
    run = mgr.transition(run.id, SetupRunStatus.RUNNING)
    assert run.status == SetupRunStatus.RUNNING
    run = mgr.transition(run.id, SetupRunStatus.AWAITING_CONSENT)
    assert run.status == SetupRunStatus.AWAITING_CONSENT
    run = mgr.transition(run.id, SetupRunStatus.RUNNING)
    run = mgr.transition(run.id, SetupRunStatus.COMPLETED)
    assert run.status == SetupRunStatus.COMPLETED
    assert run.completed_at is not None


def test_illegal_transition_rejected(file_db):
    mgr = _manager()
    run = mgr.create_run("r")
    # pending -> completed is not a legal edge.
    with pytest.raises(IllegalSetupTransition):
        mgr.transition(run.id, SetupRunStatus.COMPLETED)


def test_completed_run_is_immutable(file_db):
    mgr = _manager()
    run = mgr.create_run("r")
    mgr.transition(run.id, SetupRunStatus.RUNNING)
    mgr.transition(run.id, SetupRunStatus.COMPLETED)
    with pytest.raises(IllegalSetupTransition):
        mgr.transition(run.id, SetupRunStatus.RUNNING)
    with pytest.raises(IllegalSetupTransition):
        mgr.transition(run.id, SetupRunStatus.PAUSED)


def test_transition_on_missing_run_raises(file_db):
    with pytest.raises(SetupRunNotFound):
        _manager().transition("nope", SetupRunStatus.RUNNING)


# --- attempts accumulate ---------------------------------------------------


def test_attempts_accumulate_per_step(file_db):
    mgr = _manager()
    run = mgr.create_run("r")
    mgr.transition(run.id, SetupRunStatus.RUNNING)

    a1 = mgr.record_step_attempt(run.id, "artifacts.fetch", SetupStepStatus.FAILED)
    a2 = mgr.record_step_attempt(run.id, "artifacts.fetch", SetupStepStatus.RUNNING)
    a3 = mgr.record_step_attempt(run.id, "models.index", SetupStepStatus.RUNNING)

    assert a1.attempt == 1
    assert a2.attempt == 2  # same step -> incremented, prior row preserved
    assert a3.attempt == 1  # different step -> its own sequence

    attempts = mgr.list_attempts(run.id)
    assert len(attempts) == 3


def test_record_step_attempt_with_attempt_id_updates_in_place(file_db):
    """`attempt_id` (the T3.7 progress-report seam) updates the SAME row
    instead of appending a new one - the attempt number and row count don't
    change across a progress tick followed by a terminal write."""
    mgr = _manager()
    run = mgr.create_run("r")
    mgr.transition(run.id, SetupRunStatus.RUNNING)

    first = mgr.record_step_attempt(
        run.id, "artifacts.fetch", SetupStepStatus.RUNNING,
        progress_current=100, progress_total=1000, progress_unit="bytes",
    )
    second = mgr.record_step_attempt(
        run.id, "artifacts.fetch", SetupStepStatus.RUNNING,
        attempt_id=first.id, progress_current=500, progress_total=1000, progress_unit="bytes",
    )
    final = mgr.record_step_attempt(
        run.id, "artifacts.fetch", SetupStepStatus.SUCCEEDED,
        attempt_id=first.id, safe_output={"ok": True}, finished=True,
    )

    assert second.id == first.id
    assert final.id == first.id
    assert final.attempt == 1
    assert final.status == SetupStepStatus.SUCCEEDED
    # progress fields from the last progress tick survive onto the
    # terminal write, which didn't pass progress_current/total itself.
    assert final.progress_current == 500
    assert final.progress_total == 1000
    assert final.finished_at is not None

    attempts = mgr.list_attempts(run.id)
    assert len(attempts) == 1


def test_attempt_rejected_on_terminal_run(file_db):
    mgr = _manager()
    run = mgr.create_run("r")
    mgr.transition(run.id, SetupRunStatus.RUNNING)
    mgr.transition(run.id, SetupRunStatus.CANCELLED)
    with pytest.raises(IllegalSetupTransition):
        mgr.record_step_attempt(run.id, "any", SetupStepStatus.RUNNING)


# --- actions ---------------------------------------------------------------


def test_pause_resume_cancel(file_db):
    mgr = _manager()
    run = mgr.create_run("r")
    mgr.transition(run.id, SetupRunStatus.RUNNING)

    run = mgr.apply_action(run.id, "pause")
    assert run.status == SetupRunStatus.PAUSED
    run = mgr.apply_action(run.id, "resume")
    assert run.status == SetupRunStatus.RUNNING
    run = mgr.apply_action(run.id, "cancel")
    assert run.status == SetupRunStatus.CANCELLED


def test_retry_step_reopens_failed_run_with_new_attempt(file_db):
    mgr = _manager()
    run = mgr.create_run("r")
    mgr.transition(run.id, SetupRunStatus.RUNNING, current_step="artifacts.fetch")
    mgr.record_step_attempt(run.id, "artifacts.fetch", SetupStepStatus.FAILED)
    mgr.transition(
        run.id, SetupRunStatus.FAILED, error_code="DOWNLOAD_FAILED"
    )

    run = mgr.apply_action(run.id, "retry_step")
    assert run.status == SetupRunStatus.RUNNING
    assert run.error_code is None  # cleared on retry

    attempts = [a for a in mgr.list_attempts(run.id) if a.step_key == "artifacts.fetch"]
    assert len(attempts) == 2  # a fresh attempt was appended for the failed step


def test_illegal_action_raises(file_db):
    mgr = _manager()
    run = mgr.create_run("r")  # pending
    # retry_step is only meaningful on a FAILED run, never on a pending one.
    with pytest.raises(IllegalSetupTransition):
        mgr.apply_action(run.id, "retry_step")


# --- resume after "restart" ------------------------------------------------


def test_resume_after_fresh_manager_instance(file_db):
    """A restarted process holds no in-memory cursor; a new manager on the same
    DB reconstructs the exact persisted position."""
    first_mgr = SetupRunManager()
    run = first_mgr.create_run("r")
    first_mgr.transition(run.id, SetupRunStatus.RUNNING, current_step="models.index")
    first_mgr.record_step_attempt(run.id, "models.index", SetupStepStatus.RUNNING)

    # Simulate a restart: brand-new manager + repository, same database file.
    restarted = SetupRunManager()
    resumed = restarted.resume_position(run.id)
    assert resumed is not None
    assert resumed.status == SetupRunStatus.RUNNING
    assert resumed.current_step == "models.index"
    assert restarted.repo.get_active_run().id == run.id
    assert len(restarted.list_attempts(run.id)) == 1


# --- redaction through the manager ----------------------------------------


def test_manager_redacts_safe_input_on_create(file_db):
    mgr = _manager()
    run = mgr.create_run(
        "r",
        safe_input={
            "recipe": "native-image-starter",
            "api_key": "sk-should-not-persist",
            "hf_token": "hf_secret",
            "device": "cuda",
        },
    )
    stored = mgr.get_run(run.id)
    assert stored.safe_input == {"recipe": "native-image-starter", "device": "cuda"}
    assert "api_key" not in stored.safe_input
    assert "hf_token" not in stored.safe_input


# --- executor seam ---------------------------------------------------------


def test_execute_current_step_raises_until_phase3(file_db):
    mgr = _manager()
    run = mgr.create_run("r")
    with pytest.raises(SetupExecutorNotConfigured):
        mgr.execute_current_step(run.id)


# --- drive_async (drive() moved off the request thread) ------------
#
# These register a bare fake "executor registry" - anything exposing
# `.execute(run_manager, run) -> SetupRun` (see `execute_current_step`) -
# rather than a real `SetupExecutorRegistry` + recipe/executors, since only
# `drive`/`drive_async`'s own looping and threading semantics are under test
# here, not step dispatch (that's `test_registry.py`'s job).


def _wait_for_run(mgr, run_id, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    run = None
    while time.monotonic() < deadline:
        run = mgr.get_run(run_id)
        if predicate(run):
            return run
        time.sleep(0.01)
    raise AssertionError(f"Timed out waiting on run '{run_id}'; last status: {run and run.status}")


def _wait_for_driving_guard_clear(mgr, run_id, timeout=5.0):
    """The background drive thread's `finally` (discarding `run_id` from
    `_driving`) runs strictly after its `except` block's FAILED transition
    commits, so a run observed as FAILED can still momentarily have its id in
    `_driving` - poll instead of sampling the set once right after the status
    wait."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if run_id not in mgr._driving:
            return
        time.sleep(0.01)
    raise AssertionError(f"Timed out waiting for the driving guard to clear for run '{run_id}'")


class _OneShotRegistry:
    """Completes the run on its very first `execute()` call."""

    def __init__(self):
        self.calls = 0

    def execute(self, run_manager, run):
        self.calls += 1
        if run.status == SetupRunStatus.PENDING:
            run = run_manager.transition(run.id, SetupRunStatus.RUNNING)
        return run_manager.transition(run.id, SetupRunStatus.COMPLETED)


class _CrashingRegistry:
    """Raises straight out of `execute()` - simulates a bug in the
    registry/manager plumbing itself, as opposed to a step executor's own
    exception (which `SetupExecutorRegistry.execute` already catches and
    turns into a FAILED transition - see `registry.py`, exercised by
    `test_registry.py`)."""

    def execute(self, run_manager, run):
        raise RuntimeError("boom")


def test_drive_async_runs_the_step_and_reaches_completed(file_db):
    mgr = _manager()
    run = mgr.create_run("r")
    registry = _OneShotRegistry()
    mgr.register_executor_registry(registry)

    mgr.drive_async(run.id)
    completed = _wait_for_run(mgr, run.id, lambda r: r.status == SetupRunStatus.COMPLETED)

    assert completed.status == SetupRunStatus.COMPLETED
    assert registry.calls == 1


def test_drive_async_returns_before_the_step_finishes(file_db):
    """The core fix: `drive_async` must not block the caller for as
    long as the step takes - a route calling it can respond immediately, and
    `GET /runs/{id}` (the frontend's poll) sees the run still in flight."""
    mgr = _manager()
    run = mgr.create_run("r")
    release = threading.Event()

    class _SlowRegistry:
        def execute(self, run_manager, run):
            if run.status == SetupRunStatus.PENDING:
                run = run_manager.transition(run.id, SetupRunStatus.RUNNING)
            assert release.wait(timeout=5), "test bug: release was never set"
            return run_manager.transition(run.id, SetupRunStatus.COMPLETED)

    mgr.register_executor_registry(_SlowRegistry())

    started_at = time.monotonic()
    mgr.drive_async(run.id)
    elapsed = time.monotonic() - started_at
    assert elapsed < 1.0, "drive_async blocked waiting for the step instead of backgrounding it"

    # A poll taken right now must see the run still going, not finished -
    # this is what a concurrent GET /runs/{id} would render mid-flight.
    in_flight = mgr.get_run(run.id)
    assert in_flight.status in (SetupRunStatus.PENDING, SetupRunStatus.RUNNING)

    release.set()
    completed = _wait_for_run(mgr, run.id, lambda r: r.status == SetupRunStatus.COMPLETED)
    assert completed.status == SetupRunStatus.COMPLETED


def test_drive_async_is_a_noop_while_a_drive_is_already_in_flight(file_db):
    """Double-POST protection: a second `drive_async` call for the SAME
    run_id while the first is still running must not spawn a second thread
    racing it through the same steps - it's a no-op, the in-flight drive
    already covers the work."""
    mgr = _manager()
    run = mgr.create_run("r")
    entered = threading.Event()
    release = threading.Event()

    class _BlockingRegistry:
        def __init__(self):
            self.calls = 0

        def execute(self, run_manager, run):
            self.calls += 1
            entered.set()
            assert release.wait(timeout=5), "test bug: release was never set"
            if run.status == SetupRunStatus.PENDING:
                run = run_manager.transition(run.id, SetupRunStatus.RUNNING)
            return run_manager.transition(run.id, SetupRunStatus.COMPLETED)

    registry = _BlockingRegistry()
    mgr.register_executor_registry(registry)

    mgr.drive_async(run.id)
    assert entered.wait(timeout=5), "first drive never started"
    mgr.drive_async(run.id)  # concurrent/duplicate call - must be swallowed

    release.set()
    completed = _wait_for_run(mgr, run.id, lambda r: r.status == SetupRunStatus.COMPLETED)

    assert completed.status == SetupRunStatus.COMPLETED
    assert registry.calls == 1  # only the first call ever ran the step


def test_drive_async_without_executor_registry_is_a_noop(file_db):
    mgr = _manager()
    run = mgr.create_run("r")

    mgr.drive_async(run.id)  # no registry registered - nothing to run
    time.sleep(0.05)

    assert mgr.get_run(run.id).status == SetupRunStatus.PENDING


def test_crashed_drive_marks_the_run_failed_not_stuck_running(file_db):
    """A drive that raises outside a step executor's own error handling must
    still resolve the run to a terminal state - never leave it RUNNING
    forever with nothing left to ever poll it forward."""
    mgr = _manager()
    run = mgr.create_run("r")
    mgr.register_executor_registry(_CrashingRegistry())

    mgr.drive_async(run.id)
    failed = _wait_for_run(mgr, run.id, lambda r: r.status == SetupRunStatus.FAILED)

    assert failed.status == SetupRunStatus.FAILED
    assert failed.error_code == "SETUP_RUN_CRASHED"


def test_drive_async_recovers_the_driving_guard_after_a_crash(file_db):
    """The in-flight guard (see the no-op test above) must clear even when
    the drive crashes, or a run that failed this way could never be retried
    (retry_step -> drive_async would silently no-op forever)."""
    mgr = _manager()
    run = mgr.create_run("r")
    mgr.register_executor_registry(_CrashingRegistry())

    mgr.drive_async(run.id)
    _wait_for_run(mgr, run.id, lambda r: r.status == SetupRunStatus.FAILED)
    _wait_for_driving_guard_clear(mgr, run.id)

    assert run.id not in mgr._driving


# --- onboarding state ------------------------------------------------------


def test_onboarding_state_upsert(file_db):
    users = UserRepository()
    user = users.create(
        username="owner", email="owner@example.com",
        password_hash="$2b$12$fakehashfakehashfakehashfake",
        account_type=AccountType.ADMIN,
    )
    repo = SetupRunRepository()

    assert repo.get_onboarding_state(user.id) is None
    state = repo.upsert_onboarding_state(user.id, status=OnboardingStatus.PENDING)
    assert state.status == OnboardingStatus.PENDING

    updated = repo.upsert_onboarding_state(
        user.id, status=OnboardingStatus.COMPLETED, first_generation_id="gen-1"
    )
    assert updated.status == OnboardingStatus.COMPLETED
    assert updated.first_generation_id == "gen-1"
    assert updated.completed_at is not None
