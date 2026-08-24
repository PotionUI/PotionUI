"""Tests for the shared LTX VRAM-aware whole-clip/tiled VAE encode ladder
(extraction of the ``latent_upscaler/ltx`` logic).

Direct unit coverage of ``encode_with_oom_retry`` against a fake VAE
component -- no real model/GPU. ``latent_upscaler/ltx``'s own test suite
(``tests/pipelines/pipes/latent_upscaler/ltx/test_latent_upscaler_ltx.py``)
covers the same ladder through its thin ``_encode_with_oom_retry`` wrapper,
proving the extraction stayed behavior-preserving there; this file is the
ladder's OWN test, callable by any pipe (here: a caller name distinct from
``latent_upscaler/ltx``, to prove the profiler mark / log prefix are truly
caller-supplied and not hardcoded).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import torch

from src.platform.runtime.native.vae.ltx_tiling import LtxTilingConfig
from src.pipelines.pipes._shared.vae.ltx_tiled_encode import (
    ENCODE_BYTES_PER_PIXEL_FRAME,
    encode_with_oom_retry,
    estimate_whole_clip_encode_gb,
)

_MOD = "src.pipelines.pipes._shared.vae.ltx_tiled_encode"


class _FakeVae:
    """Stand-in for a bundle's ``vae`` (``NativeModel``-shaped): ``.module``
    with ``encode``/``tiled_encode``, ``.compute_dtype``."""

    def __init__(self, encode=None, tiled_encode=None):
        self.compute_dtype = torch.float32
        self.module = SimpleNamespace(encode=encode, tiled_encode=tiled_encode)


class _FakeResidencyManager:
    def __init__(self):
        self.offload_all_calls = []

    def offload_all(self, device, *, exclude=()):
        self.offload_all_calls.append((device, tuple(exclude)))
        return []


def test_estimate_whole_clip_encode_gb_matches_derivation():
    pixels = torch.zeros(1, 3, 121, 480, 832)
    assert estimate_whole_clip_encode_gb(pixels) == pytest.approx(
        ENCODE_BYTES_PER_PIXEL_FRAME * 121 * 480 * 832 / (1024 ** 3)
    )
    # Sanity-check against the maintainer's original datapoint: ~26GiB.
    assert estimate_whole_clip_encode_gb(pixels) == pytest.approx(26.0, abs=0.01)


def test_encode_succeeds_on_first_try_when_plausibly_fits():
    calls = []

    def encode(pixels):
        calls.append(pixels)
        return torch.zeros(1, 8, 3, 4, 4)

    vae = _FakeVae(encode=encode)
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear, \
         patch(f"{_MOD}.free_vram_gb", return_value=20.0):
        latent = encode_with_oom_retry(vae, pixels, "cuda", profiler_mark="caller.encode")

    assert len(calls) == 1
    assert manager.offload_all_calls == []
    mock_clear.assert_not_called()
    assert latent.shape == (1, 8, 3, 4, 4)


def test_encode_retries_once_after_oom_then_succeeds():
    calls = {"n": 0}

    def flaky_encode(pixels):
        calls["n"] += 1
        if calls["n"] == 1:
            raise torch.cuda.OutOfMemoryError("boom")
        return torch.zeros(1, 8, 3, 4, 4)

    vae = _FakeVae(encode=flaky_encode)
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear, \
         patch(f"{_MOD}.free_vram_gb", return_value=1.0):
        latent = encode_with_oom_retry(vae, pixels, "cuda", profiler_mark="caller.encode")

    assert calls["n"] == 2
    assert manager.offload_all_calls == [("cuda", (vae,))]
    mock_clear.assert_called_once()
    assert latent.shape == (1, 8, 3, 4, 4)


def test_encode_falls_back_to_tiled_after_oom_retry_fails():
    def always_oom(pixels):
        raise torch.cuda.OutOfMemoryError("boom")

    tiled_calls = []

    def tiled_encode(pixels, tiling_config):
        tiled_calls.append(tiling_config)
        return torch.zeros(1, 8, 3, 4, 4)

    vae = _FakeVae(encode=always_oom, tiled_encode=tiled_encode)
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory"), \
         patch(f"{_MOD}.free_vram_gb", return_value=1.0):
        latent = encode_with_oom_retry(vae, pixels, "cuda", profiler_mark="caller.encode")

    assert len(tiled_calls) == 1
    assert tiled_calls[0] == LtxTilingConfig.default()
    assert latent.shape == (1, 8, 3, 4, 4)


def test_encode_skips_whole_clip_when_estimate_exceeds_budget():
    """When the T*H*W activation estimate exceeds the
    free-VRAM budget, the ladder goes straight to tiled_encode -- proving a
    CALLER OTHER THAN latent_upscaler (e.g. a detailer tube) gets the exact
    same estimate-then-choose behavior."""
    encode_calls = []

    def whole_encode(pixels):
        encode_calls.append(pixels)
        return torch.zeros(1, 8, 3, 4, 4)  # would succeed if ever called

    tiled_calls = []

    def tiled_encode(pixels, tiling_config):
        tiled_calls.append(tiling_config)
        return torch.zeros(1, 8, 3, 4, 4)

    vae = _FakeVae(encode=whole_encode, tiled_encode=tiled_encode)
    pixels = torch.rand(1, 3, 9, 64, 96)  # T*H*W=55296 -> ~0.03GB estimate

    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory") as mock_clear, \
         patch(f"{_MOD}.free_vram_gb", return_value=0.01):
        latent = encode_with_oom_retry(vae, pixels, "cuda", profiler_mark="detailer.tube_encode")

    assert encode_calls == []
    assert len(tiled_calls) == 1
    assert manager.offload_all_calls == [("cuda", (vae,))]
    mock_clear.assert_called_once()
    assert latent.shape == (1, 8, 3, 4, 4)


def test_encode_raises_clear_error_when_tiled_also_ooms():
    def always_oom(pixels):
        raise torch.cuda.OutOfMemoryError("boom")

    def tiled_always_oom(pixels, tiling_config):
        raise torch.cuda.OutOfMemoryError("boom (tiled)")

    vae = _FakeVae(encode=always_oom, tiled_encode=tiled_always_oom)
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory"), \
         patch(f"{_MOD}.free_vram_gb", return_value=1.0):
        with pytest.raises(torch.cuda.OutOfMemoryError, match="even with tiled encoding"):
            encode_with_oom_retry(vae, pixels, "cuda", profiler_mark="caller.encode", log_prefix="caller")


def test_profiler_mark_and_log_prefix_are_caller_supplied_not_hardcoded():
    """Proves the shared ladder is truly generic: two different callers get
    two different profiler mark names / log prefixes out of the SAME
    function, rather than a name baked in for latent_upscaler only."""
    vae = _FakeVae(encode=lambda pixels: torch.zeros(1, 8, 3, 4, 4))
    pixels = torch.rand(1, 3, 9, 64, 96)
    fake_profiler = Mock()

    with patch(f"{_MOD}.get_profiler", return_value=fake_profiler), \
         patch(f"{_MOD}.free_vram_gb", return_value=20.0):
        encode_with_oom_retry(vae, pixels, "cuda", profiler_mark="detailer.tube_encode")

    marks = [call.args[0] for call in fake_profiler.mark.call_args_list]
    assert marks == ["detailer.tube_encode"]


def test_encode_uses_provided_tiling_config():
    custom = LtxTilingConfig.default()
    seen = {}

    def tiled_encode(pixels, tiling_config):
        seen["tiling_config"] = tiling_config
        return torch.zeros(1, 8, 3, 4, 4)

    vae = _FakeVae(
        encode=lambda pixels: (_ for _ in ()).throw(torch.cuda.OutOfMemoryError("boom")),
        tiled_encode=tiled_encode,
    )
    pixels = torch.rand(1, 3, 9, 64, 96)

    manager = _FakeResidencyManager()
    with patch(f"{_MOD}.get_residency_manager", return_value=manager), \
         patch(f"{_MOD}.clear_gpu_memory"), \
         patch(f"{_MOD}.free_vram_gb", return_value=1.0):
        encode_with_oom_retry(vae, pixels, "cuda", tiling_config=custom, profiler_mark="caller.encode")

    assert seen["tiling_config"] is custom
