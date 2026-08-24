"""Regression tests for the guidance-math review cluster (#38).

Covers the confirmed findings: S1 (APG fp16/sigma0), S2 (euler_restart interleave),
S3 (APG cond anchor), S5/S9/S10 (SLG), S13 (detail-warp bf16), S17 (schedule
validation), S16 (resume bounds), S19 (cache attribution).
"""

from __future__ import annotations

import pytest
import torch

from src.platform.runtime.native.sampling.algorithms.euler_restart import sample_euler_restart
from src.platform.runtime.native.sampling.cfg import (
    EmbeddedGuidance,
    NoCFG,
    SkipLayerGuidance,
    TrueCFG,
)
from src.platform.runtime.native.sampling.flow_schedule import build_sigmas


def _tagged_model(values):
    """model_fn returning ``values[tag]``; 'skip' tag for the degraded pass."""
    def model_fn(x, sigma, cond):
        if "skip_layers" in cond:
            return torch.full_like(x, values["skip"])
        return torch.full_like(x, values[cond["tag"]])
    return model_fn


# --- S1: APG at sigma==0 / fp16 must not NaN -----------------------------

def test_apg_at_sigma_zero_is_finite_fp16():
    model = _tagged_model({"c": 1.0, "u": 0.0})
    x = torch.randn(1, 4, dtype=torch.float16)
    strat = TrueCFG(7.0, cfg_zero_star=False, apg_eta=0.5)
    out = strat(model, x, torch.zeros(1), {"tag": "c"}, {"tag": "u"}, 0)
    assert torch.isfinite(out).all()


def test_apg_tiny_sigma_fp16_is_finite():
    # A small but nonzero sigma in fp16 previously underflowed the clamp to 0.
    model = _tagged_model({"c": 1.0, "u": 0.0})
    x = torch.randn(1, 4, dtype=torch.float16)
    strat = TrueCFG(7.0, cfg_zero_star=False, apg_eta=0.3)
    out = strat(model, x, torch.full((1,), 1e-4), {"tag": "c"}, {"tag": "u"}, 0)
    assert torch.isfinite(out).all()


# --- S3: APG preserves the conditional anchor ----------------------------

def test_apg_eta_zero_retains_conditional_prediction():
    # Paper scenario: x=0, sigma=1, cond_v=(-1,0), uncond_v=0, scale=7, eta=0.
    # Suppressing the wholly-parallel delta must retain x0_cond -> output == cond_v.
    def model_fn(x, sigma, cond):
        return torch.tensor([[-1.0, 0.0]]) if cond["tag"] == "c" else torch.zeros(1, 2)

    x = torch.zeros(1, 2)
    out = TrueCFG(7.0, cfg_zero_star=False, apg_eta=0.0)(
        model_fn, x, torch.ones(1), {"tag": "c"}, {"tag": "u"}, 0)
    assert torch.allclose(out, torch.tensor([[-1.0, 0.0]]), atol=1e-5)


# --- S5: SLG anchors on the conditional prediction, not CFG-amplified out --

def test_slg_uses_conditional_anchor_not_double_cfg():
    # uncond=0, cond=1, degraded=0.8, cfg=7, slg=0.5 -> 7 + 0.5*(1-0.8) = 7.1
    # (the old out-anchored form gave 7 + 0.5*(7-0.8) = 10.1).
    model = _tagged_model({"c": 1.0, "u": 0.0, "skip": 0.8})
    inner = TrueCFG(7.0, cfg_zero_star=False)
    slg = SkipLayerGuidance(inner, slg_scale=0.5, layers={0}, sigma_start=1.0, sigma_end=0.0)
    out = slg(model, torch.zeros(1, 3), torch.ones(1), {"tag": "c"}, {"tag": "u"}, 0)
    assert torch.allclose(out, torch.full((1, 3), 7.1), atol=1e-5)


# --- S9: SLG does not fire during zero-init ------------------------------

def test_slg_skipped_during_zero_init():
    calls = {"degraded": 0}

    def model(x, sigma, cond):
        if "skip_layers" in cond:
            calls["degraded"] += 1
            return torch.full_like(x, 2.0)
        return torch.full_like(x, 1.0)

    inner = TrueCFG(7.0, zero_init_steps=2)
    slg = SkipLayerGuidance(inner, slg_scale=0.5, layers={0}, sigma_start=1.0, sigma_end=0.0)
    out = slg(model, torch.zeros(1, 3), torch.ones(1), {"tag": "c"}, {"tag": "u"}, step_index=0)
    assert torch.allclose(out, torch.zeros(1, 3))  # zero-init preserved
    assert calls["degraded"] == 0                   # no degraded forward fired


# --- S10: SLG degraded pass replicates EmbeddedGuidance's "guidance" key ---

def test_slg_degraded_pass_carries_embedded_guidance():
    seen = {}

    def model(x, sigma, cond):
        if "skip_layers" in cond:
            seen["has_guidance"] = "guidance" in cond
            return torch.full_like(x, 0.5)
        return torch.full_like(x, 1.0)

    inner = EmbeddedGuidance(3.5)
    slg = SkipLayerGuidance(inner, slg_scale=0.5, layers={0}, sigma_start=1.0, sigma_end=0.0)
    slg(model, torch.zeros(1, 3), torch.ones(1), {"tag": "c"}, None, 0)
    assert seen["has_guidance"] is True


# --- S2: euler_restart interleaves and ends at sigma 0 -------------------

def test_euler_restart_nonzero_sigma_low_ends_clean():
    seen_sigmas = []

    class _Spy:
        def __call__(self, model_fn, x, sigma, cond, uncond, i):
            seen_sigmas.append(float(sigma.reshape(-1)[0]))
            return model_fn(x, sigma, cond)

    x = torch.zeros(1, 4)
    sigmas = torch.tensor([1.0, 0.6, 0.2, 0.0])
    out = sample_euler_restart(
        lambda xx, ss, cc: torch.full_like(xx, 0.5), x, sigmas, _Spy(), {}, None,
        sampler_options={"restarts": [(0.8, 0.2, 4)]},
    )
    assert torch.isfinite(out).all()
    # A restart segment re-noised up to sigma_hi (0.8) DID happen...
    assert max(seen_sigmas) >= 0.8 - 1e-6
    # ...and the LAST guidance eval is the main descent's final step (0.2 -> 0),
    # proving the descent continued to 0 after the restart (not left at 0.2).
    assert abs(seen_sigmas[-1] - 0.2) < 1e-6


def test_euler_restart_zero_restarts_matches_euler():
    from src.platform.runtime.native.sampling.algorithms.euler import sample_euler
    x = torch.randn(1, 4)
    sigmas = torch.tensor([1.0, 0.7, 0.3, 0.0])
    model = lambda xx, ss, cc: torch.full_like(xx, 1.3)
    a = sample_euler(model, x.clone(), sigmas, NoCFG(), {}, None)
    b = sample_euler_restart(model, x.clone(), sigmas, NoCFG(), {}, None)
    assert torch.equal(a, b)


# --- S13: detail-warp survives a bf16 down-cast --------------------------

def test_detail_warp_strictly_descending_in_bf16():
    s = build_sigmas(100, shift=3.0, detail_strength=0.3, detail_start=0.1, detail_end=0.9)
    s_bf16 = s.to(torch.bfloat16)
    diffs = s_bf16[:-1].float() - s_bf16[1:].float()
    assert torch.all(diffs > 0), "adjacent sigmas must stay distinct after bf16 cast"


# --- S17: schedule param validation --------------------------------------

def test_schedule_param_validation():
    with pytest.raises(ValueError, match="alpha"):
        build_sigmas(8, schedule="beta", schedule_options={"alpha": 0.0})
    with pytest.raises(ValueError, match="sigma_min"):
        build_sigmas(8, schedule="exponential", schedule_options={"sigma_min": 10.0})
    with pytest.raises(ValueError, match="threshold_noise"):
        build_sigmas(8, schedule="linear_quadratic", schedule_options={"threshold_noise": 1.5})


# --- S16: resume_step / shape bounds -------------------------------------

def _denoise_resume(resume):
    from src.platform.runtime.native.sampling.denoise_loop import denoise
    return denoise(
        lambda x, s, c: torch.zeros_like(x), latents=torch.zeros(1, 4), cond={}, steps=4,
        sampler_name="euler", sampling_settings={"shift": 2.02, "guidance": None},
        guidance_scale=0.0, resume=resume,
    )


def test_resume_step_out_of_bounds_raises():
    with pytest.raises(ValueError, match="out of range"):
        _denoise_resume((99, torch.zeros(1, 4)))
    with pytest.raises(ValueError, match="out of range"):
        _denoise_resume((-1, torch.zeros(1, 4)))


def test_resume_shape_mismatch_raises():
    with pytest.raises(ValueError, match="shape"):
        _denoise_resume((2, torch.zeros(1, 8)))


# --- S19: cache attribution bypasses an unknown branch -------------------

def test_cache_attribution_bypasses_unknown_branch():
    from src.platform.runtime.native.sampling.denoise_loop import _CachingGuidance
    from src.platform.runtime.native.sampling.step_cache import StepCacheSet

    seen = {"had_cache": None}

    def model_fn(x, s, cond):
        seen["had_cache"] = "step_cache" in cond
        return torch.zeros_like(x)

    class _Rogue:
        # hands model_fn a shallow copy of uncond: neither identity nor a marker.
        def __call__(self, model_fn, x, sigma, cond, uncond, i):
            return model_fn(x, sigma, {**uncond, "meta": 1})

    caches = StepCacheSet({"rel_threshold": 0.5})
    cg = _CachingGuidance(_Rogue(), caches, total_steps=8)
    cg(model_fn, torch.zeros(1, 4), torch.ones(1), {"a": 1}, {"b": 2}, step_index=0)
    assert seen["had_cache"] is False  # unknown branch -> not cached


def test_cache_attribution_embedded_guidance_copy_is_cond():
    from src.platform.runtime.native.sampling.denoise_loop import _CachingGuidance
    from src.platform.runtime.native.sampling.step_cache import StepCacheSet

    seen = {"had_cache": None}

    def model_fn(x, s, cond):
        seen["had_cache"] = "step_cache" in cond
        return torch.zeros_like(x)

    # EmbeddedGuidance hands a {**cond, "guidance": ...} copy -> the "guidance"
    # marker keeps it on the cond cache (the main Flux path).
    caches = StepCacheSet({"rel_threshold": 0.5})
    cg = _CachingGuidance(EmbeddedGuidance(3.5), caches, total_steps=8)
    cg(model_fn, torch.zeros(1, 4), torch.ones(1), {"context": "c"}, None, step_index=0)
    assert seen["had_cache"] is True
