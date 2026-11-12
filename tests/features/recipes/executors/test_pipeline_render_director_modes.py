from pathlib import Path

import pytest
import yaml

from scripts.preset_render import StubSettings, build_processor
from src.features.generation.pipeline_builder import PipelineBuilder
from src.features.presets.loader import PresetTemplateLoader, plugin_preset_roots
from src.features.recipes.executors.base import StepContext
from src.features.recipes.executors.pipeline_render import PipelineRenderExecutor
from src.features.presets.loader import plugin_preset_roots
from src.features.recipes.catalog import plugin_recipe_roots
from src.features.recipes.schema import parse_recipe
from src.platform.plugins.loader import PluginLoader
from src.features.recipes.records import RecipeRun, RecipeRunStatus
from src.features.recipes.schema import Recipe, RecipeStep
from src.features.video_director.simple_modes import is_director_backed
from src.platform.templating import TemplateProcessor


REPO = Path(__file__).resolve().parents[4]
PLUGINS = REPO / "content" / "plugins" / "marketplace"


def _loader():
    manifests = PluginLoader(str(PLUGINS), str(REPO / "content" / "plugins" / "_none")).discover_plugins()
    loader = PresetTemplateLoader(["content/presets", *[str(root) for root in plugin_preset_roots(manifests)]])
    loader.load_presets()
    return loader


def _is_director_mode(preset, mode):
    variables = preset.vars or {}
    video = variables.get("video_director")
    music = variables.get("music_director")
    return (
        is_director_backed(video, mode) and isinstance(video, dict)
        or isinstance(video, dict) and mode in (video.get("preset_modes") or [])
        or isinstance(music, dict) and mode in (music.get("preset_modes") or [])
    )


def _cases():
    return [
        pytest.param(preset.id, mode, id=f"{preset.name}-{mode}")
        for preset in _loader().presets
        for mode in preset.modes
        if _is_director_mode(preset, mode)
    ]


def _recipe_steps():
    manifests = PluginLoader(str(PLUGINS), str(REPO / "content" / "plugins" / "_none")).discover_plugins()
    files = sorted((REPO / "content" / "recipes" / "marketplace").glob("*.yml"))
    files += sorted(f for root in plugin_recipe_roots(manifests) for f in Path(root.path).glob("*.yml"))
    steps = []
    for path in files:
        recipe = parse_recipe(yaml.safe_load(path.read_text(encoding="utf-8")), source_path=str(path))
        for step in recipe.steps:
            if step.kind == "pipeline.render":
                steps.append(pytest.param(recipe, step, id=f"{path.stem}-{step.key}"))
    return steps


@pytest.fixture(scope="module")
def loader():
    return _loader()


@pytest.fixture(scope="module")
def executor(loader):
    return PipelineRenderExecutor(loader, TemplateProcessor(settings=StubSettings()), PipelineBuilder(loader, build_processor()))


def _context(preset_id, mode, plan):
    run = RecipeRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    params = {"preset_id": preset_id, "plan": "modes.plan"} if plan else {"preset_id": preset_id, "mode": mode}
    step = RecipeStep(key="pipeline.render", kind="pipeline.render", title="Check", params=params)
    recipe = Recipe(id="x", schema_version=1, version=1, name="X", engine="native", steps=[step])
    outputs = {"modes.plan": {"plan": {"preset_id": preset_id, "modes": [mode]}}} if plan else {}
    return StepContext(run=run, recipe=recipe, step=step, step_outputs=outputs)


def test_the_enumeration_finds_director_modes_of_several_families():
    assert len(_cases()) >= 10


@pytest.mark.parametrize("plan", [False, True], ids=["single-mode", "plan"])
@pytest.mark.parametrize("preset_id,mode", _cases())
def test_the_setup_check_renders_every_director_backed_mode(executor, preset_id, mode, plan):
    result = executor.execute(_context(preset_id, mode, plan))
    assert result.success is True, result.safe_error_detail


@pytest.mark.parametrize("recipe,step", _recipe_steps())
def test_every_starter_recipes_render_steps_pass_through_the_executor(loader, executor, recipe, step):
    preset_id = step.params["preset_id"]
    preset = loader.load_preset_by_id(preset_id)
    assert preset is not None
    if step.params.get("plan"):
        plan_step = next(s for s in recipe.steps if s.key == step.params["plan"])
        listed = plan_step.params.get("modes")
        modes = list(preset.modes) if listed in (None, "all") else [m for m in listed if m in preset.modes]
        outputs = {step.params["plan"]: {"plan": {"preset_id": preset_id, "modes": modes}}}
    else:
        outputs = {}
    run = RecipeRun(id="r1", recipe_id=recipe.id, recipe_version=1, scope="instance", status=RecipeRunStatus.RUNNING)
    result = executor.execute(StepContext(run=run, recipe=recipe, step=step, step_outputs=outputs))
    assert result.success is True, result.safe_error_detail
