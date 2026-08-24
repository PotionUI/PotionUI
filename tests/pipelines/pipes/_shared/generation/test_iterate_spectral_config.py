"""Tests for the shared `iterate_mode`/`spectral_progressive` config-spec
declarations and validation in
src/pipelines/pipes/_shared/generation/flow_generator_pipe.py -- the two
opt-in `NativeGenerator.sample()` knobs (engine.py) every native
flow-matching family reads through `FlowMatchGeneratorPipe.build_context`,
regardless of whether that family's own `configuration()` declares them.
"""

from __future__ import annotations

import pytest

from src.pipelines.pipes._shared.generation.flow_generator_pipe import (
    FlowMatchGeneratorPipe,
    iterate_mode_config_specs,
    spectral_progressive_config_specs,
)

# -- declarations -------------------------------------------------------


def test_iterate_mode_spec_shape():
    specs = {s.name: s for s in iterate_mode_config_specs()}
    spec = specs["iterate_mode"]
    assert spec.param_type is bool
    assert spec.default is False
    assert spec.required is False


def test_spectral_progressive_spec_shape():
    specs = {s.name: s for s in spectral_progressive_config_specs()}
    spec = specs["spectral_progressive"]
    assert spec.param_type is dict
    assert spec.default is None
    assert spec.required is False


# -- validate_config: iterate_mode ---------------------------------------


def test_validate_config_accepts_absent_iterate_mode():
    FlowMatchGeneratorPipe.validate_config({})  # must not raise


@pytest.mark.parametrize("value", [True, False])
def test_validate_config_accepts_bool_iterate_mode(value):
    FlowMatchGeneratorPipe.validate_config({"iterate_mode": value})  # must not raise


@pytest.mark.parametrize("value", ["true", 1, 1.0, [True]])
def test_validate_config_rejects_non_bool_iterate_mode(value):
    with pytest.raises(ValueError, match="iterate_mode"):
        FlowMatchGeneratorPipe.validate_config({"iterate_mode": value})


# -- validate_config: spectral_progressive --------------------------------


def test_validate_config_accepts_absent_spectral_progressive():
    FlowMatchGeneratorPipe.validate_config({})  # must not raise
    FlowMatchGeneratorPipe.validate_config({"spectral_progressive": None})
    FlowMatchGeneratorPipe.validate_config({"spectral_progressive": {}})


def test_validate_config_accepts_minimal_spectral_progressive():
    FlowMatchGeneratorPipe.validate_config({"spectral_progressive": {"enabled": True}})


def test_validate_config_accepts_full_spectral_progressive():
    FlowMatchGeneratorPipe.validate_config({
        "spectral_progressive": {
            "enabled": True,
            "scales": [0.3, 0.6, 1.0],
            "delta": 0.02,
            "power_beta": 2.0,
            "power_amplitude": 1.5,
            "basis": "dct",
            "transitions": [0.9, 0.7],
        },
    })


def test_validate_config_rejects_non_dict_spectral_progressive():
    with pytest.raises(ValueError, match="dict"):
        FlowMatchGeneratorPipe.validate_config({"spectral_progressive": "on"})


def test_validate_config_rejects_unknown_spectral_progressive_keys():
    with pytest.raises(ValueError, match="unknown keys"):
        FlowMatchGeneratorPipe.validate_config({"spectral_progressive": {"typo_key": 1}})


def test_validate_config_rejects_bad_basis():
    with pytest.raises(ValueError, match="basis"):
        FlowMatchGeneratorPipe.validate_config({"spectral_progressive": {"basis": "not_a_basis"}})


def test_validate_config_rejects_scales_not_ending_at_one():
    with pytest.raises(ValueError, match="scales"):
        FlowMatchGeneratorPipe.validate_config({"spectral_progressive": {"scales": [0.3, 0.6]}})


def test_validate_config_rejects_scales_not_increasing():
    with pytest.raises(ValueError, match="scales"):
        FlowMatchGeneratorPipe.validate_config({"spectral_progressive": {"scales": [0.8, 0.5, 1.0]}})


def test_validate_config_rejects_delta_out_of_range():
    with pytest.raises(ValueError, match="delta"):
        FlowMatchGeneratorPipe.validate_config({"spectral_progressive": {"delta": 1.5}})


def test_validate_config_rejects_mismatched_transitions_length():
    with pytest.raises(ValueError, match="transitions"):
        FlowMatchGeneratorPipe.validate_config({
            "spectral_progressive": {"scales": [0.5, 1.0], "transitions": [0.9, 0.7]},
        })
