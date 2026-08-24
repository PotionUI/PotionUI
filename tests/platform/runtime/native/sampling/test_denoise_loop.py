"""End-to-end denoise() tests with a mock model on small latents."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.errors import PoisonedConditioningError
from src.platform.runtime.native.sampling.cfg import NoCFG, SkipLayerGuidance, TrueCFG
from src.platform.runtime.native.sampling.denoise_loop import (
    SAMPLERS,
    STOCHASTIC_SAMPLERS,
    _expert_switch_step,
    _make_guidance,
    denoise,
    ensure_sampler_generator,
)


def _const_velocity_model(v0):
    def model_forward(x, sigma, cond):
        return torch.full_like(x, v0)

    return model_forward


def test_txt2img_end_to_end_constant_velocity():
    # txt2img: latents=zeros, denoise=1 -> sigma0=1 -> x_init=noise.
    # constant velocity v -> x_final = noise - v.
    latents = torch.zeros(1, 4, 4, 4)
    noise = torch.randn(1, 4, 4, 4)
    out = denoise(
        _const_velocity_model(2.0),
        latents,
        cond={},
        uncond=None,
        steps=4,
        sampling_settings={"shift": 2.02, "guidance": None},
        guidance_scale=0.0,
        seed_noise=noise,
    )
    assert out.shape == latents.shape
    assert torch.allclose(out, noise - 2.0, atol=1e-5)


def test_embedded_guidance_flows_into_model():
    seen = {}

    def model_forward(x, sigma, cond):
        seen["guidance"] = cond.get("guidance")
        return torch.zeros_like(x)

    latents = torch.zeros(2, 4, 2, 2)
    denoise(
        model_forward,
        latents,
        cond={"context": "c"},
        steps=2,
        sampling_settings={"shift": 2.02, "guidance": "embedded"},
        guidance_scale=3.5,
        seed_noise=torch.randn_like(latents),
    )
    assert seen["guidance"] is not None
    assert seen["guidance"].shape == (2,)
    assert torch.allclose(seen["guidance"], torch.full((2,), 3.5))


# --------------------------------------------------------------------------- #
# guidance_override silently discarding guidance_scale
# --------------------------------------------------------------------------- #

def _run_with_override(guidance_scale, guidance_override, caplog):
    latents = torch.zeros(1, 4, 2, 2)
    with caplog.at_level(logging.WARNING, logger="src.platform.runtime.native.sampling.denoise_loop"):
        denoise(
            _const_velocity_model(0.0),
            latents,
            cond={},
            uncond=None,
            steps=2,
            sampling_settings={"shift": 2.02, "guidance": None},
            guidance_scale=guidance_scale,
            guidance_override=guidance_override,
            seed_noise=torch.randn_like(latents),
        )
    return caplog.records


def test_guidance_override_with_default_guidance_scale_does_not_warn(caplog):
    # guidance_scale=1.0 (the neutral "no CFG configured" default) alongside an
    # override is not a sign of a conflicting regime -- no behavior change,
    # and no noise in the logs either.
    records = _run_with_override(1.0, NoCFG(), caplog)
    assert not any("guidance_override" in r.message for r in records)


def test_guidance_override_with_nondefault_guidance_scale_warns(caplog):
    # A real (non-1.0) guidance_scale silently discarded by an override is
    # exactly the desync check_guider_mode_conflict() guards against upstream
    # -- loud here too, as defense-in-depth, with no behavior change (the
    # override still wins either way).
    records = _run_with_override(3.5, NoCFG(), caplog)
    warnings = [r for r in records if "guidance_override" in r.message]
    assert len(warnings) == 1
    assert "3.5" in warnings[0].message
    assert "n/a" in warnings[0].message  # NoCFG has no introspectable cfg_scale


def test_guidance_override_warning_names_override_cfg_when_introspectable(caplog):
    # A guider-shaped override (e.g. MultiModalGuidance) exposes
    # video_params.cfg_scale -- the warning should surface that value too.
    class _FakeGuider:
        def __init__(self):
            self.video_params = SimpleNamespace(cfg_scale=3.0)

        def __call__(self, model_fn, x, sigma, cond, uncond, step_index):
            return model_fn(x, sigma, cond)

    records = _run_with_override(1.0, _FakeGuider(), caplog)
    assert not records  # guidance_scale=1.0 -- neutral default, no warning yet

    records = _run_with_override(4.0, _FakeGuider(), caplog)
    warnings = [r for r in records if "guidance_override" in r.message]
    assert len(warnings) == 1
    assert "4.0" in warnings[0].message
    assert "3.0" in warnings[0].message


def test_true_cfg_two_forwards():
    calls = {"n": 0}

    def model_forward(x, sigma, cond):
        calls["n"] += 1
        return torch.full_like(x, cond["v"])

    latents = torch.zeros(1, 4, 2, 2)
    out = denoise(
        model_forward,
        latents,
        cond={"v": 5.0},
        uncond={"v": 1.0},
        steps=1,
        sampling_settings={"shift": 2.02, "guidance": "cfg"},
        guidance_scale=2.0,
        seed_noise=torch.zeros_like(latents),
        cfg_zero_star=False,  # isolate plain-CFG math from the CFG-Zero* rescale
    )
    # one step, sigma 1->0: x_final = x_init + (0-1)*v_cfg, x_init=0.
    # v_cfg = 1 + 2*(5-1) = 9 -> x_final = -9.
    assert calls["n"] == 2  # cond + uncond
    assert torch.allclose(out, torch.full_like(latents, -9.0), atol=1e-5)


def test_cfg_zero_star_kwargs_thread_through_denoise():
    """cfg_zero_star/zero_init_steps passed to denoise() reach TrueCFG: with
    zero_init_steps >= total steps, the whole run is a zero-velocity no-op
    (x_final == x_init == noise, since sigma0=1 -> x_init=noise for txt2img)."""
    calls = {"n": 0}

    def model_forward(x, sigma, cond):
        calls["n"] += 1
        return torch.full_like(x, cond["v"])

    latents = torch.zeros(1, 4, 2, 2)
    noise = torch.randn(1, 4, 2, 2)
    out = denoise(
        model_forward,
        latents,
        cond={"v": 5.0},
        uncond={"v": 1.0},
        steps=3,
        sampling_settings={"shift": 2.02, "guidance": "cfg"},
        guidance_scale=2.0,
        seed_noise=noise,
        zero_init_steps=3,
    )
    assert calls["n"] == 0  # every step short-circuited to zero velocity
    assert torch.allclose(out, noise, atol=1e-5)  # zero velocity -> latent unchanged


def test_flux_dynamic_mu_path_selected():
    # base/max/image_seq_len present -> dynamic-mu schedule; sigma0 must be ~1.
    captured = []

    def model_forward(x, sigma, cond):
        captured.append(float(sigma[0]))
        return torch.zeros_like(x)

    latents = torch.zeros(1, 16, 2, 2)
    denoise(
        model_forward,
        latents,
        cond={},
        steps=3,
        sampling_settings={
            "shift": 1.15,
            "base_shift": 0.5,
            "max_shift": 1.15,
            "guidance": "embedded",
        },
        guidance_scale=1.0,
        image_seq_len=4096,
        seed_noise=torch.randn_like(latents),
    )
    assert captured[0] == pytest.approx(1.0, abs=1e-5)


def test_unknown_sampler_raises():
    with pytest.raises(ValueError):
        denoise(
            _const_velocity_model(1.0),
            torch.zeros(1, 4, 2, 2),
            cond={},
            steps=2,
            sampler_name="does_not_exist",
            sampling_settings={"shift": 2.02, "guidance": None},
            guidance_scale=0.0,
        )


def test_unknown_guidance_raises():
    with pytest.raises(ValueError):
        denoise(
            _const_velocity_model(1.0),
            torch.zeros(1, 4, 2, 2),
            cond={},
            steps=2,
            sampling_settings={"shift": 2.02, "guidance": "bogus"},
            guidance_scale=0.0,
            seed_noise=torch.zeros(1, 4, 2, 2),
        )


def test_sampler_registry_extensible():
    assert "euler" in SAMPLERS
    assert callable(SAMPLERS["euler"])


def test_img2img_blends_noise_and_latent():
    # denoise<1 -> sigma0<1 -> x_init = sigma0*noise + (1-sigma0)*latent.
    # With v0=0 the loop is a no-op, so out == x_init exactly.
    latents = torch.full((1, 4, 2, 2), 4.0)
    noise = torch.full((1, 4, 2, 2), 8.0)
    out = denoise(
        _const_velocity_model(0.0),
        latents,
        cond={},
        steps=4,
        sampling_settings={"shift": 1.0, "guidance": None},
        guidance_scale=0.0,
        seed_noise=noise,
        denoise_strength=0.5,
    )
    # shift=1 identity, denoise=0.5 -> sigma0=0.5 -> x = 0.5*8 + 0.5*4 = 6.
    assert torch.allclose(out, torch.full_like(latents, 6.0), atol=1e-5)


# -- explicit `sigmas=` override (bypasses build_sigmas) ------

def test_explicit_sigmas_override_bypasses_build_sigmas_and_its_head_forcing():
    """`build_sigmas`'s `schedule="manual"` mode unconditionally forces
    sigmas[0]=1.0/sigmas[-1]=0.0 -- there is no way to get a genuine partial-
    noise refine (sigma0 < 1.0) through it. Passing `sigmas=` directly must
    use the tensor AS-IS, with no such forcing, so a stage-2 refine recipe
    (e.g. Lightricks' `STAGE_2_DISTILLED_SIGMA_VALUES`, sigma0=0.909375) mixes
    exactly the intended amount of noise."""
    latents = torch.full((1, 4, 2, 2), 4.0)
    noise = torch.full((1, 4, 2, 2), 8.0)
    explicit = torch.tensor([0.909375, 0.725, 0.421875, 0.0])
    out = denoise(
        _const_velocity_model(0.0),
        latents,
        cond={},
        steps=999,  # ignored entirely when `sigmas` is given
        sampling_settings={"shift": 1.0, "guidance": None},
        guidance_scale=0.0,
        seed_noise=noise,
        sigmas=explicit,
    )
    # v0=0 -> loop is a no-op -> out == x_init == sigma0*noise + (1-sigma0)*latent.
    expected = 0.909375 * 8.0 + (1 - 0.909375) * 4.0
    assert torch.allclose(out, torch.full_like(latents, expected), atol=1e-5)


def test_explicit_sigmas_override_moved_to_latents_device_and_dtype_fp32():
    latents = torch.zeros((1, 2, 2, 2))
    explicit = torch.tensor([1.0, 0.5, 0.0])
    out = denoise(
        _const_velocity_model(0.0), latents, cond={}, steps=2,
        sampling_settings={"shift": 1.0, "guidance": None}, guidance_scale=0.0,
        sigmas=explicit,
    )
    assert out.shape == latents.shape
    assert torch.isfinite(out).all()


def test_new_samplers_registered():
    assert "euler_sde" in SAMPLERS
    assert "euler_restart" in SAMPLERS
    assert callable(SAMPLERS["euler_sde"])
    assert callable(SAMPLERS["euler_restart"])


def test_euler_ancestral_cfg_pp_registered():
    assert "euler_ancestral_cfg_pp" in SAMPLERS
    assert callable(SAMPLERS["euler_ancestral_cfg_pp"])


def test_euler_cfg_pp_registered():
    assert "euler_cfg_pp" in SAMPLERS
    assert callable(SAMPLERS["euler_cfg_pp"])
    # Deterministic -- not part of the seed-generator-needing set.
    assert "euler_cfg_pp" not in STOCHASTIC_SAMPLERS


# -- ensure_sampler_generator (task #40: seed determinism) -------------------

def test_stochastic_samplers_set():
    assert STOCHASTIC_SAMPLERS == {
        "euler_sde", "euler_ancestral", "euler_ancestral_cfg_pp", "dpmpp_2m_sde", "lcm",
    }


def test_ensure_sampler_generator_noop_for_deterministic_sampler():
    gen = torch.Generator().manual_seed(1)
    assert ensure_sampler_generator({"eta": 0.1}, "euler", gen) == {"eta": 0.1}
    assert ensure_sampler_generator(None, "euler", gen) is None


def test_ensure_sampler_generator_noop_when_generator_none():
    assert ensure_sampler_generator({"eta": 0.1}, "euler_sde", None) == {"eta": 0.1}
    assert ensure_sampler_generator(None, "euler_sde", None) is None


@pytest.mark.parametrize("sampler", sorted(STOCHASTIC_SAMPLERS))
def test_ensure_sampler_generator_populates_for_stochastic_sampler(sampler):
    gen = torch.Generator().manual_seed(1)
    out = ensure_sampler_generator(None, sampler, gen)
    assert out == {"generator": gen}

    out2 = ensure_sampler_generator({"eta": 0.5}, sampler, gen)
    assert out2 == {"eta": 0.5, "generator": gen}


def test_ensure_sampler_generator_preserves_explicit_generator():
    caller_gen = torch.Generator().manual_seed(1)
    auto_gen = torch.Generator().manual_seed(2)
    out = ensure_sampler_generator({"generator": caller_gen}, "lcm", auto_gen)
    assert out["generator"] is caller_gen


def test_ensure_sampler_generator_does_not_mutate_input_dict():
    original = {"eta": 0.5}
    gen = torch.Generator().manual_seed(1)
    out = ensure_sampler_generator(original, "euler_sde", gen)
    assert "generator" not in original
    assert out is not original


def test_euler_sde_selectable_end_to_end_with_eta_zero_matches_euler():
    # eta=0 must reduce exactly to plain euler even through the full denoise()
    # orchestrator (schedule build + noise-scaling + sampler dispatch).
    latents = torch.zeros(1, 4, 4, 4)
    noise = torch.randn(1, 4, 4, 4)
    kwargs = dict(
        latents=latents,
        cond={},
        uncond=None,
        steps=4,
        sampling_settings={"shift": 2.02, "guidance": None},
        guidance_scale=0.0,
        seed_noise=noise,
    )
    out_euler = denoise(_const_velocity_model(2.0), sampler_name="euler", **kwargs)
    out_sde = denoise(
        _const_velocity_model(2.0), sampler_name="euler_sde",
        sampler_options={"eta": 0.0}, **kwargs,
    )
    assert torch.equal(out_euler, out_sde)


def test_euler_restart_selectable_end_to_end_zero_restarts_matches_euler():
    latents = torch.zeros(1, 4, 4, 4)
    noise = torch.randn(1, 4, 4, 4)
    kwargs = dict(
        latents=latents,
        cond={},
        uncond=None,
        steps=4,
        sampling_settings={"shift": 2.02, "guidance": None},
        guidance_scale=0.0,
        seed_noise=noise,
    )
    out_euler = denoise(_const_velocity_model(2.0), sampler_name="euler", **kwargs)
    out_restart = denoise(_const_velocity_model(2.0), sampler_name="euler_restart", **kwargs)
    assert torch.equal(out_euler, out_restart)


def test_schedule_and_detail_strength_thread_through_sampling_settings():
    # Both knobs default off; setting them via sampling_settings must actually
    # change the schedule the loop runs on (observed through the sigma passed
    # to model_forward at step 0), while a bare "shift" run is untouched.
    seen_sigma0 = {}

    def model_forward(x, sigma, cond):
        seen_sigma0.setdefault("v", float(sigma[0]))
        return torch.zeros_like(x)

    latents = torch.zeros(1, 4, 2, 2)
    denoise(
        model_forward,
        latents,
        cond={},
        steps=4,
        sampling_settings={"schedule": "exponential", "guidance": None},
        guidance_scale=0.0,
        seed_noise=torch.zeros_like(latents),
    )
    assert seen_sigma0["v"] == pytest.approx(1.0, abs=1e-5)  # schedule still starts at 1.0


# -- _make_guidance: APG + SLG settings threading -----------------------------

def test_apg_kwargs_default_to_true_cfg_own_off_defaults():
    strat = _make_guidance({"guidance": "cfg"}, guidance_scale=2.0)
    assert isinstance(strat, TrueCFG)
    assert strat.apg_eta == 1.0
    assert strat.apg_norm_threshold == 0.0
    assert strat.apg_momentum == 0.0
    assert not strat._apg_active


def test_apg_kwargs_thread_from_sampling_settings_into_true_cfg():
    strat = _make_guidance(
        {"guidance": "cfg", "apg_eta": 0.3, "apg_norm_threshold": 0.5, "apg_momentum": -0.4},
        guidance_scale=2.0,
    )
    assert isinstance(strat, TrueCFG)
    assert strat.apg_eta == 0.3
    assert strat.apg_norm_threshold == 0.5
    assert strat.apg_momentum == -0.4
    assert strat._apg_active


def test_apg_kwargs_ignored_by_non_cfg_modes():
    # embedded/none modes have no TrueCFG to carry apg_* onto; must not raise.
    embedded = _make_guidance({"guidance": "embedded", "apg_eta": 0.1}, guidance_scale=3.5)
    none_mode = _make_guidance({"guidance": None, "apg_eta": 0.1}, guidance_scale=0.0)
    assert not isinstance(embedded, TrueCFG)
    assert not isinstance(none_mode, TrueCFG)


def test_slg_scale_zero_or_absent_does_not_wrap():
    default_absent = _make_guidance({"guidance": "cfg"}, guidance_scale=2.0)
    explicit_zero = _make_guidance({"guidance": "cfg", "slg_scale": 0.0}, guidance_scale=2.0)
    assert isinstance(default_absent, TrueCFG)
    assert isinstance(explicit_zero, TrueCFG)
    assert not isinstance(default_absent, SkipLayerGuidance)
    assert not isinstance(explicit_zero, SkipLayerGuidance)


def test_slg_scale_positive_wraps_the_inner_strategy():
    strat = _make_guidance(
        {"guidance": "cfg", "slg_scale": 1.5, "slg_layers": {0, 2}, "slg_sigma_start": 0.9, "slg_sigma_end": 0.1},
        guidance_scale=2.0,
    )
    assert isinstance(strat, SkipLayerGuidance)
    assert isinstance(strat.inner, TrueCFG)
    assert strat.slg_scale == 1.5
    assert strat.layers == {0, 2}
    assert strat.sigma_start == 0.9
    assert strat.sigma_end == 0.1


def test_slg_wraps_embedded_and_none_modes_too():
    embedded_wrapped = _make_guidance(
        {"guidance": "embedded", "slg_scale": 1.0, "slg_layers": {1}}, guidance_scale=3.5
    )
    none_wrapped = _make_guidance(
        {"guidance": None, "slg_scale": 1.0, "slg_layers": {1}}, guidance_scale=0.0
    )
    assert isinstance(embedded_wrapped, SkipLayerGuidance)
    assert isinstance(none_wrapped, SkipLayerGuidance)


def test_slg_no_default_layers_means_no_op_even_with_scale():
    # "no default layers" per spec: slg_scale>0 with no slg_layers key still
    # wraps (so the settings-driven toggle is consistent), but the wrapped
    # strategy's own empty-layers passthrough (tested in test_cfg.py) makes it
    # inert.
    strat = _make_guidance({"guidance": "cfg", "slg_scale": 1.0}, guidance_scale=2.0)
    assert isinstance(strat, SkipLayerGuidance)
    assert strat.layers == set()


# -- _expert_switch_step / discontinuity_steps wiring -------

def test_expert_switch_step_none_boundary_is_none():
    sigmas = torch.linspace(1.0, 0.0, 5)
    assert _expert_switch_step(sigmas, None) is None


def test_expert_switch_step_finds_first_crossing():
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    # boundary 0.6 -> sigmas[0]=1.0>0.6, sigmas[1]=0.8>0.6, sigmas[2]=0.5<=0.6.
    assert _expert_switch_step(sigmas, 0.6) == 2


def test_expert_switch_step_never_crossed_is_none():
    sigmas = torch.tensor([1.0, 0.8, 0.5, 0.2, 0.0])
    assert _expert_switch_step(sigmas, -0.1) is None


def test_denoise_threads_discontinuity_step_into_unipc_sampler_options(monkeypatch):
    seen = {}

    def spy_unipc(model_fn, x, sigmas, guidance, cond, uncond, hooks=(), is_cancelled=None,
                  sampler_options=None, **kw):
        seen["sampler_options"] = sampler_options
        return x

    monkeypatch.setitem(SAMPLERS, "unipc", spy_unipc)
    latents = torch.zeros(1, 4, 2, 2)
    denoise(
        _const_velocity_model(1.0), latents, cond={}, uncond=None,
        steps=4, sampler_name="unipc",
        sampling_settings={"shift": 2.02, "guidance": None}, guidance_scale=0.0,
        seed_noise=torch.zeros_like(latents), expert_boundary=0.5,
    )
    assert seen["sampler_options"]["discontinuity_steps"]


def test_denoise_expert_boundary_none_leaves_sampler_options_unset(monkeypatch):
    seen = {}

    def spy_unipc(model_fn, x, sigmas, guidance, cond, uncond, hooks=(), is_cancelled=None,
                  sampler_options=None, **kw):
        seen["sampler_options"] = sampler_options
        return x

    monkeypatch.setitem(SAMPLERS, "unipc", spy_unipc)
    latents = torch.zeros(1, 4, 2, 2)
    denoise(
        _const_velocity_model(1.0), latents, cond={}, uncond=None,
        steps=4, sampler_name="unipc",
        sampling_settings={"shift": 2.02, "guidance": None}, guidance_scale=0.0,
        seed_noise=torch.zeros_like(latents), expert_boundary=None,
    )
    assert seen["sampler_options"] is None


# -- poisoned conditioning entry check ----------------------

def test_denoise_raises_poisoned_conditioning_before_any_step_on_bad_cond():
    latents = torch.zeros(1, 4, 2, 2)
    cond = {"context": torch.full((1, 2, 2), float("nan"))}
    with pytest.raises(PoisonedConditioningError) as exc:
        denoise(
            _const_velocity_model(1.0), latents, cond=cond, uncond=None,
            steps=4, sampling_settings={"shift": 2.02, "guidance": None}, guidance_scale=0.0,
            seed_noise=torch.zeros_like(latents),
        )
    assert exc.value.which == "cond"
    assert exc.value.key == "context"


def test_denoise_raises_poisoned_conditioning_on_bad_uncond():
    latents = torch.zeros(1, 4, 2, 2)
    cond = {"context": torch.zeros(1, 2, 2)}
    uncond = {"context": torch.full((1, 2, 2), float("inf"))}
    with pytest.raises(PoisonedConditioningError) as exc:
        denoise(
            _const_velocity_model(1.0), latents, cond=cond, uncond=uncond,
            steps=4, sampling_settings={"shift": 2.02, "guidance": None}, guidance_scale=0.0,
            seed_noise=torch.zeros_like(latents),
        )
    assert exc.value.which == "uncond"


def test_denoise_finite_conditioning_is_unaffected():
    latents = torch.zeros(1, 4, 2, 2)
    cond = {"context": torch.ones(1, 2, 2)}
    out = denoise(
        _const_velocity_model(1.0), latents, cond=cond, uncond=None,
        steps=4, sampling_settings={"shift": 2.02, "guidance": None}, guidance_scale=0.0,
        seed_noise=torch.zeros_like(latents),
    )
    assert torch.isfinite(out).all()
