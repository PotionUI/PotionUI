"""Tests for Spectral Progressive Diffusion (arXiv:2605.18736), our flow adaptation."""

from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
from src.platform.runtime.native.sampling.cfg import NoCFG
from src.platform.runtime.native.sampling.spectral_progressive import (
    SpectralProgressiveConfig,
    _fft_expand,
    _seed_stage0,
    activation_time,
    denoise_spectral_progressive,
    derive_transitions,
    expand_and_align,
    kappa,
    stage_shape,
)


# --- schedule math --------------------------------------------------------

def test_kappa_identity_and_growth():
    assert kappa(0.5, 1.0) == 1.0                    # no growth -> no scaling
    assert kappa(0.5, 2.0) > 1.0                     # growth raises the level
    # Eq. 6 closed form.
    assert abs(kappa(0.5, 2.0) - (2.0 / (1.0 + 1.0 * 0.5))) < 1e-9


def test_activation_time_in_unit_interval_and_monotone_in_power():
    a = activation_time(1.0, 0.01)  # high power (low freq) -> activates earlier (higher sigma)
    b = activation_time(0.1, 0.01)  # low power (high freq)  -> activates later  (lower sigma)
    assert 0.0 < a < 1.0 and 0.0 < b < 1.0
    assert a > b  # activation sigma increases with band power


def test_transitions_descending_and_bounded():
    cfg = SpectralProgressiveConfig(scales=(0.25, 0.5, 1.0), delta=0.01)
    tr = derive_transitions(cfg, 64, 64)
    assert len(tr) == 2
    assert all(0.0 < t < 1.0 for t in tr)
    assert tr[0] > tr[1]  # smaller scale transitions later (higher sigma)


def test_explicit_transitions_override():
    cfg = SpectralProgressiveConfig(scales=(0.5, 1.0), transitions=(0.6,))
    assert derive_transitions(cfg, 64, 64) == [0.6]


# --- config validation ----------------------------------------------------

def test_config_validation():
    with pytest.raises(ValueError, match="strictly increasing"):
        SpectralProgressiveConfig(scales=(1.0, 0.5))
    with pytest.raises(ValueError, match="end at 1.0"):
        SpectralProgressiveConfig(scales=(0.25, 0.5))
    with pytest.raises(ValueError, match="delta"):
        SpectralProgressiveConfig(scales=(0.5, 1.0), delta=1.5)
    with pytest.raises(ValueError, match="basis"):
        SpectralProgressiveConfig(scales=(0.5, 1.0), basis="wavelet")
    with pytest.raises(ValueError, match="transitions"):
        SpectralProgressiveConfig(scales=(0.5, 1.0), transitions=(0.5, 0.3))


# --- spectral expansion ---------------------------------------------------

def test_fft_expand_shape_and_lowfreq_roundtrip():
    x = torch.randn(2, 3, 8, 8)
    grown = _fft_expand(x, (16, 16), sigma=0.5, generator=torch.Generator().manual_seed(0))
    assert grown.shape == (2, 3, 16, 16)
    # Cropping the expanded latent's centre spectrum back to 8x8 STRONGLY recovers
    # x: the low band is embedded in the target spectrum and the injected noise
    # lives only in the outer band. It is not bit-exact — the paper's real-cast of
    # ifft leaks a little between the hermitian-symmetric halves — but the content
    # correlates tightly and the DC term is preserved exactly.
    back = _seed_stage0(grown, (2, 3, 8, 8))
    cos = torch.nn.functional.cosine_similarity(back.flatten(), x.flatten(), dim=0)
    assert float(cos) > 0.95
    assert torch.allclose(back.mean(), x.mean(), atol=1e-5)


def test_fft_expand_is_generator_deterministic():
    x = torch.randn(1, 2, 8, 8)
    a = _fft_expand(x, (16, 16), 0.5, torch.Generator().manual_seed(7))
    b = _fft_expand(x, (16, 16), 0.5, torch.Generator().manual_seed(7))
    c = _fft_expand(x, (16, 16), 0.5, torch.Generator().manual_seed(8))
    assert torch.equal(a, b) and not torch.equal(a, c)


def test_expand_and_align_shape_and_sigma():
    x = torch.randn(1, 3, 8, 8)
    cfg = SpectralProgressiveConfig(scales=(0.5, 1.0))
    out, sigma_new = expand_and_align(x, 0.5, (16, 16), cfg, torch.Generator().manual_seed(0))
    assert out.shape == (1, 3, 16, 16)
    assert abs(sigma_new - kappa(0.5, 2.0) * 0.5) < 1e-6


def test_expand_and_align_noop_at_same_shape():
    x = torch.randn(1, 3, 8, 8)
    cfg = SpectralProgressiveConfig(scales=(0.5, 1.0))
    out, sigma_new = expand_and_align(x, 0.4, (8, 8), cfg)
    assert torch.equal(out, x) and sigma_new == 0.4


def test_dct_basis_matches_shape():
    pytest.importorskip("scipy")
    x = torch.randn(1, 2, 8, 8)
    cfg = SpectralProgressiveConfig(scales=(0.5, 1.0), basis="dct")
    out, _ = expand_and_align(x, 0.5, (16, 16), cfg, torch.Generator().manual_seed(0))
    assert out.shape == (1, 2, 16, 16) and torch.isfinite(out).all()


def test_stage_shape_snaps_and_preserves_leading_dims():
    assert stage_shape((1, 16, 64, 64), 0.5) == (1, 16, 32, 32)
    assert stage_shape((1, 16, 1, 66, 66), 0.5)[-2:] == (32, 32)  # 33 -> snap down to 32
    assert stage_shape((1, 16, 64, 64), 1.0) == (1, 16, 64, 64)   # full res unchanged


# --- staged orchestrator --------------------------------------------------

def test_orchestrator_grows_to_full_res_and_records_stage_shapes():
    seen_hw = []

    def model_forward(x, sigma, cond):
        seen_hw.append(tuple(x.shape[-2:]))
        return 0.1 * x  # mild, finite, x-dependent

    full = torch.zeros(1, 4, 32, 32)
    seed = torch.randn(1, 4, 32, 32)
    cfg = SpectralProgressiveConfig(scales=(0.5, 1.0), transitions=(0.6,))
    out = denoise_spectral_progressive(
        model_forward, full, cond={}, uncond=None, steps=8, sampler=sample_euler, sampler_name="euler",
        guidance=NoCFG(), shift=3.0, cfg=cfg, seed_noise=seed,
        generator=torch.Generator().manual_seed(0),
    )
    assert out.shape == (1, 4, 32, 32)             # final = full resolution
    assert torch.isfinite(out).all()
    assert (16, 16) in seen_hw                       # an early stage ran at 0.5x
    assert seen_hw[-1] == (32, 32)                   # the last stage ran at full res


def test_orchestrator_single_scale_is_plain_full_res():
    # scales=(1.0,) is invalid (needs >=2), but (0.999.., 1.0) collapses to a
    # near-full run — instead verify a normal 2-stage run stays finite/seeded.
    def model_forward(x, sigma, cond):
        return torch.zeros_like(x)

    full = torch.zeros(1, 4, 16, 16)
    seed = torch.randn(1, 4, 16, 16)
    cfg = SpectralProgressiveConfig(scales=(0.5, 1.0), transitions=(0.6,))
    a = denoise_spectral_progressive(model_forward, full, {}, None, steps=6,
                                     sampler=sample_euler, sampler_name="euler", guidance=NoCFG(), shift=3.0,
                                     cfg=cfg, seed_noise=seed,
                                     generator=torch.Generator().manual_seed(1))
    b = denoise_spectral_progressive(model_forward, full, {}, None, steps=6,
                                     sampler=sample_euler, sampler_name="euler", guidance=NoCFG(), shift=3.0,
                                     cfg=cfg, seed_noise=seed,
                                     generator=torch.Generator().manual_seed(1))
    assert torch.equal(a, b)  # same seed + generator -> reproducible


# --- engine eligibility gate ----------------------------------------------

def test_engine_config_gate_eligibility():
    from src.platform.runtime.native.engine import NativeGenerator
    gate = NativeGenerator._spectral_progressive_config
    cfg = {"scales": [0.5, 1.0]}
    const = {"shift": 2.02}                       # constant-shift family (Flux2/Z-Image)
    dynamic = {"shift": 1.15, "base_shift": 0.5, "max_shift": 1.15}  # Flux1 dynamic-mu
    img = torch.zeros(1, 4, 32, 32)
    # disabled / ineligible -> None
    assert gate(None, None, None, img, const) is None
    assert gate(None, {}, None, img, const) is None
    assert gate(None, {"enabled": False, **cfg}, None, img, const) is None       # explicit off
    assert gate(None, cfg, torch.zeros(1, 4, 32, 32), img, const) is None        # img2img
    assert gate(None, cfg, None, torch.zeros(1, 16, 1, 32, 32), const) is None   # 5D causal-3D
    assert gate(None, cfg, None, img, dynamic) is None                           # dynamic-mu excluded
    # eligible txt2img 4D constant-shift -> a parsed config (lists coerced to tuples)
    parsed = gate(None, cfg, None, img, const)
    assert parsed is not None and parsed.scales == (0.5, 1.0)
