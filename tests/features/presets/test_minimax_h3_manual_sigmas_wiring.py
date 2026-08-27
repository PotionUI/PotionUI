"""Tests for manual sigma-schedule wiring in the native MiniMax-H3 preset's
`video` mode: the Advanced tab's two textboxes must reach the one
`generator/video_minimax_h3` node, and default to the pipe's own empty values
so an untouched form still renders the computed schedules.

Sibling of tests/features/presets/test_minimax_h3_step_cache_wiring.py.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

_KEYS = ("manual_sigmas", "manual_audio_sigmas")


@pytest.fixture(scope="module")
def h3_template():
    loader = PresetTemplateLoader(["content/presets"])
    loader.load_presets()
    template = next((p for p in loader.presets if "MiniMax-H3" in str(p.path)), None)
    if template is None:
        pytest.skip("native/MiniMax-H3 preset not present")
    return template


def _process(h3_template, form_over: dict | None = None):
    processor = PresetProcessor(
        template_processor=TemplateProcessor(settings=Mock()),
        model_directories=Mock(),
        settings=Mock(),
        preset_template_loader=Mock(),
    )
    form_data = {
        "model": "/models/minimax_h3.safetensors",
        "text_encoder": "/models/qwen3_vl.safetensors",
        "video_vae": "/models/h3_video_vae.safetensors",
        "audio_vae": "/models/h3_audio_vae.safetensors",
        "resolution": "1344x768",
        "prompt": "a dragon",
    }
    if form_over:
        form_data.update(form_over)
    generation_data = {
        "prompts": [{"positive": "a dragon", "negative": ""}],
        "mode": "video",
        "form_data": form_data,
    }
    return processor.process(h3_template, generation_data)


def _generator(pipes):
    return next(p for p in pipes if p["name"] == "generator/video_minimax_h3")


def test_manual_sigmas_reach_the_generator(h3_template):
    cfg = _generator(_process(h3_template, {
        "manual_sigmas": "1.0, 0.8, 0.4, 0.0",
        "manual_audio_sigmas": "0.95, 0.6, 0.2, 0.0",
    }))["config"]
    assert cfg["manual_sigmas"] == "1.0, 0.8, 0.4, 0.0"
    assert cfg["manual_audio_sigmas"] == "0.95, 0.6, 0.2, 0.0"


def test_manual_sigmas_default_to_blank(h3_template):
    cfg = _generator(_process(h3_template))["config"]
    for key in _KEYS:
        assert cfg[key] == ""


def test_manual_sigmas_defaults_match_the_pipe_spec_defaults(h3_template):
    from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe

    spec_defaults = {s.name: s.default for s in GeneratorMinimaxH3Pipe.configuration()}
    cfg = _generator(_process(h3_template))["config"]
    for key in _KEYS:
        assert cfg[key] == spec_defaults[key]


def test_default_render_still_builds_the_computed_schedules(h3_template):
    """The end of the chain: an untouched form renders a config that resolves
    to the shift-12/shift-3 pair, sized by `steps`."""
    from src.pipelines.pipes.generator.video_minimax_h3.schedule import (
        AUDIO_SHIFT,
        VIDEO_SHIFT,
        build_sigma_schedule,
        resolve_schedules,
    )
    import torch

    cfg = _generator(_process(h3_template))["config"]
    steps = int(cfg["steps"])
    video, audio = resolve_schedules(steps, cfg["manual_sigmas"], cfg["manual_audio_sigmas"])
    torch.testing.assert_close(video.sigmas, build_sigma_schedule(steps, VIDEO_SHIFT).sigmas, rtol=0, atol=0)
    torch.testing.assert_close(audio.sigmas, build_sigma_schedule(steps, AUDIO_SHIFT).sigmas, rtol=0, atol=0)


def test_a_rendered_manual_schedule_passes_pipe_validation(h3_template):
    """The rendered strings must survive the pipe's own config validation --
    the format the field documents and the format the pipe accepts are one
    contract, not two."""
    from src.pipelines.pipes.generator.video_minimax_h3.main import validate_minimax_h3_config

    cfg = _generator(_process(h3_template, {"manual_sigmas": "1.0, 0.8, 0.4, 0.0"}))["config"]
    validate_minimax_h3_config(cfg, pipe_id="generator/video_minimax_h3")
