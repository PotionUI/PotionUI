"""Tests for the LTX cross-field config guard.

``validate_ltx_schedule_config`` (shared by ``generator/txt2vid_ltx`` and
``generator/video_ltx``) rejects degenerate sampler/schedule/steps
combinations that each pass their own ``PipeConfigSpec`` individually but are
nonsensical together -- a form left with an empty sampler, or a
``schedule='manual'`` left without matching sigma values, used to reach
``build_sigmas`` or the DiT connector as a cryptic crash instead of a clean
validation error.
"""

from __future__ import annotations

import pytest

from src.features.generation.generation import validate_pipe_configuration
from src.pipelines.pipes.generator.txt2vid_ltx.main import (
    GeneratorLtxTxt2VidPipe,
    validate_ltx_schedule_config,
)
from src.pipelines.pipes.generator.video_ltx.main import GeneratorLtxVideoPipe


def _base_config(**over):
    config = {
        "sampler": "euler",
        "steps": 24,
        "schedule": "",
        "schedule_options": {},
        "manual_sigmas": "",
    }
    config.update(over)
    return config


# -- direct unit tests on the shared helper ----------------------------------

def test_valid_default_config_passes():
    validate_ltx_schedule_config(_base_config(), pipe_id="generator/txt2vid_ltx")


def test_empty_sampler_raises():
    with pytest.raises(ValueError, match="'sampler' is required and cannot be empty"):
        validate_ltx_schedule_config(_base_config(sampler=""), pipe_id="generator/txt2vid_ltx")


def test_none_sampler_raises():
    with pytest.raises(ValueError, match="'sampler' is required and cannot be empty"):
        validate_ltx_schedule_config(_base_config(sampler=None), pipe_id="generator/txt2vid_ltx")


def test_schedule_manual_without_sigmas_raises():
    with pytest.raises(ValueError, match="schedule='manual' requires 'manual_sigmas'"):
        validate_ltx_schedule_config(_base_config(schedule="manual"), pipe_id="generator/txt2vid_ltx")


def test_schedule_manual_with_manual_sigmas_passes():
    validate_ltx_schedule_config(
        _base_config(schedule="manual", manual_sigmas="1.0, 0.5, 0.0"),
        pipe_id="generator/txt2vid_ltx",
    )


def test_schedule_manual_with_schedule_options_sigmas_passes():
    validate_ltx_schedule_config(
        _base_config(schedule="manual", schedule_options={"sigmas": [1.0, 0.5, 0.0]}),
        pipe_id="generator/txt2vid_ltx",
    )


def test_manual_sigmas_non_numeric_raises():
    with pytest.raises(ValueError, match="non-numeric value"):
        validate_ltx_schedule_config(_base_config(manual_sigmas="1.0, banana, 0.0"), pipe_id="generator/txt2vid_ltx")


def test_manual_sigmas_single_value_raises():
    with pytest.raises(ValueError, match="needs at least 2 sigma values"):
        validate_ltx_schedule_config(_base_config(manual_sigmas="1.0"), pipe_id="generator/txt2vid_ltx")


def test_manual_sigmas_increasing_raises():
    with pytest.raises(ValueError, match="must be non-increasing"):
        validate_ltx_schedule_config(_base_config(manual_sigmas="0.5, 1.0, 0.0"), pipe_id="generator/txt2vid_ltx")


def test_manual_sigmas_valid_descending_passes():
    validate_ltx_schedule_config(
        _base_config(manual_sigmas="1.0, 0.99375, 0.9875, 0.0"), pipe_id="generator/txt2vid_ltx"
    )


def test_steps_one_without_manual_sigmas_raises():
    # repro: steps=1 with the default multi-sigma schedule and
    # no manual override -- this combo used to reach the connector as a
    # cryptic tensor-shape crash instead of a validation error.
    with pytest.raises(ValueError, match="'steps' must be >= 2"):
        validate_ltx_schedule_config(_base_config(steps=1), pipe_id="generator/txt2vid_ltx")


def test_steps_one_with_manual_sigmas_is_allowed():
    # manual_sigmas overrides steps entirely (see schedule_settings_overrides
    # docstring) -- steps=1 alongside it is irrelevant, not degenerate.
    validate_ltx_schedule_config(
        _base_config(steps=1, manual_sigmas="1.0, 0.5, 0.0"), pipe_id="generator/txt2vid_ltx"
    )


def test_steps_two_without_manual_sigmas_is_allowed():
    validate_ltx_schedule_config(_base_config(steps=2), pipe_id="generator/txt2vid_ltx")


# -- refine_sigmas (bypasses manual_sigmas's 1.0/0.0 forcing) --

def test_refine_sigmas_non_numeric_raises():
    with pytest.raises(ValueError, match="'refine_sigmas' is invalid"):
        validate_ltx_schedule_config(_base_config(refine_sigmas="0.9, banana, 0.0"), pipe_id="generator/txt2vid_ltx")


def test_refine_sigmas_single_value_raises():
    with pytest.raises(ValueError, match="'refine_sigmas' is invalid"):
        validate_ltx_schedule_config(_base_config(refine_sigmas="0.9"), pipe_id="generator/txt2vid_ltx")


def test_refine_sigmas_increasing_raises():
    with pytest.raises(ValueError, match="'refine_sigmas' is invalid"):
        validate_ltx_schedule_config(_base_config(refine_sigmas="0.5, 0.9, 0.0"), pipe_id="generator/txt2vid_ltx")


def test_refine_sigmas_valid_descending_passes():
    validate_ltx_schedule_config(
        _base_config(refine_sigmas="0.909375, 0.725, 0.421875, 0.0"), pipe_id="generator/txt2vid_ltx"
    )


def test_refine_sigmas_does_not_force_head_to_one_unlike_manual_sigmas():
    # Sanity pin: refine_sigmas is validated (non-numeric/length/monotonic)
    # but NOT rewritten -- confirm the parsed tensor keeps its own sigma[0].
    from src.pipelines.pipes.generator.txt2vid_ltx.main import parse_explicit_sigmas
    sigmas = parse_explicit_sigmas("0.909375, 0.725, 0.421875, 0.0")
    assert sigmas[0].item() == pytest.approx(0.909375)


def test_steps_one_with_refine_sigmas_is_allowed():
    # refine_sigmas overrides steps entirely (sigmas= override to denoise()/
    # denoise_prenoised()) -- steps=1 alongside it is irrelevant, not degenerate.
    validate_ltx_schedule_config(
        _base_config(steps=1, refine_sigmas="0.909375, 0.725, 0.421875, 0.0"), pipe_id="generator/txt2vid_ltx"
    )


def test_error_message_includes_pipe_id():
    with pytest.raises(ValueError, match=r"generator/video_ltx: 'sampler'"):
        validate_ltx_schedule_config(_base_config(sampler=""), pipe_id="generator/video_ltx")


# -- wired into each pipe's validate_config classmethod ----------------------

def test_txt2vid_ltx_pipe_validate_config_delegates():
    with pytest.raises(ValueError, match="generator/txt2vid_ltx"):
        GeneratorLtxTxt2VidPipe.validate_config(_base_config(sampler=""))


def test_video_ltx_pipe_validate_config_delegates():
    with pytest.raises(ValueError, match="generator/video_ltx"):
        GeneratorLtxVideoPipe.validate_config(_base_config(sampler=""))


# -- end-to-end through validate_pipe_configuration --------------------------

def test_full_validate_pipe_configuration_rejects_empty_sampler_txt2vid_ltx():
    # Caught by the pre-existing per-field `choices` check (empty string is
    # not a valid sampler choice) -- still ends up a structured ValueError.
    config = GeneratorLtxTxt2VidPipe.get_default_config()
    config["sampler"] = ""
    with pytest.raises(ValueError):
        validate_pipe_configuration(GeneratorLtxTxt2VidPipe, config)


def test_full_validate_pipe_configuration_rejects_degenerate_steps_txt2vid_ltx():
    """Repro shape: steps=1 with a valid sampler and no manual
    sigma override -- individually-valid per-field values (steps=1 passes its
    own min_value=1; sampler='euler' is a valid choice) that are degenerate
    together. Only the new cross-field validate_config hook catches this;
    it must surface here, before build_context/generate_one ever run, instead
    of reaching the DiT as a cryptic tensor-shape crash."""
    config = GeneratorLtxTxt2VidPipe.get_default_config()
    config["steps"] = 1
    with pytest.raises(ValueError, match="'steps' must be >= 2"):
        validate_pipe_configuration(GeneratorLtxTxt2VidPipe, config)


def test_full_validate_pipe_configuration_accepts_default_txt2vid_ltx_config():
    config = GeneratorLtxTxt2VidPipe.get_default_config()
    result = validate_pipe_configuration(GeneratorLtxTxt2VidPipe, config)
    assert result["sampler"] == "euler"


def test_full_validate_pipe_configuration_rejects_empty_sampler_video_ltx():
    config = GeneratorLtxVideoPipe.get_default_config()
    config["sampler"] = ""
    with pytest.raises(ValueError):
        validate_pipe_configuration(GeneratorLtxVideoPipe, config)


def test_full_validate_pipe_configuration_rejects_degenerate_steps_video_ltx():
    config = GeneratorLtxVideoPipe.get_default_config()
    config["steps"] = 1
    with pytest.raises(ValueError, match="'steps' must be >= 2"):
        validate_pipe_configuration(GeneratorLtxVideoPipe, config)


def test_full_validate_pipe_configuration_accepts_default_video_ltx_config():
    config = GeneratorLtxVideoPipe.get_default_config()
    result = validate_pipe_configuration(GeneratorLtxVideoPipe, config)
    assert result["sampler"] == "euler"


# -- euler_ancestral (LTX-2.5 stage-1) is a valid sampler choice on both pipes -

def test_txt2vid_ltx_sampler_choices_include_euler_ancestral():
    specs = {s.name: s for s in GeneratorLtxTxt2VidPipe.configuration()}
    assert "euler_ancestral" in specs["sampler"].choices


def test_video_ltx_sampler_choices_include_euler_ancestral():
    specs = {s.name: s for s in GeneratorLtxVideoPipe.configuration()}
    assert "euler_ancestral" in specs["sampler"].choices


def test_full_validate_pipe_configuration_accepts_euler_ancestral_txt2vid_ltx():
    config = GeneratorLtxTxt2VidPipe.get_default_config()
    config["sampler"] = "euler_ancestral"
    result = validate_pipe_configuration(GeneratorLtxTxt2VidPipe, config)
    assert result["sampler"] == "euler_ancestral"


def test_full_validate_pipe_configuration_accepts_euler_ancestral_video_ltx():
    config = GeneratorLtxVideoPipe.get_default_config()
    config["sampler"] = "euler_ancestral"
    result = validate_pipe_configuration(GeneratorLtxVideoPipe, config)
    assert result["sampler"] == "euler_ancestral"


def test_full_validate_pipe_configuration_accepts_ltx_dynamic_schedule_txt2vid_ltx():
    config = GeneratorLtxTxt2VidPipe.get_default_config()
    config["schedule"] = "ltx_dynamic"
    result = validate_pipe_configuration(GeneratorLtxTxt2VidPipe, config)
    assert result["schedule"] == "ltx_dynamic"


def test_full_validate_pipe_configuration_accepts_ltx_dynamic_schedule_video_ltx():
    config = GeneratorLtxVideoPipe.get_default_config()
    config["schedule"] = "ltx_dynamic"
    result = validate_pipe_configuration(GeneratorLtxVideoPipe, config)
    assert result["schedule"] == "ltx_dynamic"
