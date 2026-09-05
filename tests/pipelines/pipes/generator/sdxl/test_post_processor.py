"""Tests for SDXLPostProcessor.decode_latents VAE-tiling restore behaviour."""

from types import SimpleNamespace

import pytest
import torch

from src.pipelines.pipes.generator.sdxl.post_processor import SDXLPostProcessor


class _FakeVAE:
    """Minimal VAE stand-in exercising the OOM-retry tiling path."""

    def __init__(self, use_tiling=False, supports_tiling=True, fail_mode="once"):
        self.use_tiling = use_tiling
        self.dtype = torch.float32
        self.config = SimpleNamespace(scaling_factor=0.13025)
        self._fail_mode = fail_mode
        self._decode_calls = 0
        self.tiling_calls = []
        if supports_tiling:
            self.enable_tiling = self._enable_tiling
            self.disable_tiling = self._disable_tiling

    def _enable_tiling(self):
        self.use_tiling = True
        self.tiling_calls.append("enable")

    def _disable_tiling(self):
        self.use_tiling = False
        self.tiling_calls.append("disable")

    def decode(self, latents, return_dict=False):
        self._decode_calls += 1
        if self._fail_mode == "always_oom":
            raise RuntimeError("CUDA out of memory.")
        if self._fail_mode == "once" and self._decode_calls == 1:
            raise RuntimeError("CUDA out of memory.")
        if self._fail_mode == "retry_error":
            if self._decode_calls == 1:
                raise RuntimeError("CUDA out of memory.")
            raise RuntimeError("second decode failure")
        if self._fail_mode == "non_oom":
            raise RuntimeError("some other cuda error")
        return (torch.zeros(1, 3, 4, 4),)


def _decode(vae):
    latents = torch.randn(1, 4, 4, 4)
    return SDXLPostProcessor.decode_latents(vae, latents, output_type="pt")


def test_already_tiled_vae_stays_tiled_after_successful_retry():
    vae = _FakeVAE(use_tiling=True, fail_mode="once")

    _decode(vae)

    assert vae.use_tiling is True
    # Already tiled: neither enable nor disable should have been invoked.
    assert vae.tiling_calls == []


def test_untiled_vae_restored_to_untiled_after_successful_retry():
    vae = _FakeVAE(use_tiling=False, fail_mode="once")

    _decode(vae)

    assert vae.use_tiling is False
    assert vae.tiling_calls == ["enable", "disable"]


def test_untiled_vae_restored_after_retry_raises():
    vae = _FakeVAE(use_tiling=False, fail_mode="retry_error")

    with pytest.raises(RuntimeError, match="second decode failure"):
        _decode(vae)

    assert vae.use_tiling is False
    assert vae.tiling_calls == ["enable", "disable"]


def test_already_tiled_vae_stays_tiled_after_retry_raises():
    vae = _FakeVAE(use_tiling=True, fail_mode="retry_error")

    with pytest.raises(RuntimeError, match="second decode failure"):
        _decode(vae)

    assert vae.use_tiling is True
    assert vae.tiling_calls == []


def test_non_oom_error_propagates_without_touching_tiling():
    vae = _FakeVAE(use_tiling=False, fail_mode="non_oom")

    with pytest.raises(RuntimeError, match="some other cuda error"):
        _decode(vae)

    assert vae.tiling_calls == []


def test_vae_without_tiling_support_does_not_crash_on_oom_retry():
    vae = _FakeVAE(use_tiling=False, supports_tiling=False, fail_mode="once")

    image = _decode(vae)

    assert image is not None
    assert not hasattr(vae, "enable_tiling")
    assert not hasattr(vae, "disable_tiling")
