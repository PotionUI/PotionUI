import pytest

from scripts.preset_render import StubSettings, build_processor
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.presets.loader import PresetTemplateLoader
from src.features.recipes.executors._fixture_form import build_fixture_form_data
from src.features.recipes.executors.base import StepContext
from src.features.recipes.executors.pipeline_render import PipelineRenderExecutor
from src.features.recipes.records import RecipeRun, RecipeRunStatus
from src.features.recipes.schema import Recipe, RecipeStep
from src.platform.templating import TemplateProcessor

WAN_PRESET_ID = "01KX47WANVIDEO0000000000TV"


@pytest.fixture
def loader():
    preset_loader = PresetTemplateLoader(["content/presets"])
    preset_loader.load_presets()
    return preset_loader


def _offering(loader, *compositions):
    preset = loader.load_preset_by_id(WAN_PRESET_ID)
    director = preset.vars["video_director"]
    director["modes"] = {name: director["modes"][name] for name in compositions}
    return preset


class SpyBuilder:
    def __init__(self, inner):
        self.inner = inner
        self.documents = []

    def build_pipeline(self, preset, form_data, mode=None, user_id=None):
        self.documents.append(form_data.get("video_director"))
        return self.inner.build_pipeline(preset, form_data, mode=mode, user_id=user_id)


def _check(loader, plan):
    spy = SpyBuilder(PipelineBuilder(loader, build_processor()))
    executor = PipelineRenderExecutor(loader, TemplateProcessor(settings=StubSettings()), spy)
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    params = {"preset_id": WAN_PRESET_ID, "plan": "modes.plan"} if plan else {"preset_id": WAN_PRESET_ID, "mode": "video"}
    step = RecipeStep(key="pipeline.render", kind="pipeline.render", title="Check", params=params)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", steps=[step])
    outputs = {"modes.plan": {"plan": {"preset_id": WAN_PRESET_ID, "modes": ["video"]}}} if plan else {}
    return executor.execute(StepContext(run=run, recipe=recipe, step=step, step_outputs=outputs)), spy.documents


@pytest.mark.parametrize("plan", [False, True], ids=["single-mode", "plan"])
@pytest.mark.parametrize(
    "compositions,roles",
    [(("i2v",), ["first"]), (("i2v", "flf"), ["first"]), (("flf",), ["first", "last"])],
    ids=["i2v", "i2v-and-flf", "flf"],
)
def test_the_setup_check_renders_a_director_preset_that_offers_no_text_to_video(loader, compositions, roles, plan):
    _offering(loader, *compositions)

    result, documents = _check(loader, plan)

    assert result.success is True, result.safe_error_detail
    assert documents[0]["mode"] == compositions[0]
    assert [item["role"] for item in documents[0]["media"]] == roles


def test_a_preset_that_offers_text_to_video_still_gets_a_text_to_video_document(loader):
    result, documents = _check(loader, plan=False)

    assert result.success is True, result.safe_error_detail
    assert documents[0]["mode"] == "t2v"
    assert documents[0]["media"] == []


def test_a_test_generation_gets_no_made_up_picture(loader):
    form_data = build_fixture_form_data(_offering(loader, "i2v"), "video")

    assert "video_director" not in form_data
