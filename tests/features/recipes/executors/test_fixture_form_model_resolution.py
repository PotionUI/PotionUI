"""`_fixture_form.py`'s real-model resolution seam.

`pipeline.render` and `generation.smoke` share `build_fixture_form_data`, but
only `generation.smoke` passes `recipe`/`model_repository` - the maintainer's
sandbox run traced a crash to `generation.smoke` handing the SDXL loader the
dry-run placeholder `/SETUP-CHECK/models/model.safetensors` instead of a real
checkpoint path. These tests exercise both halves against the real SDXL
preset tree (the same fixtures `test_pipeline_render.py` uses):

- `pipeline.render`'s call shape (no `recipe`) must keep using the sentinel -
  it's a template-rendering-only dry run, nothing ever loads that path.
- `generation.smoke`'s call shape (`recipe` + `model_repository`) must
  resolve model-typed fields to a real indexed model's `file_path`, fail
  loudly by name when a REQUIRED one (the recipe's own artifact) isn't
  indexed, and clear an unresolvable OPTIONAL one (no matching recipe
  artifact) to `""` rather than ever leaking a placeholder into a real
  generation.

Lower-level unit tests against `_resolve_model_fields` / `_collect_model_field_specs`
directly (with plain dict-shaped fake fields, no preset tree) cover the
resolution rules in isolation.
"""

import pytest

from scripts.preset_render import StubSettings, build_processor
from src.features.presets.loader import PresetTemplateLoader
from src.features.recipes.executors._fixture_form import (
    RequiredModelMissing,
    _collect_model_field_specs,
    _inject_unresolvable_defaults,
    _resolve_model_fields,
    build_fixture_form_data,
)
from src.features.recipes.schema import Recipe, RecipeArtifact
from src.platform.templating import TemplateProcessor

SDXL_PRESET_ID = "01K0W24A3RADXXABH16YQ7KE90"
CHECKPOINT_FILENAME = "cyberrealisticPony_v180Coreshift.safetensors"


class FakeModel:
    def __init__(self, file_path):
        self.file_path = file_path


class FakeModelRepository:
    def __init__(self, by_identity=None):
        self._by_identity = dict(by_identity or {})

    def get_by_identity(self, model_type, filename, include_providers=True):
        return self._by_identity.get((model_type, filename))


def _sdxl_recipe(required=True):
    artifact = RecipeArtifact(
        id="sdxl-checkpoint",
        kind="checkpoint",
        model_type="checkpoint",
        filename=CHECKPOINT_FILENAME,
        display_name="CyberRealistic Pony v18.0 Coreshift",
        required=required,
    )
    return Recipe(
        id="sdxl-starter", schema_version=1, version=1, name="SDXL Starter", engine="native",
        artifacts=[artifact],
    )


@pytest.fixture(scope="module")
def loader():
    preset_loader = PresetTemplateLoader(["content/presets"])
    preset_loader.load_presets()
    return preset_loader


@pytest.fixture(scope="module")
def template_processor():
    return TemplateProcessor(settings=StubSettings())


@pytest.fixture(scope="module")
def sdxl_preset(loader):
    return loader.load_preset_by_id(SDXL_PRESET_ID)


# --- pipeline.render's call shape: sentinel untouched -----------------------


def test_without_a_recipe_the_model_field_keeps_the_dry_run_sentinel(loader, sdxl_preset, template_processor):
    """This is exactly how `PipelineRenderExecutor` calls it - no `recipe`,
    no `model_repository` (see `pipeline_render.py`) - so it must keep
    resolving nothing for real."""
    form_data = build_fixture_form_data(
        sdxl_preset, "txt2img",
        preset_template_loader=loader, template_processor=template_processor,
    )

    assert form_data["model"] == "/SETUP-CHECK/models/model.safetensors"


def test_a_required_multi_media_field_gets_a_LIST_placeholder():
    """A `configuration.multi` media field's own validator rejects a scalar,
    so a flat placeholder would fail the dry run on a field that binds fine in
    production.

    MiniMax-H3's `refs` mode `references` field was the shipped case this
    test originally pinned to, but it stopped being individually `required`
    once `reference_videos`/`reference_audios` arrived alongside it (see
    `content/presets/marketplace/MiniMax-H3/modes/refs/tabs/references.yml` -
    the constraint is now "at least one of the three", which no single
    field's `required: true` can express) - not a bug, so this exercises
    `_inject_unresolvable_defaults` directly with a synthetic field instead
    of a preset that may change shape again."""
    fields = [{
        "name": "references", "type": "image", "required": True,
        "configuration": {"multi": True},
    }]
    form_data = {}

    _inject_unresolvable_defaults(fields, form_data)

    assert form_data["references"] == ["/SETUP-CHECK/media/references.png"]


# --- generation.smoke's call shape: real resolution -------------------------


def test_the_main_checkpoint_field_resolves_to_the_real_indexed_path(loader, sdxl_preset, template_processor):
    repo = FakeModelRepository({
        ("checkpoint", CHECKPOINT_FILENAME): FakeModel("/models/checkpoints/" + CHECKPOINT_FILENAME),
    })

    form_data = build_fixture_form_data(
        sdxl_preset, "txt2img",
        preset_template_loader=loader, template_processor=template_processor,
        recipe=_sdxl_recipe(), model_repository=repo,
    )

    assert form_data["model"] == "/models/checkpoints/" + CHECKPOINT_FILENAME
    assert "SETUP-CHECK" not in form_data["model"]


def test_missing_required_checkpoint_fails_loud_naming_the_file(loader, sdxl_preset, template_processor):
    repo = FakeModelRepository({})  # nothing indexed yet

    with pytest.raises(RequiredModelMissing) as exc_info:
        build_fixture_form_data(
            sdxl_preset, "txt2img",
            preset_template_loader=loader, template_processor=template_processor,
            recipe=_sdxl_recipe(), model_repository=repo,
        )

    assert CHECKPOINT_FILENAME in str(exc_info.value)
    assert exc_info.value.filename == CHECKPOINT_FILENAME


def test_optional_pickers_without_a_recipe_artifact_are_cleared_not_faked(loader, sdxl_preset, template_processor):
    """SDXL's embedding/controlnet/upscaler/detection_bbox/mediapipe pickers
    have no matching artifact in a recipe that only declares the checkpoint -
    they must become "" (so the preset's own non-empty `enabled:` gates skip
    them, e.g. `pipeline.yml`'s embedding stages), never a `/SETUP-CHECK`
    path a pipe might load unconditionally."""
    repo = FakeModelRepository({
        ("checkpoint", CHECKPOINT_FILENAME): FakeModel("/models/checkpoints/" + CHECKPOINT_FILENAME),
    })

    form_data = build_fixture_form_data(
        sdxl_preset, "txt2img",
        preset_template_loader=loader, template_processor=template_processor,
        recipe=_sdxl_recipe(), model_repository=repo,
    )

    for name in ("positive_embedding_1", "negative_embedding_1"):
        assert form_data[name] == ""


# --- unit tests against the resolution helpers directly ---------------------


def _field(name, model_type="checkpoint"):
    return {"name": name, "type": "model", "configuration": {"model_type": model_type}}


def test_collect_model_field_specs_recurses_into_children():
    fields = [
        _field("model"),
        {"name": "tab", "type": "tab", "children": [_field("upscaler_model", "upscaler")]},
        {"name": "loras", "type": "lora_picker"},  # excluded - not a MODEL_FIELD_TYPES entry
    ]

    specs = _collect_model_field_specs(fields)

    assert {"name": "model", "model_type": "checkpoint", "recommended": set()} in specs
    assert {"name": "upscaler_model", "model_type": "upscaler", "recommended": set()} in specs
    assert len(specs) == 2


def test_resolve_model_fields_leaves_an_explicit_default_untouched():
    """A field that already carries a real value (from `_collect_named_fields`
    - an explicit preset `default`) is never a sentinel, so resolution must
    not touch it even if the recipe declares a DIFFERENT artifact of that
    model_type."""
    fields = [_field("model")]
    form_data = {"model": "/models/checkpoints/already-set.safetensors"}
    recipe = _sdxl_recipe()
    repo = FakeModelRepository({(
        "checkpoint", CHECKPOINT_FILENAME): FakeModel("/models/checkpoints/" + CHECKPOINT_FILENAME)})

    _resolve_model_fields(fields, form_data, recipe=recipe, model_repository=repo)

    assert form_data["model"] == "/models/checkpoints/already-set.safetensors"


def test_resolve_model_fields_is_a_noop_with_no_model_fields():
    """No exception, no model_repository calls, when there's simply nothing
    to resolve."""
    _resolve_model_fields([], {}, recipe=_sdxl_recipe(), model_repository=None)


def _variant_recipe():
    from src.features.recipes.schema import RecipeArtifactVariant

    def variant(vid, default=False):
        return RecipeArtifactVariant(
            id=vid, label=vid, precision="fp8", filename=f"{vid}.safetensors", default=default
        )

    slot = RecipeArtifact(
        id="dit",
        kind="diffusion_model",
        model_type="diffusion_model",
        filename="fp8.safetensors",
        display_name="DiT",
        variants=(variant("bf16"), variant("fp8", default=True), variant("nvfp4")),
    )
    return Recipe(id="v", schema_version=1, version=1, name="V", engine="native", artifacts=[slot])


def _sentinel_form():
    return {"diffusion_model": "/SETUP-CHECK/models/diffusion_model.safetensors"}


def test_resolve_model_fields_uses_the_selected_variant():
    repo = FakeModelRepository({
        ("diffusion_model", "fp8.safetensors"): FakeModel("/m/fp8.safetensors"),
        ("diffusion_model", "nvfp4.safetensors"): FakeModel("/m/nvfp4.safetensors"),
    })
    form_data = _sentinel_form()

    _resolve_model_fields(
        [_field("diffusion_model", "diffusion_model")],
        form_data,
        recipe=_variant_recipe(),
        model_repository=repo,
        selections={"dit": "nvfp4"},
    )

    assert form_data["diffusion_model"] == "/m/nvfp4.safetensors"


def test_resolve_model_fields_accepts_any_installed_variant_without_a_selection():
    repo = FakeModelRepository({("diffusion_model", "bf16.safetensors"): FakeModel("/m/bf16.safetensors")})
    form_data = _sentinel_form()

    _resolve_model_fields(
        [_field("diffusion_model", "diffusion_model")],
        form_data,
        recipe=_variant_recipe(),
        model_repository=repo,
    )

    assert form_data["diffusion_model"] == "/m/bf16.safetensors"


def test_resolve_model_fields_fails_when_no_variant_is_installed():
    with pytest.raises(RequiredModelMissing):
        _resolve_model_fields(
            [_field("diffusion_model", "diffusion_model")],
            _sentinel_form(),
            recipe=_variant_recipe(),
            model_repository=FakeModelRepository(),
            selections={"dit": "bf16"},
        )


def test_two_artifacts_of_one_model_type_go_to_the_fields_that_recommend_them():
    video = RecipeArtifact(id="video-vae", kind="vae", model_type="vae", filename="video_vae.safetensors", display_name="Video VAE")
    audio = RecipeArtifact(id="audio-vae", kind="vae", model_type="vae", filename="audio_vae.safetensors", display_name="Audio VAE")
    recipe = Recipe(id="h3", schema_version=1, version=1, name="H3", engine="native", artifacts=[video, audio])
    fields = [
        {"name": "vae", "type": "model", "configuration": {"model_type": "vae", "recommendations": [{"name": "video_vae.safetensors"}]}},
        {"name": "audio_vae", "type": "model", "configuration": {"model_type": "vae", "recommendations": [{"name": "audio_vae.safetensors"}]}},
    ]
    repo = FakeModelRepository({
        ("vae", "video_vae.safetensors"): FakeModel("/m/video_vae.safetensors"),
        ("vae", "audio_vae.safetensors"): FakeModel("/m/audio_vae.safetensors"),
    })
    form_data = {
        "vae": "/SETUP-CHECK/models/vae.safetensors",
        "audio_vae": "/SETUP-CHECK/models/audio_vae.safetensors",
    }

    _resolve_model_fields(fields, form_data, recipe=recipe, model_repository=repo)

    assert form_data == {"vae": "/m/video_vae.safetensors", "audio_vae": "/m/audio_vae.safetensors"}
