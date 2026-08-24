"""Guidance-strategy tests: TrueCFG math, EmbeddedGuidance injection, NoCFG."""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.cfg import (
    EmbeddedGuidance,
    NoCFG,
    SkipLayerGuidance,
    TrueCFG,
    _project_parallel_orthogonal,
    _scale_at,
)


def test_no_cfg_single_forward_passthrough():
    calls = []

    def model_fn(x, sigma, cond):
        calls.append(cond)
        return torch.full_like(x, 3.0)

    x = torch.zeros(2, 4)
    out = NoCFG()(model_fn, x, torch.ones(2), {"tag": "c"}, {"tag": "u"}, 0)
    assert len(calls) == 1
    assert calls[0] == {"tag": "c"}
    assert torch.allclose(out, torch.full_like(x, 3.0))


def test_true_cfg_combination_math():
    # model_fn returns a constant velocity encoded in the conditioning dict.
    # cfg_zero_star=False reproduces the plain (pre-CFG-Zero*) formula exactly.
    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond["v"])

    x = torch.zeros(2, 4)
    cond = {"v": 5.0}
    uncond = {"v": 1.0}
    out = TrueCFG(2.0, cfg_zero_star=False)(model_fn, x, torch.ones(2), cond, uncond, 0)
    # uncond + scale*(cond - uncond) = 1 + 2*(5-1) = 9
    assert torch.allclose(out, torch.full_like(x, 9.0))


def test_true_cfg_scale_one_skips_uncond():
    calls = {"n": 0}

    def model_fn(x, sigma, cond):
        calls["n"] += 1
        return torch.full_like(x, cond["v"])

    x = torch.zeros(1, 2)
    out = TrueCFG(1.0)(model_fn, x, torch.ones(1), {"v": 7.0}, {"v": 0.0}, 0)
    assert calls["n"] == 1  # uncond pass skipped
    assert torch.allclose(out, torch.full_like(x, 7.0))


def test_true_cfg_per_step_scale():
    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond["v"])

    x = torch.zeros(1, 2)
    strat = TrueCFG([2.0, 3.0], cfg_zero_star=False)
    out0 = strat(model_fn, x, torch.ones(1), {"v": 5.0}, {"v": 1.0}, 0)
    out1 = strat(model_fn, x, torch.ones(1), {"v": 5.0}, {"v": 1.0}, 1)
    assert torch.allclose(out0, torch.full_like(x, 1 + 2 * 4))  # 9
    assert torch.allclose(out1, torch.full_like(x, 1 + 3 * 4))  # 13


# -- CFG-Zero* -------------------------------------------------------------

def test_cfg_zero_star_alpha_one_when_uncond_equals_cond():
    """If uncond == cond, alpha == 1 (dot/||u||^2 == 1), so the rescale is a
    no-op and the output matches plain CFG exactly (which is also a no-op
    here since cond - uncond == 0)."""
    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond["v"])

    x = torch.zeros(2, 4)
    same = {"v": 3.0}
    plain = TrueCFG(2.0, cfg_zero_star=False)(model_fn, x, torch.ones(2), same, same, 0)
    zero_star = TrueCFG(2.0, cfg_zero_star=True)(model_fn, x, torch.ones(2), same, same, 0)
    assert torch.allclose(plain, zero_star)
    assert torch.allclose(zero_star, torch.full_like(x, 3.0))


def test_cfg_zero_star_known_tensors_analytic():
    # Single batch element, 2 non-batch dims flattened. cond=[3,4], uncond=[1,2].
    # alpha = dot(cond,uncond)/(||uncond||^2+eps) = (3*1+4*2)/(1+4) = 11/5 = 2.2
    cond_v = torch.tensor([[3.0, 4.0]])
    uncond_v = torch.tensor([[1.0, 2.0]])

    def model_fn(x, sigma, cond):
        return cond_v if cond["tag"] == "c" else uncond_v

    x = torch.zeros(1, 2)
    scale = 2.0
    out = TrueCFG(scale, cfg_zero_star=True)(
        model_fn, x, torch.ones(1), {"tag": "c"}, {"tag": "u"}, 0
    )
    alpha = 2.2
    rescaled_uncond = uncond_v * alpha  # [2.2, 4.4]
    expected = rescaled_uncond + scale * (cond_v - rescaled_uncond)
    assert torch.allclose(out, expected, atol=1e-5)


def test_cfg_zero_star_disabled_reproduces_plain_formula():
    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond["v"])

    x = torch.zeros(2, 4)
    cond = {"v": 5.0}
    uncond = {"v": 1.0}
    plain = TrueCFG(2.0)(model_fn, x, torch.ones(2), cond, uncond, 0)  # default: cfg_zero_star=True
    old = TrueCFG(2.0, cfg_zero_star=False)(model_fn, x, torch.ones(2), cond, uncond, 0)
    # These differ (cond != uncond), proving the default now applies the rescale.
    assert not torch.allclose(plain, old)
    # ...but with the kill-switch, the old formula is reproduced exactly.
    assert torch.allclose(old, torch.full_like(x, 9.0))


# -- zero-init ---------------------------------------------------------------

def test_zero_init_steps_returns_zero_velocity_then_normal():
    calls = {"n": 0}

    def model_fn(x, sigma, cond):
        calls["n"] += 1
        return torch.full_like(x, cond["v"])

    x = torch.ones(1, 3)  # non-zero so a "zero prediction" is distinguishable
    strat = TrueCFG(2.0, cfg_zero_star=False, zero_init_steps=1)
    out0 = strat(model_fn, x, torch.ones(1), {"v": 5.0}, {"v": 1.0}, 0)
    assert torch.allclose(out0, torch.zeros_like(x))
    assert calls["n"] == 0  # no forward passes run during zero-init

    out1 = strat(model_fn, x, torch.ones(1), {"v": 5.0}, {"v": 1.0}, 1)
    assert torch.allclose(out1, torch.full_like(x, 9.0))  # normal CFG resumes
    assert calls["n"] == 2


def test_embedded_guidance_injects_scale_into_conditioning():
    seen = {}

    def model_fn(x, sigma, cond):
        seen["cond"] = cond
        return torch.zeros_like(x)

    x = torch.zeros(3, 4)
    base = {"context": "ctx"}
    EmbeddedGuidance(3.5)(model_fn, x, torch.ones(3), base, None, 0)
    g = seen["cond"]["guidance"]
    assert g.shape == (3,)  # broadcast over batch
    assert torch.allclose(g, torch.full((3,), 3.5))
    assert seen["cond"]["context"] == "ctx"
    # original conditioning dict not mutated
    assert "guidance" not in base


def test_embedded_guidance_per_step_scale():
    seen = []

    def model_fn(x, sigma, cond):
        seen.append(float(cond["guidance"][0]))
        return torch.zeros_like(x)

    x = torch.zeros(1, 2)
    strat = EmbeddedGuidance([2.0, 4.0])
    strat(model_fn, x, torch.ones(1), {}, None, 0)
    strat(model_fn, x, torch.ones(1), {}, None, 1)
    assert seen == [2.0, 4.0]


def test_scale_at_clamps_short_list():
    assert _scale_at([2.0, 3.0], 0) == 2.0
    assert _scale_at([2.0, 3.0], 5) == 3.0  # clamps to last
    assert _scale_at(4.0, 99) == 4.0
    with pytest.raises(ValueError):
        _scale_at([], 0)


# -- APG (Adaptive Projected Guidance, arXiv:2410.02416) --------------------

def _random_model_fn(seed: int):
    torch.manual_seed(seed)
    cond_v = torch.randn(2, 3, 5)
    uncond_v = torch.randn(2, 3, 5)

    def model_fn(x, sigma, cond):
        return cond_v if cond.get("tag") == "c" else uncond_v

    return model_fn, cond_v, uncond_v


def test_apg_defaults_are_bit_identical_to_plain_true_cfg():
    model_fn, cond_v, uncond_v = _random_model_fn(0)
    x = torch.randn(2, 3, 5)
    sigma = torch.full((2,), 0.6)

    baseline = TrueCFG(2.0, cfg_zero_star=False)(
        model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0
    )
    with_apg_defaults = TrueCFG(2.0, cfg_zero_star=False, apg_eta=1.0, apg_norm_threshold=0.0, apg_momentum=0.0)(
        model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0
    )
    assert torch.equal(baseline, with_apg_defaults)


def test_apg_class_constructor_defaults_match_explicit_off_values():
    # TrueCFG(...) with no apg_* kwargs must take the exact same (inactive) code
    # path as explicitly passing the off-values.
    model_fn, cond_v, uncond_v = _random_model_fn(1)
    x = torch.randn(2, 3, 5)
    sigma = torch.full((2,), 0.4)

    a = TrueCFG(1.8, cfg_zero_star=True)(model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0)
    b = TrueCFG(1.8, cfg_zero_star=True, apg_eta=1.0, apg_norm_threshold=0.0, apg_momentum=0.0)(
        model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0
    )
    assert torch.equal(a, b)


def test_apg_eta_one_threshold_zero_momentum_zero_is_degenerate_plain_cfg():
    # The paper's degenerate case: eta=1 recovers standard CFG even when the
    # APG code path is explicitly forced active by some other non-off param...
    # here we just confirm eta=1 alone (active branch, since we pass threshold
    # via a nonzero momentum=0 default) still numerically reproduces plain CFG,
    # this time going through the ACTIVE branch (threshold>0 forces activity)
    # but with a threshold large enough to never bind, and momentum=0.
    model_fn, cond_v, uncond_v = _random_model_fn(2)
    x = torch.randn(2, 3, 5)
    sigma = torch.full((2,), 0.5)

    plain = TrueCFG(2.5, cfg_zero_star=False)(model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0)
    apg_noop = TrueCFG(
        2.5, cfg_zero_star=False, apg_eta=1.0, apg_norm_threshold=1e9, apg_momentum=0.0,
    )(model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0)
    assert torch.allclose(plain, apg_noop, atol=1e-5)


def test_apg_eta_zero_parallel_component_of_applied_delta_is_zero():
    # With eta=0 the eta-weighted recombination is orthogonal + 0*parallel, so
    # re-projecting the APPLIED delta (mapped back into x0-space) onto x0_cond
    # must have ~zero parallel component.
    model_fn, cond_v, uncond_v = _random_model_fn(3)
    x = torch.randn(2, 3, 5)
    sigma = torch.full((2,), 0.5)
    scale = 2.0

    out = TrueCFG(scale, cfg_zero_star=False, apg_eta=0.0)(
        model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0
    )
    # Reconstruct delta_v_final from the combination formula, then map to x0-space.
    # Active APG anchors at the conditional prediction: out = cond + (scale-1)*delta (S3).
    delta_v_final = (out - cond_v) / (scale - 1.0)
    sigma_view = sigma.reshape(2, 1, 1)
    delta_x0_applied = -sigma_view * delta_v_final
    x0_cond = x - sigma_view * cond_v

    parallel, _ = _project_parallel_orthogonal(delta_x0_applied, x0_cond)
    assert torch.allclose(parallel, torch.zeros_like(parallel), atol=1e-4)


def test_apg_norm_threshold_caps_delta_norm():
    model_fn, cond_v, uncond_v = _random_model_fn(4)
    x = torch.randn(2, 3, 5)
    sigma = torch.full((2,), 0.5)
    scale = 2.0
    threshold = 0.05  # deliberately tiny to guarantee it binds

    out = TrueCFG(scale, cfg_zero_star=False, apg_norm_threshold=threshold)(
        model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0
    )
    # Active APG anchors at the conditional prediction (S3).
    delta_v_final = (out - cond_v) / (scale - 1.0)
    sigma_view = sigma.reshape(2, 1, 1)
    delta_x0_applied = -sigma_view * delta_v_final
    norm = delta_x0_applied.reshape(2, -1).norm(dim=-1)
    # eta=1.0 default here, so parallel+orthogonal recombine exactly to the
    # (rescaled) delta_x0 -> its norm must be <= threshold (+ eps slack).
    assert torch.all(norm <= threshold + 1e-4)

    # And it must actually have reduced the norm vs. the unthresholded case.
    out_unthresholded = TrueCFG(scale, cfg_zero_star=False)(
        model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0
    )
    delta_unthresholded = -sigma_view * ((out_unthresholded - uncond_v) / scale)
    norm_unthresholded = delta_unthresholded.reshape(2, -1).norm(dim=-1)
    assert torch.all(norm < norm_unthresholded)


def test_apg_momentum_accumulates_across_sequential_calls():
    model_fn, cond_v, uncond_v = _random_model_fn(5)
    x = torch.randn(2, 3, 5)
    sigma = torch.full((2,), 0.5)

    strat = TrueCFG(2.0, cfg_zero_star=False, apg_momentum=-0.5)
    assert strat._apg_momentum_buf is None

    strat(model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0)
    buf_after_1 = strat._apg_momentum_buf.clone()
    assert buf_after_1 is not None

    strat(model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 1)
    buf_after_2 = strat._apg_momentum_buf.clone()
    # The buffer after step 2 must differ from step 1 (momentum actually
    # accumulated, not just overwritten with the same raw delta each time).
    assert not torch.equal(buf_after_1, buf_after_2)

    # A momentum=0 strategy never even allocates the buffer.
    strat_off = TrueCFG(2.0, cfg_zero_star=False)
    strat_off(model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0)
    assert strat_off._apg_momentum_buf is None


def test_reset_momentum_clears_buffer():
    model_fn, cond_v, uncond_v = _random_model_fn(6)
    x = torch.randn(2, 3, 5)
    sigma = torch.full((2,), 0.5)

    strat = TrueCFG(2.0, cfg_zero_star=False, apg_momentum=-0.5)
    strat(model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0)
    assert strat._apg_momentum_buf is not None
    strat.reset_momentum()
    assert strat._apg_momentum_buf is None


def test_apg_composes_after_cfg_zero_star_rescale():
    # With cfg_zero_star=True (default) AND apg_eta<1, the result must differ
    # from both plain CFG and CFG-Zero*-only, proving APG operates on the
    # ALREADY alpha-rescaled delta rather than bypassing it.
    model_fn, cond_v, uncond_v = _random_model_fn(7)
    x = torch.randn(2, 3, 5)
    sigma = torch.full((2,), 0.5)

    plain = TrueCFG(2.0, cfg_zero_star=False)(model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0)
    zero_star_only = TrueCFG(2.0, cfg_zero_star=True)(model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0)
    zero_star_plus_apg = TrueCFG(2.0, cfg_zero_star=True, apg_eta=0.3)(
        model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0
    )
    assert not torch.allclose(zero_star_plus_apg, plain, atol=1e-4)
    assert not torch.allclose(zero_star_plus_apg, zero_star_only, atol=1e-4)


def test_apg_no_extra_forward_passes():
    calls = {"n": 0}

    def model_fn(x, sigma, cond):
        calls["n"] += 1
        return torch.full_like(x, cond["v"])

    x = torch.randn(2, 3)
    sigma = torch.full((2,), 0.5)
    TrueCFG(2.0, cfg_zero_star=True, apg_eta=0.3, apg_norm_threshold=0.5, apg_momentum=-0.4)(
        model_fn, x, sigma, {"v": 5.0}, {"v": 1.0}, 0
    )
    assert calls["n"] == 2  # exactly cond + uncond, no extra model evaluation


def test_apg_finite_and_shape_preserving():
    model_fn, cond_v, uncond_v = _random_model_fn(8)
    x = torch.randn(2, 3, 5)
    sigma = torch.full((2,), 0.5)
    out = TrueCFG(2.0, cfg_zero_star=True, apg_eta=0.2, apg_norm_threshold=0.3, apg_momentum=-0.3)(
        model_fn, x, sigma, {"tag": "c"}, {"tag": "u"}, 0
    )
    assert out.shape == x.shape
    assert torch.isfinite(out).all()


# -- SkipLayerGuidance (SLG, roadmap 3.5) ------------------------------------

def _slg_stub_model_fn(calls: list):
    """Returns cond['v'] normally; a different constant whenever the
    conditioning dict carries a truthy 'skip_layers' key (the degraded pass)."""

    def model_fn(x, sigma, cond):
        calls.append(dict(cond))
        if cond.get("skip_layers"):
            return torch.full_like(x, 100.0)
        return torch.full_like(x, cond["v"])

    return model_fn


def test_slg_scale_zero_is_passthrough_to_inner():
    calls = []
    model_fn = _slg_stub_model_fn(calls)
    x = torch.zeros(1, 2)
    sigma = torch.full((1,), 0.5)

    inner = NoCFG()
    plain = inner(model_fn, x, sigma, {"v": 7.0}, None, 0)
    calls.clear()
    slg = SkipLayerGuidance(NoCFG(), slg_scale=0.0, layers={0, 1}, sigma_start=1.0, sigma_end=0.0)
    wrapped = slg(model_fn, x, sigma, {"v": 7.0}, None, 0)

    assert torch.equal(plain, wrapped)
    assert len(calls) == 1  # only the inner's own forward pass, no extra call


def test_slg_empty_layers_is_passthrough_even_with_scale():
    calls = []
    model_fn = _slg_stub_model_fn(calls)
    x = torch.zeros(1, 2)
    sigma = torch.full((1,), 0.5)
    slg = SkipLayerGuidance(NoCFG(), slg_scale=2.0, layers=set(), sigma_start=1.0, sigma_end=0.0)
    out = slg(model_fn, x, sigma, {"v": 7.0}, None, 0)
    assert torch.allclose(out, torch.full_like(x, 7.0))
    assert len(calls) == 1


def test_slg_in_window_applies_push_away_hand_computed():
    calls = []
    model_fn = _slg_stub_model_fn(calls)
    x = torch.zeros(1, 2)
    sigma = torch.full((1,), 0.5)  # inside [0.2, 0.8]
    slg = SkipLayerGuidance(NoCFG(), slg_scale=1.5, layers={0}, sigma_start=0.8, sigma_end=0.2)
    out = slg(model_fn, x, sigma, {"v": 7.0}, None, 0)
    # out_inner = 7 (NoCFG passthrough); degraded = 100.
    # final = out_inner + slg_scale*(out_inner - degraded) = 7 + 1.5*(7-100) = 7 - 139.5 = -132.5
    assert torch.allclose(out, torch.full_like(x, -132.5))
    assert len(calls) == 2  # inner pass + one degraded pass


def test_slg_out_of_window_makes_no_extra_model_call():
    calls = []
    model_fn = _slg_stub_model_fn(calls)
    x = torch.zeros(1, 2)
    sigma = torch.full((1,), 0.9)  # above sigma_start=0.8 -> out of window
    slg = SkipLayerGuidance(NoCFG(), slg_scale=1.5, layers={0}, sigma_start=0.8, sigma_end=0.2)
    out = slg(model_fn, x, sigma, {"v": 7.0}, None, 0)
    assert torch.allclose(out, torch.full_like(x, 7.0))
    assert len(calls) == 1  # inner pass only


@pytest.mark.parametrize(
    "sigma_val,in_window",
    [
        (0.8, True),   # exactly sigma_start -> inclusive, in window
        (0.2, True),   # exactly sigma_end -> inclusive, in window
        (0.8 + 1e-4, False),
        (0.2 - 1e-4, False),
        (0.5, True),
        (1.0, False),
        (0.0, False),
    ],
)
def test_slg_window_edges(sigma_val, in_window):
    calls = []
    model_fn = _slg_stub_model_fn(calls)
    x = torch.zeros(1, 2)
    sigma = torch.full((1,), sigma_val)
    slg = SkipLayerGuidance(NoCFG(), slg_scale=1.0, layers={0}, sigma_start=0.8, sigma_end=0.2)
    out = slg(model_fn, x, sigma, {"v": 7.0}, None, 0)
    expected_calls = 2 if in_window else 1
    assert len(calls) == expected_calls
    if in_window:
        # final = 7 + 1*(7-100) = -86
        assert torch.allclose(out, torch.full_like(x, -86.0))
    else:
        assert torch.allclose(out, torch.full_like(x, 7.0))


def test_slg_composes_over_true_cfg_with_cfg_zero_star():
    # Wrapping TrueCFG must still run TrueCFG's own cond+uncond (+cfg-zero*)
    # math for the inner result, then add exactly one degraded pass on top.
    calls = []

    def model_fn(x, sigma, cond):
        calls.append(dict(cond))
        if cond.get("skip_layers"):
            return torch.full_like(x, 50.0)
        return torch.full_like(x, cond["v"])

    x = torch.zeros(2, 4)
    sigma = torch.full((2,), 0.5)
    cond = {"v": 5.0}
    uncond = {"v": 1.0}

    inner = TrueCFG(2.0, cfg_zero_star=True)
    plain_cfg_out = inner(model_fn, x, sigma, cond, uncond, 0)
    calls.clear()

    slg = SkipLayerGuidance(TrueCFG(2.0, cfg_zero_star=True), slg_scale=1.0, layers={0}, sigma_start=0.8, sigma_end=0.2)
    out = slg(model_fn, x, sigma, cond, uncond, 0)

    # 2 calls for TrueCFG's cond+uncond, + 1 degraded call.
    assert len(calls) == 3
    expected = plain_cfg_out + 1.0 * (plain_cfg_out - torch.full_like(x, 50.0))
    assert torch.allclose(out, expected, atol=1e-5)


def test_slg_composes_over_embedded_guidance():
    calls = []

    def model_fn(x, sigma, cond):
        calls.append(dict(cond))
        if cond.get("skip_layers"):
            return torch.full_like(x, -3.0)
        return torch.full_like(x, float(cond["guidance"][0]))

    x = torch.zeros(1, 2)
    sigma = torch.full((1,), 0.5)
    slg = SkipLayerGuidance(EmbeddedGuidance(4.0), slg_scale=0.5, layers={0}, sigma_start=0.8, sigma_end=0.2)
    out = slg(model_fn, x, sigma, {"context": "c"}, None, 0)
    # inner (EmbeddedGuidance) result = 4.0; degraded = -3.0
    # final = 4 + 0.5*(4-(-3)) = 4 + 3.5 = 7.5
    assert torch.allclose(out, torch.full_like(x, 7.5))
    assert len(calls) == 2


def test_slg_degraded_pass_uses_cond_not_uncond():
    seen_conds = []

    def model_fn(x, sigma, cond):
        seen_conds.append(dict(cond))
        return torch.zeros_like(x) if not cond.get("skip_layers") else torch.ones_like(x)

    x = torch.zeros(1, 2)
    sigma = torch.full((1,), 0.5)
    cond = {"v": 5.0, "tag": "cond"}
    uncond = {"v": 1.0, "tag": "uncond"}
    slg = SkipLayerGuidance(NoCFG(), slg_scale=1.0, layers={0}, sigma_start=0.8, sigma_end=0.2)
    slg(model_fn, x, sigma, cond, uncond, 0)

    degraded_call = [c for c in seen_conds if c.get("skip_layers")]
    assert len(degraded_call) == 1
    assert degraded_call[0]["tag"] == "cond"  # never "uncond"
    assert degraded_call[0]["skip_layers"] == {0}


# -- CFG++ anchor (last_uncond_v) ----------------------

def test_true_cfg_exposes_last_uncond_v_after_cfg_zero_star_rescale():
    cond_v = torch.tensor([[3.0, 4.0]])
    uncond_v = torch.tensor([[1.0, 2.0]])

    def model_fn(x, sigma, cond):
        return cond_v if cond["tag"] == "c" else uncond_v

    strat = TrueCFG(2.0, cfg_zero_star=True)
    assert strat.last_uncond_v is None
    strat(model_fn, torch.zeros(1, 2), torch.ones(1), {"tag": "c"}, {"tag": "u"}, 0)
    # cfg_zero_star rescales uncond onto cond BEFORE combining (alpha=2.2, see
    # test_cfg_zero_star_known_tensors_analytic) -- last_uncond_v must be that
    # RESCALED value, not the raw model output, since CFG++ wants whatever
    # correction already applies to the branch's own prediction.
    expected = uncond_v * 2.2
    assert torch.allclose(strat.last_uncond_v, expected, atol=1e-5)


def test_true_cfg_last_uncond_v_none_when_scale_is_one():
    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond["v"])

    strat = TrueCFG(1.0)
    strat(model_fn, torch.zeros(1, 2), torch.ones(1), {"v": 5.0}, {"v": 1.0}, 0)
    assert strat.last_uncond_v is None  # uncond pass never ran


def test_true_cfg_last_uncond_v_none_when_uncond_missing():
    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond["v"])

    strat = TrueCFG(2.0)
    strat(model_fn, torch.zeros(1, 2), torch.ones(1), {"v": 5.0}, None, 0)
    assert strat.last_uncond_v is None


def test_true_cfg_reset_momentum_clears_last_uncond_v():
    cond_v = torch.tensor([[3.0, 4.0]])
    uncond_v = torch.tensor([[1.0, 2.0]])

    def model_fn(x, sigma, cond):
        return cond_v if cond["tag"] == "c" else uncond_v

    strat = TrueCFG(2.0)
    strat(model_fn, torch.zeros(1, 2), torch.ones(1), {"tag": "c"}, {"tag": "u"}, 0)
    assert strat.last_uncond_v is not None
    strat.reset_momentum()
    assert strat.last_uncond_v is None


def test_skip_layer_guidance_forwards_last_uncond_v_from_inner():
    cond_v = torch.tensor([[3.0, 4.0]])
    uncond_v = torch.tensor([[1.0, 2.0]])

    def model_fn(x, sigma, cond):
        if cond.get("skip_layers"):
            return torch.full_like(cond_v, 100.0)
        return cond_v if cond["tag"] == "c" else uncond_v

    inner = TrueCFG(2.0, cfg_zero_star=True)
    slg = SkipLayerGuidance(inner, slg_scale=1.0, layers={0}, sigma_start=0.8, sigma_end=0.2)
    sigma = torch.full((1,), 0.5)  # inside the window
    slg(model_fn, torch.zeros(1, 2), sigma, {"tag": "c"}, {"tag": "u"}, 0)
    assert slg.last_uncond_v is not None
    assert torch.equal(slg.last_uncond_v, inner.last_uncond_v)


def test_skip_layer_guidance_forwards_none_when_inner_has_no_uncond_branch():
    def model_fn(x, sigma, cond):
        return torch.full_like(x, cond.get("v", 0.0))

    slg = SkipLayerGuidance(NoCFG(), slg_scale=0.0, layers={0}, sigma_start=1.0, sigma_end=0.0)
    slg(model_fn, torch.zeros(1, 2), torch.ones(1), {"v": 7.0}, None, 0)
    assert slg.last_uncond_v is None


def test_slg_negative_scale_is_a_noop():
    calls = []
    model_fn = _slg_stub_model_fn(calls)
    x = torch.zeros(1, 2)
    sigma = torch.full((1,), 0.5)
    slg = SkipLayerGuidance(NoCFG(), slg_scale=-1.0, layers={0}, sigma_start=0.8, sigma_end=0.2)
    out = slg(model_fn, x, sigma, {"v": 7.0}, None, 0)
    assert torch.allclose(out, torch.full_like(x, 7.0))
    assert len(calls) == 1
