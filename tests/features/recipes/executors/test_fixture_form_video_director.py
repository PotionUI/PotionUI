import pytest

from scripts.preset_render import StubSettings, build_processor
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.presets.loader import PresetTemplateLoader
from src.features.presets.fixture_form import VIDEO_DIRECTOR_FIXTURE_PROMPT
from src.features.recipes.executors._fixture_form import build_fixture_form_data
from src.features.recipes.executors.base import StepContext
from src.features.recipes.executors.pipeline_render import PipelineRenderExecutor
from src.features.recipes.records import RecipeRun, RecipeRunStatus
from src.features.recipes.schema import Recipe, RecipeStep
from src.platform.templating import TemplateProcessor

SDXL_PRESET_ID = "01K0W24A3RADXXABH16YQ7KE90"
WAN_PRESET_ID = "01KX47WANVIDEO0000000000TV"
DIRECTOR_PRESET_IDS = [
    WAN_PRESET_ID,
    "01KX47LTXVIDEO0000000000TV",
    "01KX47LTX25VIDEO00000000TV",
    "01KX47H3MINIMAX000000000VA",
]


@pytest.fixture(scope="module")
def loader():
    preset_loader = PresetTemplateLoader(["content/presets"])
    preset_loader.load_presets()
    return preset_loader


@pytest.fixture(scope="module")
def template_processor():
    return TemplateProcessor(settings=StubSettings())


@pytest.fixture(scope="module")
def executor(loader, template_processor):
    return PipelineRenderExecutor(loader, template_processor, PipelineBuilder(loader, build_processor()))


def _form(loader, preset_id, **kwargs):
    return build_fixture_form_data(loader.load_preset_by_id(preset_id), "video", **kwargs)


def _context(preset_id):
    run = RecipeRun(id="r1", recipe_id="video-starter", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    recipe = Recipe(id="video-starter", schema_version=1, version=1, name="Video Starter", engine="native")
    step = RecipeStep(
        key="pipeline.render", kind="pipeline.render", title="Validate pipeline",
        params={"preset_id": preset_id, "mode": "video"},
    )
    return StepContext(run=run, recipe=recipe, step=step)


@pytest.mark.parametrize("preset_id", DIRECTOR_PRESET_IDS)
def test_director_presets_get_a_one_second_text_to_video_document(loader, preset_id):
    document = _form(loader, preset_id)["video_director"]

    assert document["mode"] == "t2v"
    assert document["settings"]["duration"] == 1
    assert document["media"] == []
    assert [segment["prompt"] for segment in document["segments"]] == [VIDEO_DIRECTOR_FIXTURE_PROMPT]


def test_the_smoke_prompt_becomes_the_segment_prompt(loader):
    document = _form(loader, WAN_PRESET_ID, prompt="a paper boat in the rain")["video_director"]

    assert document["segments"][0]["prompt"] == "a paper boat in the rain"


def test_an_explicit_document_is_kept(loader):
    explicit = {
        "schema_version": 1,
        "mode": "t2v",
        "settings": {"fps": 16, "duration": 2, "resolution": "832x480", "seed": 7},
        "segments": [{"id": "shot-1", "prompt": "a lighthouse at dusk", "negative_prompt": ""}],
        "media": [],
    }

    form_data = _form(loader, WAN_PRESET_ID, overrides={"video_director": explicit})

    assert form_data["video_director"] == explicit


def test_presets_without_a_director_get_no_document(loader):
    form_data = build_fixture_form_data(loader.load_preset_by_id(SDXL_PRESET_ID), "txt2img")

    assert "video_director" not in form_data


@pytest.mark.parametrize("preset_id", DIRECTOR_PRESET_IDS)
def test_pipeline_render_passes_for_director_presets(executor, preset_id):
    result = executor.execute(_context(preset_id))

    assert result.success, result.safe_error_detail
    assert result.safe_output["pipe_count"] > 0
