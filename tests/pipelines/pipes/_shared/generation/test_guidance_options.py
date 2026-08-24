"""Tests for src/pipelines/pipes/_shared/generation/guidance_options.py — the shared
APG/SLG/schedule config-spec + override-builder surface used by the video
generator pipes (and parse_int_set, its int-set parsing helper)."""

from __future__ import annotations

import pytest

from src.pipelines.pipes._shared.generation.guidance_options import (
    apg_settings_overrides,
    build_multimodal_guider_params,
    check_guider_mode_conflict,
    parse_int_set,
    sampler_step_cache_config_specs,
    sampler_step_cache_kwargs,
    schedule_settings_config_specs,
    schedule_settings_overrides,
    slg_settings_overrides,
)


# --------------------------------------------------------------------------- #
# parse_int_set (P8)
# --------------------------------------------------------------------------- #

def test_parse_int_set_empty_and_none_variants():
    assert parse_int_set(None) == set()
    assert parse_int_set("") == set()
    assert parse_int_set("None") == set()


def test_parse_int_set_comma_separated_string():
    assert parse_int_set("0,2,5") == {0, 2, 5}
    assert parse_int_set(" 0 , 2 , 5 ") == {0, 2, 5}


def test_parse_int_set_string_skips_bad_entries_with_warning():
    assert parse_int_set("0,bad,2") == {0, 2}


def test_parse_int_set_accepts_container_of_ints():
    assert parse_int_set([0, 2, 5]) == {0, 2, 5}
    assert parse_int_set((0, 2, 5)) == {0, 2, 5}
    assert parse_int_set({0, 2, 5}) == {0, 2, 5}


def test_parse_int_set_container_skips_non_integer_strings_instead_of_raising():
    # P8 regression: the container branch used to build the set via a bare
    # {int(v) for v in raw} comprehension with NO per-element error handling,
    # so one bad string element raised an uncaught ValueError instead of
    # being warned-and-skipped like the string-parsing branch does.
    assert parse_int_set(["0", "bad", "2"]) == {0, 2}


def test_parse_int_set_container_rejects_fractional_floats():
    # P8 regression: a fractional float used to be silently truncated by
    # int() instead of being rejected as an invalid layer index.
    assert parse_int_set([1.9, 2.0, 3]) == {2, 3}


def test_parse_int_set_container_accepts_whole_floats():
    assert parse_int_set([1.0, 2.0]) == {1, 2}


# --------------------------------------------------------------------------- #
# apg / slg / schedule overrides: None-sentinel omission (P5)
# --------------------------------------------------------------------------- #

def test_apg_settings_overrides_omits_unset_keys():
    assert apg_settings_overrides({}) == {}
    assert apg_settings_overrides({"apg_eta": None, "apg_norm_threshold": None, "apg_momentum": None}) == {}


def test_apg_settings_overrides_includes_only_set_keys():
    assert apg_settings_overrides({"apg_eta": 0.3}) == {"apg_eta": 0.3}
    assert apg_settings_overrides({"apg_eta": 0.0}) == {"apg_eta": 0.0}  # 0.0 is a valid explicit value


def test_slg_settings_overrides_omits_unset_keys():
    assert slg_settings_overrides({}) == {}


def test_slg_settings_overrides_includes_only_set_keys():
    out = slg_settings_overrides({"slg_scale": 0.0, "slg_layers": "0,2"})
    assert out == {"slg_scale": 0.0, "slg_layers": {0, 2}}


def test_schedule_settings_overrides_omits_unset_keys():
    assert schedule_settings_overrides({}) == {}
    assert schedule_settings_overrides({"schedule": "", "detail_strength": None}) == {}


def test_schedule_settings_overrides_includes_only_set_keys():
    out = schedule_settings_overrides({"schedule": "beta", "detail_strength": 0.0})
    assert out == {"schedule": "beta", "detail_strength": 0.0}


# --------------------------------------------------------------------------- #
# manual_sigmas (distilled-refine recipe): priority + no-op
# --------------------------------------------------------------------------- #

_RECIPE = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"


def test_manual_sigmas_empty_default_is_a_noop():
    assert schedule_settings_overrides({"manual_sigmas": ""}) == {}
    assert schedule_settings_overrides({}) == {}


def test_manual_sigmas_sets_schedule_and_options():
    out = schedule_settings_overrides({"manual_sigmas": _RECIPE})
    assert out == {"schedule": "manual", "schedule_options": {"sigmas": _RECIPE}}


def test_manual_sigmas_takes_priority_over_schedule_and_schedule_options():
    # Even if a preset also sets schedule='beta' (e.g. a stale/leftover
    # value), a non-empty manual_sigmas must win outright -- see the
    # PipeConfigSpec docstring's documented priority.
    out = schedule_settings_overrides({
        "manual_sigmas": _RECIPE,
        "schedule": "beta",
        "schedule_options": {"alpha": 0.4, "beta": 0.9},
    })
    assert out == {"schedule": "manual", "schedule_options": {"sigmas": _RECIPE}}


def test_manual_sigmas_unset_leaves_schedule_path_untouched():
    # Byte-identical to before manual_sigmas existed when it's left at its
    # default -- schedule/schedule_options flow through exactly as before.
    out = schedule_settings_overrides({"schedule": "beta", "schedule_options": {"alpha": 0.4}})
    assert out == {"schedule": "beta", "schedule_options": {"alpha": 0.4}}


# --------------------------------------------------------------------------- #
# check_guider_mode_conflict / build_multimodal_guider_params
# --------------------------------------------------------------------------- #

def test_guider_mode_conflict_neither_set_is_allowed():
    check_guider_mode_conflict({})
    check_guider_mode_conflict({"quality_mode": False, "distilled_mode": False})


def test_guider_mode_conflict_only_quality_mode_is_allowed():
    check_guider_mode_conflict({"quality_mode": True})


def test_guider_mode_conflict_only_distilled_mode_is_allowed():
    check_guider_mode_conflict({"distilled_mode": True})


def test_guider_mode_conflict_both_set_raises():
    with pytest.raises(ValueError, match="mutually exclusive"):
        check_guider_mode_conflict({"quality_mode": True, "distilled_mode": True})


def test_guider_mode_conflict_message_includes_pipe_id_when_given():
    with pytest.raises(ValueError, match=r"generator/txt2vid_ltx: 'quality_mode'"):
        check_guider_mode_conflict(
            {"quality_mode": True, "distilled_mode": True}, pipe_id="generator/txt2vid_ltx"
        )


def test_guider_mode_conflict_message_has_no_pipe_prefix_when_omitted():
    with pytest.raises(ValueError, match=r"^'quality_mode'"):
        check_guider_mode_conflict({"quality_mode": True, "distilled_mode": True})


def test_build_multimodal_guider_params_raises_when_both_modes_set():
    # A desynced form with both flags true must never
    # silently let the quality guider win -- this used to happen because
    # build_multimodal_guider_params only ever checked quality_mode.
    with pytest.raises(ValueError, match="mutually exclusive"):
        build_multimodal_guider_params({"quality_mode": True, "distilled_mode": True})


def test_build_multimodal_guider_params_none_when_quality_mode_off():
    assert build_multimodal_guider_params({"quality_mode": False, "distilled_mode": True}) is None
    assert build_multimodal_guider_params({"distilled_mode": True}) is None


def test_build_multimodal_guider_params_builds_when_only_quality_mode_set():
    result = build_multimodal_guider_params({"quality_mode": True})
    assert result is not None
    video, audio = result
    assert video.cfg_scale > 1.0


# --------------------------------------------------------------------------- #
# sampler_step_cache_kwargs: flat scalar keys vs. the legacy `step_cache` dict
# --------------------------------------------------------------------------- #

def test_step_cache_defaults_to_none_when_nothing_set():
    out = sampler_step_cache_kwargs({})
    assert out["step_cache_options"] is None


def test_step_cache_threshold_zero_resolves_to_none():
    out = sampler_step_cache_kwargs({"step_cache_threshold": 0.0})
    assert out["step_cache_options"] is None


def test_step_cache_flat_keys_assemble_the_dict():
    out = sampler_step_cache_kwargs({
        "step_cache_threshold": 0.12,
        "step_cache_warmup_steps": 5,
        "step_cache_max_skips": 2,
    })
    assert out["step_cache_options"] == {
        "rel_threshold": 0.12,
        "warmup_steps": 5,
        "max_consecutive_skips": 2,
    }


def test_step_cache_flat_keys_use_defaults_when_only_threshold_set():
    out = sampler_step_cache_kwargs({"step_cache_threshold": 0.08})
    assert out["step_cache_options"] == {
        "rel_threshold": 0.08,
        "warmup_steps": 4,
        "max_consecutive_skips": 3,
    }


def test_step_cache_explicit_dict_wins_over_flat_keys():
    out = sampler_step_cache_kwargs({
        "step_cache": {"rel_threshold": 0.2, "warmup_steps": 1, "max_consecutive_skips": 1},
        "step_cache_threshold": 0.5,
        "step_cache_warmup_steps": 10,
        "step_cache_max_skips": 10,
    })
    assert out["step_cache_options"] == {
        "rel_threshold": 0.2,
        "warmup_steps": 1,
        "max_consecutive_skips": 1,
    }


def test_step_cache_empty_dict_falls_back_to_flat_keys():
    out = sampler_step_cache_kwargs({"step_cache": {}, "step_cache_threshold": 0.1})
    assert out["step_cache_options"] == {
        "rel_threshold": 0.1,
        "warmup_steps": 4,
        "max_consecutive_skips": 3,
    }


def test_step_cache_flat_keys_coerce_string_values():
    out = sampler_step_cache_kwargs({
        "step_cache_threshold": "0.15",
        "step_cache_warmup_steps": "6",
        "step_cache_max_skips": "4",
    })
    assert out["step_cache_options"] == {
        "rel_threshold": 0.15,
        "warmup_steps": 6,
        "max_consecutive_skips": 4,
    }


def test_step_cache_blank_string_threshold_is_a_noop():
    out = sampler_step_cache_kwargs({"step_cache_threshold": ""})
    assert out["step_cache_options"] is None


# --------------------------------------------------------------------------- #
# sampler_step_cache_kwargs: stochastic-sampler generator population
# --------------------------------------------------------------------------- #

def test_generator_populated_for_euler_ancestral():
    import torch
    gen = torch.Generator().manual_seed(1)
    out = sampler_step_cache_kwargs({}, sampler="euler_ancestral", generator=gen)
    assert out["sampler_options"] == {"generator": gen}


def test_generator_not_populated_for_deterministic_sampler():
    import torch
    gen = torch.Generator().manual_seed(1)
    out = sampler_step_cache_kwargs({}, sampler="euler", generator=gen)
    assert out["sampler_options"] is None


def test_generator_omitted_when_sampler_kwarg_absent():
    # Callers that don't pass `sampler=` at all get sampler_options exactly
    # as configured -- e.g. video_ltx's pre-fix call site.
    import torch
    gen = torch.Generator().manual_seed(1)
    out = sampler_step_cache_kwargs({}, generator=gen)
    assert out["sampler_options"] is None


# --------------------------------------------------------------------------- #
# schedule_settings_config_specs: ltx_dynamic is a valid choice
# --------------------------------------------------------------------------- #

def test_schedule_config_spec_includes_ltx_dynamic_choice():
    specs = {s.name: s for s in schedule_settings_config_specs()}
    assert "ltx_dynamic" in specs["schedule"].choices
