"""`pipeline.render` executor against the real preset tree.

Deliberately NOT mocked: the whole point of this step is proving the real
`PipelineBuilder` (the same one that drives an actual generation) renders a
preset/mode's Jinja2 templates to a valid pipe list. It reuses the same
deterministic stubs (`StubSettings`, `build_processor`) the
`scripts/preset_render.py` developer harness uses - pure template rendering,
no GPU/model/DB/network access, runs in well under a second.
"""

import pytest

from scripts.preset_render import StubSettings, build_processor
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.presets.loader import PresetTemplateLoader
from src.features.setup.executors.base import StepContext
from src.features.setup.executors.pipeline_render import PipelineRenderExecutor
from src.features.setup.recipe_schema import Recipe, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus
from src.platform.templating import TemplateProcessor

SDXL_PRESET_ID = "01K0W24A3RADXXABH16YQ7KE90"


@pytest.fixture(scope="module")
def executor():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    processor = build_processor()
    pipeline_builder = PipelineBuilder(loader, processor)
    template_processor = TemplateProcessor(settings=StubSettings())
    return PipelineRenderExecutor(loader, template_processor, pipeline_builder)


def _context(preset_id, mode):
    run = SetupRun(id="r1", recipe_id="sdxl-starter", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING)
    recipe = Recipe(id="sdxl-starter", schema_version=1, version=1, name="SDXL Starter", engine="native")
    step = RecipeStep(
        key="pipeline.render", kind="pipeline.render", title="Validate pipeline",
        params={"preset_id": preset_id, "mode": mode},
    )
    return StepContext(run=run, recipe=recipe, step=step)


def test_sdxl_txt2img_renders_successfully(executor):
    result = executor.execute(_context(SDXL_PRESET_ID, "txt2img"))

    assert result.success is True
    assert result.safe_output["preset_id"] == SDXL_PRESET_ID
    assert result.safe_output["mode"] == "txt2img"
    assert result.safe_output["pipe_count"] > 0


def test_unknown_preset_id_fails_clearly(executor):
    result = executor.execute(_context("not-a-real-preset-id", "txt2img"))

    assert result.success is False
    assert result.error_code == "PRESET_MISSING_ON_DISK"


def test_unknown_mode_fails_clearly(executor):
    result = executor.execute(_context(SDXL_PRESET_ID, "not-a-real-mode"))

    assert result.success is False
    assert result.error_code == "MODE_NOT_FOUND"


def test_missing_params_is_misconfiguration(executor):
    result = executor.execute(_context(None, "txt2img"))

    assert result.success is False
    assert result.error_code == "PIPELINE_RENDER_MISCONFIGURED"
