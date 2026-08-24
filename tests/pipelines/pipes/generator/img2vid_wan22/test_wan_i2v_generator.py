"""Tests for generator/img2vid_wan22: concat wiring (36ch), image input, video."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import patch

import torch

from src.pipelines.outputs import GalleryGenerationOutput
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.img2vid_wan22.main import GeneratorWanImg2VidPipe, _Ctx, _ExpertRouter, _I2VForward


# -- i2v forward: prepend concat to the noisy latent ----------------------

def test_i2v_forward_prepends_concat_to_36ch():
    seen = {}

    class _FakeRouter:
        active = None

        def __call__(self, x, sigma, cond):
            seen["x"] = x
            return x[:, :16]  # DiT returns 16ch velocity

    concat = torch.zeros(1, 20, 2, 2, 2)
    fwd = _I2VForward(_FakeRouter(), concat)
    out = fwd(torch.zeros(1, 16, 2, 2, 2), torch.tensor([0.5]), {})
    assert seen["x"].shape[1] == 36   # 16 noise + 20 concat
    assert out.shape[1] == 16


class _FakeDiT:
    def __init__(self):
        self.offloaded = 0
        self.module = lambda x, t, ctx: torch.zeros_like(x)

    def move_to(self, d):
        pass

    def offload(self):
        self.offloaded += 1


class _FakeVae:
    def __init__(self):
        self.offloaded = 0

    def offload(self):
        self.offloaded += 1


def _make_i2v_ctx(router, vae, concat=None):
    fwd = _I2VForward(router, concat if concat is not None else torch.zeros(1, 20, 1, 2, 2))
    return _Ctx(
        forward=fwd, vae=vae, sampling_settings={}, conditioning=[],
        steps=1, cfg=1.0, sampler="unipc", width=8, height=8, frames=1,
        fps=16.0, latent_channels=16, spatial_downscale=8, device="cuda", dtype=torch.bfloat16,
    )


class TestCtxReleaseGpu:
    """`_Ctx.release_gpu()` is what makes `BaseGeneratorPipe`'s generic
    error-path cleanup fire for this pipe: `ctx.extra` is this dataclass
    directly (not a dict wrapping an engine), so it must define its own
    `release_gpu()` — covers whatever the router's own try/finally around the
    denoise loop doesn't (most notably a VAE decode failure, which runs after
    that finally has already exited)."""

    def test_offloads_both_experts_and_vae(self):
        high, low, vae = _FakeDiT(), _FakeDiT(), _FakeVae()
        router = _ExpertRouter(high, low, boundary=0.875, device="cuda")
        _make_i2v_ctx(router, vae).release_gpu()

        assert high.offloaded == 1
        assert low.offloaded == 1
        assert vae.offloaded == 1

    def test_single_expert_wan_has_no_low_to_offload(self):
        high, vae = _FakeDiT(), _FakeVae()
        router = _ExpertRouter(high, None, boundary=0.875, device="cuda")
        _make_i2v_ctx(router, vae).release_gpu()

        assert high.offloaded == 1
        assert vae.offloaded == 1

    def test_never_raises_when_an_offload_fails(self):
        class _RaisingDit(_FakeDiT):
            def offload(self):
                raise RuntimeError("cuda error")

        high, low, vae = _RaisingDit(), _FakeDiT(), _FakeVae()
        router = _ExpertRouter(high, low, boundary=0.875, device="cuda")
        _make_i2v_ctx(router, vae).release_gpu()  # must not raise

        assert low.offloaded == 1
        assert vae.offloaded == 1


# -- generator flow -------------------------------------------------------

@dataclass
class _FakeSpec:
    variant: str = "wan22_i2v_14b"
    sampling_settings: dict = field(default_factory=lambda: {"guidance": "cfg", "expert_boundary": 0.900})
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16, "format": "wan21"})


def _bundle(in_dim=36):
    vae = SimpleNamespace(
        compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(
            encode=lambda px: torch.zeros(1, 16, (px.shape[2] - 1) // 4 + 1, px.shape[3] // 8, px.shape[4] // 8),
            decode=lambda z: torch.zeros(1, 3, z.shape[2], z.shape[3] * 8, z.shape[4] * 8),
        ),
    )
    return SimpleNamespace(
        high_dit=SimpleNamespace(compute_dtype=torch.float32, spec=_FakeSpec(),
                                 module=SimpleNamespace(patch_size=(1, 2, 2), in_dim=in_dim)),
        low_dit=SimpleNamespace(), vae=vae, spec=_FakeSpec(), is_dual_expert=True,
    )


def _pipe(**over):
    cfg = GeneratorWanImg2VidPipe.get_default_config()
    cfg.update(over)
    return GeneratorWanImg2VidPipe(config=cfg)


def _pipe_input(quantity=1, seeds=(1,), in_dim=36):
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)},
                            n_embeds={"context": torch.zeros(1, 4, 8)}) for _ in range(quantity)]
    img = torch.rand(64, 64, 3)  # HWC start frame
    return PipeInput(input={"model": _bundle(in_dim=in_dim), "conditioning": cond, "image": [img], "seed": list(seeds)})


def test_build_context_vae_offload_called_even_when_concat_build_raises():
    # build_context runs OUTSIDE process()'s try (BaseGeneratorPipe calls it
    # before that try opens), so a raise from build_i2v_concat must still be
    # caught by build_context's own try/finally -- otherwise the VAE is left
    # resident with no cleanup path at all.
    offloaded = {"n": 0}
    bundle = _bundle()
    bundle.vae.move_to = lambda d: None
    bundle.vae.offload = lambda: offloaded.__setitem__("n", offloaded["n"] + 1)
    pipe_input = PipeInput(input={
        "model": bundle,
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)],
        "image": [torch.rand(64, 64, 3)],
        "seed": [1],
    })
    with patch("src.pipelines.pipes.generator.img2vid_wan22.main.build_i2v_concat",
               side_effect=RuntimeError("encode blew up")):
        try:
            _pipe().build_context(pipe_input)
        except RuntimeError:
            pass
    assert offloaded["n"] == 1


def test_riflex_default_off_router_riflex_is_none():
    ctx = _pipe().build_context(_pipe_input())
    assert ctx.extra.forward.router.riflex is None


def test_riflex_enabled_builds_router_riflex_dict():
    ctx = _pipe(riflex=True, riflex_trained_frames=12).build_context(_pipe_input())
    assert ctx.extra.forward.router.riflex == {"enabled": True, "latent_frames_trained": 12}


def test_apg_slg_defaults_are_omitted_not_forced_onto_sampling_settings():
    # P5 fix: unset knobs must be OMITTED, not forced to a "default" value,
    # so they never clobber a non-default ModelSpec value.
    ctx = _pipe().build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    for key in ("apg_eta", "apg_norm_threshold", "apg_momentum", "slg_scale", "slg_layers"):
        assert key not in ss
    assert ss["guidance"] == "cfg"  # base spec key survives the merge


def test_apg_slg_config_overrides_thread_into_sampling_settings():
    ctx = _pipe(
        apg_eta=0.3, apg_norm_threshold=0.5, apg_momentum=-0.4,
        slg_scale=1.5, slg_layers="1,3", slg_sigma_start=0.8, slg_sigma_end=0.2,
    ).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["apg_eta"] == 0.3
    assert ss["slg_scale"] == 1.5
    assert ss["slg_layers"] == {1, 3}
    assert ss["slg_sigma_start"] == 0.8
    assert ss["slg_sigma_end"] == 0.2


def test_schedule_settings_config_overrides_thread_into_sampling_settings():
    ctx = _pipe(schedule="exponential", schedule_options={"sigma_min": 0.02},
                detail_strength=-0.1).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["schedule"] == "exponential"
    assert ss["schedule_options"] == {"sigma_min": 0.02}
    assert ss["detail_strength"] == -0.1


@patch("src.pipelines.pipes.generator.img2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_sampler_options_step_cache_absent_reach_denoise_as_none():
    captured = {}
    with patch("src.pipelines.pipes.generator.img2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe().process(_pipe_input(), lambda o: None)
    assert captured["sampler_options"] is None
    assert captured["step_cache_options"] is None


@patch("src.pipelines.pipes.generator.img2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_denoise_receives_the_managers_is_cancelled_probe():
    captured = {}
    probe = lambda: False
    with patch("src.pipelines.pipes.generator.img2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe().process(_pipe_input(), lambda o: None, is_cancelled=probe)
    assert captured["is_cancelled"] is probe


@patch("src.pipelines.pipes.generator.img2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
def test_sampler_options_step_cache_present_reach_denoise():
    captured = {}
    sampler_opts = {"restart_count": 2}
    step_cache_opts = {"rel_threshold": 0.1}
    with patch("src.pipelines.pipes.generator.img2vid_wan22.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(sampler_options=sampler_opts, step_cache=step_cache_opts).process(_pipe_input(), lambda o: None)
    assert captured["sampler_options"] == sampler_opts
    assert captured["step_cache_options"] == step_cache_opts


def test_t2v_model_in_img2vid_mode_raises_clear_error():
    """Loading a t2v Wan checkpoint (in_dim=16, no image conditioning support)
    into the img2vid pipeline should fail fast with a friendly error."""
    import pytest
    with pytest.raises(ValueError, match="t2v"):
        _pipe().build_context(_pipe_input(in_dim=16))


def test_build_context_snaps_resolution_and_frames():
    # 1000x540 -> 16px grid (992x544); frames 100 -> nearest 1+4k (101). Snapping
    # runs before the start frame is encoded into the concat.
    ctx = _pipe(resolution="1000x540", frames=100).build_context(_pipe_input())
    assert (ctx.extra.width, ctx.extra.height) == (992, 544)
    assert ctx.extra.frames == 101


def test_metadata_has_image_input_and_video_output():
    inputs = {i.name: i.io_type for i in GeneratorWanImg2VidPipe.inputs()}
    assert inputs["image"] == IOType.IMAGE
    assert GeneratorWanImg2VidPipe.outputs()[0].io_type == IOType.VIDEO


def test_sampler_choices():
    spec = next(s for s in GeneratorWanImg2VidPipe.configuration() if s.name == "sampler")
    assert set(spec.choices) == {
        "euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm",
    }


def test_missing_image_raises():
    pi = _pipe_input()
    pi.input["image"] = []
    import pytest
    with pytest.raises(ValueError):
        _pipe().build_context(pi)


@patch("src.pipelines.pipes.generator.img2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
@patch("src.pipelines.pipes.generator.img2vid_wan22.main.denoise")
def test_build_context_builds_20ch_concat_and_denoise_gets_16ch(mock_denoise):
    captured = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured["latent_ch"] = latents.shape[1]
        captured["concat_ch"] = model_forward.concat.shape[1]
        return latents

    mock_denoise.side_effect = fake_denoise
    _pipe(resolution="512x256", frames=9).process(_pipe_input(), lambda o: None)
    assert captured["latent_ch"] == 16    # denoise operates on 16ch noise
    assert captured["concat_ch"] == 20    # the i2v concat is 20ch (4 mask + 16 ref)


@patch("src.pipelines.pipes.generator.img2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
@patch("src.pipelines.pipes.generator.img2vid_wan22.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_emits_video_gallery():
    emitted = []
    result = _pipe(quantity=2).process(_pipe_input(quantity=2, seeds=(5, 6)), lambda o: emitted.append(o))
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1 and len(gallery[0].videos) == 2
    assert len(result.output["video"]) == 2


@patch("src.pipelines.pipes.generator.img2vid_wan22.main.encode_frames_to_mp4", lambda frames, path, fps: path)
@patch("src.pipelines.pipes.generator.img2vid_wan22.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_emitted_videos_carry_live_resolution():
    """Unlike image pipes, this generator used to emit
    VideoGenerationOutput.resolution=None -- the live workbench/gallery
    message had no dimensions until the file was later re-fetched from the
    DB. build_context() now stashes the (post-snap) resolution on self so
    emit_results() can stamp it onto every video it emits."""
    emitted = []
    pipe = _pipe(resolution="1000x540", quantity=2)
    pipe.process(_pipe_input(quantity=2, seeds=(5, 6)), lambda o: emitted.append(o))

    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
    assert [v.resolution for v in gallery.videos] == [(992, 544), (992, 544)]
