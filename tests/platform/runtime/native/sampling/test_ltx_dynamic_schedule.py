"""Tests for the LTX-2.5 resolution-dynamic shift schedule (``schedule="ltx_dynamic"``)."""

from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.sampling.flow_schedule import build_sigmas


def _reference(steps, tokens, base_shift=0.95, max_shift=2.05, stretch=True, terminal=0.1):
    """Independent re-derivation of Lightricks' LTX2Scheduler.execute formula
    (FACTS ONLY -- verified against the maintainer-supplied constants; no code
    consulted from the community-licensed LTX-2 repo)."""
    x1, x2 = 1024, 4096
    mm = (max_shift - base_shift) / (x2 - x1)
    b = base_shift - mm * x1
    mu = tokens * mm + b

    t = torch.linspace(1.0, 0.0, steps + 1, dtype=torch.float64)
    sigmas = torch.where(
        t != 0,
        math.exp(mu) / (math.exp(mu) + (1.0 / t - 1.0)),
        torch.zeros_like(t),
    )
    if stretch:
        nz = sigmas != 0
        one_minus = 1.0 - sigmas[nz]
        scale = one_minus[-1] / (1.0 - terminal)
        sigmas = sigmas.clone()
        sigmas[nz] = 1.0 - (one_minus / scale)
    return sigmas.to(torch.float32)


@pytest.mark.parametrize("tokens", [1024, 4096, 2048, 6000, 500])
def test_matches_independent_reference_across_token_counts(tokens):
    sigmas = build_sigmas(30, schedule="ltx_dynamic", image_seq_len=tokens)
    expected = _reference(30, tokens)
    assert torch.allclose(sigmas, expected, atol=1e-6)


def test_base_anchor_mu_equals_base_shift():
    # At tokens == 1024 (the base anchor) mu == base_shift exactly, so
    # (unstretched) sigma[1] must equal the plain constant-shift formula at
    # shift = exp(base_shift).
    tokens = 1024
    base_shift = 0.95
    sigmas = build_sigmas(4, schedule="ltx_dynamic", image_seq_len=tokens,
                           schedule_options={"stretch": False})
    t = torch.linspace(1.0, 0.0, 5, dtype=torch.float64)
    shift = math.exp(base_shift)
    expected = shift * t / (1.0 + (shift - 1.0) * t)
    assert torch.allclose(sigmas.double(), expected, atol=1e-5)


def test_max_anchor_mu_equals_max_shift():
    tokens = 4096
    max_shift = 2.05
    sigmas = build_sigmas(4, schedule="ltx_dynamic", image_seq_len=tokens,
                           schedule_options={"stretch": False})
    t = torch.linspace(1.0, 0.0, 5, dtype=torch.float64)
    shift = math.exp(max_shift)
    expected = shift * t / (1.0 + (shift - 1.0) * t)
    assert torch.allclose(sigmas.double(), expected, atol=1e-5)


def test_extrapolates_linearly_outside_anchor_range():
    # No clamping: tokens below 1024 or above 4096 keep extrapolating the
    # same line (matches _flux_mu's own no-clamp contract).
    below = build_sigmas(4, schedule="ltx_dynamic", image_seq_len=500, schedule_options={"stretch": False})
    at_base = build_sigmas(4, schedule="ltx_dynamic", image_seq_len=1024, schedule_options={"stretch": False})
    above = build_sigmas(4, schedule="ltx_dynamic", image_seq_len=6000, schedule_options={"stretch": False})
    at_max = build_sigmas(4, schedule="ltx_dynamic", image_seq_len=4096, schedule_options={"stretch": False})
    # Lower tokens -> lower mu -> lower shift -> sigmas skew smaller (interior points).
    assert below[2].item() < at_base[2].item()
    assert above[2].item() > at_max[2].item()


def test_stretch_pins_last_nonzero_sigma_to_terminal():
    for tokens in (1024, 2048, 4096):
        sigmas = build_sigmas(16, schedule="ltx_dynamic", image_seq_len=tokens)
        assert sigmas[-1].item() == 0.0
        assert sigmas[-2].item() == pytest.approx(0.1, abs=1e-6)


def test_stretch_false_leaves_last_nonzero_sigma_unpinned():
    sigmas = build_sigmas(16, schedule="ltx_dynamic", image_seq_len=1024,
                           schedule_options={"stretch": False})
    assert sigmas[-1].item() == 0.0
    assert sigmas[-2].item() != pytest.approx(0.1, abs=1e-6)


def test_custom_terminal_option():
    sigmas = build_sigmas(16, schedule="ltx_dynamic", image_seq_len=2048,
                           schedule_options={"terminal": 0.05})
    assert sigmas[-2].item() == pytest.approx(0.05, abs=1e-6)


def test_custom_base_max_shift_options():
    default = build_sigmas(16, schedule="ltx_dynamic", image_seq_len=2048)
    custom = build_sigmas(16, schedule="ltx_dynamic", image_seq_len=2048,
                           schedule_options={"base_shift": 0.5, "max_shift": 1.5})
    assert not torch.equal(default, custom)
    assert custom[0].item() == 1.0
    assert custom[-1].item() == 0.0


def test_requires_image_seq_len():
    with pytest.raises(ValueError, match="image_seq_len"):
        build_sigmas(16, schedule="ltx_dynamic")


def test_shape_and_monotonic():
    sigmas = build_sigmas(24, schedule="ltx_dynamic", image_seq_len=3072)
    assert sigmas.shape == (25,)
    assert sigmas[0].item() == pytest.approx(1.0, abs=1e-6)
    assert sigmas[-1].item() == 0.0
    diffs = sigmas[1:] - sigmas[:-1]
    assert torch.all(diffs < 0)


def test_truncates_with_denoise():
    sigmas = build_sigmas(4, schedule="ltx_dynamic", image_seq_len=3072, denoise=0.5)
    assert sigmas.shape == (5,)
    assert sigmas[0].item() < 1.0
    assert sigmas[-1].item() == 0.0


def test_composes_with_detail_daemon_warp():
    baseline = build_sigmas(24, schedule="ltx_dynamic", image_seq_len=3072)
    warped = build_sigmas(24, schedule="ltx_dynamic", image_seq_len=3072, detail_strength=0.2)
    assert not torch.equal(baseline, warped)
    assert torch.equal(baseline[0], warped[0])
    assert torch.equal(baseline[-1], warped[-1])


# --- existing behaviour is unaffected (byte-identical regression) ----------

def test_default_shift_based_schedule_unaffected():
    a = build_sigmas(20, shift=7.767901106306771)
    b = build_sigmas(20, shift=7.767901106306771, schedule=None)
    assert torch.equal(a, b)


def test_unknown_schedule_error_message_mentions_ltx_dynamic():
    with pytest.raises(ValueError, match="ltx_dynamic"):
        build_sigmas(4, schedule="bogus")
