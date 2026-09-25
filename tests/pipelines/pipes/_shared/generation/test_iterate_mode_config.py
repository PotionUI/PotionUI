from __future__ import annotations

import pytest

from src.pipelines.pipes._shared.generation.flow_generator_pipe import (
    FlowMatchGeneratorPipe,
    iterate_mode_config_specs,
)

# -- declarations -------------------------------------------------------


def test_iterate_mode_spec_shape():
    specs = {s.name: s for s in iterate_mode_config_specs()}
    spec = specs["iterate_mode"]
    assert spec.param_type is bool
    assert spec.default is False
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


def test_validate_config_ignores_stale_spectral_progressive_key():
    # An old saved form_data/preset config can still carry the removed
    # `spectral_progressive` knob; validate_config must not know or care
    # about it any more, even with the shape (unknown sub-keys, bad basis)
    # that used to fail eager validation.
    FlowMatchGeneratorPipe.validate_config({"spectral_progressive": {"typo_key": 1, "basis": "not_a_basis"}})
