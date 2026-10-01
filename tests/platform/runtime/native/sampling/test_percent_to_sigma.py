from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.detect.registry import arch_registry
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas, percent_to_sigma


def _comfy_flux_time_shift(mu, sigma, t):
    return math.exp(mu) / (math.exp(mu) + (1 / t - 1) ** sigma)


def _comfy_flux_percent_to_sigma(mu, percent):
    if percent <= 0.0:
        return 1.0
    if percent >= 1.0:
        return 0.0
    return _comfy_flux_time_shift(mu, 1.0, 1.0 - percent)


def _comfy_time_snr_shift(alpha, t):
    if alpha == 1.0:
        return t
    return alpha * t / (1 + (alpha - 1) * t)


def _qwen21_settings():
    return arch_registry.get("qwen_image21", "qwen_image21").sampling_settings


def _qwen21_mu(image_seq_len):
    dyn = _qwen21_settings()["dynamic_shift"]
    x1, x2 = (dyn["x1_px"] / dyn["align"]) ** 2, (dyn["x2_px"] / dyn["align"]) ** 2
    slope = (dyn["y2"] - dyn["y1"]) / (x2 - x1)
    return slope * image_seq_len + (dyn["y1"] - slope * x1)


@pytest.mark.parametrize("image_seq_len", [1024, 4096, 8192])
@pytest.mark.parametrize("percent", [0.05, 0.25, 0.5, 0.8, 0.99])
def test_the_qwen21_schedule_maps_percent_like_comfyui(image_seq_len, percent):
    expected = _comfy_flux_percent_to_sigma(_qwen21_mu(image_seq_len), percent)
    assert percent_to_sigma(percent, _qwen21_settings(), image_seq_len) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("percent", [0.1, 0.5, 0.9])
def test_a_constant_shift_maps_percent_like_comfyui(percent):
    expected = _comfy_time_snr_shift(3.0, 1.0 - percent)
    assert percent_to_sigma(percent, {"shift": 3.0}) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("percent,sigma", [(0.0, 1.0), (-0.5, 1.0), (1.0, 0.0), (1.5, 0.0)])
def test_the_ends_cover_the_whole_trajectory(percent, sigma):
    assert percent_to_sigma(percent, _qwen21_settings(), 4096) == sigma


def test_percent_lands_on_the_sampled_sigmas():
    settings = _qwen21_settings()
    steps = 8
    sigmas = build_sigmas(steps, shift=settings["shift"], dynamic_shift=settings["dynamic_shift"], image_seq_len=4096)
    for i in range(1, steps):
        assert percent_to_sigma(i / steps, settings, 4096) == pytest.approx(float(sigmas[i]), abs=1e-6)


def test_the_shift_schedule_is_unchanged():
    settings = _qwen21_settings()
    sigmas = build_sigmas(10, shift=settings["shift"], dynamic_shift=settings["dynamic_shift"], image_seq_len=4096)
    t = torch.linspace(1.0, 0.0, 11, dtype=torch.float32)
    mu = _qwen21_mu(4096)
    expected = torch.tensor([_comfy_flux_time_shift(mu, 1.0, float(v)) if v > 0 else 0.0 for v in t])
    torch.testing.assert_close(sigmas, expected, atol=1e-6, rtol=0)
