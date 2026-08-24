"""Tests for the VRAM-aware VAE encode/decode tiling
(native SeedVR2 video upscale OOMs at 5s/full-HD target resolution).

Covers the pure tile-sizing helper, the encode/decode shrink-on-OOM retry
loops (mocked VAE module, no real model or GPU), and the tile_size/
tile_overlap config ceiling. No real model or GPU is touched.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import torch

from src.pipelines.pipes.generator.seedvr2 import main as m
from src.pipelines.pipes.generator.seedvr2.main import (
    SeedVR2NativeGenerator,
    _adaptive_tile_size,
    _CAUSAL3D_DECODE_MB_PER_LATENT_PX,
    _CAUSAL3D_ENCODE_MB_PER_PIXEL,
    _MIN_DECODE_TILE_LATENT,
    _MIN_ENCODE_TILE_PIXEL,
)


# -- _adaptive_tile_size (pure) ----------------------------------------------

def test_adaptive_tile_size_no_vram_info_falls_back_to_full_long_axis():
    tile = _adaptive_tile_size(2001, 16, free_vram_gb_value=None, mb_per_px=1.0, frames=1)
    # Full long axis rounded UP to a multiple of 8: ((2001+7)//8)*8 == 2008.
    # (Was half-axis == 1000; SeedVR2's short clips want a whole-clip tile when
    # VRAM can't be queried, not an unconditional 2x2 split.)
    assert tile == 2008


def test_adaptive_tile_size_shrinks_with_less_free_vram():
    big = _adaptive_tile_size(2000, 16, free_vram_gb_value=40.0, mb_per_px=1.0, frames=1)
    small = _adaptive_tile_size(2000, 16, free_vram_gb_value=1.0, mb_per_px=1.0, frames=1)
    assert small < big


def test_adaptive_tile_size_scales_down_with_more_frames():
    one_frame = _adaptive_tile_size(2000, 16, free_vram_gb_value=4.0, mb_per_px=1.0, frames=1)
    many_frames = _adaptive_tile_size(2000, 16, free_vram_gb_value=4.0, mb_per_px=1.0, frames=8)
    assert many_frames < one_frame


def test_adaptive_tile_size_never_goes_below_floor():
    tile = _adaptive_tile_size(2000, 64, free_vram_gb_value=0.01, mb_per_px=1.0, frames=8)
    assert tile == 64


def test_adaptive_tile_size_never_exceeds_full_long_axis_snap():
    # With ample VRAM the tile is capped at the FULL long axis (whole clip in one
    # tile), never grown past it: ((512+7)//8)*8 == 512.
    tile = _adaptive_tile_size(512, 16, free_vram_gb_value=1000.0, mb_per_px=0.0001, frames=1)
    assert tile == 512


# -- calibrated decision matrix --------------------------------------
# The maintainer's profiled 5s/1080p clip: output 1872x1072 (234x134 latent),
# 2 latent frames per 5-frame batch, ~24GB free at decode time. With the honest
# constants the VAE encode/decode must cover it in a small number of tiles
# (was ~15 tiny tiles at the old 1.2 decode constant + half-axis start).

def _tile_count(long_axis: int, short_axis: int, tile: int) -> int:
    def n(axis: int) -> int:
        if axis <= tile:
            return 1
        overlap = min(tile // 2, max(8, tile // 8))
        step = tile - overlap
        return len(range(0, max(axis - overlap, 1), step))
    return n(long_axis) * n(short_axis)


def test_decode_tile_covers_full_hd_clip_in_few_tiles_on_24gb():
    # Decode tiles are latent px; the profiled batch-13 clip is 234x134 latent,
    # 13 OUTPUT frames (4 latent). Was tile=40 (~40 tiles) at the old constant.
    tile = _adaptive_tile_size(
        234, _MIN_DECODE_TILE_LATENT, free_vram_gb_value=24.0,
        mb_per_px=_CAUSAL3D_DECODE_MB_PER_LATENT_PX, frames=13,
    )
    assert 1 <= _tile_count(234, 134, tile) <= 4


def test_encode_tile_covers_full_hd_clip_in_few_tiles_on_24gb():
    # Encode tiles are pixel px; the 1080p input is 1872x1072, 13 input frames.
    # Was tile=256 (~40 tiles) at the old constant.
    tile = _adaptive_tile_size(
        1872, _MIN_ENCODE_TILE_PIXEL, free_vram_gb_value=24.0,
        mb_per_px=_CAUSAL3D_ENCODE_MB_PER_PIXEL, frames=13,
    )
    assert 1 <= _tile_count(1872, 1072, tile) <= 4


def test_small_batch_decodes_full_hd_clip_in_one_tile_on_24gb():
    # A short batch (5 output frames) is cheap enough that the whole 234x134
    # clip fits in ONE tile at 24GB — the byte-identical whole-clip path.
    tile = _adaptive_tile_size(
        234, _MIN_DECODE_TILE_LATENT, free_vram_gb_value=24.0,
        mb_per_px=_CAUSAL3D_DECODE_MB_PER_LATENT_PX, frames=5,
    )
    assert tile >= 234
    assert _tile_count(234, 134, tile) == 1


def test_decode_constant_far_below_inherited_ltx_wan_value():
    # The whole point of the recalibration: SeedVR2's 16-channel VAE spikes far
    # less per latent px than the inherited 1.2 (LTX/Wan) figure.
    assert _CAUSAL3D_DECODE_MB_PER_LATENT_PX < 0.2


# -- module constants sanity --------------------------------------------------

def test_encode_cost_constant_derived_from_decode_constant():
    # The encoder's per-pixel spike is modeled as the decoder's per-LATENT-pixel
    # spike divided by the 8x8 spatial downscale (64 image px per latent px).
    assert _CAUSAL3D_ENCODE_MB_PER_PIXEL == pytest.approx(_CAUSAL3D_DECODE_MB_PER_LATENT_PX / 64.0)


# -- _encode_clip / _decode_clip (mocked VAE module) -------------------------

class _FakeVAEModule:
    """Records tiled_encode/tiled_decode calls; can be told to OOM above a
    given tile size (simulating a real VAE that fits once the tile shrinks
    enough)."""

    def __init__(self, *, oom_above_tile=None, always_oom=False):
        self.oom_above_tile = oom_above_tile
        self.always_oom = always_oom
        self.encode_calls = []
        self.decode_calls = []

    def tiled_encode(self, pixels, tile_size, overlap):
        self.encode_calls.append((tile_size, overlap))
        if self.always_oom or (self.oom_above_tile is not None and tile_size > self.oom_above_tile):
            raise torch.cuda.OutOfMemoryError("synthetic OOM")
        b, _c, t, h, w = pixels.shape
        return torch.zeros((b, 16, 1 + (t - 1) // 4, h // 8, w // 8), dtype=pixels.dtype)

    def tiled_decode(self, latent, tile_size, overlap):
        self.decode_calls.append((tile_size, overlap))
        if self.always_oom or (self.oom_above_tile is not None and tile_size > self.oom_above_tile):
            raise torch.cuda.OutOfMemoryError("synthetic OOM")
        b, _c, t, h, w = latent.shape
        return torch.zeros((b, 3, t, h * 8, w * 8), dtype=latent.dtype)


def _make_generator(vae_module):
    gen = SeedVR2NativeGenerator.__new__(SeedVR2NativeGenerator)
    gen.vae = SimpleNamespace(
        module=vae_module, compute_dtype=torch.float32,
        move_to=lambda device: None, estimated_vram_gb=1.0,
    )
    gen.dit = SimpleNamespace(offload=lambda: None, estimated_vram_gb=1.0)
    return gen


def test_encode_clip_no_oom_uses_single_tiled_call(monkeypatch):
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: None)  # CPU-like: no live VRAM signal
    vae = _FakeVAEModule()
    gen = _make_generator(vae)
    clip = np.zeros((5, 64, 64, 3), dtype=np.uint8)

    out = gen._encode_clip(clip, "cpu")

    assert len(vae.encode_calls) == 1
    assert out.shape == (1, 16, 2, 8, 8)


def test_encode_clip_shrinks_tile_on_oom_until_it_fits(monkeypatch):
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: 40.0)  # plenty -> starts with a big tile
    vae = _FakeVAEModule(oom_above_tile=_MIN_ENCODE_TILE_PIXEL)  # only the floor tile succeeds
    gen = _make_generator(vae)
    clip = np.zeros((5, 2048, 2048, 3), dtype=np.uint8)

    out = gen._encode_clip(clip, "cpu")

    assert len(vae.encode_calls) > 1                      # it had to shrink at least once
    assert vae.encode_calls[-1][0] == _MIN_ENCODE_TILE_PIXEL
    assert out is not None


def test_encode_clip_raises_when_even_floor_tile_ooms(monkeypatch):
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: 40.0)
    vae = _FakeVAEModule(always_oom=True)
    gen = _make_generator(vae)
    clip = np.zeros((5, 512, 512, 3), dtype=np.uint8)

    with pytest.raises(torch.cuda.OutOfMemoryError):
        gen._encode_clip(clip, "cpu")

    assert vae.encode_calls[-1][0] == _MIN_ENCODE_TILE_PIXEL


def test_encode_clip_tile_size_config_caps_the_tile(monkeypatch):
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: 1000.0)  # would pick a huge tile otherwise
    vae = _FakeVAEModule()
    gen = _make_generator(vae)
    clip = np.zeros((5, 2048, 2048, 3), dtype=np.uint8)

    gen._encode_clip(clip, "cpu", tile_size=256)

    tile_used = vae.encode_calls[0][0]
    assert tile_used <= 256


def test_decode_clip_no_oom_uses_single_tiled_call(monkeypatch):
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: None)
    vae = _FakeVAEModule()
    gen = _make_generator(vae)
    latent = torch.zeros((1, 16, 2, 8, 8))

    out = gen._decode_clip(latent, "cpu")

    assert len(vae.decode_calls) == 1
    assert out.shape == (2, 64, 64, 3)
    assert out.dtype == np.uint8


def test_decode_clip_shrinks_tile_on_oom_until_it_fits(monkeypatch):
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: 40.0)
    vae = _FakeVAEModule(oom_above_tile=_MIN_DECODE_TILE_LATENT)
    gen = _make_generator(vae)
    latent = torch.zeros((1, 16, 2, 256, 256))

    out = gen._decode_clip(latent, "cpu")

    assert len(vae.decode_calls) > 1
    assert vae.decode_calls[-1][0] == _MIN_DECODE_TILE_LATENT
    assert out is not None


def test_decode_clip_tile_scales_with_output_frames(monkeypatch):
    # Decode VRAM scales with OUTPUT frames (1 + 4*(latent-1)), so a 4-latent
    # clip (13 output frames) must pick a SMALLER tile than a 2-latent clip (5
    # output frames) at the same free VRAM — the honest frame count.
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: 24.0)

    vae2 = _FakeVAEModule()
    gen2 = _make_generator(vae2)
    gen2._decode_clip(torch.zeros((1, 16, 2, 256, 256)), "cpu")
    tile_2lat = vae2.decode_calls[0][0]

    vae4 = _FakeVAEModule()
    gen4 = _make_generator(vae4)
    gen4._decode_clip(torch.zeros((1, 16, 4, 256, 256)), "cpu")
    tile_4lat = vae4.decode_calls[0][0]

    assert tile_4lat < tile_2lat


def test_decode_clip_evicts_foreign_residents_once_at_floor_then_retries(monkeypatch):
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: 4.0)
    calls = {"offload_all": 0}

    class _FakeManager:
        def offload_all(self, device, exclude=()):
            calls["offload_all"] += 1
            return []

    monkeypatch.setattr(m, "get_residency_manager", lambda: _FakeManager())

    # Only ONE eviction retry is attempted at the floor tile (mirrors
    # NativeGenerator's single-retry _free_for_decode_retry semantics, not an
    # open-ended loop): OOMs through the shrink loop down to the floor, OOMs
    # once AT the floor (triggering the eviction retry), then succeeds.
    class _EventuallySucceeds(_FakeVAEModule):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def tiled_decode(self, latent, tile_size, overlap):
            self.attempts += 1
            self.decode_calls.append((tile_size, overlap))
            # OOM through every above-floor shrink AND the first floor attempt,
            # so the floor OOM triggers the one-shot foreign-resident eviction;
            # the retry after eviction (also at the floor) is what succeeds. The
            # full-axis tile start reaches the floor in one more shrink
            # than the old half-axis start, so the threshold tracks attempt count
            # rather than a fixed tile size.
            if tile_size > _MIN_DECODE_TILE_LATENT or self.attempts <= 3:
                raise torch.cuda.OutOfMemoryError("synthetic OOM")
            b, _c, t, h, w = latent.shape
            return torch.zeros((b, 3, t, h * 8, w * 8), dtype=latent.dtype)

    vae = _EventuallySucceeds()
    gen = _make_generator(vae)
    latent = torch.zeros((1, 16, 1, 64, 64))

    out = gen._decode_clip(latent, "cpu")

    assert calls["offload_all"] == 1
    assert out is not None
    # The shrink loop bottomed out at the floor, then the eviction retry
    # (also at the floor) is what finally succeeded.
    assert vae.decode_calls[-1][0] == _MIN_DECODE_TILE_LATENT
    assert vae.decode_calls[-2][0] == _MIN_DECODE_TILE_LATENT


def test_decode_clip_raises_after_eviction_retry_also_ooms(monkeypatch):
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: 4.0)

    class _FakeManager:
        def offload_all(self, device, exclude=()):
            return []

    monkeypatch.setattr(m, "get_residency_manager", lambda: _FakeManager())
    vae = _FakeVAEModule(always_oom=True)
    gen = _make_generator(vae)
    latent = torch.zeros((1, 16, 1, 64, 64))

    with pytest.raises(torch.cuda.OutOfMemoryError):
        gen._decode_clip(latent, "cpu")


def test_decode_clip_tile_overlap_config_caps_the_overlap(monkeypatch):
    monkeypatch.setattr(m, "effective_free_vram_gb", lambda device: 1000.0)
    vae = _FakeVAEModule()
    gen = _make_generator(vae)
    latent = torch.zeros((1, 16, 1, 512, 512))

    gen._decode_clip(latent, "cpu", tile_overlap=16)  # -> latent-space cap of 2

    overlap_used = vae.decode_calls[0][1]
    assert overlap_used <= 2
