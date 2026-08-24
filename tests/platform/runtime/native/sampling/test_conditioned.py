"""Tests for the pre-noised conditioned sampling entry (`denoise_prenoised`).

Toy linear models only: pinned-token invariance across all euler steps, the
x0-blend / velocity-CFG commutation identity the video-director wrapper relies
on, and schedule identity with `conditioned_sigmas`.
"""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from src.platform.runtime.native.sampling import build_sigmas, conditioned_sigmas, denoise_prenoised  # noqa: E402

SETTINGS_NOCFG = {"guidance": None, "shift": 2.37}
SETTINGS_CFG = {"guidance": "cfg", "shift": 2.37}


def _blended_forward(mask: torch.Tensor, clean: torch.Tensor, raw_velocity):
    """Mimic the video-director wrapper: raw model velocity, then the x0-space
    conditioning blend converted back to velocity space."""

    def forward(x, sigma, conditioning):
        v = raw_velocity(x, sigma, conditioning)
        s = sigma.view(-1, 1, 1)
        m = mask.unsqueeze(-1)
        x0 = x - s * v
        x0 = x0 * (1 - m) + clean * m
        return (x - x0) / s

    return forward


def test_conditioned_sigmas_schedule_shape_and_start():
    sigmas = conditioned_sigmas(8, SETTINGS_NOCFG)
    assert len(sigmas) == 9
    assert float(sigmas[0]) == pytest.approx(1.0)
    assert float(sigmas[-1]) == pytest.approx(0.0)
    # Identical to what denoise() itself would build.
    assert torch.equal(sigmas, build_sigmas(8, shift=2.37))


def test_pinned_tokens_are_invariant_across_all_steps():
    torch.manual_seed(0)
    clean = torch.randn(1, 6, 4)
    mask = torch.zeros(1, 6)
    mask[:, :2] = 1.0  # tokens 0-1 fully conditioned

    sigmas = conditioned_sigmas(6, SETTINGS_NOCFG)
    noise = torch.randn(1, 6, 4)
    scaled = (1 - mask).unsqueeze(-1) * float(sigmas[0])
    x_init = noise * scaled + clean * (1 - scaled)

    # Raw model: arbitrary garbage velocity — the blend must still pin tokens.
    forward = _blended_forward(mask, clean, lambda x, s, c: torch.ones_like(x) * 3.0)
    out = denoise_prenoised(forward, x_init, {"context": None},
                            steps=6, sampling_settings=SETTINGS_NOCFG,
                            guidance_scale=1.0, sigmas=sigmas)
    assert torch.allclose(out[:, :2], clean[:, :2], atol=1e-5)
    assert not torch.allclose(out[:, 2:], clean[:, 2:])  # free tokens moved


def test_x0_blend_commutes_with_velocity_cfg():
    """blend(CFG(u, c)) == CFG(blend(u), blend(c)) — the identity that lets the
    wrapper blend inside model_forward while TrueCFG combines outside."""
    torch.manual_seed(1)
    x = torch.randn(1, 5, 3)
    clean = torch.randn(1, 5, 3)
    mask = torch.rand(1, 5)
    sigma = torch.tensor([0.7])
    v_cond, v_uncond = torch.randn_like(x), torch.randn_like(x)
    scale = 4.0

    def blend(v):
        s = sigma.view(-1, 1, 1)
        m = mask.unsqueeze(-1)
        x0 = x - s * v
        x0 = x0 * (1 - m) + clean * m
        return (x - x0) / s

    inside = blend(v_uncond) + scale * (blend(v_cond) - blend(v_uncond))
    outside = blend(v_uncond + scale * (v_cond - v_uncond))
    assert torch.allclose(inside, outside, atol=1e-5)


def test_denoise_prenoised_runs_cfg_strategy():
    torch.manual_seed(2)
    x_init = torch.randn(1, 4, 2)
    calls = []

    def forward(x, sigma, conditioning):
        calls.append(conditioning["tag"])
        return x * 0.1

    out = denoise_prenoised(forward, x_init, {"tag": "cond"}, {"tag": "uncond"},
                            steps=3, sampling_settings=SETTINGS_CFG, guidance_scale=4.0)
    assert out.shape == x_init.shape
    assert "cond" in calls and "uncond" in calls  # TrueCFG ran both passes


def test_guidance_override_with_default_guidance_scale_does_not_warn(caplog):
    import logging

    class _FakeGuider:
        def __call__(self, model_fn, x, sigma, cond, uncond, step_index):
            return model_fn(x, sigma, cond)

    x_init = torch.zeros(1, 4, 2)
    with caplog.at_level(logging.WARNING, logger="src.platform.runtime.native.sampling.conditioned"):
        denoise_prenoised(lambda x, s, c: torch.zeros_like(x), x_init, {"context": None},
                          steps=2, sampling_settings=SETTINGS_NOCFG,
                          guidance_scale=1.0, guidance_override=_FakeGuider())
    assert not any("guidance_override" in r.message for r in caplog.records)


def test_guidance_override_with_nondefault_guidance_scale_warns(caplog):
    # video_ltx routes through denoise_prenoised (not
    # denoise_loop.denoise), so it needs the identical silent-discard warning
    # -- a real guidance_scale being replaced by a guider override is exactly
    # the desync check_guider_mode_conflict() guards against upstream.
    import logging

    class _FakeGuider:
        def __init__(self):
            self.video_params = type("P", (), {"cfg_scale": 3.0})()

        def __call__(self, model_fn, x, sigma, cond, uncond, step_index):
            return model_fn(x, sigma, cond)

    x_init = torch.zeros(1, 4, 2)
    with caplog.at_level(logging.WARNING, logger="src.platform.runtime.native.sampling.conditioned"):
        denoise_prenoised(lambda x, s, c: torch.zeros_like(x), x_init, {"context": None},
                          steps=2, sampling_settings=SETTINGS_NOCFG,
                          guidance_scale=5.0, guidance_override=_FakeGuider())
    warnings = [r for r in caplog.records if "guidance_override" in r.message]
    assert len(warnings) == 1
    assert "5.0" in warnings[0].message
    assert "3.0" in warnings[0].message


def test_unknown_sampler_raises():
    with pytest.raises(ValueError, match="unknown sampler"):
        denoise_prenoised(lambda x, s, c: x, torch.zeros(1, 2, 2), {},
                          steps=2, sampler_name="banana",
                          sampling_settings=SETTINGS_NOCFG, guidance_scale=1.0)


# -- step_cache_options (FBCache) --------------------------------------------

def test_step_cache_options_absent_is_byte_identical():
    torch.manual_seed(3)
    x_init = torch.randn(1, 4, 2)

    def forward(x, sigma, conditioning):
        return torch.full_like(x, 0.1)

    kwargs = dict(steps=5, sampling_settings=SETTINGS_NOCFG, guidance_scale=1.0)
    base = denoise_prenoised(forward, x_init.clone(), {"context": None}, **kwargs)
    with_off = denoise_prenoised(
        forward, x_init.clone(), {"context": None}, step_cache_options={"rel_threshold": 0.0}, **kwargs,
    )
    assert torch.equal(base, with_off)


def test_step_cache_options_skips_reduce_model_evals():
    steps = 8
    warmup = 2
    probe = torch.ones(1, 4, 2)
    state = {"evals": 0}

    def forward(x, sigma, conditioning):
        cache = conditioning.get("step_cache")
        if cache is not None and cache.should_skip(probe):
            return cache.record_skip()
        state["evals"] += 1
        out = torch.full_like(x, 2.0)
        if cache is not None:
            cache.record_compute(probe, out)
        return out

    denoise_prenoised(
        forward, torch.zeros(1, 4, 2), {"context": None},
        steps=steps, sampling_settings=SETTINGS_NOCFG, guidance_scale=1.0,
        step_cache_options={"rel_threshold": 0.5, "warmup_steps": warmup, "max_consecutive_skips": 999},
    )
    assert state["evals"] < steps
    assert state["evals"] >= warmup + 1  # warmup + forced final compute


def test_step_cache_options_final_step_always_computes():
    probe = torch.ones(1, 4, 2)
    state = {"evals": 0}

    def forward(x, sigma, conditioning):
        cache = conditioning.get("step_cache")
        if cache is not None and cache.should_skip(probe):
            return cache.record_skip()
        state["evals"] += 1
        out = torch.full_like(x, 2.0)
        if cache is not None:
            cache.record_compute(probe, out)
        return out

    denoise_prenoised(
        forward, torch.zeros(1, 4, 2), {"context": None},
        steps=1, sampling_settings=SETTINGS_NOCFG, guidance_scale=1.0,
        step_cache_options={"rel_threshold": 1.0, "warmup_steps": 0},
    )
    assert state["evals"] == 1
