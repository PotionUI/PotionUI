from __future__ import annotations

import pytest
import torch
from scipy.special import betainc

from src.platform.plugins import core_sampling
from src.platform.plugins.sampling import schedule_registry
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas

EIGHT_STEP_KNOTS = [1.0, 0.889398, 0.719286, 0.535685, 0.361084, 0.210908, 0.096177, 0.024407, 0.0]


def _shifted(t: list[float], shift: float) -> list[float]:
    return [shift * x / (1.0 + (shift - 1.0) * x) for x in t]


def test_beta57_knots_are_the_beta_half_point_seven_quantiles():
    sigmas = build_sigmas(8, schedule="beta57")
    assert sigmas.tolist() == pytest.approx(EIGHT_STEP_KNOTS, abs=1e-6)
    quantiles = [1.0 - i / 8 for i in range(9)]
    assert betainc(0.5, 0.7, sigmas.double().numpy()).tolist() == pytest.approx(quantiles, abs=1e-6)


@pytest.mark.parametrize("steps", (1, 4, 8, 30))
@pytest.mark.parametrize("shift", (None, 3.0, 12.0))
def test_beta57_is_strictly_descending_from_one_to_zero(steps, shift):
    sigmas = build_sigmas(steps, shift=shift, schedule="beta57")
    assert sigmas.shape == (steps + 1,)
    assert sigmas[0].item() == 1.0
    assert sigmas[-1].item() == 0.0
    assert bool((sigmas[1:] < sigmas[:-1]).all())


def test_beta57_spaces_the_knots_before_the_model_shift():
    shifted = build_sigmas(8, shift=12.0, schedule="beta57")
    assert shifted.tolist() == pytest.approx(_shifted(EIGHT_STEP_KNOTS, 12.0), abs=1e-5)


def test_beta57_leans_toward_the_clean_end_unlike_the_symmetric_beta():
    beta57 = build_sigmas(8, schedule="beta57")
    beta = build_sigmas(8, schedule="beta")
    assert beta[4].item() == pytest.approx(0.5, abs=1e-6)
    assert beta57[4].item() < 0.4


def test_beta57_is_a_universal_core_schedule():
    assert "beta57" in [d.key for d in core_sampling.CORE_SCHEDULES]
    definition = schedule_registry.get("beta57")
    assert definition.label == "Beta57"
    assert definition.families == ("*",)
    assert definition.options == ()
