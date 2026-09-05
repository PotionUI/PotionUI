"""Cancellation contract for SDXLModelWrapper.apply_model().

apply_model() is the per-step model-evaluation boundary CompVisDenoiser calls
once per k-diffusion sampling step; these tests prove the cancellation probe
is checked there, before any UNet work, and raises the shared
`SamplingCancelled` exception used across every native sampler.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import torch

from src.platform.runtime.native.errors import SamplingCancelled
from src.pipelines.pipes.generator.sdxl.model_wrapper import SDXLModelWrapper


def _fake_scheduler():
    # A real alphas_cumprod tensor short-circuits _setup_alphas_cumprod's
    # from-betas fallback, which otherwise reads config fields that don't
    # exist on a bare fake object.
    return SimpleNamespace(alphas_cumprod=torch.linspace(0.9991, 0.0047, 10))


def _fake_unet(noise_pred: torch.Tensor):
    unet = Mock()
    unet.parameters = lambda: iter([torch.zeros(1, dtype=torch.float32)])
    unet.return_value = (noise_pred,)
    return unet


def _build_wrapper(is_cancelled=None, num_inference_steps=10, unet=None):
    return SDXLModelWrapper(
        unet=unet or Mock(),
        scheduler=_fake_scheduler(),
        prompt_embeds=None,
        add_text_embeds=None,
        add_time_ids=None,
        num_inference_steps=num_inference_steps,
        guidance_scale=5.0,
        do_classifier_free_guidance=False,
        is_cancelled=is_cancelled,
    )


class TestApplyModelCancellation:
    def test_raises_sampling_cancelled_before_any_unet_work(self):
        wrapper = _build_wrapper(is_cancelled=lambda: True)
        x = torch.zeros(1, 4, 8, 8)
        t = torch.tensor([500.0])

        with pytest.raises(SamplingCancelled) as excinfo:
            wrapper.apply_model(x, t)

        assert excinfo.value.step_index == 0
        # The check must precede any UNet touch -- current_step (only
        # incremented after a successful forward) proves no work happened.
        assert wrapper.current_step == 0

    def test_proceeds_and_advances_step_when_not_cancelled(self):
        noise_pred = torch.zeros(1, 4, 8, 8)
        wrapper = _build_wrapper(is_cancelled=lambda: False, unet=_fake_unet(noise_pred))
        x = torch.zeros(1, 4, 8, 8)
        t = torch.tensor([500.0])

        result = wrapper.apply_model(x, t)

        assert torch.equal(result, noise_pred)
        assert wrapper.current_step == 1

    def test_default_is_cancelled_never_fires(self):
        noise_pred = torch.zeros(1, 4, 8, 8)
        wrapper = _build_wrapper(is_cancelled=None, unet=_fake_unet(noise_pred))
        x = torch.zeros(1, 4, 8, 8)
        t = torch.tensor([500.0])

        for _ in range(5):
            wrapper.apply_model(x, t)

        assert wrapper.current_step == 5

    def test_raises_partway_through_a_fake_sampling_loop(self):
        """Probe flips true on step k; steps before k must have run, k itself
        must raise, and nothing after k must run."""
        cancel_at_step = 2
        calls = []

        def is_cancelled():
            return len(calls) >= cancel_at_step

        noise_pred = torch.zeros(1, 4, 8, 8)
        # A fresh iterator per apply_model() call, matching a real unet's
        # re-callable .parameters().
        unet = Mock()
        unet.parameters = lambda: iter([torch.zeros(1, dtype=torch.float32)])
        unet.return_value = (noise_pred,)
        unet.side_effect = lambda *a, **kw: calls.append(1) or (noise_pred,)

        wrapper = _build_wrapper(is_cancelled=is_cancelled, unet=unet)
        x = torch.zeros(1, 4, 8, 8)
        t = torch.tensor([500.0])

        with pytest.raises(SamplingCancelled) as excinfo:
            for _ in range(5):
                wrapper.apply_model(x, t)

        assert len(calls) == cancel_at_step
        assert excinfo.value.step_index == cancel_at_step
        assert wrapper.current_step == cancel_at_step
