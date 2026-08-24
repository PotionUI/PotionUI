"""Tests for the generator/txt2vid_ltx pipe: geometry snapping, single-DiT
forward wrapping, decode math, mp4 emission, family guard."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import torch

from src.pipelines.outputs import GalleryGenerationOutput
from src.platform.runtime.native.errors import DecodeNumericsError
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.txt2vid_ltx.main import (
    GeneratorLtxTxt2VidPipe,
    _decode_video,
    _snap_geometry,
    _LTXCtx,
    _SPATIAL_DOWNSCALE,
    _TEMPORAL_DOWNSCALE,
    _LATENT_CHANNELS,
)


# -- geometry snapping -------------------------------------------------------

def test_snap_geometry_resolution_and_frames():
    # 1000x540 -> 32px grid; frames 50 -> nearest 1+8k.
    w, h, f = _snap_geometry(1000, 540, 50)
    assert w % _SPATIAL_DOWNSCALE == 0 and h % _SPATIAL_DOWNSCALE == 0
    assert (f - 1) % _TEMPORAL_DOWNSCALE == 0


def test_snap_geometry_already_valid_is_unchanged():
    w, h, f = _snap_geometry(768, 512, 49)
    assert (w, h, f) == (768, 512, 49)


# -- family guard -------------------------------------------------------------

@dataclass
class _FakeSpec:
    family: str = "ltx"
    variant: str = "ltxav"
    sampling_settings: dict = field(default_factory=lambda: {"prediction": "const", "shift": 2.37, "guidance": "cfg"})


def _bundle(family="ltx", te_cache_key=None):
    vae = SimpleNamespace(
        compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(decode=lambda z: torch.zeros(1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32)),
    )
    dit = SimpleNamespace(compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
                           module=lambda x_list, sigma, ctx, **kw: torch.zeros_like(x_list[0]))
    return SimpleNamespace(dit=dit, vae=vae, spec=_FakeSpec(family=family), projections={},
                           te=SimpleNamespace(module=None), te_cache_key=te_cache_key)


class _FakeModelsService:
    """Records evict_dead_weight(key) calls; returns True (evicted) by default."""

    def __init__(self, evict_result=True, raise_on_evict=False):
        self.evict_calls: list[str] = []
        self._evict_result = evict_result
        self._raise = raise_on_evict

    def evict_dead_weight(self, key: str) -> bool:
        self.evict_calls.append(key)
        if self._raise:
            raise RuntimeError("boom")
        return self._evict_result


def _pipe(**over):
    cfg = GeneratorLtxTxt2VidPipe.get_default_config()
    cfg.update(over)
    return GeneratorLtxTxt2VidPipe(config=cfg)


def _pipe_input(quantity=1, seeds=(1,), family="ltx", bundle=None, models=None):
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)},
                            n_embeds={"context": torch.zeros(1, 4, 8)}) for _ in range(quantity)]
    inp = {"model": bundle or _bundle(family=family), "conditioning": cond, "seed": list(seeds)}
    if models is not None:
        inp["MODELS"] = models
    return PipeInput(input=inp)


def test_non_ltx_model_raises_clear_error():
    import pytest
    with pytest.raises(ValueError, match="LTX"):
        _pipe().build_context(_pipe_input(family="wan"))


def test_metadata():
    assert GeneratorLtxTxt2VidPipe.name == "generator"
    assert GeneratorLtxTxt2VidPipe.outputs()[0].io_type == IOType.VIDEO
    inputs = {i.name: i.io_type for i in GeneratorLtxTxt2VidPipe.inputs()}
    assert inputs["conditioning"] == IOType.CONDITIONING


def test_sampler_choices():
    spec = next(s for s in GeneratorLtxTxt2VidPipe.configuration() if s.name == "sampler")
    assert set(spec.choices) == {
        "euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm",
        "euler_ancestral", "euler_ancestral_cfg_pp", "euler_cfg_pp",
    }


def test_build_context_default_geometry():
    ctx = _pipe().build_context(_pipe_input())
    assert (ctx.extra.width, ctx.extra.height, ctx.extra.frames) == (768, 512, 49)


# -- TE eviction (host RAM release) -------------------------------------------

def test_metadata_includes_models_service_input():
    inputs = {i.name: i for i in GeneratorLtxTxt2VidPipe.inputs()}
    assert inputs["MODELS"].io_type == IOType.SERVICE
    assert inputs["MODELS"].required is False


def test_build_context_evicts_idle_te_when_models_and_cache_key_present():
    models = _FakeModelsService()
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    _pipe().build_context(_pipe_input(bundle=bundle, models=models))
    assert models.evict_calls == ["native/te/gemma3.safetensors"]


def test_build_context_no_te_eviction_without_models_service():
    # No MODELS injected (e.g. an isolated pipe test) -> no AttributeError,
    # no eviction attempted.
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    ctx = _pipe().build_context(_pipe_input(bundle=bundle))
    assert ctx.extra.width == 768  # build_context still completed normally


def test_build_context_no_te_eviction_without_cache_key():
    # Bundle built outside the MODELS cache (te_cache_key=None) -> no-op.
    models = _FakeModelsService()
    bundle = _bundle(te_cache_key=None)
    _pipe().build_context(_pipe_input(bundle=bundle, models=models))
    assert models.evict_calls == []


def test_build_context_te_eviction_survives_raising():
    # A RAM optimisation must never fail the generation over an eviction error.
    models = _FakeModelsService(raise_on_evict=True)
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    ctx = _pipe().build_context(_pipe_input(bundle=bundle, models=models))
    assert ctx.extra.width == 768


# -- latent shape + model_forward wrapping ------------------------------------

@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_latent_shape_is_5d_and_128_channels(mock_denoise):
    captured = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured["shape"] = tuple(latents.shape)
        return latents

    mock_denoise.side_effect = fake_denoise
    # frames 49 -> t_lat = (49-1)//8 + 1 = 7; 768x512 /32 = 24x16
    _pipe(device="cpu", resolution="768x512", frames=49).process(_pipe_input(), lambda o: None)
    assert captured["shape"] == (1, _LATENT_CHANNELS, 7, 16, 24)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_model_forward_wraps_single_element_list_with_frame_rate(mock_denoise):
    seen = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        x = torch.zeros(1, _LATENT_CHANNELS, 7, 16, 24)
        model_forward(x, torch.tensor([0.5]), cond)
        return latents

    mock_denoise.side_effect = fake_denoise

    class _RecordingDit:
        def __call__(self, x_list, sigma, ctx, attention_mask=None, frame_rate=None):
            seen["x_list_len"] = len(x_list)
            seen["frame_rate"] = frame_rate
            seen["attention_mask"] = attention_mask
            return torch.zeros_like(x_list[0])

    bundle = _bundle()
    bundle.dit.module = _RecordingDit()
    pipe_input = PipeInput(input={
        "model": bundle,
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)],
        "seed": [1],
    })
    _pipe(device="cpu", fps=25.0).process(pipe_input, lambda o: None)
    assert seen["x_list_len"] == 1
    assert seen["frame_rate"] == 25.0
    assert seen["attention_mask"] is None


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_true_cfg_uncond_passed_through():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(cond=cond, uncond=uncond) or latents
        _pipe(device="cpu").process(_pipe_input(), lambda o: None)
    assert "context" in captured["cond"]
    assert captured["uncond"] is not None


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_emits_gallery_with_videos():
    emitted = []
    result = _pipe(device="cpu", quantity=2).process(_pipe_input(quantity=2, seeds=(5, 6)), lambda o: emitted.append(o))
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1
    assert len(gallery[0].videos) == 2
    assert len(result.output["video"]) == 2


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_emitted_videos_carry_live_resolution():
    """This generator used to emit VideoGenerationOutput with
    resolution=None -- the live workbench/gallery message had no dimensions
    until the file was later re-fetched from the DB. build_context() now
    stashes the (post-snap) resolution on self so emit_results() can stamp
    it onto every video it emits, matching what image pipes already do."""
    emitted = []
    _pipe(device="cpu", resolution="1000x540", quantity=2).process(_pipe_input(quantity=2, seeds=(5, 6)), lambda o: emitted.append(o))
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)][0]
    # 1000x540 -> 32px grid (992x544), same snap this pipe's build_context applies.
    assert [v.resolution for v in gallery.videos] == [(992, 544), (992, 544)]


# -- decode math ---------------------------------------------------------------

# -- guidance_options: cfg_zero_star / zero_init_steps -----------------------

@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_cfg_zero_star_and_zero_init_steps_reach_denoise():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(device="cpu", cfg_zero_star=False, zero_init_steps=3).process(_pipe_input(), lambda o: None)
    assert captured["cfg_zero_star"] is False
    assert captured["zero_init_steps"] == 3


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_cfg_zero_star_defaults_true_zero_init_steps_defaults_zero():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(device="cpu").process(_pipe_input(), lambda o: None)
    assert captured["cfg_zero_star"] is True
    assert captured["zero_init_steps"] == 0


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_denoise_receives_the_managers_is_cancelled_probe():
    captured = {}
    probe = lambda: False
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(device="cpu").process(_pipe_input(), lambda o: None, is_cancelled=probe)
    assert captured["is_cancelled"] is probe


# -- APG settings threading ---------------------------------------------------

def test_apg_defaults_are_omitted_not_forced_onto_sampling_settings():
    # P5 fix: an unset apg_* config knob must be OMITTED, not forced to a
    # "default" value, so it never clobbers a non-default ModelSpec value.
    ctx = _pipe().build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    for key in ("apg_eta", "apg_norm_threshold", "apg_momentum"):
        assert key not in ss
    assert ss["guidance"] == "cfg"  # base spec key survives the merge


def test_apg_config_overrides_thread_into_sampling_settings():
    ctx = _pipe(apg_eta=0.25, apg_norm_threshold=0.7, apg_momentum=-0.5).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["apg_eta"] == 0.25
    assert ss["apg_norm_threshold"] == 0.7
    assert ss["apg_momentum"] == -0.5


def test_schedule_settings_config_overrides_thread_into_sampling_settings():
    ctx = _pipe(schedule="exponential", schedule_options={"sigma_min": 0.05},
                detail_strength=0.25, detail_start=0.2).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["schedule"] == "exponential"
    assert ss["schedule_options"] == {"sigma_min": 0.05}
    assert ss["detail_strength"] == 0.25
    assert ss["detail_start"] == 0.2


def test_manual_sigmas_config_threads_into_sampling_settings_as_manual_schedule():
    # the maintainer's validated ComfyUI distilled-refine recipe,
    # expressed end to end through this pipe's own config surface.
    recipe = "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0"
    ctx = _pipe(
        sampler="euler_ancestral_cfg_pp", cfg=1.0, manual_sigmas=recipe,
        schedule="beta", schedule_options={"alpha": 0.4, "beta": 0.9},  # must be overridden, not merged
    ).build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert ss["schedule"] == "manual"
    assert ss["schedule_options"] == {"sigmas": recipe}
    assert ctx.extra.sampler == "euler_ancestral_cfg_pp"
    assert ctx.extra.cfg == 1.0

    # And build_sigmas (the actual consumer) parses it into the expected
    # 9-value descending schedule -- proving the string survives intact
    # rather than being pre-parsed/mangled somewhere in the pipe.
    from src.platform.runtime.native.sampling.flow_schedule import build_sigmas
    sigmas = build_sigmas(24, schedule=ss["schedule"], schedule_options=ss["schedule_options"])
    assert sigmas.shape == (9,)
    assert sigmas[0].item() == 1.0
    assert sigmas[-1].item() == 0.0


def test_manual_sigmas_default_empty_leaves_schedule_unset():
    ctx = _pipe().build_context(_pipe_input())
    ss = ctx.extra.sampling_settings
    assert "schedule" not in ss
    assert "schedule_options" not in ss


# -- sampler_options / step_cache ---------------------------------------------

@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_sampler_options_step_cache_absent_reach_denoise_as_none():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(device="cpu").process(_pipe_input(), lambda o: None)
    assert captured["sampler_options"] is None
    assert captured["step_cache_options"] is None


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_sampler_options_step_cache_present_reach_denoise():
    captured = {}
    sampler_opts = {"eta": 0.9}
    step_cache_opts = {"rel_threshold": 0.08, "warmup_steps": 5}
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(k) or latents
        _pipe(device="cpu", sampler_options=sampler_opts, step_cache=step_cache_opts).process(_pipe_input(), lambda o: None)
    assert captured["sampler_options"] == sampler_opts
    assert captured["step_cache_options"] == step_cache_opts


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_step_cache_absent_no_kwarg_reaches_dit_module_forward(mock_denoise):
    seen = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        x = torch.zeros(1, _LATENT_CHANNELS, 7, 16, 24)
        model_forward(x, torch.tensor([0.5]), cond)
        return latents

    mock_denoise.side_effect = fake_denoise

    class _RecordingDit:
        def __call__(self, x_list, sigma, ctx, attention_mask=None, frame_rate=None, **kwargs):
            seen["kwargs"] = kwargs
            return torch.zeros_like(x_list[0])

    bundle = _bundle()
    bundle.dit.module = _RecordingDit()
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)]
    pipe_input = PipeInput(input={"model": bundle, "conditioning": cond, "seed": [1]})
    _pipe(device="cpu").process(pipe_input, lambda o: None)

    assert "step_cache" not in seen["kwargs"]


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_step_cache_present_in_conditioning_reaches_dit_module_forward(mock_denoise):
    # Emulates what denoise()'s _CachingGuidance would inject: a "step_cache"
    # key on the conditioning dict handed to model_forward.
    seen = {}
    sentinel_cache = object()

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        x = torch.zeros(1, _LATENT_CHANNELS, 7, 16, 24)
        model_forward(x, torch.tensor([0.5]), {**cond, "step_cache": sentinel_cache})
        return latents

    mock_denoise.side_effect = fake_denoise

    class _RecordingDit:
        def __call__(self, x_list, sigma, ctx, attention_mask=None, frame_rate=None, **kwargs):
            seen["kwargs"] = kwargs
            return torch.zeros_like(x_list[0])

    bundle = _bundle()
    bundle.dit.module = _RecordingDit()
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)]
    pipe_input = PipeInput(input={"model": bundle, "conditioning": cond, "seed": [1]})
    _pipe(device="cpu").process(pipe_input, lambda o: None)

    assert seen["kwargs"]["step_cache"] is sentinel_cache


# -- FreeInit ------------------------------------------------------------------

@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_freeinit_default_off_is_exactly_one_denoise_call():
    calls = {"n": 0}
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        def fake_denoise(mf, latents, cond, uncond, **kw):
            calls["n"] += 1
            return latents
        md.side_effect = fake_denoise
        _pipe(device="cpu").process(_pipe_input(), lambda o: None)
    assert calls["n"] == 1


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_freeinit_iterations_two_makes_three_denoise_calls_with_distinct_inits():
    seed_noises = []
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        def fake_denoise(mf, latents, cond, uncond, **kw):
            seed_noises.append(kw["seed_noise"].clone())
            return torch.randn_like(latents)
        md.side_effect = fake_denoise
        _pipe(device="cpu", freeinit_iterations=2, resolution="64x64", frames=9).process(
            _pipe_input(), lambda o: None)

    assert len(seed_noises) == 3
    assert not torch.equal(seed_noises[0], seed_noises[1])
    assert not torch.equal(seed_noises[1], seed_noises[2])
    assert all(torch.isfinite(sn).all() for sn in seed_noises)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_freeinit_deterministic_across_runs_same_seed():
    def run():
        seed_noises = []
        with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
            def fake_denoise(mf, latents, cond, uncond, **kw):
                seed_noises.append(kw["seed_noise"].clone())
                return torch.full_like(latents, 0.4)
            md.side_effect = fake_denoise
            _pipe(device="cpu", freeinit_iterations=2, resolution="64x64", frames=9).process(
                _pipe_input(), lambda o: None)
        return seed_noises

    run1 = run()
    run2 = run()
    assert len(run1) == len(run2) == 3
    for a, b in zip(run1, run2):
        assert torch.equal(a, b)


# -- NAG-LTX -------------------------------------------------------------------

@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_nag_default_off_does_not_touch_cond():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(cond=cond) or latents
        _pipe(device="cpu").process(_pipe_input(), lambda o: None)  # nag_scale defaults to 1.0
    assert "nag_context" not in captured["cond"]
    assert "nag" not in captured["cond"]


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
def test_nag_scale_above_one_attaches_negative_context_equal_to_uncond_context():
    captured = {}
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(cond=cond, uncond=uncond) or latents
        _pipe(device="cpu", nag_scale=1.5, nag_tau=2.0, nag_alpha=0.25).process(_pipe_input(), lambda o: None)
    # NAG's negative context is EXACTLY uncond["context"] -- already the
    # post-apply_text_conditioning projected format (ltx_clip.py), no second
    # projection call, mirroring _attach_nag's Wan semantics exactly.
    assert torch.equal(captured["cond"]["nag_context"], captured["uncond"]["context"])
    assert captured["cond"]["nag"] == {"scale": 1.5, "tau": 2.0, "alpha": 0.25}
    assert "context" in captured["cond"]  # additive, not replaced


def test_nag_scale_above_one_without_negative_conditioning_is_noop():
    captured = {}
    cond_no_neg = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)]
    pipe_input = PipeInput(input={"model": _bundle(), "conditioning": cond_no_neg, "seed": [1]})
    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path), \
         patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise") as md:
        md.side_effect = lambda mf, latents, cond, uncond, **k: captured.update(cond=cond) or latents
        _pipe(device="cpu", nag_scale=1.5).process(pipe_input, lambda o: None)
    assert "nag_context" not in captured["cond"]


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_nag_context_and_params_reach_dit_module_forward(mock_denoise):
    seen = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        x = torch.zeros(1, _LATENT_CHANNELS, 7, 16, 24)
        model_forward(x, torch.tensor([0.5]), cond)
        return latents

    mock_denoise.side_effect = fake_denoise

    class _RecordingDit:
        def __call__(self, x_list, sigma, ctx, attention_mask=None, frame_rate=None, **kwargs):
            seen["kwargs"] = kwargs
            return torch.zeros_like(x_list[0])

    bundle = _bundle()
    bundle.dit.module = _RecordingDit()
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={"context": torch.zeros(1, 4, 8)})]
    pipe_input = PipeInput(input={"model": bundle, "conditioning": cond, "seed": [1]})
    _pipe(device="cpu", nag_scale=1.8, nag_tau=1.5, nag_alpha=0.6).process(pipe_input, lambda o: None)

    assert torch.equal(seen["kwargs"]["nag_context"], torch.zeros(1, 4, 8))
    assert seen["kwargs"]["nag"] == {"scale": 1.8, "tau": 1.5, "alpha": 0.6}


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_nag_off_no_extra_kwargs_reach_dit_module_forward(mock_denoise):
    # Regression pin: with NAG off, the call into dit_module must have NO
    # nag_context/nag kwargs at all -- identical call shape to before NAG-LTX.
    seen = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        x = torch.zeros(1, _LATENT_CHANNELS, 7, 16, 24)
        model_forward(x, torch.tensor([0.5]), cond)
        return latents

    mock_denoise.side_effect = fake_denoise

    class _RecordingDit:
        def __call__(self, x_list, sigma, ctx, attention_mask=None, frame_rate=None, **kwargs):
            seen["kwargs"] = kwargs
            return torch.zeros_like(x_list[0])

    bundle = _bundle()
    bundle.dit.module = _RecordingDit()
    cond = [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={"context": torch.zeros(1, 4, 8)})]
    pipe_input = PipeInput(input={"model": bundle, "conditioning": cond, "seed": [1]})
    _pipe(device="cpu").process(pipe_input, lambda o: None)

    assert seen["kwargs"] == {}


# -- best-effort DiT-to-VRAM restore after decode -------------

@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.restore_dit_best_effort")
def test_restore_called_once_after_last_seed_of_quantity(mock_restore):
    bundle = _bundle()
    pipe_input = PipeInput(input={
        "model": bundle,
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)
                         for _ in range(2)],
        "seed": [5, 6],
    })
    _pipe(quantity=2, device="cpu").process(pipe_input, lambda o: None)
    mock_restore.assert_called_once_with(bundle.dit, "cpu")


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.restore_dit_best_effort")
def test_restore_not_called_between_seeds_of_a_single_invocation(mock_restore):
    # quantity=3: generate_one runs 3 times in this one process() call --
    # restore must fire on the LAST of those three, not the first two.
    calls = {"n": 0}
    orig_generate_one = GeneratorLtxTxt2VidPipe.generate_one

    def counting_generate_one(self, ctx, index, seed, progress):
        calls["n"] += 1
        assert mock_restore.call_count == 0, "restore fired before the final seed"
        return orig_generate_one(self, ctx, index, seed, progress)

    bundle = _bundle()
    pipe_input = PipeInput(input={
        "model": bundle,
        "conditioning": [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds=None)
                         for _ in range(3)],
        "seed": [1, 2, 3],
    })
    with patch.object(GeneratorLtxTxt2VidPipe, "generate_one", counting_generate_one):
        _pipe(quantity=3, device="cpu").process(pipe_input, lambda o: None)
    assert calls["n"] == 3
    mock_restore.assert_called_once()


def test_decode_video_no_mean_std_unnormalization():
    """LTX's VAE un-normalizes internally -- unlike Wan there is no separate
    latent mean/std step in _decode_video."""
    vae = SimpleNamespace(
        compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(decode=lambda z: torch.zeros(1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32) - 1.0),
    )
    c = _LTXCtx(bundle=None, vae=vae, sampling_settings={}, conditioning=[], steps=1, cfg=1.0,
                sampler="euler", width=64, height=64, frames=1, fps=25.0, device="cpu", dtype=torch.float32)
    latent = torch.zeros(1, _LATENT_CHANNELS, 1, 2, 2)
    frames = _decode_video(c, latent, seed=1234)
    assert frames.shape == (1, 64, 64, 3)
    assert frames.dtype.name == "uint8"
    assert (frames == 0).all()  # decode returns -1.0 -> pixel value 0


def test_decode_video_seeds_the_decode_noise_generator_from_the_request_seed():
    """The 2.5 diffusion decoder samples the pixels it denoises, so the call
    site must hand the ladder a generator seeded off the request seed -- passing
    none silently falls back to global RNG and makes decodes unreproducible."""
    from src.pipelines.pipes._shared.vae.ltx_tiled_decode import DECODE_NOISE_SEED_OFFSET

    seen = {}

    def fake_ladder(vae, z, device, *, generator, profiler_mark, log_prefix):
        seen["generator"] = generator
        return torch.zeros(1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32)

    vae = SimpleNamespace(
        compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(decode=lambda z: None),
    )
    c = _LTXCtx(bundle=None, vae=vae, sampling_settings={}, conditioning=[], steps=1, cfg=1.0,
                sampler="euler", width=64, height=64, frames=1, fps=25.0, device="cpu", dtype=torch.float32)

    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.decode_with_oom_retry", fake_ladder):
        _decode_video(c, torch.zeros(1, _LATENT_CHANNELS, 1, 2, 2), seed=1234)

    assert seen["generator"] is not None
    expected = torch.Generator(device="cpu").manual_seed(1234 + DECODE_NOISE_SEED_OFFSET)
    assert torch.equal(seen["generator"].get_state(), expected.get_state())


def test_decode_video_raises_on_nan_pixels_instead_of_silent_black_frame():
    # A NaN pixel must fail loudly (DecodeNumericsError), not silently clamp
    # to a black 0 via pixels_3thw_to_uint8_frames's uint8 cast.
    vae = SimpleNamespace(
        compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(decode=lambda z: torch.full((1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32), float("nan"))),
    )
    c = _LTXCtx(bundle=None, vae=vae, sampling_settings={}, conditioning=[], steps=1, cfg=1.0,
                sampler="euler", width=64, height=64, frames=1, fps=25.0, device="cpu", dtype=torch.float32)
    latent = torch.zeros(1, _LATENT_CHANNELS, 1, 2, 2)
    with pytest.raises(DecodeNumericsError):
        _decode_video(c, latent, seed=1234)


def test_decode_video_finite_pixels_pass_through_unaffected():
    vae = SimpleNamespace(
        compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(decode=lambda z: torch.zeros(1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32) - 1.0),
    )
    c = _LTXCtx(bundle=None, vae=vae, sampling_settings={}, conditioning=[], steps=1, cfg=1.0,
                sampler="euler", width=64, height=64, frames=1, fps=25.0, device="cpu", dtype=torch.float32)
    latent = torch.zeros(1, _LATENT_CHANNELS, 1, 2, 2)
    frames = _decode_video(c, latent, seed=1234)
    assert frames.shape == (1, 64, 64, 3)


# -- _decode_video: temporal-chunked decode routing (LTX has no new_feat_cache) --

def test_decode_video_never_chunks_ltx_vae_has_no_feat_cache():
    # LTX's VAE module exposes no new_feat_cache -> causal3d_chunk_frames
    # returns None unconditionally -> chunked_decode_causal3d must never be
    # called, byte-identical single .decode() call, even with a huge clip and
    # a forced-favorable VRAM query.
    calls = {"decode": 0}

    def counting_decode(z):
        calls["decode"] += 1
        return torch.zeros(1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32)

    vae = SimpleNamespace(
        compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(decode=counting_decode),
    )
    c = _LTXCtx(bundle=None, vae=vae, sampling_settings={}, conditioning=[], steps=1, cfg=1.0,
                sampler="euler", width=64, height=64, frames=49, fps=25.0, device="cpu", dtype=torch.float32)
    latent = torch.zeros(1, _LATENT_CHANNELS, 20, 2, 2)  # long clip

    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.free_vram_gb", return_value=1000.0), \
         patch("src.pipelines.pipes.generator.txt2vid_ltx.main.chunked_decode_causal3d") as mock_chunked:
        _decode_video(c, latent, seed=1234)

    assert calls["decode"] == 1
    mock_chunked.assert_not_called()


# -- _LTXCtx.release_gpu -----------------------------------------------------

class _FakeOffloadable:
    def __init__(self, raise_on_offload=False):
        self.offloaded = 0
        self._raise = raise_on_offload

    def offload(self):
        self.offloaded += 1
        if self._raise:
            raise RuntimeError("cuda error")


class TestLtxCtxReleaseGpu:
    """`_LTXCtx.release_gpu()` is what makes `BaseGeneratorPipe`'s generic
    error-path cleanup fire for this pipe: `ctx.extra` is this dataclass
    directly, so it must define its own `release_gpu()` -- covers a mid-
    generation failure (e.g. a VAE decode failure) that would otherwise
    leave the ~23GB DiT + VAE resident."""

    def test_offloads_dit_and_vae(self):
        dit, vae = _FakeOffloadable(), _FakeOffloadable()
        bundle = SimpleNamespace(dit=dit)
        c = _LTXCtx(bundle=bundle, vae=vae, sampling_settings={}, conditioning=[], steps=1, cfg=1.0,
                    sampler="euler", width=8, height=8, frames=1, fps=25.0, device="cuda", dtype=torch.bfloat16)
        c.release_gpu()
        assert dit.offloaded == 1
        assert vae.offloaded == 1

    def test_never_raises_when_an_offload_fails(self):
        dit, vae = _FakeOffloadable(raise_on_offload=True), _FakeOffloadable()
        bundle = SimpleNamespace(dit=dit)
        c = _LTXCtx(bundle=bundle, vae=vae, sampling_settings={}, conditioning=[], steps=1, cfg=1.0,
                    sampler="euler", width=8, height=8, frames=1, fps=25.0, device="cuda", dtype=torch.bfloat16)
        c.release_gpu()  # must not raise
        assert vae.offloaded == 1


# -- initial_latent / decode=false (upscale refine stage) -----

def test_metadata_includes_latent_output_and_initial_latent_input():
    outputs = {o.name: o.io_type for o in GeneratorLtxTxt2VidPipe.outputs()}
    assert outputs["latent"] == IOType.LATENT
    inputs = {i.name: i for i in GeneratorLtxTxt2VidPipe.inputs()}
    assert inputs["initial_latent"].io_type == IOType.LATENT
    assert inputs["initial_latent"].required is False


# -- audio_source: standalone upscale audio passthrough + fps sync ----------

def test_metadata_includes_audio_source_input():
    inputs = {i.name: i for i in GeneratorLtxTxt2VidPipe.inputs()}
    assert inputs["audio_source"].io_type == IOType.VIDEO
    assert inputs["audio_source"].required is False
    assert inputs["audio_source"].is_array is False


def test_build_context_audio_source_absent_defaults_none():
    ctx = _pipe().build_context(_pipe_input())
    assert ctx.extra.audio_source is None
    assert ctx.extra.fps == 25.0  # unchanged: no source to sync to


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps", lambda _p: 29.97)
def test_build_context_audio_source_present_threads_into_ctx(_probe=None):
    pipe_input = _pipe_input()
    pipe_input.input["audio_source"] = "/tmp/source.mp4"
    ctx = _pipe().build_context(pipe_input)
    assert ctx.extra.audio_source == "/tmp/source.mp4"


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps", lambda _p: None)
def test_build_context_audio_source_probe_failure_raises_clear_error(_probe=None):
    """A probe failure (missing ffprobe, corrupt file, ...) must NOT
    silently fall back to a default fps -- that is exactly the whole-clip
    audio/video drift bug this guards against. No explicit fps configured and
    no usable rate from the source -> a clear, named error instead."""
    import pytest

    pipe_input = _pipe_input()
    pipe_input.input["audio_source"] = "/tmp/source.mp4"
    with pytest.raises(ValueError, match=r"could not determine a frame rate.*source\.mp4"):
        _pipe().build_context(pipe_input)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps", lambda _p: 0.0)
def test_build_context_audio_source_non_positive_probe_raises_clear_error(_probe=None):
    import pytest

    pipe_input = _pipe_input()
    pipe_input.input["audio_source"] = "/tmp/source.mp4"
    with pytest.raises(ValueError, match="could not determine a frame rate"):
        _pipe().build_context(pipe_input)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps", lambda _p: 29.97)
def test_build_context_audio_source_syncs_output_fps_to_probed_source(_probe=None):
    """Standalone upscale mode has no fps field of its own -- when a source is
    wired in and no explicit fps is configured, its real (effective) fps must
    be used so muxed audio doesn't drift out of sync."""
    pipe_input = _pipe_input()
    pipe_input.input["audio_source"] = "/tmp/source.mp4"
    ctx = _pipe().build_context(pipe_input)
    assert ctx.extra.fps == 29.97


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps", lambda _p: 25.0)
def test_build_context_audio_source_matching_fps_is_a_noop(_probe=None):
    """Source already at the pipe's own fallback default -- no spurious log/reassignment."""
    pipe_input = _pipe_input()
    pipe_input.input["audio_source"] = "/tmp/source.mp4"
    ctx = _pipe().build_context(pipe_input)
    assert ctx.extra.fps == 25.0


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps")
def test_build_context_explicit_fps_wins_over_audio_source_probe(mock_probe):
    """The video-mode contract: an explicitly configured fps always wins, even
    with audio_source connected -- the probe must not even be consulted."""
    pipe_input = _pipe_input()
    pipe_input.input["audio_source"] = "/tmp/source.mp4"
    ctx = _pipe(fps=30.0).build_context(pipe_input)
    assert ctx.extra.fps == 30.0
    mock_probe.assert_not_called()


def test_build_context_no_audio_source_and_no_explicit_fps_uses_default():
    ctx = _pipe().build_context(_pipe_input())
    assert ctx.extra.fps == 25.0


def test_build_context_no_audio_source_explicit_fps_unchanged():
    ctx = _pipe(fps=48.0).build_context(_pipe_input())
    assert ctx.extra.fps == 48.0


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps", lambda _p: 25.0)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_generate_one_passes_audio_source_through_to_encode(mock_denoise, _probe=None):
    mock_denoise.side_effect = lambda model_forward, latents, cond, uncond, **kw: latents
    captured = {}

    def fake_encode(frames, path, fps, audio=None):
        captured["audio"] = audio
        captured["fps"] = fps
        return path

    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", fake_encode):
        pipe_input = _pipe_input()
        pipe_input.input["audio_source"] = "/tmp/source.mp4"
        _pipe(device="cpu").process(pipe_input, lambda o: None)

    assert captured["audio"] == "/tmp/source.mp4"


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_generate_one_no_audio_source_passes_none_to_encode(mock_denoise):
    mock_denoise.side_effect = lambda model_forward, latents, cond, uncond, **kw: latents
    captured = {}

    def fake_encode(frames, path, fps, audio=None):
        captured["audio"] = audio
        return path

    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", fake_encode):
        _pipe(device="cpu").process(_pipe_input(), lambda o: None)

    assert captured["audio"] is None


# -- source_frame_count: standalone-upscale tail-padding trim --------

def test_metadata_includes_source_frame_count_input():
    inputs = {i.name: i for i in GeneratorLtxTxt2VidPipe.inputs()}
    assert inputs["source_frame_count"].io_type == IOType.INT
    assert inputs["source_frame_count"].required is False
    assert inputs["source_frame_count"].is_array is False


def test_build_context_source_frame_count_absent_defaults_none():
    ctx = _pipe().build_context(_pipe_input())
    assert ctx.extra.trim_to_frame_count is None


def test_build_context_source_frame_count_threads_into_ctx():
    pipe_input = _pipe_input()
    pipe_input.input["source_frame_count"] = 7
    ctx = _pipe().build_context(pipe_input)
    assert ctx.extra.trim_to_frame_count == 7


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps", lambda _p: 25.0)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_generate_one_trims_padded_tail_frames_before_mux():
    """latent_upscaler/ltx pads a source's frame count up to the VAE's 1+8k
    lattice by repeating the last frame (see its `_pad_frames_to_temporal_
    grid`) -- `source_frame_count` (wired from its own 'source_frame_count'
    output) tells this pipe how many of ITS OWN decoded frames are real vs.
    padding, so the padded tail never reaches the mux."""
    captured = {}

    def fake_decode(c, latent, seed):
        return torch.zeros(10, 4, 4, 3, dtype=torch.uint8)  # 10 decoded frames (padded)

    def fake_encode(frames, path, fps, audio=None):
        captured["frame_count"] = frames.shape[0]
        return path

    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main._decode_video", fake_decode), \
         patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", fake_encode):
        pipe_input = _pipe_input()
        pipe_input.input["audio_source"] = "/tmp/source.mp4"
        pipe_input.input["source_frame_count"] = 7  # 3 padded tail frames to drop
        _pipe(device="cpu").process(pipe_input, lambda o: None)

    assert captured["frame_count"] == 7


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_generate_one_no_source_frame_count_does_not_trim():
    """Absent source_frame_count (in-flow two-stage, or a source that was
    already exactly on-grid) -- decoded output passes through unchanged."""
    captured = {}

    def fake_decode(c, latent, seed):
        return torch.zeros(10, 4, 4, 3, dtype=torch.uint8)

    def fake_encode(frames, path, fps, audio=None):
        captured["frame_count"] = frames.shape[0]
        return path

    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main._decode_video", fake_decode), \
         patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", fake_encode):
        _pipe(device="cpu").process(_pipe_input(), lambda o: None)

    assert captured["frame_count"] == 10


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps", lambda _p: 25.0)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_generate_one_source_frame_count_equal_to_decoded_is_a_noop():
    """The source was already exactly on the VAE's temporal grid -- no
    padding was ever added, so a trim count equal to the decoded length must
    not crop anything (and must not raise)."""
    captured = {}

    def fake_decode(c, latent, seed):
        return torch.zeros(9, 4, 4, 3, dtype=torch.uint8)

    def fake_encode(frames, path, fps, audio=None):
        captured["frame_count"] = frames.shape[0]
        return path

    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main._decode_video", fake_decode), \
         patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", fake_encode):
        pipe_input = _pipe_input()
        pipe_input.input["audio_source"] = "/tmp/source.mp4"
        pipe_input.input["source_frame_count"] = 9
        _pipe(device="cpu").process(pipe_input, lambda o: None)

    assert captured["frame_count"] == 9


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.probe_effective_fps", lambda _p: 25.0)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_generate_one_source_frame_count_larger_than_decoded_is_ignored():
    """A source_frame_count that (erroneously) exceeds the decoded length must
    never grow the output -- the guard only ever trims, never pads."""
    captured = {}

    def fake_decode(c, latent, seed):
        return torch.zeros(9, 4, 4, 3, dtype=torch.uint8)

    def fake_encode(frames, path, fps, audio=None):
        captured["frame_count"] = frames.shape[0]
        return path

    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main._decode_video", fake_decode), \
         patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", fake_encode):
        pipe_input = _pipe_input()
        pipe_input.input["audio_source"] = "/tmp/source.mp4"
        pipe_input.input["source_frame_count"] = 50
        _pipe(device="cpu").process(pipe_input, lambda o: None)

    assert captured["frame_count"] == 9


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_initial_latent_absent_seeds_zero_latent_unchanged(mock_denoise):
    """No initial_latent -> byte-identical to the pre-upscaler behavior: latents start at zero."""
    captured = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured["latents"] = latents.clone()
        return latents

    mock_denoise.side_effect = fake_denoise
    _pipe(device="cpu").process(_pipe_input(), lambda o: None)
    assert torch.equal(captured["latents"], torch.zeros(1, _LATENT_CHANNELS, 7, 16, 24))


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_initial_latent_present_seeds_denoise_latents(mock_denoise):
    captured = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured["latents"] = latents.clone()
        return latents

    mock_denoise.side_effect = fake_denoise
    seed_latent = torch.full((1, _LATENT_CHANNELS, 7, 16, 24), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    _pipe(device="cpu", refine_sigmas="0.9, 0.0").process(pipe_input, lambda o: None)
    assert torch.equal(captured["latents"], seed_latent)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_initial_latent_bare_tensor_from_upscaler_pipe(mock_denoise):
    """latent_upscaler/ltx outputs a BARE Tensor (is_array=False) -- `or []`
    on it raised 'Boolean value of Tensor is ambiguous' (maintainer repro)."""
    captured = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured["latents"] = latents.clone()
        return latents

    mock_denoise.side_effect = fake_denoise
    seed_latent = torch.full((1, _LATENT_CHANNELS, 7, 16, 24), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = seed_latent
    _pipe(device="cpu", refine_sigmas="0.9, 0.0").process(pipe_input, lambda o: None)
    assert torch.equal(captured["latents"], seed_latent)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_initial_latent_without_refine_sigmas_raises_clear_error():
    import pytest

    seed_latent = torch.full((1, _LATENT_CHANNELS, 7, 16, 24), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    with pytest.raises(ValueError, match="refine_sigmas' is empty"):
        _pipe(device="cpu").process(pipe_input, lambda o: None)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_refine_sigmas_parsed_verbatim_and_passed_to_denoise_sigmas_kwarg(mock_denoise):
    captured = {}

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured["sigmas"] = kw["sigmas"]
        return latents

    mock_denoise.side_effect = fake_denoise
    seed_latent = torch.full((1, _LATENT_CHANNELS, 7, 16, 24), 0.5)
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    recipe = "0.909375, 0.725, 0.421875, 0.0"
    _pipe(device="cpu", refine_sigmas=recipe).process(pipe_input, lambda o: None)
    # NOT forced to 1.0 (unlike manual_sigmas) -- the whole point of this knob.
    assert torch.allclose(captured["sigmas"], torch.tensor([0.909375, 0.725, 0.421875, 0.0]))


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_no_initial_latent_passes_none_sigmas_override(mock_denoise):
    captured = {}
    mock_denoise.side_effect = lambda mf, latents, c, u, **kw: captured.update(kw) or latents
    _pipe(device="cpu").process(_pipe_input(), lambda o: None)
    assert captured["sigmas"] is None


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_initial_latent_shape_derives_resolution_frames_ignoring_config():
    """Width/height/frames come from the seed latent's own
    shape (never known at preset-render time for an upstream/standalone
    latent), NOT from `resolution`/`frames` config -- a config left at the
    unrelated default must not raise or get silently ignored-then-mismatched."""
    seed_latent = torch.full((1, _LATENT_CHANNELS, 3, 4, 4), 0.5)  # arbitrary, unrelated to config resolution
    pipe_input = _pipe_input()
    pipe_input.input["initial_latent"] = [seed_latent]
    ctx = _pipe(resolution="768x512", frames=49).build_context(pipe_input)
    # 4 latent cols/rows * 32px spatial downscale; 3 latent frames -> (3-1)*8+1=17 pixel frames.
    assert (ctx.extra.width, ctx.extra.height, ctx.extra.frames) == (128, 128, 17)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_initial_latents_inconsistent_shape_across_seeds_raises_clear_error():
    import pytest

    first = torch.zeros(1, _LATENT_CHANNELS, 3, 4, 4)
    mismatched = torch.zeros(1, _LATENT_CHANNELS, 3, 4, 5)  # different w_lat
    pipe_input = _pipe_input(quantity=2, seeds=(1, 2))
    pipe_input.input["initial_latent"] = [first, mismatched]
    with pytest.raises(ValueError, match=r"initial_latent\[1\] shape .* does not match"):
        _pipe(quantity=2, device="cpu", refine_sigmas="0.9, 0.0").process(pipe_input, lambda o: None)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise")
def test_initial_latent_falls_back_to_last_when_fewer_than_quantity(mock_denoise):
    captured = []

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        captured.append(latents.clone())
        return latents

    mock_denoise.side_effect = fake_denoise
    only_latent = torch.full((1, _LATENT_CHANNELS, 7, 16, 24), 0.7)
    pipe_input = _pipe_input(quantity=2, seeds=(1, 2))
    pipe_input.input["initial_latent"] = [only_latent]  # only one, quantity=2
    _pipe(quantity=2, device="cpu", refine_sigmas="0.9, 0.0").process(pipe_input, lambda o: None)
    assert len(captured) == 2
    assert torch.equal(captured[0], only_latent)
    assert torch.equal(captured[1], only_latent)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4")
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main._decode_video")
def test_decode_false_skips_decode_and_emits_latent_output(mock_decode, mock_encode):
    result = _pipe(decode=False, device="cpu").process(_pipe_input(), lambda o: None)
    mock_decode.assert_not_called()
    mock_encode.assert_not_called()
    assert result.output["video"] == []
    assert len(result.output["latent"]) == 1
    assert torch.equal(result.output["latent"][0], torch.zeros(1, _LATENT_CHANNELS, 7, 16, 24))


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4")
def test_decode_false_emits_no_gallery(mock_encode):
    emitted = []
    _pipe(decode=False, device="cpu").process(_pipe_input(), lambda o: emitted.append(o))
    assert not any(isinstance(o, GalleryGenerationOutput) for o in emitted)


@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.encode_frames_to_mp4", lambda frames, path, fps, audio=None: path)
@patch("src.pipelines.pipes.generator.txt2vid_ltx.main.denoise", lambda mf, latents, c, u, **k: latents)
def test_decode_true_default_output_has_empty_latent_list():
    result = _pipe(device="cpu").process(_pipe_input(), lambda o: None)
    assert result.output["latent"] == []
    assert len(result.output["video"]) == 1


def test_decode_video_chunked_path_would_be_used_if_forced():
    # Even though real LTX never reaches it, verify the wiring itself: if
    # causal3d_chunk_frames is forced to return a chunk size (simulating a
    # hypothetical future LTX VAE with new_feat_cache), the chunked primitive
    # is called with the exact latent and chunk size, and .decode() is not
    # called directly.
    seen = {}

    def fake_chunked(vae, z, chunk_latent_frames, **kw):
        seen["z"] = z.clone()
        seen["chunk_latent_frames"] = chunk_latent_frames
        return torch.zeros(1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32)

    calls = {"decode": 0}

    def counting_decode(z):
        calls["decode"] += 1
        return torch.zeros(1, 3, z.shape[2], z.shape[3] * 32, z.shape[4] * 32)

    vae = SimpleNamespace(
        compute_dtype=torch.float32,
        move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(decode=counting_decode),
    )
    c = _LTXCtx(bundle=None, vae=vae, sampling_settings={}, conditioning=[], steps=1, cfg=1.0,
                sampler="euler", width=64, height=64, frames=49, fps=25.0, device="cpu", dtype=torch.float32)
    latent = torch.randn(1, _LATENT_CHANNELS, 7, 2, 2)

    with patch("src.pipelines.pipes.generator.txt2vid_ltx.main.causal3d_chunk_frames", return_value=3), \
         patch("src.pipelines.pipes.generator.txt2vid_ltx.main.chunked_decode_causal3d", side_effect=fake_chunked):
        _decode_video(c, latent.clone(), seed=1234)

    assert calls["decode"] == 0
    assert seen["chunk_latent_frames"] == 3
    assert torch.equal(seen["z"], latent)  # LTX applies no denorm at all


# -- TE eviction (additional coverage) ----------------------------------------
# See the "TE eviction (host RAM release)" section above for the base
# fires/no-cache-key/no-models/survives-raising cases; these cover the two
# multi-call shapes those don't: per-seed batching and the standalone-upscale
# refine's own (second) build_context call for the same bundle.

def test_te_eviction_fires_once_per_build_context_call_not_per_seed():
    models = _FakeModelsService()
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    _pipe().build_context(_pipe_input(quantity=3, seeds=(1, 2, 3), bundle=bundle, models=models))
    assert models.evict_calls == ["native/te/gemma3.safetensors"]  # not 3x


def test_te_eviction_also_wired_for_the_standalone_upscale_refine_call():
    """This pipe's own `initial_latent`-connected refine call (standalone
    upscale mode) is a SECOND eviction attempt for the same key -- already a
    no-op by then in real use (latent_upscaler/ltx's own call fires first),
    modeled here by an already-evicted MODELS stand-in; must not raise."""
    models = _FakeModelsService(evict_result=False)  # already evicted upstream
    bundle = _bundle(te_cache_key="native/te/gemma3.safetensors")
    pipe_input = _pipe_input(bundle=bundle, models=models)
    pipe_input.input["initial_latent"] = [torch.zeros(1, _LATENT_CHANNELS, 3, 2, 2)]
    _pipe(refine_sigmas="0.9, 0.0").build_context(pipe_input)
    assert models.evict_calls == ["native/te/gemma3.safetensors"]
