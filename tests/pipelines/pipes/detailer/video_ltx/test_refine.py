"""Tube-refine tests for the LTX video detailer.

The pure, load-bearing custom logic -- the strength->sigma mapping and the
temporal-grid pad -- plus the ``refine_tube_pixels`` wiring (encode -> denoise
with our sigmas -> decode -> trim), with ``denoise``/``place_dit_for_sequence``
patched at the module boundary so no real model or GPU is touched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.pipelines.pipes.detailer.video_ltx import refine as R
from src.pipelines.pipes.detailer.video_ltx.refine import (
    STRENGTH_SIGMA_START,
    pad_pixels_to_temporal_grid,
    refine_tube_pixels,
    strength_to_refine_sigmas,
)

_MOD = "src.pipelines.pipes.detailer.video_ltx.refine"
# refine_tube_pixels's encode now routes through the shared
# ladder in this module -- its own get_residency_manager/clear_gpu_memory/
# free_vram_gb calls live HERE, not in `_MOD`, so tests that exercise the
# "estimate exceeds budget" / OOM-retry rungs must patch this namespace.
_SHARED_MOD = "src.pipelines.pipes._shared.vae.ltx_tiled_encode"
# Same story for the decode side: refine_tube_pixels's decode routes through
# the shared decode ladder's own get_residency_manager/clear_gpu_memory.
_SHARED_DECODE_MOD = "src.pipelines.pipes._shared.vae.ltx_tiled_decode"


def _fake_placement(mode="resident", weight_budget_gb=10.0):
    """Stand-in for `place_dit_for_sequence`'s `DitPlacementDecision` return
    value (`refine_tube_pixels` now reads
    `.weight_budget_gb`/`.mode` off it for the decode's profiler mark)."""
    return SimpleNamespace(mode=mode, weight_budget_gb=weight_budget_gb)


# -- strength -> sigma ----------------------------------------------------


@pytest.mark.parametrize("strength,start", list(STRENGTH_SIGMA_START.items()))
def test_strength_to_refine_sigmas_starts_at_mapped_value(strength, start):
    sig = strength_to_refine_sigmas(strength)
    assert float(sig[0]) == pytest.approx(start)
    assert float(sig[-1]) == 0.0


def test_strength_to_refine_sigmas_is_strictly_descending():
    for strength in ("light", "balanced", "strong"):
        sig = strength_to_refine_sigmas(strength).tolist()
        assert all(a > b for a, b in zip(sig, sig[1:]))


def test_strength_to_refine_sigmas_unknown_falls_back_to_balanced():
    assert torch.equal(strength_to_refine_sigmas("nonsense"), strength_to_refine_sigmas("balanced"))


def test_stronger_strength_reinjects_more_noise():
    assert float(strength_to_refine_sigmas("strong")[0]) > float(strength_to_refine_sigmas("light")[0])


# -- (2026-07-16 quality round): restoration-grade recalibration.
# The starts moved to 0.40/0.55/0.70, and the tail LENGTHENS with strength so a
# stronger rebuild gets more denoise steps, not just a steeper first hop.


def test_recalibrated_starts_are_restoration_grade():
    assert STRENGTH_SIGMA_START == {"light": 0.40, "balanced": 0.55, "strong": 0.70}


def test_tail_lengthens_with_strength():
    # ~4 nodes at the light floor, ~5 balanced, ~6 strong
    assert strength_to_refine_sigmas("light").numel() == 4
    assert strength_to_refine_sigmas("balanced").numel() == 5
    assert strength_to_refine_sigmas("strong").numel() == 6
    # strictly more nodes as strength rises
    counts = [strength_to_refine_sigmas(s).numel() for s in ("light", "balanced", "strong")]
    assert counts[0] < counts[1] < counts[2]


def test_strong_tail_stays_on_lightricks_shape_and_descends_to_zero():
    sig = strength_to_refine_sigmas("strong").tolist()
    assert sig[0] == pytest.approx(0.70)
    assert sig[-1] == 0.0
    assert all(a > b for a, b in zip(sig, sig[1:]))  # still strictly descending
    # every interior node sits within the scaled shape's span (a resample of the
    # descending curve, never above the start nor below 0)
    assert all(0.0 <= v <= 0.70 for v in sig)


# -- temporal pad ---------------------------------------------------------


def test_pad_pixels_to_temporal_grid_rounds_up_repeating_last():
    px = torch.arange(10).float().reshape(1, 1, 10, 1, 1).expand(1, 3, 10, 2, 2).contiguous()
    padded, orig = pad_pixels_to_temporal_grid(px)  # 10 -> 17 (1 + 16*8)
    assert orig == 10
    assert padded.shape[2] == 17
    assert torch.equal(padded[:, :, :10], px)
    assert torch.equal(padded[:, :, 10:], px[:, :, -1:].expand(1, 3, 7, 2, 2))


def test_pad_pixels_to_temporal_grid_noop_when_on_grid():
    px = torch.rand(1, 3, 9, 2, 2)  # 9 == 1 + 8
    padded, orig = pad_pixels_to_temporal_grid(px)
    assert orig == 9 and padded.shape[2] == 9
    assert padded is px


# -- refine wiring --------------------------------------------------------


def _bundle():
    enc_seen = {}

    def encode(pixels):
        enc_seen["shape"] = tuple(pixels.shape)
        b, c, t, h, w = pixels.shape
        return torch.zeros(1, 128, (t - 1) // 8 + 1, h // 32, w // 32)

    def decode(latent):
        # padded temporal length (17) -> refine trims back to orig (10)
        return torch.zeros(1, 3, 17, 32, 32)

    vae = SimpleNamespace(
        compute_dtype=torch.float32,
        move_to=lambda d: enc_seen.setdefault("vae_moved", []).append(d) or enc_seen["vae_moved"],
        module=SimpleNamespace(encode=encode, decode=decode),
    )
    dit = SimpleNamespace(compute_dtype=torch.float32, module=SimpleNamespace())
    spec = SimpleNamespace(sampling_settings={"shift": 2.37})
    return SimpleNamespace(vae=vae, dit=dit, spec=spec), enc_seen


def test_refine_tube_pixels_wires_encode_denoise_decode(monkeypatch):
    bundle, enc_seen = _bundle()
    cond_model = SimpleNamespace(embeds={"context": torch.zeros(1, 1, 8)})
    calls = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        calls["uncond"] = uncond
        calls["guidance_scale"] = kw["guidance_scale"]
        calls["sigmas"] = kw["sigmas"]
        calls["cfg_zero_star"] = kw["cfg_zero_star"]
        return latents  # pass the encoded latent straight through

    monkeypatch.setattr(f"{_MOD}.denoise", fake_denoise)
    monkeypatch.setattr(f"{_MOD}.place_dit_for_sequence", lambda *a, **k: _fake_placement())

    pixels = torch.zeros(1, 3, 10, 32, 32)  # 10 frames -> padded to 17 for encode
    out = refine_tube_pixels(bundle, cond_model, pixels, strength="balanced",
                             device="cpu", fps=24.0, seed=7)

    # encode saw the temporally-padded tube (17), not the raw 10
    assert enc_seen["shape"][2] == 17
    # refine runs CFG 1.0, no negative pass, our balanced sigma schedule
    assert calls["uncond"] is None
    assert calls["guidance_scale"] == 1.0
    assert calls["cfg_zero_star"] is False
    assert torch.equal(calls["sigmas"], strength_to_refine_sigmas("balanced"))
    # output trimmed back to the original 10 frames, uint8 (T,H,W,3)
    assert out.shape == (10, 32, 32, 3)
    assert out.dtype.name == "uint8"


def test_refine_tube_pixels_places_dit_for_the_tube(monkeypatch):
    bundle, _ = _bundle()
    cond_model = SimpleNamespace(embeds={"context": torch.zeros(1, 1, 8)})
    placed = {}

    def fake_place(dit, device, *, video_tokens, own_models, reserve_gb=0.0):
        placed["video_tokens"] = video_tokens
        placed["reserve_gb"] = reserve_gb
        return _fake_placement()

    monkeypatch.setattr(f"{_MOD}.denoise", lambda mf, latents, *a, **k: latents)
    monkeypatch.setattr(f"{_MOD}.place_dit_for_sequence", fake_place)

    refine_tube_pixels(bundle, cond_model, torch.zeros(1, 3, 9, 64, 64),
                       strength="light", device="cpu", fps=24.0)
    # tube tokens = t_lat*h_lat*w_lat = 2 * (64/32) * (64/32) = 2*2*2 = 8
    assert placed["video_tokens"] == 8
    # A decode-headroom reserve is now always passed,
    # sized off this tube's own (small) decoded pixel dimensions.
    assert placed["reserve_gb"] > 0.0


# -- OOM fix: the tube encode goes through the shared VRAM-aware
# whole-clip/tiled ladder (`_shared/vae/ltx_tiled_encode.py`) instead of a
# plain `vae.module.encode` -- a tube spanning a long track at working
# resolution can be a multi-GB encode, the same OOM shape the standalone
# upscale path already fixed.


def test_refine_tube_pixels_uses_tiled_encode_when_estimate_exceeds_budget(monkeypatch):
    bundle, enc_seen = _bundle()
    tiled_seen = {}

    def tiled_encode(pixels, tiling_config):
        tiled_seen["shape"] = tuple(pixels.shape)
        b, c, t, h, w = pixels.shape
        return torch.zeros(1, 128, (t - 1) // 8 + 1, h // 32, w // 32)

    bundle.vae.module.tiled_encode = tiled_encode
    cond_model = SimpleNamespace(embeds={"context": torch.zeros(1, 1, 8)})
    monkeypatch.setattr(f"{_MOD}.denoise", lambda mf, latents, *a, **k: latents)
    monkeypatch.setattr(f"{_MOD}.place_dit_for_sequence", lambda *a, **k: _fake_placement())

    pixels = torch.zeros(1, 3, 10, 32, 32)  # -> padded to 17 frames for encode
    with patch(f"{_SHARED_MOD}.free_vram_gb", return_value=0.0), \
         patch(f"{_SHARED_MOD}.get_residency_manager") as mock_grm, \
         patch(f"{_SHARED_MOD}.clear_gpu_memory") as mock_clear:
        out = refine_tube_pixels(bundle, cond_model, pixels, strength="balanced",
                                 device="cpu", fps=24.0, seed=7)

    assert tiled_seen["shape"][2] == 17  # tiled_encode saw the padded tube
    assert "shape" not in enc_seen       # whole-clip encode was never attempted
    mock_grm.return_value.offload_all.assert_called_once()
    mock_clear.assert_called_once()
    assert out.shape == (10, 32, 32, 3)


def test_refine_tube_pixels_retries_whole_clip_once_after_oom_then_succeeds(monkeypatch):
    bundle, enc_seen = _bundle()
    real_encode = bundle.vae.module.encode
    calls = {"n": 0}

    def flaky_encode(pixels):
        calls["n"] += 1
        if calls["n"] == 1:
            raise torch.cuda.OutOfMemoryError("boom")
        return real_encode(pixels)

    bundle.vae.module.encode = flaky_encode
    cond_model = SimpleNamespace(embeds={"context": torch.zeros(1, 1, 8)})
    monkeypatch.setattr(f"{_MOD}.denoise", lambda mf, latents, *a, **k: latents)
    monkeypatch.setattr(f"{_MOD}.place_dit_for_sequence", lambda *a, **k: _fake_placement())

    pixels = torch.zeros(1, 3, 10, 32, 32)
    with patch(f"{_SHARED_MOD}.free_vram_gb", return_value=20.0), \
         patch(f"{_SHARED_MOD}.get_residency_manager") as mock_grm, \
         patch(f"{_SHARED_MOD}.clear_gpu_memory"):
        out = refine_tube_pixels(bundle, cond_model, pixels, strength="balanced",
                                 device="cpu", fps=24.0, seed=7)

    assert calls["n"] == 2  # first attempt OOM'd, the retry succeeded
    assert enc_seen["shape"][2] == 17
    mock_grm.return_value.offload_all.assert_called_once()
    assert out.shape == (10, 32, 32, 3)


# -- A tiny tube's near-floor activation reserve alone
# lets the DiT go fully resident (correct for its OWN denoise), but leaves the
# tube's SUBSEQUENT decode with almost no headroom (live maintainer OOM:
# 27.87GB allocated, 109MB free). `estimate_decode_reserve_gb` sizes extra
# headroom from the tube's own decoded pixel dims, and `place_dit_for_sequence`
# is now called with it -- forcing partial residency when it matters -- with
# an OOM-tolerant decode retry as a belt underneath.


def test_estimate_decode_reserve_gb_floors_at_the_chunk_safety_multiple():
    # A tiny tube: the pixel-volume scaling term is negligible, so the floor
    # (8 x _MAX_CHUNK_BYTES == exactly 1 GiB) dominates.
    assert R.estimate_decode_reserve_gb(t_pixel=9, h_pixel=32, w_pixel=32) == pytest.approx(1.0, abs=1e-6)
    assert R.estimate_decode_reserve_gb(t_pixel=1, h_pixel=32, w_pixel=32) == pytest.approx(1.0, abs=1e-6)


def test_estimate_decode_reserve_gb_scales_with_pixel_volume_for_large_tubes():
    small = R.estimate_decode_reserve_gb(9, 512, 512)
    large = R.estimate_decode_reserve_gb(241, 768, 512)  # a track spanning most of a clip
    bigger = R.estimate_decode_reserve_gb(241, 1536, 1024)
    assert small < large < bigger
    # Comfortably inside the sanity ballpark for a long/large tube.
    assert 1.0 <= large <= 6.0


def test_refine_tube_pixels_passes_the_estimated_decode_reserve_to_placement(monkeypatch):
    """The exact round-2 wiring: `place_dit_for_sequence` must receive
    a `reserve_gb` matching this tube's own decoded pixel dimensions -- not
    zero, and not a flat unrelated constant."""
    bundle, _ = _bundle()
    cond_model = SimpleNamespace(embeds={"context": torch.zeros(1, 1, 8)})
    placed = {}

    def fake_place(dit, device, *, video_tokens, own_models, reserve_gb=0.0):
        placed["reserve_gb"] = reserve_gb
        return _fake_placement()

    monkeypatch.setattr(f"{_MOD}.denoise", lambda mf, latents, *a, **k: latents)
    monkeypatch.setattr(f"{_MOD}.place_dit_for_sequence", fake_place)

    # 9 frames (already on the temporal grid, no pad); 64x64 -> t_lat=2, h_lat=w_lat=2
    refine_tube_pixels(bundle, cond_model, torch.zeros(1, 3, 9, 64, 64),
                       strength="light", device="cpu", fps=24.0)

    expected = R.estimate_decode_reserve_gb(t_pixel=9, h_pixel=64, w_pixel=64)
    assert placed["reserve_gb"] == pytest.approx(expected)


# -- decode fix: seeded generator + shared OOM/tiled ladder ----------------
#
# The tube decode used to hand-roll its own retry (`_decode_tube_with_oom_retry`,
# now deleted) with NO `generator=` -- on the LTX-2.5 diffusion VAE, which
# SAMPLES the pixels it denoises, that meant the same seed decoded differently
# every run. `refine_tube_pixels` now routes its decode through the shared
# `_shared/vae/ltx_tiled_decode.py::decode_with_oom_retry` ladder (the same one
# `generator/txt2vid_ltx` uses), seeded off `seed + DECODE_NOISE_SEED_OFFSET`.


def test_refine_tube_pixels_decode_is_seeded_and_deterministic(monkeypatch):
    bundle, _ = _bundle()
    cond_model = SimpleNamespace(embeds={"context": torch.zeros(1, 1, 8)})
    monkeypatch.setattr(f"{_MOD}.denoise", lambda mf, latents, *a, **k: latents)
    monkeypatch.setattr(f"{_MOD}.place_dit_for_sequence", lambda *a, **k: _fake_placement())

    captured = []

    def fake_decode_with_oom_retry(vae, latent, device, *, generator=None, **kw):
        captured.append(generator)
        # a stand-in for a decoder that SAMPLES its pixels: draw from the
        # generator, so a missing/differently-seeded generator changes the
        # output, just like the real LTX-2.5 diffusion decoder.
        return torch.randn(1, 3, 17, 32, 32, generator=generator)

    monkeypatch.setattr(f"{_MOD}.decode_with_oom_retry", fake_decode_with_oom_retry)

    pixels = torch.zeros(1, 3, 10, 32, 32)
    out1 = refine_tube_pixels(bundle, cond_model, pixels, strength="balanced",
                              device="cpu", fps=24.0, seed=7)
    out2 = refine_tube_pixels(bundle, cond_model, pixels, strength="balanced",
                              device="cpu", fps=24.0, seed=7)
    out3 = refine_tube_pixels(bundle, cond_model, pixels, strength="balanced",
                              device="cpu", fps=24.0, seed=8)

    assert len(captured) == 3 and all(g is not None for g in captured)
    assert captured[0].initial_seed() == captured[1].initial_seed() == 7 + R.DECODE_NOISE_SEED_OFFSET
    assert captured[2].initial_seed() == 8 + R.DECODE_NOISE_SEED_OFFSET
    assert np.array_equal(out1, out2)          # same seed -> identical decode
    assert not np.array_equal(out1, out3)       # different seed -> different decode


def test_refine_tube_pixels_decode_oom_retries_via_shared_ladder_end_to_end(monkeypatch):
    """Integration: the tube decode survives an OOM through the shared ladder's
    own evict-and-retry rung (`_shared/vae/ltx_tiled_decode.py`), not a
    hand-rolled `bundle.dit.offload()`."""
    bundle, enc_seen = _bundle()
    calls = {"n": 0}

    def flaky_decode(latent):
        calls["n"] += 1
        if calls["n"] == 1:
            raise torch.cuda.OutOfMemoryError("boom")
        return torch.zeros(1, 3, 17, 32, 32)

    bundle.vae.module.decode = flaky_decode
    cond_model = SimpleNamespace(embeds={"context": torch.zeros(1, 1, 8)})
    monkeypatch.setattr(f"{_MOD}.denoise", lambda mf, latents, *a, **k: latents)
    monkeypatch.setattr(f"{_MOD}.place_dit_for_sequence", lambda *a, **k: _fake_placement())

    pixels = torch.zeros(1, 3, 10, 32, 32)
    with patch(f"{_SHARED_DECODE_MOD}.get_residency_manager") as mock_grm, \
         patch(f"{_SHARED_DECODE_MOD}.clear_gpu_memory") as mock_clear:
        out = refine_tube_pixels(bundle, cond_model, pixels, strength="balanced",
                                 device="cpu", fps=24.0, seed=7)

    assert calls["n"] == 2       # first decode attempt OOM'd, the retry succeeded
    mock_grm.return_value.offload_all.assert_called_once_with("cpu", exclude=(bundle.vae,))
    mock_clear.assert_called_once()
    assert out.shape == (10, 32, 32, 3)
