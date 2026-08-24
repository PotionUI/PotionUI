"""`SetupExecutorRegistry` - drives one run forward by exactly one step per
`execute()` call, against a real `SetupRunManager`/DB (mirrors
`test_setup_run_manager.py`'s `file_db` pattern) and a small hand-built
recipe + fake executors, so this is isolated from real plugins/presets/models.
"""

import pytest

from src.features.setup.executors.base import StepContext, StepResult
from src.features.setup.executors.registry import SetupExecutorRegistry
from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRunStatus, SetupStepStatus
from src.features.setup.run_manager import SetupRunError, SetupRunManager
from src.platform.database.database import db as global_db
from src.platform.database.migration_runner import MigrationManager


@pytest.fixture
def file_db(tmp_path):
    original_path = global_db.db_path
    global_db.db_path = tmp_path / "setup_executor_registry.db"
    try:
        MigrationManager().run_migrations()
        yield global_db
    finally:
        global_db.db_path = original_path


class AlwaysSucceedsExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, context: StepContext) -> StepResult:
        self.calls += 1
        return StepResult.ok({"call": self.calls})


class AlwaysFailsExecutor:
    def execute(self, context: StepContext) -> StepResult:
        return StepResult.fail("BOOM", "This step always fails.", suggested_repair="Try something else.")


class RaisesExecutor:
    def execute(self, context: StepContext) -> StepResult:
        raise RuntimeError("kaboom")


class AwaitsConsentThenSucceedsExecutor:
    """Parks on `StepResult.awaiting` the first call, succeeds the next -
    mirrors `artifacts.plan` succeeding after `grant_consent` records a fresh
    attempt for the same step and the run is driven forward again."""

    def __init__(self):
        self.calls = 0

    def execute(self, context: StepContext) -> StepResult:
        self.calls += 1
        if self.calls == 1:
            return StepResult.awaiting({"artifacts": [{"id": "a", "display_name": "A", "size_bytes": 1, "kind": "checkpoint"}], "total_bytes": 1})
        return StepResult.ok({"call": self.calls})


class FailsOnceThenSucceedsExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, context: StepContext) -> StepResult:
        self.calls += 1
        if self.calls == 1:
            return StepResult.fail("TRANSIENT", "First try didn't work.")
        return StepResult.ok({"call": self.calls})


class ReportsProgressExecutor:
    """Mirrors `artifacts.fetch`: reports interim progress ticks through
    `context.report_progress` before returning its final result - see
    `StepContext.report_progress` (`executors/base.py`) and the seam that
    wires it in `SetupExecutorRegistry.execute`."""

    def __init__(self, succeed: bool = True):
        self.succeed = succeed
        self.ticks = []

    def execute(self, context: StepContext) -> StepResult:
        for current in (1_000, 5_000, 9_999):
            context.report_progress(progress_current=current, progress_total=10_000, progress_unit="bytes")
            self.ticks.append(current)
        if self.succeed:
            return StepResult.ok({"done": True})
        return StepResult.fail("DOWNLOAD_FAILED", "It broke partway through.")


class FakeCatalog:
    def __init__(self, recipe):
        self.recipe = recipe

    def get_recipe(self, recipe_id, version=None):
        if self.recipe is None:
            return None
        if self.recipe.id != recipe_id:
            return None
        if version is not None and self.recipe.version != version:
            return None
        return self.recipe


def _two_step_recipe():
    return Recipe(
        id="fake-recipe",
        schema_version=1,
        version=1,
        name="Fake Recipe",
        engine="native",
        steps=[
            RecipeStep(key="step-one", kind="kind.one", title="Step One"),
            RecipeStep(key="step-two", kind="kind.two", title="Step Two"),
        ],
    )


def test_advances_through_multiple_steps_to_completion(file_db):
    recipe = _two_step_recipe()
    step_one = AlwaysSucceedsExecutor()
    step_two = AlwaysSucceedsExecutor()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": step_one, "kind.two": step_two})

    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe", created_by="owner-1")
    assert run.status == SetupRunStatus.PENDING
    assert run.current_step is None

    run = manager.execute_current_step(run.id)
    assert run.status == SetupRunStatus.RUNNING
    assert run.current_step == "step-two"
    assert step_one.calls == 1
    assert step_two.calls == 0

    run = manager.execute_current_step(run.id)
    assert run.status == SetupRunStatus.COMPLETED
    assert run.current_step == "step-two"
    assert step_two.calls == 1

    attempts = manager.list_attempts(run.id)
    by_step = {a.step_key: a for a in attempts}
    assert by_step["step-one"].status == SetupStepStatus.SUCCEEDED
    assert by_step["step-two"].status == SetupStepStatus.SUCCEEDED


def test_failing_step_moves_run_to_failed_with_error_detail(file_db):
    recipe = _two_step_recipe()
    registry = SetupExecutorRegistry(
        FakeCatalog(recipe), {"kind.one": AlwaysFailsExecutor(), "kind.two": AlwaysSucceedsExecutor()}
    )
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")

    run = manager.execute_current_step(run.id)

    assert run.status == SetupRunStatus.FAILED
    assert run.error_code == "BOOM"
    assert run.safe_error_detail == "This step always fails."
    assert run.current_step == "step-one"

    attempt = manager.list_attempts(run.id)[0]
    assert attempt.status == SetupStepStatus.FAILED
    assert attempt.safe_output.get("suggested_repair") == "Try something else."


def test_progress_reports_land_on_a_single_attempt_row_then_terminate(file_db):
    """Interim `report_progress` ticks update the SAME row in place (no
    extra rows per tick), and the terminal write reuses that row too - one
    execute() call still yields exactly one `setup_step_attempts` row for a
    non-retried attempt, matching the pre-existing single-row assumption
    the other tests in this file rely on."""
    recipe = _two_step_recipe()
    step_one = ReportsProgressExecutor(succeed=True)
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": step_one, "kind.two": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")

    run = manager.execute_current_step(run.id)

    assert step_one.ticks == [1_000, 5_000, 9_999]
    attempts = [a for a in manager.list_attempts(run.id) if a.step_key == "step-one"]
    assert len(attempts) == 1  # one logical attempt, not one row per tick
    attempt = attempts[0]
    assert attempt.status == SetupStepStatus.SUCCEEDED
    assert attempt.attempt == 1
    # The terminal write reused the row the ticks created, so the last
    # reported progress values survive onto the finished row.
    assert attempt.progress_current == 9_999
    assert attempt.progress_total == 10_000
    assert attempt.progress_unit == "bytes"
    assert attempt.finished_at is not None


def test_progress_reports_survive_a_failed_step(file_db):
    recipe = _two_step_recipe()
    step_one = ReportsProgressExecutor(succeed=False)
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": step_one, "kind.two": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")

    run = manager.execute_current_step(run.id)

    assert run.status == SetupRunStatus.FAILED
    attempts = [a for a in manager.list_attempts(run.id) if a.step_key == "step-one"]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.status == SetupStepStatus.FAILED
    assert attempt.error_code == "DOWNLOAD_FAILED"
    assert attempt.progress_current == 9_999  # last tick before the failure is preserved


def test_executor_that_never_reports_progress_is_unaffected(file_db):
    """An executor that never calls `report_progress` (the common case)
    still gets exactly the old behavior: a bare `StepContext` with the
    default no-op callback, and a single plain-inserted terminal row."""
    recipe = _two_step_recipe()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": AlwaysSucceedsExecutor(), "kind.two": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")

    manager.execute_current_step(run.id)

    attempt = [a for a in manager.list_attempts(run.id) if a.step_key == "step-one"][0]
    assert attempt.progress_current is None
    assert attempt.progress_total is None


def test_unregistered_step_kind_fails_with_not_implemented(file_db):
    recipe = _two_step_recipe()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")

    manager.execute_current_step(run.id)  # step-one succeeds, advances to step-two
    run = manager.execute_current_step(run.id)  # step-two has no registered executor

    assert run.status == SetupRunStatus.FAILED
    assert run.error_code == "STEP_NOT_IMPLEMENTED"


def test_executor_exception_is_contained_not_propagated(file_db):
    recipe = _two_step_recipe()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": RaisesExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")

    run = manager.execute_current_step(run.id)

    assert run.status == SetupRunStatus.FAILED
    assert run.error_code == "STEP_EXECUTOR_ERROR"
    assert "kaboom" in run.safe_error_detail


def test_recipe_not_found_fails_the_run(file_db):
    registry = SetupExecutorRegistry(FakeCatalog(None), {})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("missing-recipe")

    run = manager.execute_current_step(run.id)

    assert run.status == SetupRunStatus.FAILED
    assert run.error_code == "RECIPE_NOT_FOUND"


def test_cannot_execute_a_paused_run(file_db):
    recipe = _two_step_recipe()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")
    manager.apply_action(run.id, "pause")

    with pytest.raises(SetupRunError):
        manager.execute_current_step(run.id)


def test_retry_step_reruns_the_same_failed_step(file_db):
    recipe = _two_step_recipe()
    step_one = FailsOnceThenSucceedsExecutor()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": step_one, "kind.two": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")

    run = manager.execute_current_step(run.id)
    assert run.status == SetupRunStatus.FAILED
    assert run.current_step == "step-one"

    run = manager.apply_action(run.id, "retry_step")
    assert run.status == SetupRunStatus.RUNNING
    assert run.current_step == "step-one"  # retry_step never advances the pointer

    run = manager.execute_current_step(run.id)
    assert run.status == SetupRunStatus.RUNNING
    assert run.current_step == "step-two"
    assert step_one.calls == 2


def test_resume_before_any_execute_still_starts_at_first_step(file_db):
    """`apply_action("resume")` moves PENDING -> RUNNING without ever touching
    `current_step` - the registry must still find and run the first step."""
    recipe = _two_step_recipe()
    step_one = AlwaysSucceedsExecutor()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": step_one, "kind.two": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")
    run = manager.apply_action(run.id, "resume")
    assert run.status == SetupRunStatus.RUNNING
    assert run.current_step is None

    run = manager.execute_current_step(run.id)

    assert step_one.calls == 1
    assert run.current_step == "step-two"


def test_awaiting_consent_parks_the_run_without_advancing(file_db):
    recipe = _two_step_recipe()
    step_one = AwaitsConsentThenSucceedsExecutor()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": step_one, "kind.two": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")

    run = manager.execute_current_step(run.id)

    assert run.status == SetupRunStatus.AWAITING_CONSENT
    assert run.current_step == "step-one"
    attempt = manager.list_attempts(run.id)[0]
    assert attempt.status == SetupStepStatus.AWAITING_CONSENT
    assert attempt.finished_at is None
    assert attempt.safe_output["consent_request"]["total_bytes"] == 1

    # Calling execute() again while parked is rejected (see _EXECUTABLE_STATUSES) -
    # only grant_consent (SetupRunManager) is allowed to move it on.
    with pytest.raises(SetupRunError):
        manager.execute_current_step(run.id)


def test_grant_consent_completes_the_step_and_advances(file_db):
    recipe = _two_step_recipe()
    step_one = AwaitsConsentThenSucceedsExecutor()
    step_two = AlwaysSucceedsExecutor()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": step_one, "kind.two": step_two})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")
    run = manager.execute_current_step(run.id)
    assert run.status == SetupRunStatus.AWAITING_CONSENT

    run = manager.grant_consent(run.id, "step-one", granted_by="admin-1")

    assert run.status == SetupRunStatus.RUNNING
    assert run.current_step == "step-two"
    # step-one never actually re-runs (its executor's second call never
    # happens) - grant_consent records the approval itself, it doesn't
    # re-invoke the parked executor.
    assert step_one.calls == 1

    attempts = [a for a in manager.list_attempts(run.id) if a.step_key == "step-one"]
    assert attempts[-1].status == SetupStepStatus.SUCCEEDED
    assert attempts[-1].safe_output["consent_granted"] is True
    assert attempts[-1].safe_output["granted_by"] == "admin-1"


def test_grant_consent_on_non_consent_step_is_rejected(file_db):
    recipe = _two_step_recipe()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": AlwaysSucceedsExecutor(), "kind.two": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")  # PENDING, nothing awaiting consent

    with pytest.raises(SetupRunError):
        manager.grant_consent(run.id, "step-one")


def test_grant_consent_with_wrong_step_key_is_rejected(file_db):
    recipe = _two_step_recipe()
    step_one = AwaitsConsentThenSucceedsExecutor()
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {"kind.one": step_one, "kind.two": AlwaysSucceedsExecutor()})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("fake-recipe")
    run = manager.execute_current_step(run.id)
    assert run.status == SetupRunStatus.AWAITING_CONSENT

    with pytest.raises(SetupRunError):
        manager.grant_consent(run.id, "step-two")


def test_recipe_with_no_steps_completes_immediately(file_db):
    recipe = Recipe(id="empty", schema_version=1, version=1, name="Empty", engine="native", steps=[])
    registry = SetupExecutorRegistry(FakeCatalog(recipe), {})
    manager = SetupRunManager()
    manager.register_executor_registry(registry)
    run = manager.create_run("empty")

    run = manager.execute_current_step(run.id)

    assert run.status == SetupRunStatus.COMPLETED
