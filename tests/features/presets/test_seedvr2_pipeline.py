"""Tests for the SeedVR2 `upscale` mode pipeline
(content/presets/marketplace/SeedVR2/modes/upscale/pipeline.yml).

SeedVR2 has no text encoder and no denoise loop, so unlike the other native image
presets there is no prompt_encoder and no seed_generator pipe -- the form seed is
passed straight into generator/seedvr2's own `seed` config. This asserts the
rendered pipe list is media_loader -> model_loader/seedvr2 -> generator/seedvr2 ->
gallery, wired with the right inputs/config values, and that it validates via
GenerationManager.validate_pipeline.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.generation.generation import GenerationManager
from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

from src.pipelines.pipes.gallery.main import GalleryPipe
from src.pipelines.pipes.generator.seedvr2.main import GeneratorSeedVR2Pipe
from src.pipelines.pipes.media_loader.main import MediaLoaderPipe
from src.pipelines.pipes.model_loader.seedvr2.main import ModelLoaderSeedVR2Pipe
from src.pipelines.pipes.param_emitter.main import ParamEmitterPipe


PIPE_CLASSES = {
    "media_loader": MediaLoaderPipe,
    "model_loader/seedvr2": ModelLoaderSeedVR2Pipe,
    "param_emitter": ParamEmitterPipe,
    "generator/seedvr2": GeneratorSeedVR2Pipe,
    "gallery": GalleryPipe,
}


@pytest.fixture(scope="module")
def seedvr2_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "native/SeedVR2" in str(p.path)), None)
    if template is None:
        pytest.skip("native/SeedVR2 preset not present")
    return template


def _process(seedvr2_template, form_over=None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "input_image": "/storage/uploads/source.png",
        "diffusion_model": "models/diffusion_models/seedvr2_ema_3b_fp16.safetensors",
        "vae": "models/vae/ema_vae_fp16.safetensors",
        "prompt_embedding": "models/text_encoders/pos_emb.pt",
        "scale": 2.0,
        "color_correction": "wavelet",
        "seed": -1,
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "upscale", "form_data": form_data}
    return processor.process(seedvr2_template, generation_data)


def _process_minimal(seedvr2_template, form_over=None):
    # Unlike _process, this omits scale/target_short_side/latent_noise_scale/
    # input_noise_scale/color_correction from the base fixture entirely --
    # _process bakes explicit values for those (so its callers exercise the
    # "form field present" path), which would shadow the resolution_target/
    # restoration_intent ternary DEFAULTS this suite is pinning.
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "input_image": "/storage/uploads/source.png",
        "diffusion_model": "models/diffusion_models/seedvr2_ema_3b_fp16.safetensors",
        "vae": "models/vae/ema_vae_fp16.safetensors",
        "prompt_embedding": "models/text_encoders/pos_emb.pt",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "upscale", "form_data": form_data}
    return processor.process(seedvr2_template, generation_data)


def _pipe(pipes, name=None, pipe_id=None):
    if pipe_id is not None:
        return next(p for p in pipes if p.get("id") == pipe_id)
    return next(p for p in pipes if p["name"] == name)


def _validate(pipes):
    manager = GenerationManager(
        gpu=Mock(), model_manager=Mock(), pipe_catalog=Mock(get_pipe=Mock(side_effect=PIPE_CLASSES.get)),
        settings_manager=Mock(), system_monitor=Mock(), memory_manager=Mock(),
        llm_service=Mock(), models=Mock(),
    )
    manager.validate_pipeline(pipes)


def test_pipe_order_and_names(seedvr2_template):
    pipes = _process(seedvr2_template)
    assert [p["name"] for p in pipes] == [
        "media_loader", "model_loader/seedvr2", "param_emitter", "generator/seedvr2", "gallery",
    ]
    assert all(p["enabled"] for p in pipes)


def test_media_loader_resolves_absolute_image_path(seedvr2_template):
    pipes = _process(seedvr2_template)
    media = _pipe(pipes, pipe_id="media_loader")
    assert media["config"]["media"] == [{"type": "image", "path": "/storage/uploads/source.png"}]


def test_model_loader_receives_the_three_components(seedvr2_template):
    pipes = _process(seedvr2_template)
    loader = _pipe(pipes, pipe_id="model_loader")
    cfg = loader["config"]
    assert cfg["diffusion_model"]["file_path"] == "models/diffusion_models/seedvr2_ema_3b_fp16.safetensors"
    assert cfg["vae"]["file_path"] == "models/vae/ema_vae_fp16.safetensors"
    assert cfg["prompt_embedding"]["file_path"] == "models/text_encoders/pos_emb.pt"


def test_generator_wired_to_media_loader_and_model_loader(seedvr2_template):
    pipes = _process(seedvr2_template)
    generator = _pipe(pipes, pipe_id="generator")
    inputs = {i["name"]: (i["provider"], i["output_var"]) for i in generator["input"]}
    assert inputs["image"] == ("media_loader", "image")
    assert inputs["model"] == ("model_loader", "model")


def test_generator_config_values(seedvr2_template):
    pipes = _process(seedvr2_template, {"scale": 3.0, "color_correction": "adain", "seed": 12345})
    generator = _pipe(pipes, pipe_id="generator")
    cfg = generator["config"]
    assert cfg["scale"] == 3.0
    assert cfg["color_correction"] == "adain"
    assert cfg["seed"] == 12345


def test_generator_config_defaults_when_form_omits_optional_fields(seedvr2_template):
    # the deliberate default change -- Output Size defaults to "Full HD
    # (1080p)" (form.resolution_target unset -> 'fhd1080'), so target_short_side
    # now defaults to 1080 (was 0 = "use scale" before this rework). scale still
    # falls back to preset.vars.default_scale (2.0) but is ignored by the
    # generator whenever target_short_side > 0.
    pipes = _process(seedvr2_template)
    cfg = _pipe(pipes, pipe_id="generator")["config"]
    assert cfg["scale"] == 2.0
    assert cfg["target_short_side"] == 1080
    assert cfg["latent_noise_scale"] == 0.0
    assert cfg["input_noise_scale"] == 0.0
    assert cfg["tile_size"] == 2048
    assert cfg["tile_overlap"] == 256


def test_gallery_reads_generator_output(seedvr2_template):
    pipes = _process(seedvr2_template)
    gallery = _pipe(pipes, pipe_id="gallery")
    image_input = next(i for i in gallery["input"] if i["name"] == "image")
    assert image_input["provider"] == "generator"
    assert image_input["output_var"] == "image"


def test_rendered_pipeline_validates(seedvr2_template):
    pipes = _process(seedvr2_template)
    _validate(pipes)


# -- "Output Size" / "Restoration Intent" select mappings ------------
#
# Both selects (Generation tab) are plain-language fronts for the raw
# scale/target_short_side/latent_noise_scale/input_noise_scale config keys
# (Advanced tab). The Advanced-tab fields carry reactions that bake the
# mapped value into the field itself when a user changes the select in the
# UI; these tests instead pin the SERVER-SIDE fallback in pipeline.yml (the
# ternary chain a reaction-less API caller relies on), mirroring
# test_wan_speed_profiles.py's harness style for the Wan speed_profile select.

_RESOLUTION_TARGET_MAP = [
    # (resolution_target, expected scale, expected target_short_side)
    ("x1_5", 1.5, 0),
    ("x2", 2.0, 0),
    ("hd720", 2.0, 720),
    ("fhd1080", 2.0, 1080),
    ("qhd1440", 2.0, 1440),
    ("uhd2160", 2.0, 2160),
]


@pytest.mark.parametrize("resolution_target,expected_scale,expected_short_side", _RESOLUTION_TARGET_MAP)
def test_resolution_target_maps_to_scale_and_short_side(
    seedvr2_template, resolution_target, expected_scale, expected_short_side
):
    pipes = _process_minimal(seedvr2_template, {"resolution_target": resolution_target})
    cfg = _pipe(pipes, pipe_id="generator")["config"]
    assert cfg["scale"] == expected_scale
    assert cfg["target_short_side"] == expected_short_side


def test_resolution_target_explicit_scale_and_short_side_win(seedvr2_template):
    # The Advanced-tab reactions lock scale/target_short_side to the selected
    # Output Size in the UI, but the server-side mapping is only a DEFAULT --
    # an explicit form submission (API caller, or UI with the lock bypassed)
    # always wins regardless of resolution_target.
    pipes = _process_minimal(seedvr2_template, {
        "resolution_target": "uhd2160", "scale": 3.5, "target_short_side": 999,
    })
    cfg = _pipe(pipes, pipe_id="generator")["config"]
    assert cfg["scale"] == 3.5
    assert cfg["target_short_side"] == 999


_RESTORATION_INTENT_MAP = [
    # (restoration_intent, expected latent_noise_scale, expected input_noise_scale)
    ("faithful", 0.0, 0.0),
    ("heavy", 0.2, 0.1),
]


@pytest.mark.parametrize("intent,expected_latent,expected_input", _RESTORATION_INTENT_MAP)
def test_restoration_intent_maps_to_noise_scales(seedvr2_template, intent, expected_latent, expected_input):
    pipes = _process_minimal(seedvr2_template, {"restoration_intent": intent})
    cfg = _pipe(pipes, pipe_id="generator")["config"]
    assert cfg["latent_noise_scale"] == expected_latent
    assert cfg["input_noise_scale"] == expected_input


def test_restoration_intent_explicit_noise_scales_win(seedvr2_template):
    pipes = _process_minimal(seedvr2_template, {
        "restoration_intent": "heavy", "latent_noise_scale": 0.55, "input_noise_scale": 0.33,
    })
    cfg = _pipe(pipes, pipe_id="generator")["config"]
    assert cfg["latent_noise_scale"] == 0.55
    assert cfg["input_noise_scale"] == 0.33


def test_resolution_and_intent_stay_native_typed(seedvr2_template):
    # Regression guard mirroring test_wan_speed_profiles.py's type-preservation
    # check: the nested ternary chains must render through TemplateProcessor's
    # single-expression path (native python types), not the string-render path.
    pipes = _process_minimal(seedvr2_template, {"resolution_target": "hd720", "restoration_intent": "heavy"})
    cfg = _pipe(pipes, pipe_id="generator")["config"]
    assert isinstance(cfg["scale"], float)
    assert isinstance(cfg["target_short_side"], int)
    assert isinstance(cfg["latent_noise_scale"], float)
    assert isinstance(cfg["input_noise_scale"], float)
