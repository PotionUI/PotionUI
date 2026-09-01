"""denoise() FBCache wiring: per-branch caches, final-step + skip_layers bypass.

These use a stub ``model_forward`` that reports a block-0-style probe through the
reserved ``step_cache`` key the real arch would drive, so the wiring can be
exercised without a model. The stub emulates the arch contract precisely:
``should_skip(probe)`` -> ``record_skip()`` (no compute) else ``record_compute``.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.denoise_loop import denoise
from src.platform.runtime.native.sampling.multimodal_guider import (
    MultiModalGuidance,
    MultiModalGuiderParams,
)


def _fbcache_stub(probe_for_step, velocity=0.0):
    """A model_forward that emulates a Flux forward driving a FirstBlockCache.

    ``probe_for_step(step)`` yields the block-0 probe tensor for the current
    step; identical probes across steps let the cache skip. ``evals`` counts real
    computes (a skip does no model work).
    """
    state = {"evals": 0, "step": 0}

    def model_forward(x, sigma, cond):
        cache = cond.get("step_cache")
        probe = probe_for_step(state["step"])
        if cache is not None and cache.should_skip(probe):
            return cache.record_skip()
        state["evals"] += 1
        out = torch.full_like(x, velocity)
        if cache is not None:
            cache.record_compute(probe, out)
        return out

    return model_forward, state


def test_no_options_is_byte_identical():
    latents = torch.zeros(1, 4, 4, 4)
    noise = torch.randn(1, 4, 4, 4)
    kwargs = dict(
        latents=latents, cond={"context": "c"}, uncond=None, steps=5,
        sampling_settings={"shift": 2.02, "guidance": None},
        guidance_scale=0.0, seed_noise=noise,
    )

    def const(x, sigma, cond):
        return torch.full_like(x, 2.0)

    base = denoise(const, **kwargs)
    with_opts_off = denoise(const, step_cache_options={"rel_threshold": 0.0}, **kwargs)
    assert torch.equal(base, with_opts_off)


def test_skips_reduce_model_evals_but_keep_warmup_and_final():
    # Constant probe -> after warmup every step is a skip candidate, except the
    # final step which the wiring forces to compute (step_cache=None there).
    steps = 8
    warmup = 2
    probe = torch.ones(1, 4, 4, 4)
    model_forward, state = _fbcache_stub(lambda s: probe)

    denoise(
        model_forward,
        latents=torch.zeros(1, 4, 4, 4),
        cond={"context": "c"},
        uncond=None,
        steps=steps,
        sampling_settings={"shift": 2.02, "guidance": None},
        guidance_scale=0.0,
        seed_noise=torch.randn(1, 4, 4, 4),
        step_cache_options={"rel_threshold": 0.5, "warmup_steps": warmup,
                            "max_consecutive_skips": 999},
    )
    # warmup computes + final compute; the middle steps skip. evals << steps.
    assert state["evals"] < steps
    assert state["evals"] >= warmup + 1  # warmup steps + forced final compute


def test_final_step_always_computes():
    # A single-step run has step_index 0 == total_steps-1 == final -> never cached.
    probe = torch.ones(1, 4, 2, 2)
    model_forward, state = _fbcache_stub(lambda s: probe)
    denoise(
        model_forward,
        latents=torch.zeros(1, 4, 2, 2),
        cond={"context": "c"},
        uncond=None,
        steps=1,
        sampling_settings={"shift": 2.02, "guidance": None},
        guidance_scale=0.0,
        seed_noise=torch.zeros(1, 4, 2, 2),
        step_cache_options={"rel_threshold": 1.0, "warmup_steps": 0},
    )
    assert state["evals"] == 1  # the sole (final) step computed


def test_cond_and_uncond_get_independent_caches():
    # Asymmetric probes: cond probe is constant (skippable) while uncond probe
    # changes every step (never skippable). Independent caches must yield
    # different skip patterns; a shared cache could not.
    steps = 6
    cond_probe = torch.ones(1, 4, 2, 2)
    calls = {"cond": 0, "uncond": 0, "cond_evals": 0, "uncond_evals": 0}

    def model_forward(x, sigma, cond):
        which = cond["branch"]
        calls[which] += 1
        cache = cond.get("step_cache")
        if which == "cond":
            probe = cond_probe
        else:
            # alternate 0<->100 so the relative change is always huge -> never skippable.
            probe = torch.full((1, 4, 2, 2), 100.0 * (calls["uncond"] % 2))
        if cache is not None and cache.should_skip(probe):
            return cache.record_skip()
        calls[which + "_evals"] += 1
        out = torch.full_like(x, 3.0 if which == "cond" else 1.0)
        if cache is not None:
            cache.record_compute(probe, out)
        return out

    denoise(
        model_forward,
        latents=torch.zeros(1, 4, 2, 2),
        cond={"branch": "cond", "v": 3.0},
        uncond={"branch": "uncond", "v": 1.0},
        steps=steps,
        sampling_settings={"shift": 2.02, "guidance": "cfg"},
        guidance_scale=2.0,
        seed_noise=torch.zeros(1, 4, 2, 2),
        cfg_zero_star=False,
        step_cache_options={"rel_threshold": 0.5, "warmup_steps": 1,
                            "max_consecutive_skips": 999},
    )
    # cond can skip (constant probe) so it computes far fewer than its calls;
    # uncond's probe always changes so it computes on every call it receives.
    assert calls["cond_evals"] < calls["cond"]
    assert calls["uncond_evals"] == calls["uncond"]


def test_skip_layers_pass_never_uses_the_cache():
    # SLG's degraded pass injects conditioning["skip_layers"]; the wiring must
    # bypass caching for it entirely (fresh eval, never touches/consumes a cache).
    steps = 6
    probe = torch.ones(1, 4, 2, 2)
    seen = {"skip_layers_had_cache": [], "cond_evals": 0}

    def model_forward(x, sigma, cond):
        cache = cond.get("step_cache")
        if "skip_layers" in cond:
            seen["skip_layers_had_cache"].append(cache is not None)
            return torch.zeros_like(x)
        if cache is not None and cache.should_skip(probe):
            return cache.record_skip()
        seen["cond_evals"] += 1
        out = torch.full_like(x, 2.0)
        if cache is not None:
            cache.record_compute(probe, out)
        return out

    denoise(
        model_forward,
        latents=torch.zeros(1, 4, 2, 2),
        cond={"context": "c"},
        uncond=None,
        steps=steps,
        sampling_settings={"guidance": "embedded", "slg_scale": 2.0, "slg_layers": {0}},
        guidance_scale=3.5,
        seed_noise=torch.zeros(1, 4, 2, 2),
        step_cache_options={"rel_threshold": 0.5, "warmup_steps": 1,
                            "max_consecutive_skips": 999},
    )
    # the degraded pass ran and NEVER carried a step_cache.
    assert seen["skip_layers_had_cache"]
    assert not any(seen["skip_layers_had_cache"])


def test_multimodal_guider_composes_with_fbcache():
    """MultiModalGuidance (quality_mode's guidance_override) + FBCache: the
    cond/uncond forwards it makes with the identical cond/uncond dict objects
    get identity-matched, per-branch caches (same routing plain CFG gets);
    the STG-perturbed forward it builds as a fresh ``{**cond, ...}`` dict
    never identity-matches, so it always bypasses the cache and computes in
    full every step -- the two features coexist without either corrupting
    the other's branch."""
    steps = 6
    cond_probe = torch.ones(1, 6, 4)
    calls = {"cond": 0, "uncond": 0, "cond_evals": 0, "uncond_evals": 0, "stg": 0}
    stg_had_cache = []

    def model_forward(x, sigma, conditioning):
        cache = conditioning.get("step_cache")
        if "stg_skip_blocks" in conditioning:
            calls["stg"] += 1
            stg_had_cache.append(cache is not None)
            return torch.full_like(x, 5.0)
        branch = conditioning["branch"]
        calls[branch] += 1
        if cache is not None and cache.should_skip(cond_probe):
            return cache.record_skip()
        calls[branch + "_evals"] += 1
        out = torch.full_like(x, 3.0 if branch == "cond" else 1.0)
        if cache is not None:
            cache.record_compute(cond_probe, out)
        return out

    video_params = MultiModalGuiderParams(
        cfg_scale=3.0, stg_scale=1.0, modality_scale=1.0, rescale_scale=0.0, stg_blocks=[0],
    )
    guidance_override = MultiModalGuidance(video_params)

    denoise(
        model_forward,
        latents=torch.zeros(1, 6, 4),
        cond={"context": "pos", "mm_video_tokens": 6, "branch": "cond"},
        uncond={"context": "neg", "mm_video_tokens": 6, "branch": "uncond"},
        steps=steps,
        sampling_settings={"shift": 2.02, "guidance": "cfg"},  # unused: guidance_override wins
        guidance_scale=1.0,
        seed_noise=torch.zeros(1, 6, 4),
        guidance_override=guidance_override,
        step_cache_options={"rel_threshold": 0.5, "warmup_steps": 1,
                            "max_consecutive_skips": 999},
    )

    # STG runs every step (the guider doesn't skip it) and NEVER sees a cache.
    assert calls["stg"] == steps
    assert stg_had_cache and not any(stg_had_cache)
    # cond/uncond each get their own cache and benefit from it (warmup + the
    # forced final compute are the only real evals; the rest skip).
    assert calls["cond"] == steps and calls["cond_evals"] < calls["cond"]
    assert calls["uncond"] == steps and calls["uncond_evals"] < calls["uncond"]
