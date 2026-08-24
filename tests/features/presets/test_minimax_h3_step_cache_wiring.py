"""Tests for FBCache step-cache wiring in the native MiniMax-H3 preset's
`video` mode: the Advanced tab's three controls must reach the one
`generator/video_minimax_h3` node, and default to the pipe's own off values so
an untouched form renders exactly the pipeline it did before the controls
existed.

Mirrors tests/features/presets/test_ltx_step_cache_wiring.py. MiniMax-H3 has a
single generator node (no upscale/refine second stage), so there is no
per-stage split here.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

_CACHE_ON = {
    "step_cache_threshold": 0.12,
    "step_cache_warmup_steps": 6,
    "step_cache_max_skips": 2,
}

_KEYS = ("step_cache_threshold", "step_cache_warmup_steps", "step_cache_max_skips")


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
        template_processor=TemplateProcessor(settings_manager=Mock()),
        model_manager=Mock(),
        settings_manager=Mock(),
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


def test_step_cache_values_reach_the_generator(h3_template):
    generator = _generator(_process(h3_template, _CACHE_ON))
    assert generator["enabled"] is True
    cfg = generator["config"]
    assert cfg["step_cache_threshold"] == 0.12
    assert cfg["step_cache_warmup_steps"] == 6
    assert cfg["step_cache_max_skips"] == 2


def test_step_cache_values_arrive_as_the_declared_types(h3_template):
    cfg = _generator(_process(h3_template, _CACHE_ON))["config"]
    assert isinstance(cfg["step_cache_threshold"], float)
    assert isinstance(cfg["step_cache_warmup_steps"], int)
    assert isinstance(cfg["step_cache_max_skips"], int)


def test_step_cache_defaults_to_off(h3_template):
    cfg = _generator(_process(h3_template))["config"]
    assert cfg["step_cache_threshold"] == 0.0
    assert cfg["step_cache_warmup_steps"] == 4
    assert cfg["step_cache_max_skips"] == 3


def test_step_cache_defaults_match_the_pipe_spec_defaults(h3_template):
    """The preset must not invent its own defaults: pin them to the specs the
    generator actually declares."""
    from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe

    spec_defaults = {s.name: s.default for s in GeneratorMinimaxH3Pipe.configuration()}
    cfg = _generator(_process(h3_template))["config"]
    for key in _KEYS:
        assert cfg[key] == spec_defaults[key]


def test_default_render_builds_no_cache(h3_template):
    """The end of the chain: an untouched form renders a config the pipe turns
    into no cache at all, so nothing about sampling changes."""
    from src.pipelines.pipes.generator.video_minimax_h3.main import build_step_cache

    assert build_step_cache(_generator(_process(h3_template))["config"]) is None
