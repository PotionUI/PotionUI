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


def test_restart_reconciles_a_running_run_to_a_retryable_failure(file_db):
    runner = RecipeRunner()
    run = runner.create_run("demo", mode=MODE_ADMIN)
    runner.transition(run.id, RecipeRunStatus.RUNNING, current_step="fetch")
    runner.record_step_attempt(run.id, "fetch", RecipeStepStatus.RUNNING, progress_current=5, progress_total=10)

    assert runner.reconcile_interrupted() == 1
    reconciled = runner.get_run(run.id)
    assert reconciled.status == RecipeRunStatus.FAILED
    assert reconciled.error_code == "RUN_INTERRUPTED"
    attempt = runner.list_attempts(run.id)[-1]
    assert (attempt.status, attempt.error_code, attempt.finished_at is not None) == (RecipeStepStatus.FAILED, "RUN_INTERRUPTED", True)
    assert runner.apply_action(run.id, "retry_step").status == RecipeRunStatus.RUNNING
    assert runner.reconcile_interrupted() == 1


def test_parked_runs_survive_a_restart(file_db):
    runner = RecipeRunner()
    run = runner.create_run("demo", mode=MODE_ADMIN)
    runner.transition(run.id, RecipeRunStatus.AWAITING_CONSENT, current_step="plan")

    assert runner.reconcile_interrupted() == 0
    assert runner.get_run(run.id).status == RecipeRunStatus.AWAITING_CONSENT


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
