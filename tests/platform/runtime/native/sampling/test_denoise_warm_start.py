"""Trajectory warm-start end-to-end: the bit-identical resume proof + gating."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import torch

from src.platform.runtime.native.engine import NativeGenerator
from src.platform.runtime.native.sampling.denoise_loop import denoise
from src.platform.runtime.native.sampling.trajectory_cache import (
    CheckpointCaptureHook,
    TrajectoryEntry,
    checkpoint_steps,
    get_trajectory_cache,
)


def _model(bias: float):
    # x-dependent, deterministic velocity so the trajectory genuinely evolves
    # (checkpoints at different depths differ) and euler math is reproducible.
    def model_forward(x, sigma, cond):
        return 0.3 * x + cond["bias"]

    return model_forward


_SETTINGS = {"shift": 2.02, "guidance": None}


def _run(model, cond, steps=8, seed_noise=None, resume=None, hooks=()):
    return denoise(
        model,
        latents=torch.zeros(1, 4, 4, 4),
        cond=cond,
        uncond=None,
        steps=steps,
        sampler_name="euler",
        sampling_settings=_SETTINGS,
        guidance_scale=0.0,
        seed_noise=seed_noise,
        hooks=hooks,
        resume=resume,
    )


# --- the key correctness test: bit-identical resume -----------------------

def test_resume_is_bit_identical_to_cold_run():
    torch.manual_seed(0)
    noise = torch.randn(1, 4, 4, 4)
    cond = {"bias": torch.full((1, 4, 4, 4), 0.05)}
    entry = TrajectoryEntry(("k",), total_steps=8, schedule_sig="s")

    cold_final = _run(_model(0.05), cond, seed_noise=noise,
                      hooks=(CheckpointCaptureHook(entry, 8),))

    assert sorted(entry.checkpoints) == checkpoint_steps(8) == [2, 4, 6]
    for k in (2, 4, 6):
        warm_final = _run(_model(0.05), cond, resume=(k, entry.checkpoints[k]))
        assert torch.equal(warm_final, cold_final), f"resume at {k} diverged from cold"


def test_different_cond_resume_is_finite_and_differs():
    torch.manual_seed(1)
    noise = torch.randn(1, 4, 4, 4)
    cond = {"bias": torch.full((1, 4, 4, 4), 0.05)}
    entry = TrajectoryEntry(("k",), total_steps=8, schedule_sig="s")
    cold_final = _run(_model(0.05), cond, seed_noise=noise,
                      hooks=(CheckpointCaptureHook(entry, 8),))
    # resume from the same checkpoint but with CHANGED conditioning -> the tail
    # re-steers to a different (but valid) result.
    changed = {"bias": torch.full((1, 4, 4, 4), 0.9)}
    warm = _run(_model(0.9), changed, resume=(4, entry.checkpoints[4]))
    assert torch.isfinite(warm).all()
    assert not torch.allclose(warm, cold_final)


def test_resume_rejects_non_euler_sampler():
    entry_latent = torch.zeros(1, 4, 4, 4)
    with pytest.raises(ValueError, match="euler"):
        denoise(
            _model(0.0), latents=torch.zeros(1, 4, 4, 4), cond={"bias": torch.zeros(1, 4, 4, 4)},
            steps=8, sampler_name="dpmpp_2m", sampling_settings=_SETTINGS,
            guidance_scale=0.0, resume=(4, entry_latent),
        )


# --- engine gating + metadata (via _plan_warm_start, no real model) -------

def _fake_generator():
    g = SimpleNamespace(
        spec=SimpleNamespace(family="flux", variant="dev",
                             sampling_settings={"guidance": None, "shift": 2.02}),
        dit=SimpleNamespace(module=object()),
    )
    return g


def _plan(g, **kw):
    args = dict(warm_start=True, sampler="euler", init_latent=None,
                cond={"context": torch.randn(1, 4, 8)}, uncond=None, seed=1234,
                latents_shape=(1, 4, 4, 4), steps=8, image_seq_len=None, hooks=(),
                settings={"guidance": None, "shift": 2.02},
                guidance_options={}, sampler_options=None, step_cache_options=None,
                sigmas=None)
    args.update(kw)
    return NativeGenerator._plan_warm_start(
        g, args["warm_start"], args["sampler"], args["init_latent"], args["cond"],
        args["uncond"], args["seed"], args["latents_shape"], args["steps"],
        args["image_seq_len"], args["hooks"], args["settings"],
        args["guidance_options"], args["sampler_options"], args["step_cache_options"],
        sigmas=args["sigmas"],
    )


@pytest.fixture(autouse=True)
def _clean_cache():
    get_trajectory_cache().clear()
    yield
    get_trajectory_cache().clear()


def test_disabled_installs_no_capture_hook():
    g = _fake_generator()
    base_hooks = ("h",)
    resume, hooks = _plan(g, warm_start=False, hooks=base_hooks)
    assert resume is None
    assert hooks is base_hooks  # untouched — no capture hook, zero checkpoint copies
    assert g.last_warm_start is None


def test_ineligible_sampler_and_img2img_are_cold():
    g = _fake_generator()
    assert _plan(g, sampler="dpmpp_2m")[0] is None
    assert _plan(g, init_latent=torch.zeros(1, 4, 4, 4))[0] is None


def test_enabled_installs_capture_hook_cold_first_run():
    g = _fake_generator()
    resume, hooks = _plan(g)
    assert resume is None                       # nothing cached yet -> cold
    assert any(isinstance(h, CheckpointCaptureHook) for h in hooks)
    assert g.last_warm_start is None


def test_second_identical_run_resumes_and_emits_metadata():
    g = _fake_generator()
    cond = {"context": torch.randn(1, 4, 8)}
    # first run: cold, but installs a capture hook against a cache entry.
    resume1, hooks1 = _plan(g, cond=cond)
    assert resume1 is None
    entry = get_trajectory_cache().get(  # the entry the hook writes into
        next(iter(get_trajectory_cache()._entries))
    )
    # simulate the run having captured checkpoints.
    entry.checkpoints = {2: torch.zeros(1, 4, 4, 4), 4: torch.ones(1, 4, 4, 4),
                         6: torch.full((1, 4, 4, 4), 2.0)}
    # second run, identical conditioning -> similarity 1.0 -> resume at 75% (step 6).
    resume2, _ = _plan(g, cond=cond)
    assert resume2 is not None and resume2[0] == 6
    assert g.last_warm_start == {"resume_step": 6, "total_steps": 8,
                                 "steps_skipped": 6, "similarity": 1.0}


def test_apg_momentum_disables_warm_start():
    # Stateful APG momentum breaks the resumed-Euler identity, so warm-start must
    # be disabled outright (S6): cold + no capture hook.
    g = _fake_generator()
    resume, hooks = _plan(g, settings={"guidance": "cfg", "apg_momentum": -0.5})
    assert resume is None
    assert not any(isinstance(h, CheckpointCaptureHook) for h in hooks)


def test_settings_change_prevents_cross_resume():
    # A cached trajectory must not be resumed once a trajectory-affecting setting
    # (e.g. zero_init_steps / an APG knob) changes — different static key (E2).
    g = _fake_generator()
    cond = {"context": torch.randn(1, 4, 8)}
    _plan(g, cond=cond, guidance_options={"zero_init_steps": 0})
    entry = get_trajectory_cache().get(next(iter(get_trajectory_cache()._entries)))
    entry.checkpoints = {6: torch.full((1, 4, 4, 4), 2.0)}
    # same conditioning but a changed setting -> a fresh (cold) entry, no resume.
    resume, _ = _plan(g, cond=cond, guidance_options={"zero_init_steps": 3})
    assert resume is None


# --- explicit sigmas isolate the trajectory-cache key -------------

def test_explicit_sigmas_cannot_cross_resume_with_derived_schedule_of_same_length():
    g = _fake_generator()
    cond = {"context": torch.randn(1, 4, 8)}
    # A derived-schedule run (8 steps) caches checkpoints.
    _plan(g, cond=cond)
    entry = get_trajectory_cache().get(next(iter(get_trajectory_cache()._entries)))
    entry.checkpoints = {6: torch.full((1, 4, 4, 4), 2.0)}
    # An explicit-list run of the SAME nominal length (8 effective steps) and
    # identical conditioning must still be cold: distinct schedule signature.
    explicit = torch.tensor([1.0, 0.875, 0.75, 0.625, 0.5, 0.375, 0.25, 0.125, 0.0])
    resume, _ = _plan(g, cond=cond, sigmas=explicit)
    assert resume is None


def test_two_explicit_sigma_runs_share_a_cache_entry_when_identical():
    g = _fake_generator()
    cond = {"context": torch.randn(1, 4, 8)}
    explicit = torch.tensor([1.0, 0.5, 0.0])
    resume1, _ = _plan(g, cond=cond, steps=2, sigmas=explicit)
    assert resume1 is None  # cold first run
    entry = get_trajectory_cache().get(next(iter(get_trajectory_cache()._entries)))
    entry.checkpoints = {1: torch.full((1, 4, 4, 4), 3.0)}
    resume2, _ = _plan(g, cond=cond, steps=2, sigmas=explicit)
    assert resume2 is not None and resume2[0] == 1
