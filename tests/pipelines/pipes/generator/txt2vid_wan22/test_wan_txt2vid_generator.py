"""Tests for the generator/txt2vid_wan22 pipe: expert router, 5D latents, video."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.pipelines.outputs import GalleryGenerationOutput
from vendor.gpl.comfyui.ops import disable_weight_init
from src.platform.runtime.native.errors import DecodeNumericsError
from src.platform.runtime.native.vae.causal_3d import AutoEncoderCausal3D, LATENTS_MEAN, LATENTS_STD
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.txt2vid_wan22.main import (
    GeneratorWanTxt2VidPipe, _ExpertRouter, _WanCtx, _decode_video,
)
from src.pipelines.pipes._shared.generation.dit_placement import _dit_lora_delta_gb


class _FakeDiT:
    def __init__(self, tag):
        self.tag = tag
        self.moved = []
        self.offloaded = 0
        self.module = lambda x, t, ctx: torch.zeros_like(x)

    def move_to(self, d):
        self.moved.append(d)

    def offload(self):
        self.offloaded += 1


# -- expert router ---------------------------------------------------------

def test_router_selects_high_above_boundary_low_below():
    high, low = _FakeDiT("high"), _FakeDiT("low")
    router = _ExpertRouter(high, low, boundary=0.875, device="cpu")
    cond = {"context": torch.zeros(1, 2, 4)}
    x = torch.zeros(1, 16, 1, 2, 2)
    router(x, torch.tensor([0.95]), cond)          # above boundary -> high
    assert router.active is high
    router(x, torch.tensor([0.5]), cond)            # below boundary -> low
    assert router.active is low
    assert high.offloaded == 1                      # high offloaded on transition


def test_router_single_expert_always_high():
    high = _FakeDiT("high")
    router = _ExpertRouter(high, None, boundary=0.875, device="cpu")
    cond = {"context": torch.zeros(1, 2, 4)}
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.1]), cond)
    assert router.active is high


def test_router_scales_timestep_by_1000():
    seen = {}
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx: seen.setdefault("t", t) or torch.zeros_like(x)
    router = _ExpertRouter(high, None, 0.875, "cpu")
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), {"context": torch.zeros(1, 2, 4)})
    assert torch.allclose(seen["t"], torch.tensor([500.0]))  # 0.5 * 1000


class _FakeVae:
    def __init__(self):
        self.offloaded = 0

    def offload(self):
        self.offloaded += 1


def _make_wan_ctx(router, vae):
    return _WanCtx(
        router=router, vae=vae, sampling_settings={}, conditioning=[],
        steps=1, cfg=1.0, sampler="unipc", width=8, height=8, frames=1,
        fps=16.0, latent_channels=16, spatial_downscale=8, device="cuda", dtype=torch.bfloat16,
    )


class TestWanCtxReleaseGpu:
    """`_WanCtx.release_gpu()` is what makes `BaseGeneratorPipe`'s generic
    error-path cleanup fire for this pipe: `ctx.extra` is this dataclass
    directly (not a dict wrapping an engine), so it must define its own
    `release_gpu()` — covers whatever the router's own try/finally around the
    denoise loop doesn't (most notably a VAE decode failure, which runs after
    that finally has already exited)."""

    def test_offloads_both_experts_and_vae(self):
        high, low, vae = _FakeDiT("high"), _FakeDiT("low"), _FakeVae()
        router = _ExpertRouter(high, low, boundary=0.875, device="cuda")
        _make_wan_ctx(router, vae).release_gpu()

        assert high.offloaded == 1
        assert low.offloaded == 1
        assert vae.offloaded == 1

    def test_single_expert_wan_has_no_low_to_offload(self):
        high, vae = _FakeDiT("high"), _FakeVae()
        router = _ExpertRouter(high, None, boundary=0.875, device="cuda")
        _make_wan_ctx(router, vae).release_gpu()

        assert high.offloaded == 1
        assert vae.offloaded == 1

    def test_never_raises_when_an_offload_fails(self):
        class _RaisingDit(_FakeDiT):
            def offload(self):
                raise RuntimeError("cuda error")

        high, low, vae = _RaisingDit("high"), _FakeDiT("low"), _FakeVae()
        router = _ExpertRouter(high, low, boundary=0.875, device="cuda")
        _make_wan_ctx(router, vae).release_gpu()  # must not raise

        assert low.offloaded == 1
        assert vae.offloaded == 1


# -- generator flow --------------------------------------------------------

@dataclass
class _FakeSpec:
    variant: str = "wan_t2v_14b"
    sampling_settings: dict = field(default_factory=lambda: {"guidance": "cfg", "shift": 8.0, "expert_boundary": 0.875})
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16, "format": "wan21"})


def _bundle(dual=True, in_dim=16):
    vae = SimpleNamespace(
        compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(decode=lambda z: torch.zeros(1, 3, z.shape[2], z.shape[3] * 8, z.shape[4] * 8)),
    )
    return SimpleNamespace(
        high_dit=SimpleNamespace(compute_dtype=torch.float32, spec=_FakeSpec(),
                                 module=SimpleNamespace(patch_size=(1, 2, 2), in_dim=in_dim)),
        low_dit=SimpleNamespace() if dual else None,
        vae=vae, spec=_FakeSpec(), is_dual_expert=dual,
    )


def _pipe(**over):
    cfg = GeneratorWanTxt2VidPipe.get_default_config()
    cfg.update(over)
    return GeneratorWanTxt2VidPipe(config=cfg)


def _pipe_input(quantity=1, seeds=(1,), in_dim=16):
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)},
                            n_embeds={"context": torch.zeros(1, 4, 8)}) for _ in range(quantity)]
    return PipeInput(input={"model": _bundle(in_dim=in_dim), "conditioning": cond, "seed": list(seeds)})


def test_i2v_model_in_txt2vid_mode_raises_clear_error():
    """Loading an i2v Wan checkpoint (in_dim=36) into the txt2vid pipeline should
    fail fast with a friendly error instead of a cryptic channel-mismatch crash."""
    import pytest
    with pytest.raises(ValueError, match="i2v"):
        _pipe().build_context(_pipe_input(in_dim=36))


def test_expert_boundary_override_applied_to_router():
    ctx = _pipe(expert_boundary="0.6").build_context(_pipe_input())
    assert ctx.extra.router.boundary == 0.6


def test_expert_boundary_defaults_to_spec():
    ctx = _pipe().build_context(_pipe_input())
    assert ctx.extra.router.boundary == 0.875  # _FakeSpec expert_boundary


def test_build_context_snaps_resolution_and_frames():
    # 1000x540 -> 16px grid (992x544); frames 100 -> nearest 1+4k (101).
    ctx = _pipe(resolution="1000x540", frames=100).build_context(_pipe_input())
    assert (ctx.extra.width, ctx.extra.height) == (992, 544)
    assert ctx.extra.frames == 101


def test_metadata():
    assert GeneratorWanTxt2VidPipe.name == "generator"
    assert GeneratorWanTxt2VidPipe.outputs()[0].io_type == IOType.VIDEO
    inputs = {i.name: i.io_type for i in GeneratorWanTxt2VidPipe.inputs()}
    assert inputs["conditioning"] == IOType.CONDITIONING


def test_sampler_choices():
    spec = next(s for s in GeneratorWanTxt2VidPipe.configuration() if s.name == "sampler")
    assert set(spec.choices) == {
        "euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm",
    }


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise")
def test_latent_shape_is_5d_temporal(mock_denoise):
    captured = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured["shape"] = tuple(latents.shape)
        return latents

    mock_denoise.side_effect = fake_denoise
    # frames 81 -> t_lat = (81-1)//4 + 1 = 21; 832x480 /8 = 104x60
    _pipe(resolution="832x480", frames=81).process(_pipe_input(), lambda o: None)
    assert captured["shape"] == (1, 16, 21, 60, 104)


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_emits_gallery_with_videos():
    emitted = []
    result = _pipe(quantity=2).process(_pipe_input(quantity=2, seeds=(5, 6)), lambda o: emitted.append(o))
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1
    assert len(gallery[0].videos) == 2
    assert len(result.output["video"]) == 2


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_emitted_videos_carry_live_resolution():
    """This generator used to emit VideoGenerationOutput with
    resolution=None -- the live workbench/gallery message had no dimensions
    until the file was later re-fetched from the DB. build_context() now
    stashes the (post-snap) resolution on self so emit_results() can stamp
    it onto every video it emits, matching what image pipes already do."""
    emitted = []
    _pipe(resolution="1000x540", quantity=2).process(_pipe_input(quantity=2, seeds=(5, 6)), lambda o: emitted.append(o))
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
    assert [v.resolution for v in gallery.videos] == [(992, 544), (992, 544)]


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_true_cfg_uncond_passed_through():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(cond=cond, uncond=uncond) or latents
        _pipe().process(_pipe_input(), lambda o: None)
    assert "context" in captured["cond"]
    assert captured["uncond"] is not None  # negative encoded for true CFG


# -- NAG config flow ---------------------------------------------------------

@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_denoise_receives_the_managers_is_cancelled_probe():
    captured = {}
    probe = lambda: False
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(device="cpu").process(_pipe_input(), lambda o: None, is_cancelled=probe)
    assert captured["is_cancelled"] is probe


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_nag_default_off_does_not_touch_cond():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(cond=cond) or latents
        _pipe(device="cpu").process(_pipe_input(), lambda o: None)  # nag_scale defaults to 1.0
    assert "nag_context" not in captured["cond"]
    assert "nag" not in captured["cond"]


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_nag_scale_above_one_attaches_negative_context_and_params():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(cond=cond, uncond=uncond) or latents
        _pipe(device="cpu", nag_scale=1.5, nag_tau=2.0, nag_alpha=0.25).process(_pipe_input(), lambda o: None)
    assert torch.equal(captured["cond"]["nag_context"], captured["uncond"]["context"])
    assert captured["cond"]["nag"] == {"scale": 1.5, "tau": 2.0, "alpha": 0.25}
    # the original cond keys must still be present (additive, not replaced)
    assert "context" in captured["cond"]


def test_nag_scale_above_one_without_negative_conditioning_is_noop():
    """No n_embeds (no negative prompt encoded) -> NAG must not attach even
    when nag_scale > 1.0 (nothing to guide against)."""
    captured = {}
    cond_no_neg = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)]
    pipe_input = PipeInput(input={"model": _bundle(), "conditioning": cond_no_neg, "seed": [1]})
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path), \
         patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(cond=cond) or latents
        _pipe(device="cpu", nag_scale=1.5).process(pipe_input, lambda o: None)
    assert "nag_context" not in captured["cond"]


# -- riflex: router construction ---------------------------------------------

def test_riflex_default_off_router_riflex_is_none():
    ctx = _pipe().build_context(_pipe_input())
    assert ctx.extra.router.riflex is None


def test_riflex_enabled_builds_router_riflex_dict():
    ctx = _pipe(riflex=True, riflex_trained_frames=12).build_context(_pipe_input())
    assert ctx.extra.router.riflex == {"enabled": True, "latent_frames_trained": 12}


def test_riflex_enabled_without_trained_frames_omits_key():
    ctx = _pipe(riflex=True).build_context(_pipe_input())
    assert ctx.extra.router.riflex == {"enabled": True}


# -- expert router: skip_layers / riflex kwarg routing ------------------------

def test_router_skip_layers_absent_by_default():
    seen = {}
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx, **kw: seen.update(kw) or torch.zeros_like(x)
    router = _ExpertRouter(high, None, 0.875, "cpu")
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), {"context": torch.zeros(1, 2, 4)})
    assert "skip_layers" not in seen
    assert "riflex" not in seen


def test_router_pops_skip_layers_from_conditioning_and_forwards_as_kwarg():
    seen = {}
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx, **kw: seen.update(kw) or torch.zeros_like(x)
    router = _ExpertRouter(high, None, 0.875, "cpu")
    conditioning = {"context": torch.zeros(1, 2, 4), "skip_layers": {0, 2}}
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), conditioning)
    assert seen["skip_layers"] == {0, 2}


def test_router_empty_skip_layers_set_is_not_forwarded():
    # SkipLayerGuidance never calls the degraded pass with an empty set (it
    # short-circuits before that), but the router itself must also treat an
    # empty/falsy set as "nothing to skip" defensively.
    seen = {}
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx, **kw: seen.update(kw) or torch.zeros_like(x)
    router = _ExpertRouter(high, None, 0.875, "cpu")
    conditioning = {"context": torch.zeros(1, 2, 4), "skip_layers": set()}
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), conditioning)
    assert "skip_layers" not in seen


def test_router_skip_layers_does_not_leak_into_persistent_conditioning_dict():
    # P4 (REFUTED as a functional bug, verified here): the router only ever
    # .get()s skip_layers/step_cache off `conditioning`; it must never mutate
    # the caller's dict, so a SECOND call reusing the SAME persistent
    # cond/uncond dict (as denoise()'s real call pattern does across steps)
    # never sees a stale key from a PRIOR call that happened to carry one.
    seen_calls = []
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx, **kw: seen_calls.append(dict(kw)) or torch.zeros_like(x)
    router = _ExpertRouter(high, None, 0.875, "cpu")

    persistent_cond = {"context": torch.zeros(1, 2, 4)}
    degraded_cond = {**persistent_cond, "skip_layers": {0, 2}}  # SLG's own fresh spread
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), degraded_cond)
    assert "skip_layers" not in persistent_cond  # the spread never touched the original

    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), persistent_cond)
    assert "skip_layers" not in seen_calls[1]  # second (normal) call sees nothing leaked


def test_router_step_cache_does_not_leak_into_persistent_conditioning_dict():
    seen_calls = []
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx, **kw: seen_calls.append(dict(kw)) or torch.zeros_like(x)
    router = _ExpertRouter(high, None, 0.875, "cpu")

    persistent_cond = {"context": torch.zeros(1, 2, 4)}
    cached_cond = {**persistent_cond, "step_cache": object()}  # denoise_loop's own fresh spread
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), cached_cond)
    assert "step_cache" not in persistent_cond

    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), persistent_cond)
    assert "step_cache" not in seen_calls[1]


def test_router_forwards_riflex_kwarg_when_set():
    seen = {}
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx, **kw: seen.update(kw) or torch.zeros_like(x)
    riflex = {"enabled": True, "latent_frames_trained": 8}
    router = _ExpertRouter(high, None, 0.875, "cpu", riflex=riflex)
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), {"context": torch.zeros(1, 2, 4)})
    assert seen["riflex"] == riflex


# -- APG / SLG: sampling_settings merge --------------------------------------

def test_apg_slg_defaults_are_omitted_not_forced_onto_sampling_settings():
    # P5 fix: an unset pipe config knob must NOT inject a "default" value into
    # sampling_settings -- it must be omitted entirely, so it never overrides
    # a value the ModelSpec itself might carry (see next test).
    ctx = _pipe().build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    for key in ("apg_eta", "apg_norm_threshold", "apg_momentum",
                "slg_scale", "slg_layers", "slg_sigma_start", "slg_sigma_end"):
        assert key not in ss, f"{key} should be absent when unset in pipe config"
    # base spec keys must survive the merge untouched.
    assert ss["guidance"] == "cfg"
    assert ss["shift"] == 8.0


def test_apg_slg_unset_config_lets_modelspec_sampling_settings_survive():
    # THE P5 regression: a ModelSpec that ships its own non-default APG/SLG
    # values must NOT be clobbered by the pipe's own (unset) config.
    bundle = _bundle()
    bundle.high_dit.spec.sampling_settings = {
        "guidance": "cfg", "shift": 8.0, "expert_boundary": 0.875,
        "apg_eta": 0.4, "slg_scale": 2.0,
    }
    bundle.spec.sampling_settings = bundle.high_dit.spec.sampling_settings
    pipe_input = PipeInput(input={
        "model": bundle,
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)},
                                         n_embeds={"context": torch.zeros(1, 4, 8)})],
        "seed": [1],
    })
    ctx = _pipe().build_context(pipe_input)
    ss = ctx.extra.sampling_settings
    assert ss["apg_eta"] == 0.4
    assert ss["slg_scale"] == 2.0


def test_apg_slg_config_overrides_thread_into_sampling_settings():
    ctx = _pipe(
        apg_eta=0.3, apg_norm_threshold=0.5, apg_momentum=-0.4,
        slg_scale=1.5, slg_layers="0,2,5", slg_sigma_start=0.9, slg_sigma_end=0.1,
    ).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["apg_eta"] == 0.3
    assert ss["apg_norm_threshold"] == 0.5
    assert ss["apg_momentum"] == -0.4
    assert ss["slg_scale"] == 1.5
    assert ss["slg_layers"] == {0, 2, 5}
    assert ss["slg_sigma_start"] == 0.9
    assert ss["slg_sigma_end"] == 0.1


def test_schedule_settings_default_off_is_omitted_not_forced():
    ctx = _pipe().build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss.get("schedule") is None
    assert ss.get("schedule_options") is None
    for key in ("detail_strength", "detail_start", "detail_end"):
        assert key not in ss, f"{key} should be absent when unset in pipe config"


def test_schedule_settings_config_overrides_thread_into_sampling_settings():
    ctx = _pipe(
        schedule="beta", schedule_options={"alpha": 0.4, "beta": 0.9},
        detail_strength=0.2, detail_start=0.15, detail_end=0.85,
    ).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["schedule"] == "beta"
    assert ss["schedule_options"] == {"alpha": 0.4, "beta": 0.9}
    assert ss["detail_strength"] == 0.2
    assert ss["detail_start"] == 0.15
    assert ss["detail_end"] == 0.85


# -- FreeInit ------------------------------------------------------------------

class _FakeProgress:
    def __init__(self):
        self.calls = []

    def step(self, current, total, **kw):
        self.calls.append((current, total))

    def preview(self, *a, **kw):
        pass


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_freeinit_default_off_is_exactly_one_denoise_call():
    calls = {"n": 0}
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        def fake_denoise(mf, latents, cond, uncond, **kw):
            calls["n"] += 1
            return latents
        md.side_effect = fake_denoise
        _pipe().process(_pipe_input(), lambda o: None)
    assert calls["n"] == 1


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_freeinit_default_off_seed_noise_unmodified():
    # iterations=0 must pass the ORIGINAL seed_noise straight through, never
    # routed through freeinit_blend.
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(seed_noise=k["seed_noise"]) or latents
        _pipe(device="cpu").process(_pipe_input(), lambda o: None)

    # Recompute the expected initial noise the same way generate_one does.
    gen = torch.Generator(device="cpu").manual_seed(1)
    ctx = _pipe(device="cpu").build_context(_pipe_input())
    shape = (1, ctx.extra.latent_channels, (ctx.extra.frames - 1) // 4 + 1,
             ctx.extra.height // ctx.extra.spatial_downscale, ctx.extra.width // ctx.extra.spatial_downscale)
    expected_noise = torch.randn(shape, generator=gen, device="cpu", dtype=ctx.extra.dtype)
    assert torch.equal(captured["seed_noise"], expected_noise)


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_freeinit_iterations_two_makes_three_denoise_calls_with_distinct_inits():
    seed_noises = []
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        def fake_denoise(mf, latents, cond, uncond, **kw):
            seed_noises.append(kw["seed_noise"].clone())
            # Return something that varies per call so the blend isn't degenerate.
            return torch.randn_like(latents)
        md.side_effect = fake_denoise
        _pipe(device="cpu", freeinit_iterations=2, resolution="64x64", frames=5).process(
            _pipe_input(), lambda o: None)

    assert len(seed_noises) == 3
    assert not torch.equal(seed_noises[0], seed_noises[1])
    assert not torch.equal(seed_noises[1], seed_noises[2])
    assert all(torch.isfinite(sn).all() for sn in seed_noises)


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_freeinit_deterministic_across_runs_same_seed():
    def run():
        seed_noises = []
        with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
            def fake_denoise(mf, latents, cond, uncond, **kw):
                seed_noises.append(kw["seed_noise"].clone())
                # Deterministic "clean latent" stand-in independent of RNG state,
                # so only the FreeInit generator draws drive divergence.
                return torch.full_like(latents, 0.3)
            md.side_effect = fake_denoise
            _pipe(device="cpu", freeinit_iterations=2, resolution="64x64", frames=5).process(
                _pipe_input(), lambda o: None)
        return seed_noises

    run1 = run()
    run2 = run()
    assert len(run1) == len(run2) == 3
    for a, b in zip(run1, run2):
        assert torch.equal(a, b)


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_freeinit_progress_scales_total_across_passes():
    pipe = _pipe(device="cpu", freeinit_iterations=2, preview=False, resolution="64x64", frames=5)
    ctx = pipe.build_context(_pipe_input())
    fake_progress = _FakeProgress()
    captured_hooks = []

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured_hooks.append(kw["hooks"])
        return torch.zeros_like(latents)

    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise", side_effect=fake_denoise):
        pipe.generate_one(ctx, index=0, seed=1, progress=fake_progress)

    total_passes = 3
    steps = ctx.extra.steps
    assert len(captured_hooks) == total_passes
    for it, hooks in enumerate(captured_hooks):
        progress_hook = hooks[0]
        progress_hook.on_step(0, steps, None, 0.5, None)  # first step of this pass
    currents = [c[0] for c in fake_progress.calls]
    totals = [c[1] for c in fake_progress.calls]
    assert currents == [it * steps + 1 for it in range(total_passes)]
    assert totals == [total_passes * steps] * total_passes


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_freeinit_progress_uses_hook_reported_total_not_configured_steps():
    # P7 regression: a sampler (e.g. euler_restart) can report a DIFFERENT
    # actual step count than the pipe's configured `steps` -- progress math
    # must key off the hook's own `total` argument, not c.steps, or the bar
    # desyncs.
    configured_steps = 10
    actual_steps = 15  # e.g. euler_restart's extra restart segments
    pipe = _pipe(device="cpu", freeinit_iterations=1, preview=False,
                 resolution="64x64", frames=5, steps=configured_steps)
    ctx = pipe.build_context(_pipe_input())
    fake_progress = _FakeProgress()
    captured_hooks = []

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured_hooks.append(kw["hooks"])
        return torch.zeros_like(latents)

    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise", side_effect=fake_denoise):
        pipe.generate_one(ctx, index=0, seed=1, progress=fake_progress)

    total_passes = 2
    assert len(captured_hooks) == total_passes
    for it, hooks in enumerate(captured_hooks):
        hooks[0].on_step(0, actual_steps, None, 0.5, None)  # first step, reports actual_steps
    currents = [c[0] for c in fake_progress.calls]
    totals = [c[1] for c in fake_progress.calls]
    assert currents == [it * actual_steps + 1 for it in range(total_passes)]
    assert totals == [total_passes * actual_steps] * total_passes


# -- sampler_options / step_cache: denoise() kwarg threading -----------------

@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_sampler_options_step_cache_absent_reach_denoise_as_none():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe().process(_pipe_input(), lambda o: None)
    assert captured["sampler_options"] is None
    assert captured["step_cache_options"] is None


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_sampler_options_step_cache_present_reach_denoise():
    captured = {}
    sampler_opts = {"eta": 0.5}
    step_cache_opts = {"rel_threshold": 0.12, "warmup_steps": 3}
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(sampler_options=sampler_opts, step_cache=step_cache_opts).process(_pipe_input(), lambda o: None)
    assert captured["sampler_options"] == sampler_opts
    assert captured["step_cache_options"] == step_cache_opts


# -- seed determinism: stochastic samplers get a seeded generator (task #40) -

@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_stochastic_sampler_populates_generator_in_sampler_options():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(sampler="euler_sde").process(_pipe_input(), lambda o: None)
    assert isinstance(captured["sampler_options"]["generator"], torch.Generator)


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_deterministic_sampler_leaves_sampler_options_none():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(sampler="unipc").process(_pipe_input(), lambda o: None)
    assert captured["sampler_options"] is None


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_stochastic_sampler_explicit_generator_preserved():
    captured = {}
    explicit_gen = torch.Generator().manual_seed(4321)
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(sampler="euler_sde", sampler_options={"eta": 0.5, "generator": explicit_gen}) \
            .process(_pipe_input(), lambda o: None)
    assert captured["sampler_options"]["generator"] is explicit_gen


@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_stochastic_sampler_same_seed_produces_same_generator_state():
    # Sanity check that the SAME generator object (not two independently-seeded
    # ones) is threaded through -- draw a value from it inside the fake denoise
    # and confirm two same-seed runs produce identical draws.
    draws = []

    def fake_denoise(mf, latents, cond, uncond, **k):
        gen = k["sampler_options"]["generator"]
        draws.append(torch.randn(4, generator=gen))
        return latents

    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise", side_effect=fake_denoise):
        _pipe(sampler="euler_sde", device="cpu").process(_pipe_input(quantity=1, seeds=(42,)), lambda o: None)
        _pipe(sampler="euler_sde", device="cpu").process(_pipe_input(quantity=1, seeds=(42,)), lambda o: None)
    assert torch.equal(draws[0], draws[1])


# -- expert router: step_cache pop/forward ------------------------------------

def test_router_step_cache_absent_by_default():
    seen = {}
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx, **kw: seen.update(kw) or torch.zeros_like(x)
    router = _ExpertRouter(high, None, 0.875, "cpu")
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), {"context": torch.zeros(1, 2, 4)})
    assert "step_cache" not in seen


def test_router_pops_step_cache_from_conditioning_and_forwards_as_kwarg():
    seen = {}
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx, **kw: seen.update(kw) or torch.zeros_like(x)
    router = _ExpertRouter(high, None, 0.875, "cpu")
    sentinel_cache = object()  # stand-in for a FirstBlockCache instance
    conditioning = {"context": torch.zeros(1, 2, 4), "step_cache": sentinel_cache}
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), conditioning)
    assert seen["step_cache"] is sentinel_cache


def test_router_none_step_cache_is_not_forwarded():
    # A conditioning dict that carries a "step_cache" key explicitly set to
    # None (defensive: no real caller does this today) must still not reach
    # the module -- never pass step_cache=None.
    seen = {}
    high = _FakeDiT("high")
    high.module = lambda x, t, ctx, **kw: seen.update(kw) or torch.zeros_like(x)
    router = _ExpertRouter(high, None, 0.875, "cpu")
    conditioning = {"context": torch.zeros(1, 2, 4), "step_cache": None}
    router(torch.zeros(1, 16, 1, 2, 2), torch.tensor([0.5]), conditioning)
    assert "step_cache" not in seen


# -- _decode_video: temporal-chunked decode routing ---------------------------

def _tiny_wan_vae() -> AutoEncoderCausal3D:
    module = AutoEncoderCausal3D.from_config({}, disable_weight_init)
    module.eval()
    with torch.no_grad():
        for p in module.parameters():
            if p.is_floating_point():
                p.normal_(std=0.02)
    return module


class _FakeVaeWrapper:
    def __init__(self, module):
        self.module = module
        self.compute_dtype = torch.float32
        self.moved = []
        self.offloaded = 0

    def move_to(self, d):
        self.moved.append(d)

    def offload(self):
        self.offloaded += 1


def test_decode_video_small_clip_no_vram_query_uses_single_decode_call():
    # device="cpu" -> free_vram_gb(...) is None -> causal3d_chunk_frames must
    # return None -> the exact single .decode() call, unchanged from before
    # chunking existed.
    vae_module = _tiny_wan_vae()
    calls = {"decode": 0}
    orig_decode = vae_module.decode

    def counting_decode(z, **kw):
        calls["decode"] += 1
        return orig_decode(z, **kw)
    vae_module.decode = counting_decode

    c = SimpleNamespace(vae=_FakeVaeWrapper(vae_module), device="cpu", latent_channels=16)
    latent = torch.randn(1, 16, 9, 4, 4)
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.chunked_decode_causal3d") as mock_chunked:
        out = _decode_video(c, latent)
    assert calls["decode"] == 1
    mock_chunked.assert_not_called()
    assert out.dtype == np.uint8
    assert c.vae.offloaded == 1


def test_decode_video_forced_chunking_matches_single_decode_output():
    # Force causal3d_chunk_frames to report a chunk size smaller than the clip,
    # and verify chunked_decode_causal3d (a REAL chunked decode against a tiny
    # real VAE, not a mock) reproduces the exact single-decode output --
    # chunking must be output-transparent.
    torch.manual_seed(0)
    vae_module = _tiny_wan_vae()
    latent = torch.randn(1, 16, 9, 4, 4)

    c1 = SimpleNamespace(vae=_FakeVaeWrapper(vae_module), device="cpu", latent_channels=16)
    baseline = _decode_video(c1, latent.clone())

    c2 = SimpleNamespace(vae=_FakeVaeWrapper(vae_module), device="cpu", latent_channels=16)
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.causal3d_chunk_frames", return_value=3):
        chunked_out = _decode_video(c2, latent.clone())

    assert np.array_equal(baseline, chunked_out)


def test_decode_video_chunked_path_receives_denormalized_latent():
    # The chunked path must receive the SAME denormalized z the single-call
    # path would have (mean/std applied BEFORE the chunk/no-chunk branch).
    vae_module = _tiny_wan_vae()
    seen = {}

    def fake_chunked(vae, z, chunk_latent_frames, **kw):
        seen["z"] = z.clone()
        seen["chunk_latent_frames"] = chunk_latent_frames
        return vae.decode(z)

    latent = torch.randn(1, 16, 5, 4, 4)
    c = SimpleNamespace(vae=_FakeVaeWrapper(vae_module), device="cpu", latent_channels=16)
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.causal3d_chunk_frames", return_value=2), \
         patch("src.pipelines.pipes.generator.txt2vid_wan22.main.chunked_decode_causal3d", side_effect=fake_chunked):
        _decode_video(c, latent.clone())

    mean = torch.tensor(LATENTS_MEAN).view(1, -1, 1, 1, 1)
    std = torch.tensor(LATENTS_STD).view(1, -1, 1, 1, 1)
    expected_z = latent * std + mean
    assert torch.allclose(seen["z"], expected_z, atol=1e-6)
    assert seen["chunk_latent_frames"] == 2


def test_decode_video_raises_on_nan_pixels_instead_of_silent_black_frame():
    # A NaN pixel must fail loudly (DecodeNumericsError), not silently clamp
    # to a black 0 via pixels_3thw_to_uint8_frames's uint8 cast.
    vae_module = _tiny_wan_vae()
    c = SimpleNamespace(vae=_FakeVaeWrapper(vae_module), device="cpu", latent_channels=16)
    latent = torch.randn(1, 16, 5, 4, 4)
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.causal3d_chunk_frames", return_value=None), \
         patch.object(vae_module, "decode", return_value=torch.full((1, 3, 5, 4, 4), float("nan"))):
        with pytest.raises(DecodeNumericsError):
            _decode_video(c, latent)


def test_decode_video_finite_pixels_pass_through_unaffected():
    vae_module = _tiny_wan_vae()
    c = SimpleNamespace(vae=_FakeVaeWrapper(vae_module), device="cpu", latent_channels=16)
    latent = torch.randn(1, 16, 5, 4, 4)
    out = _decode_video(c, latent)
    assert out.dtype == np.uint8


def test_decode_video_wan22_5b_48ch_skips_normalization_in_both_paths():
    # 48ch (Wan22 5B) latent format is a no-op denorm by design -- verify the
    # chunked path receives the RAW latent too, not incorrectly normalized.
    # (The tiny fixture VAE is a 16ch-input toy, so the fake just records z
    # rather than round-tripping it through a real 48ch-incompatible decode.)
    vae_module = _tiny_wan_vae()
    seen = {}

    def fake_chunked(vae, z, chunk_latent_frames, **kw):
        seen["z"] = z.clone()
        return torch.zeros(1, 3, z.shape[2], z.shape[3] * 8, z.shape[4] * 8)

    latent = torch.randn(1, 48, 5, 4, 4)
    c = SimpleNamespace(vae=_FakeVaeWrapper(vae_module), device="cpu", latent_channels=48)
    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.causal3d_chunk_frames", return_value=2), \
         patch("src.pipelines.pipes.generator.txt2vid_wan22.main.chunked_decode_causal3d", side_effect=fake_chunked):
        _decode_video(c, latent.clone())

    assert torch.equal(seen["z"], latent)


# -- activation-aware partial-residency expert placement --------------
#
# A Wan 2.2 14B expert is 14-27GB; the 720p/5s activation working set is tens of
# GB (sampling_headroom_gb scales with T*H*W), so the two don't co-fit a 32GB
# card. The router must stream the expert (partial residency) when a full move_to
# won't leave room -- the fix for the OOM that ComfyUI's block-swap avoids. These
# drive the decision on CPU by mocking the free-VRAM read + residency manager;
# sampling_headroom_gb runs for real.

import src.pipelines.pipes.generator.txt2vid_wan22.main as _wan_mod


class _PlaceDiT:
    def __init__(self, tag, est_gb):
        self.tag = tag
        self.estimated_vram_gb = est_gb
        self.moved = []
        self.streamed = []
        self.offloaded = 0
        self.module = lambda x, t, ctx: torch.zeros_like(x)

    def move_to(self, d):
        self.moved.append(d)

    def stream_to(self, d, budget):
        self.streamed.append((d, budget))

    def offload(self):
        self.offloaded += 1


class _FakeResidency:
    def __init__(self):
        self.ensure_free_calls = []
        self.offload_all_calls = []

    def ensure_free(self, device, need, free, *, exclude=()):
        self.ensure_free_calls.append((need, free, tuple(exclude)))
        return []

    def offload_all(self, device, *, exclude=()):
        self.offload_all_calls.append(tuple(exclude))
        return []


def _patch_placement(monkeypatch, free_gb):
    res = _FakeResidency()
    monkeypatch.setattr(_wan_mod, "free_vram_gb", lambda device: free_gb)
    monkeypatch.setattr(_wan_mod, "get_residency_manager", lambda: res)
    return res


_HIRES_5S = (1, 16, 21, 160, 90)   # 720x1280 @ 81 frames -> ~29.5GB activation headroom


def test_router_streams_expert_when_it_wont_cofit_activations(monkeypatch):
    _patch_placement(monkeypatch, free_gb=30.0)
    high = _PlaceDiT("high", est_gb=27.0)
    router = _ExpertRouter(high, None, boundary=0.875, device="cuda:0", latents_shape=_HIRES_5S)
    router(torch.zeros(_HIRES_5S), torch.tensor([0.95]), {"context": torch.zeros(1, 2, 4)})
    # budget = 30 - ~29.5 = ~0.5GB << 27GB expert -> streamed, never full-moved.
    assert high.streamed and high.streamed[0][0] == "cuda:0"
    assert high.streamed[0][1] < 27.0
    assert high.moved == []


def test_router_full_moves_expert_when_it_fits(monkeypatch):
    _patch_placement(monkeypatch, free_gb=60.0)     # huge card, tiny clip
    high = _PlaceDiT("high", est_gb=14.0)
    router = _ExpertRouter(high, None, boundary=0.875, device="cuda:0",
                           latents_shape=(1, 16, 5, 30, 30))
    router(torch.zeros(1, 16, 5, 30, 30), torch.tensor([0.95]), {"context": torch.zeros(1, 2, 4)})
    assert high.moved == ["cuda:0"]
    assert high.streamed == []


def test_router_without_latents_shape_is_backward_compatible_full_move(monkeypatch):
    # latents_shape unset (single-DiT / callers not yet wired) -> pre-existing full
    # move even on cuda, and the placement machinery is never consulted.
    monkeypatch.setattr(_wan_mod, "free_vram_gb",
                        lambda device: (_ for _ in ()).throw(AssertionError("must not query VRAM")))
    high = _PlaceDiT("high", est_gb=27.0)
    router = _ExpertRouter(high, None, boundary=0.875, device="cuda:0")
    router(torch.zeros(_HIRES_5S), torch.tensor([0.95]), {"context": torch.zeros(1, 2, 4)})
    assert high.moved == ["cuda:0"]
    assert high.streamed == []


def test_router_evicts_foreign_residents_but_not_own_experts(monkeypatch):
    res = _patch_placement(monkeypatch, free_gb=30.0)
    high, low = _PlaceDiT("high", 27.0), _PlaceDiT("low", 27.0)
    router = _ExpertRouter(high, low, boundary=0.875, device="cuda:0", latents_shape=_HIRES_5S)
    router(torch.zeros(_HIRES_5S), torch.tensor([0.95]), {"context": torch.zeros(1, 2, 4)})
    # ensure_free was asked to make room, excluding BOTH our experts.
    assert res.ensure_free_calls
    _need, _free, exclude = res.ensure_free_calls[0]
    assert high in exclude and low in exclude


def test_router_offloads_previous_expert_before_streaming_next(monkeypatch):
    _patch_placement(monkeypatch, free_gb=30.0)
    high, low = _PlaceDiT("high", 27.0), _PlaceDiT("low", 27.0)
    router = _ExpertRouter(high, low, boundary=0.875, device="cuda:0", latents_shape=_HIRES_5S)
    cond = {"context": torch.zeros(1, 2, 4)}
    router(torch.zeros(_HIRES_5S), torch.tensor([0.95]), cond)   # high (streamed)
    router(torch.zeros(_HIRES_5S), torch.tensor([0.5]), cond)    # transition -> low
    assert high.offloaded == 1          # previous expert released before the next arrives
    assert low.streamed                 # next expert streamed, not full-pinned


def test_router_stream_oom_falls_back_to_zero_budget(monkeypatch):
    _patch_placement(monkeypatch, free_gb=30.0)

    class _OOMOnceDiT(_PlaceDiT):
        def stream_to(self, d, budget):
            self.streamed.append((d, budget))
            if len(self.streamed) == 1:
                raise torch.cuda.OutOfMemoryError("boom")

    high = _OOMOnceDiT("high", est_gb=27.0)
    router = _ExpertRouter(high, None, boundary=0.875, device="cuda:0", latents_shape=_HIRES_5S)
    router(torch.zeros(_HIRES_5S), torch.tensor([0.95]), {"context": torch.zeros(1, 2, 4)})
    assert len(high.streamed) == 2      # first OOM'd, retried
    assert high.streamed[1][1] == 0.0   # zero-budget backstop (every leaf streamed)
    assert high.offloaded == 1          # offloaded between the two attempts


# -- streamed expert must be torn down even on a mid-sampling error ----
#
# The active expert may be placed with partial residency (weights
# pinned in host RAM). If an OOM / error mid-sampling skipped the offload, the
# streamer would stay ACTIVE and its pinned host pool would survive in the
# RAM-cached expert -- the krea2 incident shape, and on a repeated-failed-run
# debug loop it accumulates. generate_one's finally guarantees teardown.

@patch("src.pipelines.pipes.generator.txt2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_generate_one_offloads_active_expert_on_denoise_error():
    pipe = _pipe(device="cpu", preview=False, resolution="64x64", frames=5)
    ctx = pipe.build_context(_pipe_input())
    active = _FakeDiT("active")
    ctx.extra.router.active = active                     # an expert already placed on-device

    def boom(*a, **k):
        raise torch.cuda.OutOfMemoryError("mid-sampling OOM")

    with patch("src.pipelines.pipes.generator.txt2vid_wan22.main.denoise", side_effect=boom):
        with pytest.raises(torch.cuda.OutOfMemoryError):
            pipe.generate_one(ctx, index=0, seed=1, progress=_FakeProgress())

    assert active.offloaded == 1                         # torn down (unpinned) despite the error


# -- torch.compile hook: a resident expert is offered to maybe_compile_dit --
#
# _ExpertRouter has no NativeGenerator instance to call the image path's
# private ``_maybe_compile()`` on, so it calls the same gated, reversible
# ``maybe_compile_dit`` directly the moment an expert lands FULLY resident.

import torch.nn as _nn


class _CompilableModule(_nn.Module):
    """Real homogeneous-block ``nn.Module`` so ``compile_gate`` reaches "ok"
    instead of tripping on ``_FakeDiT``/``_PlaceDiT``'s bare-callable
    ``.module``, while still matching the router's ``dit.module(x, t, ctx)``
    call shape."""

    def __init__(self, n: int = 2, dim: int = 4) -> None:
        super().__init__()
        self.blocks = _nn.ModuleList(_nn.Linear(dim, dim) for _ in range(n))

    def forward(self, x, t, ctx):
        return torch.zeros_like(x)


class _CompilablePlaceDiT(_PlaceDiT):
    """Same placement behavior as ``_PlaceDiT``, but ``.module`` is a REAL
    ``nn.Module`` (see ``_CompilableModule``)."""

    def __init__(self, tag, est_gb):
        super().__init__(tag, est_gb)
        self.module = _CompilableModule()
        self.quant_format = None


def _enable_compile(monkeypatch):
    from src.platform.runtime.native.optimizations import compile as tc

    monkeypatch.setenv(tc.NATIVE_TORCH_COMPILE_ENV, "on")
    return tc


def test_router_compiles_a_resident_expert_when_enabled(monkeypatch):
    tc = _enable_compile(monkeypatch)
    _patch_placement(monkeypatch, free_gb=60.0)
    high = _CompilablePlaceDiT("high", est_gb=14.0)
    router = _ExpertRouter(high, None, boundary=0.875, device="cuda:0",
                           latents_shape=(1, 16, 5, 30, 30))
    router(torch.zeros(1, 16, 5, 30, 30), torch.tensor([0.95]), {"context": torch.zeros(1, 2, 4)})
    assert high.moved == ["cuda:0"]
    assert high._compiled is not None and high._compiled.active
    assert all(tc.is_compiled(b) for b in high.module.blocks)


def test_router_never_compiles_a_streamed_expert(monkeypatch):
    _enable_compile(monkeypatch)
    _patch_placement(monkeypatch, free_gb=30.0)
    high = _CompilablePlaceDiT("high", est_gb=27.0)
    router = _ExpertRouter(high, None, boundary=0.875, device="cuda:0", latents_shape=_HIRES_5S)
    router(torch.zeros(_HIRES_5S), torch.tensor([0.95]), {"context": torch.zeros(1, 2, 4)})
    assert high.streamed  # confirms this hit the partial-residency branch
    assert getattr(high, "_compiled", None) is None


def test_router_compile_disabled_by_default_leaves_expert_untouched(monkeypatch):
    from src.platform.runtime.native.optimizations import compile as tc

    monkeypatch.delenv(tc.NATIVE_TORCH_COMPILE_ENV, raising=False)
    _patch_placement(monkeypatch, free_gb=60.0)
    high = _CompilablePlaceDiT("high", est_gb=14.0)
    router = _ExpertRouter(high, None, boundary=0.875, device="cuda:0",
                           latents_shape=(1, 16, 5, 30, 30))
    router(torch.zeros(1, 16, 5, 30, 30), torch.tensor([0.95]), {"context": torch.zeros(1, 2, 4)})
    assert high.moved == ["cuda:0"]
    assert getattr(high, "_compiled", None) is None


# -- expert budget must credit resident runtime LoRA deltas, and the resident
# placement must go through the shared OOM-guarded helper -----------------
#
# ``estimated_vram_gb`` is only the base checkpoint's file size -- a resident
# runtime LoRA delta (unbaked, quantized-storage path) is genuinely resident
# VRAM invisible to that number. And a raw ``dit.move_to`` has no OOM ladder,
# unlike ``_move_resident`` (mirrored from the LTX shared placement helper).

def test_router_weights_budget_credits_resident_lora_delta(monkeypatch):
    res = _patch_placement(monkeypatch, free_gb=30.0)
    high = _PlaceDiT("high", est_gb=20.0)
    high.module = _nn.Sequential(_nn.Linear(64, 64))
    delta = SimpleNamespace(down=torch.zeros(64, 64), up=torch.zeros(64, 64))
    high.module[0].lora_deltas = [delta]
    expected_delta_gb = _dit_lora_delta_gb(high)
    assert expected_delta_gb > 0.0

    router = _ExpertRouter(high, None, boundary=0.875, device="cuda:0", latents_shape=_HIRES_5S)
    router._weights_budget_gb(high)

    assert res.ensure_free_calls
    need, _free, _exclude = res.ensure_free_calls[0]
    assert need == pytest.approx(20.0 + expected_delta_gb)


def test_router_resident_placement_routes_through_guarded_move(monkeypatch):
    _patch_placement(monkeypatch, free_gb=60.0)   # huge card, tiny clip -> fits resident
    high = _PlaceDiT("high", est_gb=14.0)
    calls = []

    def _fake_move_resident(dit, device, own_models):
        calls.append((dit, device, tuple(own_models)))
        return "resident"

    monkeypatch.setattr(_wan_mod, "_move_resident", _fake_move_resident)
    router = _ExpertRouter(high, None, boundary=0.875, device="cuda:0",
                           latents_shape=(1, 16, 5, 30, 30))
    router(torch.zeros(1, 16, 5, 30, 30), torch.tensor([0.95]), {"context": torch.zeros(1, 2, 4)})

    assert calls == [(high, "cuda:0", (high,))]
    assert high.moved == []   # never called dit.move_to directly -- routed through the guard
