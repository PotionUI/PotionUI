import pytest

from src.features.recipes.executors.base import StepContext, StepResult
from src.features.recipes.executors.registry import RecipeExecutorRegistry
from src.features.recipes.records import MODE_ADMIN, RecipeRunStatus, RecipeStepStatus
from src.features.recipes.runner import RecipeRunError, RecipeRunner
from src.features.recipes.schema import Recipe, RecipeStep
from src.platform.database.database import db as global_db
from tests.fixtures.db_template import copy_template_db


@pytest.fixture
def file_db(tmp_path):
    db_path = tmp_path / "recipe_robustness.db"
    copy_template_db(db_path)
    original_path = global_db.db_path
    global_db.db_path = db_path
    try:
        yield global_db
    finally:
        global_db.db_path = original_path


class Catalog:
    def __init__(self, recipe):
        self.recipe = recipe

    def get_recipe(self, recipe_id, version=None):
        return self.recipe if recipe_id == self.recipe.id else None


def _recipe(*steps):
    return Recipe(id="demo", schema_version=1, version=1, name="Demo", engine="native", steps=list(steps))


def _step(key, kind=None):
    return RecipeStep(key=key, kind=kind or key, title=key.title())


class _Counting:
    def __init__(self):
        self.calls = 0

    def execute(self, context: StepContext) -> StepResult:
        self.calls += 1
        return StepResult.ok({})


class _AdvisoryStep(_Counting):
    advisory = True


def _runner(recipe, **executors):
    runner = RecipeRunner()
    runner.register_executor_registry(RecipeExecutorRegistry(Catalog(recipe), executors))
    return runner


def _interrupt(runner, run_id, step_key, *, progress=5):
    if runner.get_run(run_id).status == RecipeRunStatus.RUNNING:
        runner.repo.update_run(run_id, current_step=step_key)
    else:
        runner.transition(run_id, RecipeRunStatus.RUNNING, current_step=step_key)
    runner.record_step_attempt(run_id, step_key, RecipeStepStatus.RUNNING, progress_current=progress, progress_total=10)


def test_a_restart_resumes_a_running_run_on_the_same_step(file_db):
    recipe = _recipe(_step("fetch"), _step("render"))
    runner = _runner(recipe, fetch=_Counting(), render=_Counting())
    run = runner.create_run("demo", mode=MODE_ADMIN)
    _interrupt(runner, run.id, "fetch")

    assert runner.reconcile_interrupted() == run.id

    resumed = runner.get_run(run.id)
    assert resumed.status == RecipeRunStatus.RUNNING
    assert resumed.current_step == "fetch"
    assert resumed.error_code is None
    attempt = runner.list_attempts(run.id)[-1]
    assert (attempt.status, attempt.error_code, attempt.finished_at is not None) == (RecipeStepStatus.FAILED, "RUN_INTERRUPTED", True)


def test_the_resumed_run_drives_on_and_completes(file_db):
    fetch, render = _Counting(), _Counting()
    runner = _runner(_recipe(_step("fetch"), _step("render")), fetch=fetch, render=render)
    run = runner.create_run("demo", mode=MODE_ADMIN)
    _interrupt(runner, run.id, "fetch")

    runner.drive(runner.reconcile_interrupted())

    assert runner.get_run(run.id).status == RecipeRunStatus.COMPLETED
    assert (fetch.calls, render.calls) == (1, 1)


def test_the_third_interruption_of_the_same_step_gives_up(file_db):
    recipe = _recipe(_step("fetch"), _step("render"))
    runner = _runner(recipe, fetch=_Counting(), render=_Counting())
    run = runner.create_run("demo", mode=MODE_ADMIN)

    _interrupt(runner, run.id, "fetch")
    assert runner.reconcile_interrupted() == run.id
    _interrupt(runner, run.id, "fetch")
    assert runner.reconcile_interrupted() == run.id
    _interrupt(runner, run.id, "fetch")
    assert runner.reconcile_interrupted() is None

    gave_up = runner.get_run(run.id)
    assert gave_up.status == RecipeRunStatus.FAILED
    assert gave_up.error_code == "RUN_INTERRUPTED"
    assert gave_up.safe_error_detail == "Setup was interrupted several times in a row. Continue to start it again."
    assert runner.apply_action(run.id, "retry_step").status == RecipeRunStatus.RUNNING


def test_a_crash_before_any_attempt_exists_is_counted_and_gives_up_on_the_third_boot(file_db):
    runner = _runner(_recipe(_step("fetch"), _step("render")), fetch=_Counting(), render=_Counting())
    run = runner.create_run("demo", mode=MODE_ADMIN)
    runner.transition(run.id, RecipeRunStatus.RUNNING, current_step="fetch")

    assert runner.reconcile_interrupted() == run.id
    assert runner.reconcile_interrupted() == run.id
    assert runner.reconcile_interrupted() is None

    gave_up = runner.get_run(run.id)
    assert gave_up.status == RecipeRunStatus.FAILED
    assert gave_up.error_code == "RUN_INTERRUPTED"
    assert "several times" in gave_up.safe_error_detail
    assert runner.apply_action(run.id, "retry_step").status == RecipeRunStatus.RUNNING


def test_a_run_that_reached_a_new_step_between_interruptions_is_counted_per_step(file_db):
    runner = _runner(_recipe(_step("fetch"), _step("render")), fetch=_Counting(), render=_Counting())
    run = runner.create_run("demo", mode=MODE_ADMIN)
    runner.transition(run.id, RecipeRunStatus.RUNNING, current_step="fetch")
    runner.reconcile_interrupted()
    runner.reconcile_interrupted()
    runner.repo.update_run(run.id, current_step="render")

    assert runner.reconcile_interrupted() == run.id
    assert runner.get_run(run.id).status == RecipeRunStatus.RUNNING


def test_an_interruption_of_another_step_starts_its_own_count(file_db):
    recipe = _recipe(_step("fetch"), _step("render"))
    runner = _runner(recipe, fetch=_Counting(), render=_Counting())
    run = runner.create_run("demo", mode=MODE_ADMIN)
    _interrupt(runner, run.id, "fetch")
    runner.reconcile_interrupted()
    _interrupt(runner, run.id, "fetch")
    runner.reconcile_interrupted()

    _interrupt(runner, run.id, "render")

    assert runner.reconcile_interrupted() == run.id
    assert runner.get_run(run.id).status == RecipeRunStatus.RUNNING


def test_an_interrupted_advisory_step_is_recorded_as_needing_attention_and_skipped(file_db):
    recipe = _recipe(_step("smoke"), _step("activate"))
    runner = _runner(recipe, smoke=_AdvisoryStep(), activate=_Counting())
    run = runner.create_run("demo", mode=MODE_ADMIN)
    _interrupt(runner, run.id, "smoke")

    assert runner.reconcile_interrupted() == run.id

    moved = runner.get_run(run.id)
    assert moved.status == RecipeRunStatus.RUNNING
    assert moved.current_step == "activate"
    attempt = runner.list_attempts(run.id)[-1]
    assert attempt.status == RecipeStepStatus.ACTION_REQUIRED
    assert attempt.error_code == "RUN_INTERRUPTED"
    assert attempt.safe_error_detail == "PotionUI stopped while making the test picture."


def test_an_interrupted_advisory_step_never_loops_even_after_many_restarts(file_db):
    runner = _runner(_recipe(_step("smoke"), _step("activate")), smoke=_AdvisoryStep(), activate=_Counting())
    run = runner.create_run("demo", mode=MODE_ADMIN)
    _interrupt(runner, run.id, "smoke")
    runner.reconcile_interrupted()
    _interrupt(runner, run.id, "activate")
    runner.reconcile_interrupted()

    assert sum(1 for a in runner.list_attempts(run.id) if a.step_key == "smoke") == 1


def test_an_interrupted_last_advisory_step_completes_the_run(file_db):
    runner = _runner(_recipe(_step("smoke")), smoke=_AdvisoryStep())
    run = runner.create_run("demo", mode=MODE_ADMIN)
    _interrupt(runner, run.id, "smoke")

    assert runner.reconcile_interrupted() is None
    assert runner.get_run(run.id).status == RecipeRunStatus.COMPLETED


def test_a_pending_run_is_resumed_too(file_db):
    runner = _runner(_recipe(_step("fetch")), fetch=_Counting())
    run = runner.create_run("demo", mode=MODE_ADMIN)

    assert runner.reconcile_interrupted() == run.id
    assert runner.get_run(run.id).status == RecipeRunStatus.PENDING


def test_parked_runs_survive_a_restart(file_db):
    runner = RecipeRunner()
    run = runner.create_run("demo", mode=MODE_ADMIN)
    runner.transition(run.id, RecipeRunStatus.AWAITING_CONSENT, current_step="plan")

    assert runner.reconcile_interrupted() is None
    assert runner.get_run(run.id).status == RecipeRunStatus.AWAITING_CONSENT


def test_nothing_to_reconcile_returns_nothing(file_db):
    assert RecipeRunner().reconcile_interrupted() is None


class _FailingSmoke:
    advisory = True

    def execute(self, context: StepContext) -> StepResult:
        return StepResult.fail("GENERATION_SMOKE_FAILED", "The test didn't finish: it took longer than 5 minutes.")


def test_a_smoke_that_needs_attention_still_lets_activation_finish_the_run(file_db):
    from src.features.setup.records import OnboardingStatus
    from src.features.setup.workspace_activate import WorkspaceActivateExecutor

    class Repos:
        def __init__(self, runner):
            self.runner = runner
            self.upserts = []

        def list_attempts(self, run_id):
            return self.runner.list_attempts(run_id)

        def upsert_onboarding_state(self, user_id, *, status=None, first_generation_id=None, dismissed=False):
            self.upserts.append((user_id, status, first_generation_id))

    recipe = _recipe(
        RecipeStep(key="generation.smoke", kind="generation.smoke", title="Test"),
        RecipeStep(key="workspace.activate", kind="workspace.activate", title="Finish"),
    )
    runner = RecipeRunner()
    repos = Repos(runner)
    runner.register_executor_registry(
        RecipeExecutorRegistry(
            Catalog(recipe),
            {
                "generation.smoke": _FailingSmoke(),
                "workspace.activate": WorkspaceActivateExecutor(repos, repos),
            },
        )
    )
    run = runner.create_run("demo", mode=MODE_ADMIN, created_by="owner-1")

    run = runner.drive(run.id)

    assert run.status == RecipeRunStatus.COMPLETED
    statuses = {a.step_key: a.status for a in runner.list_attempts(run.id)}
    assert statuses == {
        "generation.smoke": RecipeStepStatus.ACTION_REQUIRED,
        "workspace.activate": RecipeStepStatus.SUCCEEDED,
    }
    assert repos.upserts == [("owner-1", OnboardingStatus.COMPLETED, None)]


class CancelledMidStep:
    def __init__(self, runner):
        self.runner = runner
        self.saw_cancel = None

    def execute(self, context: StepContext) -> StepResult:
        context.report_progress(progress_current=1, progress_total=2, progress_unit="bytes", safe_output={"files": []})
        self.runner.apply_action(context.run.id, "cancel")
        self.saw_cancel = context.is_cancelled()
        context.report_progress(progress_current=2, progress_total=2, progress_unit="bytes")
        return StepResult.fail("RUN_CANCELLED", "The install was cancelled.")


def test_cancel_while_a_step_runs_ends_the_run_and_closes_the_attempt(file_db):
    runner = RecipeRunner()
    executor = CancelledMidStep(runner)
    registry = RecipeExecutorRegistry(Catalog(_recipe(_step("fetch"), _step("after"))), {"fetch": executor, "after": executor})
    runner.register_executor_registry(registry)
    run = runner.create_run("demo", mode=MODE_ADMIN)

    result = runner.drive(run.id)

    assert executor.saw_cancel is True
    assert result.status == RecipeRunStatus.CANCELLED
    attempts = runner.list_attempts(run.id)
    assert [(a.step_key, a.status) for a in attempts] == [("fetch", RecipeStepStatus.CANCELLED)]
    assert attempts[0].progress_current == 1


class PlanStep:
    def __init__(self):
        self.grants = []

    def execute(self, context):
        return StepResult.awaiting({"slots": [{"id": "model", "variants": [{"id": "a"}, {"id": "b"}], "recommended_variant_id": "a"}], "modes": [{"id": "video", "selected": True}]})

    def grant(self, context, consent_request, grant):
        self.grants.append(grant)
        if grant.modes and "ghost" in grant.modes:
            raise ValueError("'ghost' is not one of the modes this step offers.")
        return {"plan": {"modes": grant.modes or ["video"], "target": grant.target}}


class ReadsPlan:
    def __init__(self):
        self.seen = None

    def execute(self, context):
        self.seen = context.plans("plan")
        return StepResult.ok()


def test_grant_hands_modes_and_target_to_the_step_and_later_steps_read_its_plan(file_db):
    runner = RecipeRunner()
    plan_step, reader = PlanStep(), ReadsPlan()
    registry = RecipeExecutorRegistry(Catalog(_recipe(_step("plan"), _step("fetch"))), {"plan": plan_step, "fetch": reader})
    runner.register_executor_registry(registry)
    run = runner.create_run("demo", mode=MODE_ADMIN)
    runner.drive(run.id)

    with pytest.raises(RecipeRunError, match="'ghost'"):
        runner.grant_consent(run.id, "plan", modes=["ghost"])
    assert runner.get_run(run.id).status == RecipeRunStatus.AWAITING_CONSENT

    runner.grant_consent(run.id, "plan", selections={"model": "b"}, modes=["video", "extend"], target="pod")
    runner.drive(run.id)

    assert plan_step.grants[-1].selections == {"model": "b"}
    assert reader.seen == [{"modes": ["video", "extend"], "target": "pod"}]
    assert runner.get_run(run.id).status == RecipeRunStatus.COMPLETED


class PlainConsent:
    def execute(self, context):
        return StepResult.awaiting({"artifacts": []})


def test_modes_and_target_are_refused_by_a_step_without_a_grant_hook(file_db):
    runner = RecipeRunner()
    registry = RecipeExecutorRegistry(Catalog(_recipe(_step("plan"))), {"plan": PlainConsent()})
    runner.register_executor_registry(registry)
    run = runner.create_run("demo", mode=MODE_ADMIN)
    runner.drive(run.id)

    with pytest.raises(RecipeRunError, match="does not offer modes"):
        runner.grant_consent(run.id, "plan", modes=["video"])
    assert runner.grant_consent(run.id, "plan").status == RecipeRunStatus.COMPLETED
