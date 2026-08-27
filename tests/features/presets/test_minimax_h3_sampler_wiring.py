"""Tests for sampler/scheduler wiring in the native MiniMax-H3 preset's
`video` mode: the Advanced tab's two selects must reach the one
`generator/video_minimax_h3` node, default to the reference euler/simple pair,
and offer exactly the values the pipe accepts.

Sibling of tests/features/presets/test_minimax_h3_manual_sigmas_wiring.py.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
import yaml

from src.features.presets import PresetTemplateLoader
from src.features.presets.processor import PresetProcessor
from src.platform.templating.processor import TemplateProcessor

_KEYS = ("sampler", "scheduler")


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


def _advanced_tab_fields():
    # Sampler/scheduler live on the Advanced tab (shared by `video` and `refs`
    # modes), like every other native preset's solver knobs -- not Generation.
    with open("content/presets/marketplace/MiniMax-H3/modes/video/tabs/advanced.yml") as handle:
        tab = yaml.safe_load(handle)

    def walk(fields):
        for field in fields:
            if field.get("type") in ("row", "section", "group"):
                yield from walk(field.get("children", []))
            else:
                yield field

    return {field["name"]: field for field in walk(tab["fields"]) if field.get("name")}


@pytest.mark.parametrize("sampler", ("euler", "res_multistep", "dpmpp_2m"))
def test_the_sampler_choice_reaches_the_generator(h3_template, sampler):
    assert _generator(_process(h3_template, {"sampler": sampler}))["config"]["sampler"] == sampler


@pytest.mark.parametrize("scheduler", ("simple", "beta"))
def test_the_scheduler_choice_reaches_the_generator(h3_template, scheduler):
    assert _generator(_process(h3_template, {"scheduler": scheduler}))["config"]["scheduler"] == scheduler


def test_an_untouched_form_renders_the_reference_pair(h3_template):
    cfg = _generator(_process(h3_template))["config"]
    assert cfg["sampler"] == "euler"
    assert cfg["scheduler"] == "simple"


def test_defaults_match_the_pipe_spec_defaults(h3_template):
    from src.pipelines.pipes.generator.video_minimax_h3.main import GeneratorMinimaxH3Pipe

    spec_defaults = {s.name: s.default for s in GeneratorMinimaxH3Pipe.configuration()}
    cfg = _generator(_process(h3_template))["config"]
    for key in _KEYS:
        assert cfg[key] == spec_defaults[key]


def test_the_offered_options_are_exactly_what_the_pipe_accepts():
    """A select offering a value the pipe rejects is a form that can only fail
    at generation time, so the two lists are compared rather than spot-checked."""
    from src.pipelines.pipes.generator.video_minimax_h3.samplers import SAMPLERS
    from src.pipelines.pipes.generator.video_minimax_h3.schedule import SCHEDULERS

    fields = _advanced_tab_fields()
    for key, accepted in (("sampler", SAMPLERS), ("scheduler", SCHEDULERS)):
        offered = tuple(o["value"] for o in fields[key]["configuration"]["options"])
        assert offered == accepted, key
        assert fields[key]["default"] == accepted[0]


def test_every_rendered_choice_passes_pipe_validation(h3_template):
    from src.pipelines.pipes.generator.video_minimax_h3.main import validate_minimax_h3_config
    from src.pipelines.pipes.generator.video_minimax_h3.samplers import SAMPLERS
    from src.pipelines.pipes.generator.video_minimax_h3.schedule import SCHEDULERS

    for sampler in SAMPLERS:
        for scheduler in SCHEDULERS:
            cfg = _generator(_process(h3_template, {"sampler": sampler, "scheduler": scheduler}))["config"]
            validate_minimax_h3_config(cfg, pipe_id="generator/video_minimax_h3")


def test_a_non_default_scheduler_with_manual_sigmas_is_refused(h3_template):
    """Both knobs answer "where do the knots go", so the combination is
    refused at validation rather than resolved by a silent precedence."""
    from src.pipelines.pipes.generator.video_minimax_h3.main import validate_minimax_h3_config

    cfg = _generator(_process(h3_template, {
        "scheduler": "beta", "manual_sigmas": "1.0, 0.8, 0.4, 0.0",
    }))["config"]
    with pytest.raises(ValueError, match="manual sigma grid"):
        validate_minimax_h3_config(cfg, pipe_id="generator/video_minimax_h3")


def test_the_advanced_tab_selects_carry_an_ai_hint_and_no_description():
    """Preset fields describe themselves with `label` + `ai_hint`; a
    `description` key is not part of the field contract here."""
    fields = _advanced_tab_fields()
    for key in _KEYS:
        assert fields[key]["ai_hint"].strip()
        assert "description" not in fields[key]


def test_the_turbo_profile_runs_the_modeltc_reference_grid():
    """The end of the chain for the profile that cares most: `speed_profiles.
    turbo.steps` must be an EVALUATION count that lands on ModelTC's published
    4-NFE schedules."""
    from src.pipelines.pipes.generator.video_minimax_h3.schedule import resolve_schedules

    with open("content/presets/marketplace/MiniMax-H3/preset.yml") as handle:
        turbo_steps = yaml.safe_load(handle)["speed_profiles"]["turbo"]["steps"]

    video, audio = resolve_schedules(turbo_steps)
    assert video.sigmas.tolist() == pytest.approx([1.0, 0.972973, 0.923077, 0.8, 0.0], abs=1e-5)
    assert audio.sigmas.tolist() == pytest.approx([1.0, 0.9, 0.75, 0.5, 0.0], abs=1e-5)
    assert video.timesteps.numel() == turbo_steps == 4
