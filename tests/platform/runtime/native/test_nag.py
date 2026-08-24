"""Unit tests for the NAG (Normalized Attention Guidance) blend math."""

from __future__ import annotations

import torch

from src.platform.runtime.native.nag import apply_nag


def test_scale_one_is_identity():
    pos = torch.randn(2, 3, 8)
    neg = torch.randn(2, 3, 8)
    out = apply_nag(pos, neg, scale=1.0)
    assert torch.allclose(out, pos)


def test_hand_computed_blend_no_clamp():
    # 1D feature vectors so the L1 norms are easy to hand-verify.
    pos = torch.tensor([[1.0, 1.0, 1.0, 1.0]])   # ||pos||_1 = 4
    neg = torch.tensor([[0.0, 0.0, 0.0, 0.0]])   # ||neg||_1 = 0
    scale = 2.0
    # g = pos*2 - neg*1 = [2,2,2,2]; ||g||_1 = 8; r = 8/4 = 2 <= tau(3.5) -> no clamp
    tau, alpha = 3.5, 0.5
    out = apply_nag(pos, neg, scale=scale, tau=tau, alpha=alpha)
    g = pos * scale - neg * (scale - 1.0)
    expected = g * alpha + pos * (1.0 - alpha)
    assert torch.allclose(out, expected)
    assert torch.allclose(out, torch.tensor([[1.5, 1.5, 1.5, 1.5]]))


def test_norm_clamp_triggers_when_ratio_exceeds_tau():
    # Push neg strongly opposite pos so the extrapolated g has a much larger
    # L1 norm than pos, forcing r > tau and the rescale branch to engage.
    pos = torch.tensor([[1.0, 1.0, 1.0, 1.0]])       # ||pos||_1 = 4
    neg = torch.tensor([[-10.0, -10.0, -10.0, -10.0]])
    scale = 3.0
    tau, alpha = 1.0, 1.0  # alpha=1 isolates g (no positive blend-back)
    out = apply_nag(pos, neg, scale=scale, tau=tau, alpha=alpha)

    g = pos * scale - neg * (scale - 1.0)  # = [3,3,3,3] - [-20,-20,-20,-20] = [23]*4
    pos_norm = pos.abs().sum(-1, keepdim=True)
    g_norm = g.abs().sum(-1, keepdim=True)
    ratio = g_norm / (pos_norm + 1e-7)
    assert ratio.item() > tau  # sanity: the clamp condition is actually exercised

    expected_g = g * (tau * pos_norm / (g_norm + 1e-7))
    assert torch.allclose(out, expected_g, atol=1e-5)
    # Clamped g should have (approximately) tau times pos's L1 norm.
    assert torch.allclose(out.abs().sum(-1), tau * pos_norm.squeeze(-1), atol=1e-4)


def test_alpha_zero_returns_pos_unchanged():
    pos = torch.randn(2, 5)
    neg = torch.randn(2, 5)
    out = apply_nag(pos, neg, scale=5.0, alpha=0.0)
    assert torch.allclose(out, pos)


def test_all_zero_pos_stays_finite():
    # pos all-zero -> ||pos||_1 == 0; the +1e-7 epsilons must keep the ratio
    # and rescale divisions finite (no literal div-by-zero nan/inf).
    pos = torch.zeros(1, 4)
    neg = torch.randn(1, 4)
    out = apply_nag(pos, neg, scale=2.0)
    assert torch.isfinite(out).all()
