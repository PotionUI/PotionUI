"""`generation.smoke` against a fake orchestrator - never a real backend/GPU
(see the executor's own docstring). Fixture-form construction is monkeypatched
out; it's exercised for real by `test_pipeline_render.py`'s shared helper."""

from types import SimpleNamespace

import pytest

import src.features.setup.executors.generation_smoke as generation_smoke
from src.features.setup.executors.base import StepContext
from src.features.setup.executors.generation_smoke import GenerationSmokeExecutor
from src.features.setup.recipe_schema import Recipe, RecipeSmokeRef, RecipeStep
from src.features.setup.records import SetupRun, SetupRunStatus


class FakePresetTemplate:
    def __init__(self, name="SDXL Starter", modes=("txt2img",)):
        self.name = name
        self.modes = {m: object() for m in modes}


class FakePresetLoader:
    def __init__(self, template):
        self.template = template

    def load_preset_by_id(self, preset_id):
        return self.template


class FakeFileRepository:
    def __init__(self, files=()):
        self.files = list(files)

    def get_generation_files(self, generation_id, user_id=None, file_type=None, is_final=None):
        return self.files


class FakeOrchestrator:
    """`start_generation` immediately calls the collector with one saved
    image, then a completion signal - so the executor's wait loop returns on
    its first check, no sleeping."""

    def __init__(self, images=1, error=None):
        self.images = images
        self.error = error

    async def start_generation(self, request, user_id, output_callback):
        from src.pipelines.outputs import ErrorGenerationOutput, ImageGenerationOutput

        if self.error:
            await output_callback("gen-1", ErrorGenerationOutput(error=self.error))
        else:
            for _ in range(self.images):
                await output_callback("gen-1", ImageGenerationOutput(image=None, temporary=False))
        await output_callback("gen-1", None)
        return {"generation_id": "gen-1"}

    async def get_generation_status(self, generation_id):
        return {"status": "failed" if self.error else "completed"}


def _context(recipe):
    run = SetupRun(id="r1", recipe_id="x", recipe_version=1, scope="instance", status=SetupRunStatus.RUNNING, created_by="owner-1")
    step = next(s for s in recipe.steps if s.kind == "generation.smoke")
    return StepContext(run=run, recipe=recipe, step=step)


def _recipe(smoke=None):
    return Recipe(
        id="x",
        schema_version=1,
        version=1,
        name="X",
        engine="native",
        smoke=smoke or RecipeSmokeRef(preset_id="p1", mode="txt2img"),
        steps=[RecipeStep(key="generation.smoke", kind="generation.smoke", title="Smoke", params={"preset_id": "p1", "mode": "txt2img"})],
    )


@pytest.fixture(autouse=True)
def _fake_fixture_form(monkeypatch):
    monkeypatch.setattr(generation_smoke, "build_fixture_form_data", lambda *a, **k: {"seed": 1})


def test_successful_generation_reports_generation_id_and_file():
    template = FakePresetTemplate()
    loader = FakePresetLoader(template)
    orchestrator = FakeOrchestrator(images=1)
    file_repo = FakeFileRepository(files=[SimpleNamespace(file_path="images/gen-1/out.png")])
    executor = GenerationSmokeExecutor(loader, object(), orchestrator, file_repo)

    result = executor.execute(_context(_recipe()))

    assert result.success is True
    assert result.safe_output["generation_id"] == "gen-1"
    assert result.safe_output["output_count"] == 1
    assert result.safe_output["filename"] == "out.png"
    assert result.safe_output["file_path"] == "images/gen-1/out.png"


def test_generation_error_fails_with_plain_message_and_repair():
    template = FakePresetTemplate()
    loader = FakePresetLoader(template)
    orchestrator = FakeOrchestrator(error="backend unavailable")
    executor = GenerationSmokeExecutor(loader, object(), orchestrator, FakeFileRepository())

    result = executor.execute(_context(_recipe()))

    assert result.success is False
    assert result.error_code == "GENERATION_SMOKE_FAILED"
    assert "backend unavailable" in result.safe_error_detail
    assert result.suggested_repair


def test_missing_preset_fails_clearly():
    loader = FakePresetLoader(None)
    executor = GenerationSmokeExecutor(loader, object(), FakeOrchestrator(), FakeFileRepository())

    result = executor.execute(_context(_recipe()))

    assert result.success is False
    assert result.error_code == "PRESET_MISSING_ON_DISK"


def test_unknown_mode_fails_clearly():
    template = FakePresetTemplate(modes=("img2img",))
    loader = FakePresetLoader(template)
    executor = GenerationSmokeExecutor(loader, object(), FakeOrchestrator(), FakeFileRepository())

    result = executor.execute(_context(_recipe()))

    assert result.success is False
    assert result.error_code == "MODE_NOT_FOUND"


# --- a missing real model fails loud, before the orchestrator ------


def test_required_model_missing_fails_up_front_by_name(monkeypatch):
    """`build_fixture_form_data` raising `RequiredModelMissing` (see
    `_fixture_form.py` - real resolution against the recipe's declared
    artifacts) must fail the step with the missing file named, and must never
    reach the orchestrator (contrast `test_generation_error_fails_with_plain_
    message_and_repair`, whose message is about a real orchestrator failure -
    this must not say anything about a backend)."""
    from src.features.setup.executors._fixture_form import RequiredModelMissing

    def _raise(*a, **k):
        raise RequiredModelMissing("CyberRealistic Pony v18.0", "cyberrealisticPony.safetensors")

    monkeypatch.setattr(generation_smoke, "build_fixture_form_data", _raise)

    template = FakePresetTemplate()
    loader = FakePresetLoader(template)
    orchestrator = FakeOrchestrator()
    executor = GenerationSmokeExecutor(loader, object(), orchestrator, FakeFileRepository())

    result = executor.execute(_context(_recipe()))

    assert result.success is False
    assert result.error_code == "GENERATION_SMOKE_MODEL_MISSING"
    assert "cyberrealisticPony.safetensors" in result.safe_error_detail
    assert "CyberRealistic Pony v18.0" in result.safe_error_detail
    # Never the backend-not-running message this failure mode used to be
    # lumped into (see GENERATION_SMOKE_FAILED's suggested_repair below).
    assert "backend" not in result.safe_error_detail.lower()
    assert result.suggested_repair


def test_model_repository_and_recipe_are_passed_to_form_building(monkeypatch):
    """The executor must hand its own `model_repository` and the step's
    `recipe` through to `build_fixture_form_data` - this is the seam that
    turns real-model resolution on (see `_fixture_form.build_fixture_form_data`'s
    docstring: `pipeline.render` never passes `recipe`, only this does)."""
    captured = {}

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return {"seed": 1}

    monkeypatch.setattr(generation_smoke, "build_fixture_form_data", _capture)

    template = FakePresetTemplate()
    loader = FakePresetLoader(template)
    sentinel_repo = object()
    executor = GenerationSmokeExecutor(loader, object(), FakeOrchestrator(), FakeFileRepository(), sentinel_repo)

    recipe = _recipe()
    executor.execute(_context(recipe))

    assert captured["recipe"] is recipe
    assert captured["model_repository"] is sentinel_repo


def test_default_model_repository_is_lazily_constructed():
    """No `model_repository` passed - the constructor must fall back to the
    real singleton (mirrors `file_repository`'s existing default) rather than
    leaving `self.model_repository` `None` and silently skipping resolution."""
    executor = GenerationSmokeExecutor(FakePresetLoader(None), object(), FakeOrchestrator())

    assert executor.model_repository is not None
