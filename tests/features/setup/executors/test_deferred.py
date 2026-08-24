"""`DeferredStepExecutor` - the wave-2 step-kind stub. Always a clear,
honest "not yet" failure, never a crash."""

from src.features.setup.executors.base import StepContext
from src.features.setup.executors.deferred import DeferredStepExecutor
from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus


def test_reports_a_clear_not_yet_failure():
    executor = DeferredStepExecutor("artifacts.fetch")
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native")
    step = RecipeStep(key="artifacts.fetch", kind="artifacts.fetch", title="Download the checkpoint", params={})

    result = executor.execute(StepContext(run=run, recipe=recipe, step=step))

    assert result.success is False
    assert result.error_code == "STEP_NOT_IMPLEMENTED"
    assert "Download the checkpoint" in result.safe_error_detail
    assert "artifacts.fetch" in result.safe_error_detail
