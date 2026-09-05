"""Cancellation contract for SDXLSamplerConfig.create_k_callback().

The vendored `k_sampling.sample_*` functions invoke `callback(info)` once per
step with no knowledge of PotionUI's cancellation contract (see
vendor/k_diffusion/sampling.py -- `if callback is not None: callback(...)`,
uncaught). create_k_callback's closure is the boundary where the probe is
polled and `SamplingCancelled` raised.
"""
from unittest.mock import Mock

import pytest
import torch

from src.platform.runtime.native.errors import SamplingCancelled
from src.pipelines.pipes.generator.sdxl.sampler_config import SDXLSamplerConfig


def _make_callback(callback_on_step_end=None, is_cancelled=None):
    return SDXLSamplerConfig.create_k_callback(
        callback_on_step_end,
        ["latents"],
        pipeline_self=Mock(),
        prompt_embeds=torch.zeros(1),
        negative_prompt_embeds=torch.zeros(1),
        add_text_embeds=torch.zeros(1),
        add_time_ids=torch.zeros(1),
        is_cancelled=is_cancelled,
    )


def _info(i: int):
    return {"x": torch.zeros(1), "i": i, "sigma": torch.tensor(1.0), "denoised": torch.zeros(1)}


class TestCreateKCallback:
    def test_returns_none_when_no_progress_callback_and_no_probe(self):
        assert _make_callback(callback_on_step_end=None, is_cancelled=None) is None

    def test_returns_callable_when_only_is_cancelled_given(self):
        k_callback = _make_callback(callback_on_step_end=None, is_cancelled=lambda: False)
        assert k_callback is not None
        k_callback(_info(0))  # must not raise, must not error on a missing progress callback

    def test_raises_sampling_cancelled_with_step_index(self):
        k_callback = _make_callback(callback_on_step_end=None, is_cancelled=lambda: True)

        with pytest.raises(SamplingCancelled) as excinfo:
            k_callback(_info(3))

        assert excinfo.value.step_index == 3

    def test_falls_through_to_progress_callback_when_not_cancelled(self):
        progress = Mock()
        k_callback = _make_callback(callback_on_step_end=progress, is_cancelled=lambda: False)

        k_callback(_info(1))

        assert progress.called
        args, _ = progress.call_args
        assert args[1] == 1  # step

    def test_cancellation_preempts_progress_callback_at_the_same_step(self):
        progress = Mock()
        k_callback = _make_callback(callback_on_step_end=progress, is_cancelled=lambda: True)

        with pytest.raises(SamplingCancelled):
            k_callback(_info(0))

        progress.assert_not_called()

    def test_raises_partway_through_a_fake_sampler_loop(self):
        """Mirrors vendor/k_diffusion's `for i in range(...): callback(...)`
        loop shape -- no try/except around the callback call."""
        progress = Mock()
        cancel_at_step = 2

        state = {"step": -1}

        def is_cancelled():
            return state["step"] >= cancel_at_step

        k_callback = _make_callback(callback_on_step_end=progress, is_cancelled=is_cancelled)

        with pytest.raises(SamplingCancelled) as excinfo:
            for i in range(5):
                state["step"] = i
                k_callback(_info(i))

        assert excinfo.value.step_index == cancel_at_step
        assert progress.call_count == cancel_at_step
