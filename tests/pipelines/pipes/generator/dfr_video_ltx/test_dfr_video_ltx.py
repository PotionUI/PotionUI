"""Tests for the generator/dfr_video_ltx pipe -- tiny synthetic configs, fake
bundles, no real weights.

The temporal-upscaler checkpoint is not on disk anywhere these run, so every
test here drives ``process()`` end to end with a fake bundle whose "upsampler"
is a frame-axis doubler and whose "DiT" returns a zero velocity. What is under
test is the ORCHESTRATION -- round count, anchor synthesis, per-tile seeds, the
stitch geometry, the fps triple, audio passthrough and cancellation -- not the
sampling numerics, which the shared sampler's own suites cover.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes._shared.generation.dfr_layout import plan_canvas, plan_round
from src.pipelines.pipes.generator.dfr_video_ltx.anchors import (
    frame_to_pixels,
    synthesize_anchor_bag,
)
from src.pipelines.pipes.generator.dfr_video_ltx.main import (
    GeneratorDfrLtxPipe,
    _resolve_pixel_frame,
    tile_noise_seed,
)

_MOD = "src.pipelines.pipes.generator.dfr_video_ltx.main"

C_LAT = 128
H_LAT = W_LAT = 2
FRAMES = 121          # canvas 121, segment 24, slots [24, 48, 72, 96, 120]
T_LAT = (FRAMES - 1) // 8 + 1


# -- doubles ------------------------------------------------------------------

class _Component:
    """Stand-in for a ``NativeModel`` slot: records move_to/offload."""

    def __init__(self, module):
        self.module = module
        self.compute_dtype = torch.float32
        self.device = "cpu"
        self.moved_to = []
        self.offloaded = 0

    def move_to(self, device):
        self.moved_to.append(device)
        self.device = str(device)

    def offload(self):
        self.offloaded += 1
        self.device = "cpu"


class _Stats:
    def un_normalize(self, x):
        return x

    def normalize(self, x):
        return x


class _TemporalUpsampler:
    """Frame axis ``T -> 2T - 1``, the contract the real arch honours (double
    then drop the first output frame)."""

    def __init__(self):
        self.calls = []

    def __call__(self, z):
        self.calls.append(tuple(z.shape))
        b, c, f, h, w = z.shape
        return torch.zeros(b, c, 2 * f - 1, h, w, dtype=z.dtype)


class _Vae:
    def __init__(self):
        self.per_channel_statistics = _Stats()
        self.encodes = []

    def encode(self, pixels):
        self.encodes.append(tuple(pixels.shape))
        _, _, t, h, w = pixels.shape
        return torch.zeros(1, C_LAT, (t - 1) // 8 + 1, h // 32, w // 32)


class _Dit:
    """Zero-velocity DiT that records the ``frame_rate`` (the RoPE time base)
    of every forward."""

    def __init__(self):
        self.frame_rates = []

    def __call__(self, model_x, timestep, context, **kw):
        self.frame_rates.append(kw["frame_rate"])
        extra = kw.get("extra_video_tokens")
        extra_out = torch.zeros_like(extra) if extra is not None else None
        return (torch.zeros_like(model_x[0]), None, extra_out)


def _bundle(with_temporal=True):
    dit_module = _Dit()
    return SimpleNamespace(
        spec=SimpleNamespace(family="ltx", variant="2.5", sampling_settings={}),
        dit=_Component(dit_module),
        vae=_Component(_Vae()),
        temporal_upsampler=_Component(_TemporalUpsampler()) if with_temporal else None,
        audio_vae=None,
        vocoder=None,
    )


def _conditioning():
    return SimpleNamespace(embeds={"context": torch.zeros(1, 2, 8)}, n_embeds=None)


def _latent(t_lat=T_LAT):
    return torch.zeros(1, C_LAT, t_lat, H_LAT, W_LAT)


def _pipe(**over):
    cfg = GeneratorDfrLtxPipe.get_default_config()
    cfg.update({
        "rounds": 1,
        "fps": 25.0,
        "frames": FRAMES,
        "resolution": f"{W_LAT * 32}x{H_LAT * 32}",
        "device": "cpu",
    })
    cfg.update(over)
    return GeneratorDfrLtxPipe(config=cfg)


def _pipe_input(bundle, *, latents=None, seeds=(7,), images=(), audio=()):
    return PipeInput(input={
        "model": bundle,
        "conditioning": [_conditioning()],
        "latent": list(latents if latents is not None else [_latent()]),
        "seed": list(seeds),
        "image": list(images),
        "audio": list(audio),
    })


class _Recorder:
    """Collects the pipe's generation_outputs emissions."""

    def __init__(self):
        self.items = []

    def __call__(self, output):
        self.items.append(output)


def _fake_decode(_ctx, latent, _seed):
    t_pix = (int(latent.shape[2]) - 1) * 8 + 1
    return np.zeros((t_pix, H_LAT * 32, W_LAT * 32, 3), dtype=np.uint8)


class _DenoiseSpy:
    """Stub for ``denoise_prenoised``: records every call and returns the state
    unchanged, so a whole multi-round run costs no sampling."""

    def __init__(self):
        self.calls = []

    def __call__(self, forward, x, cond, uncond, **kw):
        self.calls.append({"forward": forward, "x": x, **kw})
        return x


def _run(pipe, bundle, *, denoise=None, is_cancelled=None, **input_kw):
    """Drive ``process()`` with every host-side side effect stubbed.

    ``recorder.encode`` is the ``encode_frames_to_mp4`` mock, so a test can
    assert on the mux call without re-patching (a second patch of the same
    target would shadow this one and never be called).
    """
    recorder = _Recorder()
    stack = [
        patch(f"{_MOD}._decode_video", side_effect=_fake_decode),
        patch(f"{_MOD}.release_idle_te", return_value=0.0),
        patch(f"{_MOD}.place_dit_for_sequence", return_value=None),
        patch(f"{_MOD}.restore_dit_best_effort", return_value=None),
        patch(f"{_MOD}.clear_gpu_memory", return_value=None),
    ]
    if denoise is not None:
        stack.append(patch(f"{_MOD}.denoise_prenoised", side_effect=denoise))
    encode_patch = patch(f"{_MOD}.encode_frames_to_mp4", return_value=None)
    for ctx in stack:
        ctx.start()
    recorder.encode = encode_patch.start()
    try:
        return pipe.process(_pipe_input(bundle, **input_kw), recorder, is_cancelled), recorder
    finally:
        encode_patch.stop()
        for ctx in reversed(stack):
            ctx.stop()


# -- registration / IO --------------------------------------------------------

def test_name_and_io():
    assert GeneratorDfrLtxPipe.name == "generator"
    inputs = {i.name: i for i in GeneratorDfrLtxPipe.inputs()}
    assert inputs["latent"].io_type == IOType.LATENT and inputs["latent"].required
    assert inputs["audio"].io_type == IOType.AUDIO
    outputs = {o.name: o for o in GeneratorDfrLtxPipe.outputs()}
    assert set(outputs) == {"video", "latent", "audio"}


def test_default_round_sigmas_are_the_distilled_tail():
    # The temporal-round schedule is the distilled list from its index-4 knot
    # on -- a HIGHER starting noise level than the detailing pass's 0.909375.
    assert GeneratorDfrLtxPipe.get_default_config()["round_sigmas"] == \
        "0.975,0.909375,0.725,0.421875,0.0"


def test_default_anchor_strength_and_eta():
    cfg = GeneratorDfrLtxPipe.get_default_config()
    assert cfg["anchor_strength"] == 0.95
    assert cfg["ancestral_eta"] == 0.5


# -- preflight ----------------------------------------------------------------

def test_rounds_without_a_temporal_upsampler_is_a_hard_error():
    pipe = _pipe(rounds=1)
    with pytest.raises(ValueError, match="temporal_upscale_model"):
        _run(pipe, _bundle(with_temporal=False), denoise=_DenoiseSpy())


def test_rounds_zero_needs_no_temporal_upsampler():
    pipe = _pipe(rounds=0)
    out, _ = _run(pipe, _bundle(with_temporal=False), denoise=_DenoiseSpy())
    assert len(out.output["video"]) == 1


def test_non_ltx_bundle_is_rejected():
    bundle = _bundle()
    bundle.spec = SimpleNamespace(family="wan", variant="2.2", sampling_settings={})
    with pytest.raises(ValueError, match="not an LTX checkpoint"):
        _run(_pipe(), bundle, denoise=_DenoiseSpy())


def test_resolution_mismatch_is_caught_before_any_gpu_work():
    pipe = _pipe(resolution="1024x1024")
    with pytest.raises(ValueError, match="configured for 1024x1024"):
        _run(pipe, _bundle(), denoise=_DenoiseSpy())


def test_raw_form_resolution_that_snaps_to_the_latent_passes_preflight():
    # The form carries a raw value (e.g. 720x480) while the stage that
    # produced the latent already snapped it onto the LTX grid (704x480);
    # the preflight must compare through the same snap.
    w, h = W_LAT * 32, H_LAT * 32
    pipe = _pipe(resolution=f"{w + 15}x{h + 15}")
    _run(pipe, _bundle(), denoise=_DenoiseSpy())


# -- round count and the T -> 2T-1 upsampler mapping --------------------------

@pytest.mark.parametrize("rounds,upsample_calls", [(0, 0), (1, 1), (2, 2)])
def test_one_temporal_upsample_per_round(rounds, upsample_calls):
    bundle = _bundle()
    out, _ = _run(_pipe(rounds=rounds), bundle, denoise=_DenoiseSpy())
    assert len(bundle.temporal_upsampler.module.calls) == upsample_calls
    assert out.output["video"]


@pytest.mark.parametrize("rounds,expected_latents", [
    (0, T_LAT),        # 121 frames
    (1, 31),           # 241 frames
    (2, 61),           # 481 frames
])
def test_output_latent_frames_follow_the_frame_contract(rounds, expected_latents):
    # (frames - 1) * 2**rounds + 1 pixel frames, i.e. T -> 2T - 1 latent frames
    # per round -- and the stitch has to land on exactly that.
    pipe = _pipe(rounds=rounds, decode=False)
    out, _ = _run(pipe, _bundle(), denoise=_DenoiseSpy())
    latent = out.output["latent"][0]
    assert latent.shape[2] == expected_latents
    assert (latent.shape[2] - 1) * 8 + 1 == (FRAMES - 1) * (2 ** rounds) + 1


def test_stitched_extent_matches_the_layout_prediction():
    spy = _DenoiseSpy()
    pipe = _pipe(rounds=1, decode=False)
    out, _ = _run(pipe, _bundle(), denoise=spy)
    layout = plan_round((48, 96, 144, 192, 240), frames=241, num_tiles=2)
    assert out.output["latent"][0].shape[2] == layout.expected_latents == 31
    assert len(spy.calls) == len(layout.tiles)


# -- anchor synthesis ---------------------------------------------------------

def test_anchor_synthesis_encodes_one_standalone_clip_per_seam():
    # One VAE encode per canvas slot, each of a ONE-frame clip. Slicing a
    # mid-stream latent frame instead would be silently wrong: under causal
    # encoding it covers 8 pixel frames relative to its predecessors.
    bundle = _bundle()
    _run(_pipe(rounds=1), bundle, denoise=_DenoiseSpy())
    canvas = plan_canvas(FRAMES)
    single_frame_encodes = [s for s in bundle.vae.module.encodes if s[2] == 1]
    assert len(single_frame_encodes) == len(canvas.slot_positions) == 5


def test_anchor_synthesis_rejects_a_position_past_the_decoded_clip():
    def decode(_latent):
        return np.zeros((25, 32, 32, 3), dtype=np.uint8)

    with pytest.raises(ValueError, match="outside the 25 decoded frames"):
        synthesize_anchor_bag(torch.zeros(1, 8, 4, 1, 1), [24, 48],
                              decode=decode, encode_frame=lambda p: p)


def test_frame_to_pixels_shape_and_range():
    frame = np.full((32, 64, 3), 255, dtype=np.uint8)
    pixels = frame_to_pixels(frame)
    assert pixels.shape == (1, 3, 1, 32, 64)
    assert torch.allclose(pixels, torch.ones_like(pixels))


def test_reanchor_each_round_re_derives_the_bag_at_full_density():
    # Without it the bag only doubles, so round 2 runs on 5 seams; with it the
    # round-1 timeline is re-anchored at the canvas segment length (10 seams).
    coarse = _DenoiseSpy()
    _run(_pipe(rounds=2), _bundle(), denoise=coarse)
    dense = _DenoiseSpy()
    _run(_pipe(rounds=2, reanchor_each_round=True), _bundle(), denoise=dense)

    # Both run 2 tiles in round 1 and 4 in round 2, so the tile COUNT proves
    # nothing -- the seam POSITIONS are what differ. Coarse round 2 carries the
    # 5 doubled round-1 seams (96, 192, ...), which puts every tile at 25
    # latent frames; re-anchored round 2 carries the full 10-seam canvas grid,
    # whose tiles are the specification's 19/25/19/19.
    def base_latent_frames(spy):
        return [call["forward"].t_lat for call in spy.calls[2:]]

    assert base_latent_frames(coarse) == [25, 25, 25, 25]
    assert base_latent_frames(dense) == [19, 25, 19, 19]


# -- risk R3: per-tile ancestral noise ---------------------------------------

def test_tile_noise_seed_is_the_documented_stream_offset():
    # seed + ANCESTRAL_NOISE_SEED_OFFSET (10000) + 1000*round + tile, which
    # stays inside the ancestral band and clear of the decode stream (+20000).
    assert tile_noise_seed(7, 1, 0) == 7 + 10000 + 1000
    assert tile_noise_seed(7, 2, 3) == 7 + 10000 + 2000 + 3


def test_every_tile_of_every_round_gets_a_distinct_ancestral_generator():
    # Tiles are positionally identical -- same resolution, same local frame
    # layout, same conditioning structure -- so a shared ancestral stream would
    # inject byte-identical noise into each and correlate them visibly.
    spy = _DenoiseSpy()
    _run(_pipe(rounds=2), _bundle(), denoise=spy)
    seeds = [call["sampler_options"]["generator"].initial_seed() for call in spy.calls]
    assert len(seeds) == 6           # 2 tiles in round 1 + 4 in round 2
    assert len(set(seeds)) == len(seeds)


def test_ancestral_eta_and_sampler_reach_the_denoise_call():
    spy = _DenoiseSpy()
    _run(_pipe(rounds=1, ancestral_eta=0.25), _bundle(), denoise=spy)
    for call in spy.calls:
        assert call["sampler_name"] == "euler_ancestral"
        assert call["sampler_options"]["eta"] == 0.25


def test_round_sigmas_are_used_verbatim():
    # Never routed through manual_sigmas / flow_schedule's manual mode, which
    # force-rewrites sigmas[0] to 1.0 and would destroy the partial-noise start.
    spy = _DenoiseSpy()
    _run(_pipe(rounds=1, round_sigmas="0.9,0.5,0.0"), _bundle(), denoise=spy)
    for call in spy.calls:
        assert [round(float(s), 4) for s in call["sigmas"]] == [0.9, 0.5, 0.0]
        assert call["steps"] == 2


# -- risk R2: the fps triple --------------------------------------------------

def test_conditioning_fps_doubles_per_round_and_is_capped_at_sixty():
    # base 25 -> round 1 conditioning fps 50, round 2 would want 100 and is
    # capped to 60. Playback fps is base * 2**rounds, UNCAPPED.
    bundle = _bundle()
    _, recorder = _run(_pipe(rounds=2, fps=25.0), bundle)
    assert sorted(set(bundle.dit.module.frame_rates)) == [50.0, 60.0]
    assert recorder.encode.call_args.kwargs["fps"] == 100.0


def test_conditioning_fps_never_exceeds_the_cap_even_at_a_high_base():
    bundle = _bundle()
    _run(_pipe(rounds=2, fps=60.0), bundle)
    assert bundle.dit.module.frame_rates
    assert max(bundle.dit.module.frame_rates) <= 60.0


def test_playback_fps_is_uncapped():
    _, recorder = _run(_pipe(rounds=2, fps=60.0), _bundle())
    assert recorder.encode.call_args.kwargs["fps"] == 240.0


# -- audio --------------------------------------------------------------------

def test_audio_is_passed_through_to_the_mux_verbatim():
    track = object()
    out, recorder = _run(_pipe(rounds=1), _bundle(), audio=[track])
    assert recorder.encode.call_args.kwargs["audio"] is track
    assert out.output["audio"] == [track]


def test_no_audio_input_muxes_nothing():
    out, recorder = _run(_pipe(rounds=1), _bundle())
    assert recorder.encode.call_args.kwargs["audio"] is None
    assert out.output["audio"] == []


# -- cancellation -------------------------------------------------------------

def test_cancellation_mid_round_stops_before_the_next_tile():
    spy = _DenoiseSpy()
    state = {"n": 0}

    def cancelled():
        state["n"] += 1
        return state["n"] > 3   # let the first tile through, then cancel

    out, _ = _run(_pipe(rounds=2), _bundle(), denoise=spy, is_cancelled=cancelled)
    assert len(spy.calls) < 6
    assert out.output["video"] == []


def test_cancellation_before_the_first_latent_emits_nothing():
    out, _ = _run(_pipe(rounds=1), _bundle(), denoise=_DenoiseSpy(),
                  is_cancelled=lambda: True)
    assert out.output["video"] == [] and out.output["latent"] == []


# -- the VRAM relief valve ----------------------------------------------------

def test_num_tiles_override_splits_finer_without_changing_the_stitch():
    spy = _DenoiseSpy()
    pipe = _pipe(rounds=1, decode=False, num_tiles_override=4)
    out, _ = _run(pipe, _bundle(), denoise=spy)
    assert len(spy.calls) == 4                       # instead of 2**1
    assert out.output["latent"][0].shape[2] == 31    # unchanged contract


def test_max_tile_tokens_forces_a_finer_split():
    # Round 1 tile 0 is 19 latent frames + 3 anchors at 4 tokens each = 88
    # tokens; a 60-token budget cannot be met at 2 tiles.
    spy = _DenoiseSpy()
    _run(_pipe(rounds=1, max_tile_tokens=60), _bundle(), denoise=spy)
    assert len(spy.calls) > 2


def test_tile_count_is_clamped_to_the_segment_count():
    spy = _DenoiseSpy()
    _run(_pipe(rounds=1, num_tiles_override=99), _bundle(), denoise=spy)
    assert len(spy.calls) == 5   # 5 canvas segments -> 5 tiles, the floor


# -- media conditioning -------------------------------------------------------

@pytest.mark.parametrize("frame,expected", [
    ("first", 0), (0, 0), (None, 0), ("last", FRAMES - 1),
    (24, 24), (999, FRAMES - 1),
])
def test_resolve_pixel_frame(frame, expected):
    assert _resolve_pixel_frame(frame, FRAMES) == expected


def test_video_sourced_placements_are_rejected():
    pipe = _pipe(rounds=1, media_placements=[{"source": "video", "index": 0, "frame": 0}])
    with pytest.raises(ValueError, match="not supported"):
        _run(pipe, _bundle(), denoise=_DenoiseSpy())


def test_reference_role_placements_are_rejected():
    pipe = _pipe(rounds=1, media_placements=[
        {"source": "image", "index": 0, "frame": 0, "role": "reference"}])
    with pytest.raises(ValueError, match="IC-LoRA"):
        _run(pipe, _bundle(), denoise=_DenoiseSpy(), images=[torch.rand(64, 64, 3)])


def _round_one_conditions(pipe, tile_index, placements):
    """The conditioning list one round-1 tile is built from, without running
    anything -- the layout and the bag are both deterministic."""
    layout = plan_round((48, 96, 144, 192, 240), frames=241, num_tiles=2)
    bag = {p: torch.zeros(1, C_LAT, 1, H_LAT, W_LAT) for p in layout.seams}
    return pipe._tile_conditions(
        tile=layout.tiles[tile_index], bag=bag, images=[torch.rand(64, 64, 3)],
        placements=placements, round_index=1)


def test_an_opening_image_conditions_only_the_first_tile():
    # Frame index 0 means THIS tile's first frame, so re-applying the opening
    # image on a non-first tile would pin the wrong content onto the seam. The
    # first tile keeps the caller's images verbatim; every other tile keeps only
    # those falling inside its own window.
    pipe = _pipe(rounds=1)
    placements = [{"index": 0, "frame": 0, "strength": 1.0}]

    tile0 = _round_one_conditions(pipe, 0, placements)
    images0 = [c for c in tile0 if c.frames is not None]
    assert len(images0) == 1
    assert images0[0].latent_index == 0 and images0[0].pixel_frame_index is None

    tile1 = _round_one_conditions(pipe, 1, placements)
    assert [c for c in tile1 if c.frames is not None] == []


def test_a_mid_clip_image_is_rebased_onto_each_tile_that_holds_it():
    # A base-timeline frame index scales by 2**round onto the round's timeline
    # (60 -> 120 at round 1) and is then rebased per tile: tile 0's window opens
    # at pixel 0 so it stays 120; tile 1's opens at 96 so it becomes 24.
    pipe = _pipe(rounds=1)
    placements = [{"index": 0, "frame": 60, "strength": 0.8}]
    assert [c.pixel_frame_index for c in _round_one_conditions(pipe, 0, placements)
            if c.frames is not None] == [120]
    assert [c.pixel_frame_index for c in _round_one_conditions(pipe, 1, placements)
            if c.frames is not None] == [24]


def test_anchors_go_in_at_tile_local_positions_and_strength_095():
    # Mask polarity is STRENGTH: 1 = fully pinned. Anchors sit at 0.95 so the
    # tiles either side of a seam can still settle it between them.
    pipe = _pipe(rounds=1)
    anchors = [c for c in _round_one_conditions(pipe, 1, []) if c.latent is not None]
    assert all(c.strength == 0.95 for c in anchors)
    # Tile 1's window is 96…240 with anchors at 96, 144, 192, 240 -> locals
    # 0, 48, 96, 144. The local-0 anchor addresses the tile's own first latent
    # frame (a first-frame overwrite), the rest are appended tokens.
    assert [c.pixel_frame_index for c in anchors] == [None, 48, 96, 144]
    assert anchors[0].latent_index == 0


def test_first_tile_has_no_window_opening_anchor():
    # Frame 0 is not a keyframe, so the first tile's window start contributes
    # no anchor -- every one of its anchors is an appended token.
    pipe = _pipe(rounds=1)
    anchors = [c for c in _round_one_conditions(pipe, 0, []) if c.latent is not None]
    assert [c.pixel_frame_index for c in anchors] == [48, 96, 144]


# -- end to end through the real sampler --------------------------------------

def test_full_run_through_the_real_sampler():
    # No denoise stub: the zero-velocity fake DiT drives the actual ancestral
    # loop, so the packed-state shapes, the per-token timesteps and the
    # conditioning blend all have to line up for this to return at all.
    bundle = _bundle()
    out, recorder = _run(_pipe(rounds=1, round_sigmas="0.975,0.5,0.0"), bundle)
    assert len(out.output["video"]) == 1
    assert bundle.dit.module.frame_rates            # the DiT really ran
    assert any(getattr(item, "progress", None) is not None for item in recorder.items)
