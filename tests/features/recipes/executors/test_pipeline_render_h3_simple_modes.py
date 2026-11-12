import pytest

from scripts.preset_render import StubSettings, build_processor
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.presets.loader import PresetTemplateLoader
from src.features.recipes.executors.base import StepContext
from src.features.recipes.executors.pipeline_render import PipelineRenderExecutor
from src.features.recipes.mode_slots import derive_mode_slots
from src.features.recipes.records import RecipeRun, RecipeRunStatus
from src.features.recipes.schema import Recipe, RecipeStep
from src.platform.templating import TemplateProcessor

H3 = "01KX47H3MINIMAX000000000VA"
SIMPLE = ["txt2video", "img2video", "first_last"]


@pytest.fixture(scope="module")
def loader():
    preset_loader = PresetTemplateLoader(["content/presets"])
    preset_loader.load_presets()
    return preset_loader


def _context(modes):
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    step = RecipeStep(key="pipeline.render", kind="pipeline.render", title="Check", params={"preset_id": H3, "plan": "modes.plan"})
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", steps=[step])
    return StepContext(run=run, recipe=recipe, step=step, step_outputs={"modes.plan": {"plan": {"preset_id": H3, "modes": modes}}})


def test_the_setup_check_renders_every_simple_mode_next_to_the_director(loader):
    executor = PipelineRenderExecutor(loader, TemplateProcessor(settings=StubSettings()), PipelineBuilder(loader, build_processor()))

    result = executor.execute(_context([*SIMPLE, "video"]))

    assert result.success is True, result.safe_error_detail
    assert [entry["mode"] for entry in result.safe_output["modes"]] == [*SIMPLE, "video"]
    assert len({entry["pipe_count"] for entry in result.safe_output["modes"]}) == 1


def test_a_simple_mode_needs_exactly_the_files_the_director_needs(loader):
    plan = derive_mode_slots(loader.load_preset_by_id(H3), [*SIMPLE, "video"])

    assert plan.modes["video"].slot_ids
    for mode in SIMPLE:
        assert plan.modes[mode].slot_ids == plan.modes["video"].slot_ids
        assert plan.modes[mode].extra_slot_ids == plan.modes["video"].extra_slot_ids


class SpyBuilder:
    def __init__(self, inner):
        self.inner = inner
        self.documents = {}

    def build_pipeline(self, preset, form_data, mode=None, user_id=None):
        self.documents[mode] = form_data.get("video_director")
        return self.inner.build_pipeline(preset, form_data, mode=mode, user_id=user_id)


def test_the_setup_check_runs_the_real_adapter_with_a_fixture_prompt_in_each_simple_mode(loader):
    spy = SpyBuilder(PipelineBuilder(loader, build_processor()))
    executor = PipelineRenderExecutor(loader, TemplateProcessor(settings=StubSettings()), spy)

    assert executor.execute(_context([*SIMPLE])).success is True

    assert {mode: spy.documents[mode]["mode"] for mode in SIMPLE} == {"txt2video": "t2v", "img2video": "i2v", "first_last": "flf"}
    assert all(spy.documents[mode]["segments"][0]["prompt"] for mode in SIMPLE)
    assert [m["role"] for m in spy.documents["first_last"]["media"]] == ["first", "last"]


@pytest.mark.parametrize("mode", SIMPLE)
def test_the_single_mode_check_passes_for_a_simple_mode_without_real_pictures(loader, mode):
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    step = RecipeStep(key="pipeline.render", kind="pipeline.render", title="Check", params={"preset_id": H3, "mode": mode})
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", steps=[step])
    executor = PipelineRenderExecutor(loader, TemplateProcessor(settings=StubSettings()), PipelineBuilder(loader, build_processor()))

    result = executor.execute(StepContext(run=run, recipe=recipe, step=step))

    assert result.success is True, result.safe_error_detail
