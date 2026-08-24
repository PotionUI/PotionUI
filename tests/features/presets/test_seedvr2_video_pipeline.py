"""Tests for the SeedVR2 `video_upscale` mode pipeline
(content/presets/marketplace/SeedVR2/modes/video_upscale/pipeline.yml).

Mirrors test_seedvr2_pipeline.py for the video mode: the rendered pipe list is
media_loader (video) -> model_loader/seedvr2 -> generator/seedvr2 -> gallery,
the generator gets the VIDEO input plus the temporal-batching config, and the
whole thing validates via GenerationManager.validate_pipeline.
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
        "input_video": "/storage/uploads/source.mp4",
        "diffusion_model": "models/diffusion_models/seedvr2_ema_3b_fp16.safetensors",
        "vae": "models/vae/ema_vae_fp16.safetensors",
        "prompt_embedding": "models/clip/pos_emb.pt",
        "scale": 2.0,
        "color_correction": "wavelet",
        "seed": -1,
        "batch_size": 5,
        "temporal_overlap": 2,
        "prepend_frames": 4,
        "uniform_batch_size": True,
        "keep_audio": True,
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "video_upscale", "form_data": form_data}
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
        "input_video": "/storage/uploads/source.mp4",
        "diffusion_model": "models/diffusion_models/seedvr2_ema_3b_fp16.safetensors",
        "vae": "models/vae/ema_vae_fp16.safetensors",
        "prompt_embedding": "models/clip/pos_emb.pt",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {"prompts": [], "mode": "video_upscale", "form_data": form_data}
    return processor.process(seedvr2_template, generation_data)


def _pipe(pipes, pipe_id):
    return next(p for p in pipes if p.get("id") == pipe_id)


def test_video_pipe_order_and_names(seedvr2_template):
    pipes = _process(seedvr2_template)
    assert [p["name"] for p in pipes] == [
        "media_loader", "model_loader/seedvr2", "param_emitter", "generator/seedvr2", "gallery",
    ]
    assert all(p["enabled"] for p in pipes)


def test_media_loader_carries_the_video(seedvr2_template):
    pipes = _process(seedvr2_template)
    media = _pipe(pipes, "media_loader")
    assert media["config"]["media"] == [{"type": "video", "path": "/storage/uploads/source.mp4"}]


def test_generator_gets_video_input_and_temporal_config(seedvr2_template):
    pipes = _process(seedvr2_template)
    generator = _pipe(pipes, "generator")
    inputs = {i["name"]: (i["provider"], i["output_var"]) for i in generator["input"]}
    assert inputs["video"] == ("media_loader", "video")
    assert inputs["model"] == ("model_loader", "model")
    cfg = generator["config"]
    assert float(cfg["scale"]) == 2.0
    assert int(cfg["batch_size"]) == 5
    assert int(cfg["temporal_overlap"]) == 2
    assert int(cfg["prepend_frames"]) == 4
    assert str(cfg["uniform_batch_size"]).lower() in ("true", "1")
    assert str(cfg["keep_audio"]).lower() in ("true", "1")


def test_batch_size_defaults_to_zero_auto_when_form_omits_it(seedvr2_template):
    # an omitted Batch Size renders 0 = "auto-size to free VRAM"
    # (the generator's shrink-on-OOM ladder is the safety net for the estimate).
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "input_video": "/storage/uploads/source.mp4",
        "diffusion_model": "models/diffusion_models/seedvr2_ema_3b_fp16.safetensors",
        "vae": "models/vae/ema_vae_fp16.safetensors",
        "prompt_embedding": "models/clip/pos_emb.pt",
    }
    pipes = processor.process(
        seedvr2_template, {"prompts": [], "mode": "video_upscale", "form_data": form_data}
    )
    cfg = _pipe(pipes, "generator")["config"]
    assert int(cfg["batch_size"]) == 0


def test_gallery_reads_generator_video(seedvr2_template):
    pipes = _process(seedvr2_template)
    gallery = _pipe(pipes, "gallery")
    inputs = {i["name"]: (i["provider"], i["output_var"]) for i in gallery["input"]}
    assert inputs["video"] == ("generator", "video")


def test_video_pipeline_validates(seedvr2_template):
    pipes = _process(seedvr2_template)
    manager = GenerationManager(
        gpu=Mock(), model_manager=Mock(),
        pipe_catalog=Mock(get_pipe=Mock(side_effect=PIPE_CLASSES.get)),
        settings_manager=Mock(), system_monitor=Mock(), memory_manager=Mock(),
        llm_service=Mock(), models=Mock(),
    )
    manager.validate_pipeline(pipes)


# -- "Output Size" / "Restoration Intent" select mappings ------------
#
# Same selects and the same server-side ternary fallback as the image
# 'upscale' mode (see test_seedvr2_pipeline.py's mirror of this block) --
# the video pipeline.yml duplicates the identical mapping expressions.

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
    cfg = _pipe(pipes, "generator")["config"]
    assert cfg["scale"] == expected_scale
    assert cfg["target_short_side"] == expected_short_side


def test_resolution_target_explicit_scale_and_short_side_win(seedvr2_template):
    pipes = _process_minimal(seedvr2_template, {
        "resolution_target": "uhd2160", "scale": 3.5, "target_short_side": 999,
    })
    cfg = _pipe(pipes, "generator")["config"]
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
    cfg = _pipe(pipes, "generator")["config"]
    assert cfg["latent_noise_scale"] == expected_latent
    assert cfg["input_noise_scale"] == expected_input


def test_restoration_intent_explicit_noise_scales_win(seedvr2_template):
    pipes = _process_minimal(seedvr2_template, {
        "restoration_intent": "heavy", "latent_noise_scale": 0.55, "input_noise_scale": 0.33,
    })
    cfg = _pipe(pipes, "generator")["config"]
    assert cfg["latent_noise_scale"] == 0.55
    assert cfg["input_noise_scale"] == 0.33


def test_resolution_and_intent_stay_native_typed(seedvr2_template):
    pipes = _process_minimal(seedvr2_template, {"resolution_target": "hd720", "restoration_intent": "heavy"})
    cfg = _pipe(pipes, "generator")["config"]
    assert isinstance(cfg["scale"], float)
    assert isinstance(cfg["target_short_side"], int)
    assert isinstance(cfg["latent_noise_scale"], float)
    assert isinstance(cfg["input_noise_scale"], float)
