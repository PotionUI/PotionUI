"""Tests for the FreeInit frequency-blend helper (src/pipelines/pipes/_shared/generation/freeinit.py)."""

from __future__ import annotations

import pytest
import torch

from src.pipelines.pipes._shared.generation.freeinit import (
    butterworth_lowpass_mask,
    freeinit_blend,
    resolve_freeinit,
)


# --------------------------------------------------------------------------- #
# butterworth_lowpass_mask
# --------------------------------------------------------------------------- #

def test_mask_shape_matches_input():
    mask = butterworth_lowpass_mask((4, 6, 8), cutoff=0.25, order=4)
    assert mask.shape == (4, 6, 8)


def test_mask_dc_bin_is_always_one():
    # DC (all-zero frequency) is bin index 0 on every axis regardless of cutoff/order.
    for cutoff in (0.01, 0.25, 1.0, 100.0):
        mask = butterworth_lowpass_mask((5, 5, 5), cutoff=cutoff, order=4)
        assert mask[0, 0, 0].item() == pytest.approx(1.0, abs=1e-6)


def test_mask_values_in_unit_interval():
    mask = butterworth_lowpass_mask((6, 6, 6), cutoff=0.25, order=4)
    assert torch.all(mask > 0.0)
    assert torch.all(mask <= 1.0 + 1e-6)


def test_mask_monotonically_decreases_away_from_dc_along_one_axis():
    # Along the T axis (H=W=1 conceptually — check the T-axis slice at H=W DC bin).
    mask = butterworth_lowpass_mask((8, 8, 8), cutoff=0.3, order=4)
    slice_t = mask[:, 0, 0]  # varying only the T frequency, H/W held at DC
    # fftfreq order is [0, 1, 2, 3, -4, -3, -2, -1]/n -- magnitude first rises
    # to the Nyquist bin (index n//2) then falls back toward 0 as bins go negative.
    n = slice_t.shape[0]
    rising = slice_t[: n // 2 + 1]
    falling = slice_t[n // 2:]
    assert torch.all(rising[1:] <= rising[:-1] + 1e-6)  # non-increasing toward Nyquist
    assert torch.all(falling[1:] >= falling[:-1] - 1e-6)  # non-decreasing back toward DC(wrap)


def test_mask_higher_order_is_steeper_at_cutoff_boundary():
    # At d == cutoff exactly, H(d) = 1/(1+1) = 0.5 regardless of order (a
    # property of the Butterworth family) -- but moving one bin past cutoff,
    # a higher order should drop off harder.
    shape = (16, 16, 16)
    low_order = butterworth_lowpass_mask(shape, cutoff=0.25, order=1)
    high_order = butterworth_lowpass_mask(shape, cutoff=0.25, order=8)
    # Pick a bin with distance clearly beyond cutoff.
    far = low_order[4, 0, 0].item(), high_order[4, 0, 0].item()
    if far[0] > 1e-6:  # only meaningful if not already numerically zero
        assert far[1] <= far[0]


# --------------------------------------------------------------------------- #
# freeinit_blend
# --------------------------------------------------------------------------- #

def test_blend_output_shape_and_dtype():
    torch.manual_seed(0)
    clean = torch.randn(1, 4, 4, 6, 6, dtype=torch.float16)
    renoise = torch.randn_like(clean)
    fresh = torch.randn_like(clean)
    out = freeinit_blend(clean, renoise, fresh, sigma_max=0.98, cutoff=0.25, order=4)
    assert out.shape == clean.shape
    assert out.dtype == clean.dtype


def test_blend_cutoff_huge_is_pure_renoised():
    torch.manual_seed(1)
    clean = torch.randn(1, 2, 4, 4, 4, dtype=torch.float64)
    renoise = torch.randn_like(clean)
    fresh = torch.randn_like(clean)
    sigma_max = 0.9
    out = freeinit_blend(clean.float(), renoise.float(), fresh.float(),
                         sigma_max=sigma_max, cutoff=1e6, order=4)
    expected = (1.0 - sigma_max) * clean.float() + sigma_max * renoise.float()
    assert torch.allclose(out, expected, atol=1e-3)


def test_blend_identical_effective_sources_ignores_mask_entirely():
    # Pick fresh_noise to equal the RENOISED signal exactly (a mix of clean
    # and renoise_noise at this sigma_max); low+high of one signal
    # reconstructs it exactly regardless of the mask -- a strong FFT
    # round-trip check, valid at any sigma_max (not just the old sigma_max=1
    # degenerate case).
    torch.manual_seed(2)
    clean = torch.randn(1, 2, 4, 4, 4)
    renoise = torch.randn_like(clean)
    sigma_max = 0.9
    renoised = (1.0 - sigma_max) * clean + sigma_max * renoise
    out = freeinit_blend(clean, renoise, renoised, sigma_max=sigma_max, cutoff=0.2, order=3)
    assert torch.allclose(out, renoised, atol=1e-4)


def test_blend_sigma_max_zero_is_pure_clean_low_freq_mixed_with_fresh_high_freq():
    # sigma_max=0 -> "renoised" degenerates to clean_latent itself; blend must
    # still combine clean's low freq with fresh's high freq (not a no-op).
    torch.manual_seed(3)
    clean = torch.randn(1, 2, 4, 4, 4)
    renoise = torch.randn_like(clean)  # must be ignored entirely at sigma_max=0
    fresh = torch.randn_like(clean)
    out_a = freeinit_blend(clean, renoise, fresh, sigma_max=0.0, cutoff=0.25, order=4)
    out_b = freeinit_blend(clean, torch.randn_like(clean), fresh, sigma_max=0.0, cutoff=0.25, order=4)
    assert torch.allclose(out_a, out_b, atol=1e-4)  # renoise_noise had zero effect
    assert not torch.allclose(out_a, clean, atol=1e-2)  # but it's not a no-op (fresh's highs mixed in)


def test_blend_invalid_sigma_max_raises():
    clean = torch.randn(1, 2, 4, 4, 4)
    with pytest.raises(ValueError):
        freeinit_blend(clean, clean, clean, sigma_max=1.5)
    with pytest.raises(ValueError):
        freeinit_blend(clean, clean, clean, sigma_max=-0.1)


def test_blend_sigma_max_one_raises():
    # P1 fix: sigma_max must be strictly < 1.0 -- at exactly 1.0 the flow
    # interpolation collapses to pure noise and the blend loses ALL
    # dependence on clean_latent (see freeinit_blend's docstring). This used
    # to be the function's own DEFAULT, which was the actual bug.
    clean = torch.randn(1, 2, 4, 4, 4)
    with pytest.raises(ValueError, match="strictly below 1.0"):
        freeinit_blend(clean, clean, clean, sigma_max=1.0)


def test_blend_at_default_sigma_max_depends_on_clean_latent():
    # THE regression test for the sigma_max=1.0 degeneracy: with the function's
    # OWN default sigma_max, two different clean latents combined with the
    # SAME renoise_noise/fresh_noise must produce DIFFERENT blends -- proving
    # the extra FreeInit pass is actually anchored to the previous pass's
    # result, not an unrelated regeneration.
    torch.manual_seed(5)
    shape = (1, 4, 5, 6, 6)
    clean_a = torch.randn(shape)
    clean_b = torch.randn(shape)
    renoise = torch.randn(shape)
    fresh = torch.randn(shape)

    out_a = freeinit_blend(clean_a, renoise, fresh)  # default sigma_max
    out_b = freeinit_blend(clean_b, renoise, fresh)
    assert not torch.allclose(out_a, out_b, atol=1e-3)

    # And it must be the SAME clean latent -> SAME blend (determinism, not
    # just "differs by chance").
    out_a_again = freeinit_blend(clean_a, renoise, fresh)
    assert torch.equal(out_a, out_a_again)


def test_blend_result_is_real_valued_no_nans():
    torch.manual_seed(4)
    clean = torch.randn(2, 3, 5, 7, 9)  # odd dims, exercises fftfreq's odd-length branch
    renoise = torch.randn_like(clean)
    fresh = torch.randn_like(clean)
    out = freeinit_blend(clean, renoise, fresh, sigma_max=0.98, cutoff=0.25, order=4)
    assert torch.isfinite(out).all()
    assert not torch.is_complex(out)


# --------------------------------------------------------------------------- #
# resolve_freeinit
# --------------------------------------------------------------------------- #

def test_resolve_freeinit_defaults():
    iterations, cutoff, order = resolve_freeinit({})
    assert iterations == 0
    assert cutoff == 0.25
    assert order == 4


def test_resolve_freeinit_reads_overrides():
    iterations, cutoff, order = resolve_freeinit(
        {"freeinit_iterations": 2, "freeinit_cutoff": 0.4, "freeinit_order": 6}
    )
    assert iterations == 2
    assert cutoff == 0.4
    assert order == 6
