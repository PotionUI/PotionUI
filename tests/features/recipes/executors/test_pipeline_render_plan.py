from types import SimpleNamespace

from src.features.recipes.executors.base import StepContext
from src.features.recipes.executors.pipeline_render import PipelineRenderExecutor
from src.features.recipes.records import RecipeRun, RecipeRunStatus
from src.features.recipes.schema import Recipe, RecipeStep
from tests.features.recipes.mode_fixtures import FakeLoader, video_preset


class Builder:
    def __init__(self, broken=()):
        self.broken = set(broken)
        self.modes = []

    def build_pipeline(self, preset, form_data, mode=None, user_id=None):
        self.modes.append(mode)
        if mode in self.broken:
            raise ValueError("template error")
        return SimpleNamespace(pipes=[1, 2, 3])


def _context(modes):
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    step = RecipeStep(key="pipeline.render", kind="pipeline.render", title="Check", params={"preset_id": "VID", "plan": "modes.plan"})
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", steps=[step])
    return StepContext(run=run, recipe=recipe, step=step, step_outputs={"modes.plan": {"plan": {"preset_id": "VID", "modes": modes}}})


def _executor(builder):
    executor = PipelineRenderExecutor(FakeLoader(video_preset()), None, builder)
    executor._build_fixture_form_data = lambda preset, mode: {}
    return executor


def test_renders_every_chosen_mode():
    builder = Builder()
    result = _executor(builder).execute(_context(["video", "cinemagraph"]))

    assert result.success is True
    assert builder.modes == ["video", "cinemagraph"]
    assert result.safe_output == {"modes": [{"mode": "video", "pipe_count": 3}, {"mode": "cinemagraph", "pipe_count": 3}]}


def test_a_broken_mode_fails_with_its_name():
    result = _executor(Builder(broken={"cinemagraph"})).execute(_context(["video", "cinemagraph"]))

    assert result.error_code == "PIPELINE_RENDER_FAILED"
    assert "'cinemagraph'" in result.safe_error_detail


def test_unknown_mode_and_missing_plan_fail_plainly():
    assert _executor(Builder()).execute(_context(["ghost"])).error_code == "MODE_NOT_FOUND"
    context = _context([])
    context.step_outputs.clear()
    assert _executor(Builder()).execute(context).error_code == "PIPELINE_RENDER_NO_PLAN"
