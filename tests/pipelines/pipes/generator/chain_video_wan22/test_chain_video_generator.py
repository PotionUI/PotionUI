"""Tests for generator/chain_video_wan22: the per-segment sub-type routing
across two checkpoint sets (t2v / i2v), the sequential segment loop, tail
handoff, per-segment LoRA patch/unpatch, seed policy, cancellation, and the
per-set checkpoint-arch guards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest.mock import patch

import numpy as np
import pytest
import torch

from src.pipelines.outputs import GalleryGenerationOutput, ParamGenerationOutput
from src.platform.runtime.native.errors import DecodeNumericsError, SamplingNumericsError
from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes.generator.chain_video_wan22.main import GeneratorWanChainVideoPipe


# -- fakes ------------------------------------------------------------------

@dataclass
class _FakeSpec:
    variant: str = "wan22_i2v_14b"
    sampling_settings: dict = field(default_factory=lambda: {"guidance": "cfg", "expert_boundary": 0.900})
    latent_format: dict = field(default_factory=lambda: {"latent_channels": 16, "format": "wan21", "spatial_downscale": 8})


def _fake_dit(in_dim=36):
    return SimpleNamespace(
        compute_dtype=torch.float32,
        spec=_FakeSpec(),
        module=SimpleNamespace(patch_size=(1, 2, 2), in_dim=in_dim),
        move_to=lambda d: None,
        offload=lambda: None,
    )


def _bundle(in_dim=36, dual=True, variant="wan22_i2v_14b", loras_high=None, loras_low=None):
    vae = SimpleNamespace(
        compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(encode=lambda px: torch.zeros(1, 16, (px.shape[2] - 1) // 4 + 1, 2, 2)),
    )
    spec = _FakeSpec(variant=variant)
    b = SimpleNamespace(
        high_dit=_fake_dit(in_dim), low_dit=_fake_dit(in_dim) if dual else None,
        vae=vae, is_dual_expert=dual, spec=spec,
        loras_high=loras_high or [], loras_low=loras_low or [],
    )
    b.high_dit.spec = spec
    return b


class _FakeModels:
    """Records (key, fingerprint) and returns a fresh fake DiT WITHOUT invoking
    the loader closure (so no real NativeEngineLoader.load() ever runs)."""

    def __init__(self):
        self.calls = []

    def acquire(self, key, fingerprint, loader, estimated_vram_gb=None):
        self.calls.append((key, fingerprint))
        return _fake_dit(36)


def _segment(seg_id, frames=13, prompt="a", **over):
    seg = {
        "id": seg_id, "prompt": prompt, "negative_prompt": "", "start": None, "end": None,
        "frames": frames, "seed": None, "steps": None, "cfg": None, "loras": None,
    }
    seg.update(over)
    return seg


def _first_media(seg_id, path="/up/start.png"):
    return {"id": f"m-{seg_id}", "role": "first", "segment_id": seg_id, "at": None,
            "strength": 1.0, "media": {"path": path}}


def _document(n_segments=2, frames=13, continuation=None, start_on_seg0=True, media=None):
    """A chain of prompt-only segments. By default a start image is attached to
    seg-0 (so it resolves to i2v and the rest to chain -- the pure-i2v-set
    case). Pass start_on_seg0=False for a t2v opener (needs the t2v set)."""
    segments = [_segment(f"seg-{i}", frames=frames) for i in range(n_segments)]
    if media is None:
        media = [_first_media("seg-0")] if start_on_seg0 else []
    return {
        "schema_version": 1, "mode": "director",
        "settings": {"fps": 16, "duration": None, "resolution": "", "seed": 12345, "continuation": continuation},
        "segments": segments, "media": media, "audio": [], "ic_lora": [],
    }


def _cond(quantity):
    return [SimpleNamespace(embeds={"context": torch.ones(1, 4, 8)}, n_embeds={"context": torch.zeros(1, 4, 8)})
            for _ in range(quantity)]


def _pipe(**over):
    cfg = GeneratorWanChainVideoPipe.get_default_config()
    cfg.update({
        "resolution": "16x16",
        "device": "cpu",
        "t2v_high_noise_model": {"file_path": "/m/wan_t2v_high.safetensors"},
        "t2v_low_noise_model": {"file_path": "/m/wan_t2v_low.safetensors"},
        "i2v_high_noise_model": {"file_path": "/m/wan_i2v_high.safetensors"},
        "i2v_low_noise_model": {"file_path": "/m/wan_i2v_low.safetensors"},
    })
    cfg.update(over)
    return GeneratorWanChainVideoPipe(config=cfg)


def _inputs(model=None, model_t2v=None, conditioning=None, image=None, **extra):
    inp = {"conditioning": conditioning if conditioning is not None else _cond(2)}
    if model is not None:
        inp["model"] = model
    if model_t2v is not None:
        inp["model_t2v"] = model_t2v
    if image is not None:
        inp["image"] = image
    inp.update(extra)
    return PipeInput(input=inp)


def _fake_decode_factory():
    """Deterministic decode fake: frame values encode (segment_index, frame_index)
    so tail-handoff values can be asserted exactly."""
    calls: List[int] = []

    def fake_decode(ctx, latent):
        t_lat = latent.shape[2]
        frames = (t_lat - 1) * 4 + 1
        idx = len(calls)
        calls.append(idx)
        base = (idx + 1) * 10
        arr = np.zeros((frames, 2, 2, 3), dtype=np.uint8)
        for f in range(frames):
            arr[f] = min(255, base + f)
        return arr

    return fake_decode, calls


def _fake_build_i2v_concat_factory():
    captured: List[torch.Tensor] = []
    anchors: List[object] = []
    tail_latents: List[object] = []

    def fake_build(start_frames, vae_encode, *, length, height, width, latents_mean, latents_std,
                    end_frames=None, anchor_frames=None, anchor_strength=1.0, tail_latent=None,
                    device="cpu", dtype=torch.float32):
        captured.append(start_frames.clone())
        anchors.append(None if anchor_frames is None else anchor_frames.clone())
        tail_latents.append(None if tail_latent is None else tail_latent.clone())
        t_lat = (length - 1) // 4 + 1
        return torch.zeros(1, 20, t_lat, height // 8, width // 8, dtype=dtype)

    fake_build.captured_anchors = anchors
    fake_build.captured_tail_latents = tail_latents
    return fake_build, captured


def _patches(fake_decode=None, fake_build=None, denoise_side_effect=None):
    if fake_decode is None:
        fake_decode, _ = _fake_decode_factory()
    if fake_build is None:
        fake_build, _ = _fake_build_i2v_concat_factory()

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        if denoise_side_effect is not None:
            denoise_side_effect(model_forward, latents, cond, uncond, kw)
        return latents

    return (
        patch("src.pipelines.pipes.generator.chain_video_wan22.main.encode_frames_to_mp4",
              lambda frames, path, fps: Path(path).write_bytes(b"fake")),
        patch("src.pipelines.pipes.generator.chain_video_wan22.main.denoise", side_effect=fake_denoise),
        patch("src.pipelines.pipes.generator.chain_video_wan22.main._decode_video", side_effect=fake_decode),
        patch("src.pipelines.pipes.generator.chain_video_wan22.main.build_i2v_concat", side_effect=fake_build),
    )


def _no_stitch(out_fn=lambda paths, overlap, out, fps: out):
    return patch("src.pipelines.pipes.generator.chain_video_wan22.main.stitch_segments", side_effect=out_fn)


# -- per-set checkpoint-arch guards -----------------------------------------

def test_i2v_set_holding_a_t2v_checkpoint_raises():
    # seg-0 has a start image -> i2v sub-type -> the i2v set; but the i2v bundle
    # is a t2v (in_dim=16) checkpoint.
    pipe = _pipe(document=_document(n_segments=1))
    pi = _inputs(model=_bundle(in_dim=16, dual=False), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])
    with pytest.raises(ValueError, match="i2v"):
        pipe.process(pi, lambda o: None)


def test_t2v_set_holding_an_i2v_checkpoint_raises():
    # A prompt-only opener -> t2v sub-type -> the t2v set; but the t2v bundle is
    # an i2v (in_dim=36) checkpoint.
    pipe = _pipe(document=_document(n_segments=1, start_on_seg0=False))
    pi = _inputs(model_t2v=_bundle(in_dim=36), conditioning=_cond(1))
    with pytest.raises(ValueError, match="t2v"):
        pipe.process(pi, lambda o: None)


def test_segment_needing_an_unloaded_set_raises():
    # t2v opener but no t2v bundle was loaded.
    pipe = _pipe(document=_document(n_segments=1, start_on_seg0=False))
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1))
    with pytest.raises(ValueError, match="t2v checkpoint set"):
        pipe.process(pi, lambda o: None)


def test_missing_segments_raises():
    pipe = _pipe(document={"segments": []})
    pi = _inputs(model=_bundle(in_dim=16, dual=False), conditioning=_cond(1))
    with pytest.raises(ValueError, match="segments"):
        pipe.process(pi, lambda o: None)


# -- per-segment sub-type routing -------------------------------------------

def test_t2v_opener_runs_on_t2v_set_without_concat_then_chain_on_i2v_set():
    # seg-0 prompt-only -> t2v (no concat); seg-1 prompt-only continuation ->
    # chain (i2v concat from seg-0's tail). Needs BOTH sets.
    doc = _document(n_segments=2, start_on_seg0=False)
    pipe = _pipe(document=doc)
    t2v_bundle = _bundle(in_dim=16, dual=False, variant="wan22_t2v")
    i2v_bundle = _bundle(in_dim=36, variant="wan22_i2v_14b")
    pi = _inputs(model=i2v_bundle, model_t2v=t2v_bundle, conditioning=_cond(2))

    concats = []

    def record(model_forward, latents, cond, uncond, kw):
        concats.append(model_forward.concat)

    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_build=build, denoise_side_effect=record)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    # seg-0 (t2v): no concat. seg-1 (chain): concat present.
    assert concats[0] is None
    assert concats[1] is not None
    # build_i2v_concat only ran for the chain segment.
    assert len(captured) == 1


def test_i2v_opener_uses_its_own_start_image_rest_continue():
    doc = _document(n_segments=3, start_on_seg0=True)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    # All three segments condition (i2v opener + 2 chain continuations).
    assert len(captured) == 3
    assert captured[0].shape[0] == 1  # seg-0's single uploaded frame


def test_concat_encode_routes_through_the_shared_oom_ladder_not_raw_encode():
    # Bare `mset.vae.module.encode` has no OOM safety net; the segment's
    # concat build must instead go through `make_wan_vae_encode` (the same
    # shrink-on-OOM tiled ladder generator/img2vid_wan22 uses), not the raw
    # unwrapped method.
    doc = _document(n_segments=1, start_on_seg0=True)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])

    sentinel = lambda pixels: torch.zeros(1, 16, 1, 1, 1)
    seen_encode = {}

    def fake_build(start_frames, vae_encode, **kw):
        seen_encode["fn"] = vae_encode
        t_lat = (kw["length"] - 1) // 4 + 1
        return torch.zeros(1, 20, t_lat, kw["height"] // 8, kw["width"] // 8)

    p1, p2, p3, p4 = _patches(fake_build=fake_build)
    with p1, p2, p3, p4, \
         patch("src.pipelines.pipes.generator.chain_video_wan22.main.make_wan_vae_encode",
               return_value=sentinel) as mock_make_encode, \
         _no_stitch():
        pipe.process(pi, lambda o: None)

    mock_make_encode.assert_called_once()
    assert seen_encode["fn"] is sentinel


def test_per_segment_sub_type_override_forces_t2v_mid_chain():
    doc = _document(n_segments=2, start_on_seg0=True)
    doc["segments"][1]["sub_type"] = "t2v"  # hard cut, fresh shot
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), model_t2v=_bundle(in_dim=16, dual=False),
                 conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    concats = []
    p1, p2, p3, p4 = _patches(denoise_side_effect=lambda mf, l, c, u, kw: concats.append(mf.concat))
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert concats[0] is not None  # seg-0 i2v
    assert concats[1] is None      # seg-1 forced t2v -> fresh, no concat


# -- segment loop / gallery emission ----------------------------------------

def test_n_segments_produce_n_plus_stitched_videos():
    doc = _document(n_segments=3, frames=13)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])
    emitted = []
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, _no_stitch(lambda paths, overlap, out, fps: (Path(out).write_bytes(b"stitched"), out)[1]):
        result = pipe.process(pi, lambda o: emitted.append(o))

    assert len(result.output["video"]) == 4  # 3 segments + 1 stitched
    gallery = [o for o in emitted if isinstance(o, GalleryGenerationOutput)]
    assert len(gallery) == 1 and len(gallery[0].videos) == 4
    assert [v.resolution for v in gallery[0].videos] == [(16, 16)] * 4


def test_single_segment_no_stitch_call():
    doc = _document(n_segments=1, frames=13)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, patch("src.pipelines.pipes.generator.chain_video_wan22.main.stitch_segments") as mock_stitch:
        result = pipe.process(pi, lambda o: None)

    mock_stitch.assert_not_called()
    assert len(result.output["video"]) == 1


def test_stitch_disabled_via_continuation_settings():
    doc = _document(n_segments=2, frames=13, continuation={"source": None, "overlap_frames": 4, "stitch": False})
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, patch("src.pipelines.pipes.generator.chain_video_wan22.main.stitch_segments") as mock_stitch:
        result = pipe.process(pi, lambda o: None)

    mock_stitch.assert_not_called()
    assert len(result.output["video"]) == 2


# -- tail handoff -------------------------------------------------------

def test_tail_handoff_defaults_to_one_motion_latent():
    # SVI Pro recipe default (motion_latent_count=1): a single-frame (1 latent
    # slot) hand-off, NOT the full overlap tail.
    doc = _document(n_segments=3, frames=13, continuation={"source": None, "overlap_frames": 4, "stitch": True})
    pipe = _pipe(document=doc)  # motion_latent_count defaults to 1
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    decode, _ = _fake_decode_factory()
    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_decode=decode, fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert len(captured) == 3
    assert captured[0].shape[0] == 1  # seg-0's single uploaded image
    assert captured[1].shape[0] == 1  # seg-1 carries seg-0's LAST frame only
    assert captured[2].shape[0] == 1

    # seg-0's decode fill values are base=10; the single carried frame is the last (index 12).
    expected = torch.tensor([[10 + 12] * 3], dtype=torch.float32) / 255.0
    assert torch.allclose(captured[1][:, 0, 0, :], expected)


def test_high_motion_latent_count_reproduces_full_overlap_tail():
    # A motion_latent_count large enough to cover the whole overlap tail must
    # reproduce the pre-SVI-Pro behavior exactly: the full 4-frame tail hand-off.
    doc = _document(n_segments=3, frames=13, continuation={"source": None, "overlap_frames": 4, "stitch": True})
    pipe = _pipe(document=doc, motion_latent_count=4)  # (4-1)*4+1 = 13 px, capped at the 4-frame tail
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    decode, _ = _fake_decode_factory()
    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_decode=decode, fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert captured[1].shape[0] == 4
    assert captured[2].shape[0] == 4
    # seg-0's decode fill values are base=10, frames 9..12 (last 4 of 0..12).
    expected_tail_0 = torch.tensor([[10 + f] * 3 for f in range(9, 13)], dtype=torch.float32) / 255.0
    assert torch.allclose(captured[1][:, 0, 0, :], expected_tail_0)


def test_motion_latent_count_two_carries_five_pixel_frames():
    # motion_latent_count=2 -> (2-1)*4+1 = 5 pixel frames, when the overlap tail
    # is long enough to supply them.
    doc = _document(n_segments=2, frames=13, continuation={"source": None, "overlap_frames": 8, "stitch": True})
    pipe = _pipe(document=doc, motion_latent_count=2)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert captured[1].shape[0] == 5


def test_anchor_latent_strength_threads_into_concat():
    doc = _document(n_segments=2, frames=13)
    pipe = _pipe(document=doc, anchor_latent_strength=0.75)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    seen = {}

    def fake_build(start_frames, vae_encode, *, length, height, width, latents_mean, latents_std,
                   end_frames=None, anchor_frames=None, anchor_strength=1.0, tail_latent=None,
                   device="cpu", dtype=torch.float32):
        seen["anchor_strength"] = anchor_strength
        t_lat = (length - 1) // 4 + 1
        return torch.zeros(1, 20, t_lat, height // 8, width // 8, dtype=dtype)

    p1, p2, p3, p4 = _patches(fake_build=fake_build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert seen["anchor_strength"] == 0.75


# -- latent-splice seam hand-off (seam_handoff knob) ----------------

def test_seam_handoff_defaults_to_latent_and_threads_prev_sampled_latent():
    # Default config: a chain continuation's tail_latent is the previous
    # segment's own sampled latent (here all-zeros, since the fake denoise
    # returns `latents` unchanged), sliced to motion_latent_count's slot count
    # (1 by default).
    doc = _document(n_segments=2, frames=13)
    pipe = _pipe(document=doc)
    assert pipe.config["seam_handoff"] == "latent"
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    build, _ = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    tails = build.captured_tail_latents
    assert tails[0] is None       # seg-0 (i2v opener): no previous segment
    assert tails[1] is not None   # seg-1 (chain): spliced tail_latent present
    assert tails[1].shape == (1, 16, 1, 2, 2)  # 1 motion_latent_count slot, 16x16->2x2 latent
    assert torch.equal(tails[1], torch.zeros_like(tails[1]))


def test_seam_handoff_pixel_mode_never_threads_tail_latent():
    # The knob's escape hatch: 'pixel' must never pass tail_latent, reproducing
    # today's decode->uint8->re-encode hand-off exactly (concat.py's own
    # test_tail_latent_none_is_byte_identical proves that omission is
    # byte-identical to the prior behavior).
    doc = _document(n_segments=3, frames=13, continuation={"source": None, "overlap_frames": 4, "stitch": True})
    pipe = _pipe(document=doc, seam_handoff="pixel")
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert build.captured_tail_latents == [None, None, None]
    # The pixel-domain start frames are untouched by the knob either way.
    assert captured[1].shape[0] == 1


def test_tail_latent_slot_count_matches_motion_latent_count():
    # motion_latent_count=3 with a generous overlap -> tail_count = 9 px ->
    # tail_latent_slots = (9-1)//4+1 = 3 latent frames spliced.
    doc = _document(n_segments=2, frames=21, continuation={"source": None, "overlap_frames": 12, "stitch": True})
    pipe = _pipe(document=doc, motion_latent_count=3)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    build, _ = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert build.captured_tail_latents[1].shape[2] == 3


def test_tail_latent_carries_this_segments_own_sampled_latent_not_stale():
    # Each chain segment's tail_latent must be THIS run's own just-sampled
    # latent tail (captured before decode) -- not the previous run's, and not
    # some earlier segment's -- mirroring the existing pixel prev_tail
    # regression tests' per-segment fill-value technique.
    doc = _document(n_segments=3, frames=13, continuation={"source": None, "overlap_frames": 4, "stitch": True})
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        idx = fake_denoise.calls
        fake_denoise.calls += 1
        return torch.full_like(latents, float((idx + 1) * 100))
    fake_denoise.calls = 0

    build, _ = _fake_build_i2v_concat_factory()
    decode, _ = _fake_decode_factory()
    with patch("src.pipelines.pipes.generator.chain_video_wan22.main.encode_frames_to_mp4",
               lambda frames, path, fps: Path(path).write_bytes(b"fake")), \
         patch("src.pipelines.pipes.generator.chain_video_wan22.main.denoise", side_effect=fake_denoise), \
         patch("src.pipelines.pipes.generator.chain_video_wan22.main._decode_video", side_effect=decode), \
         patch("src.pipelines.pipes.generator.chain_video_wan22.main.build_i2v_concat", side_effect=build), \
         _no_stitch():
        pipe.process(pi, lambda o: None)

    tails = build.captured_tail_latents
    # seg-1's tail_latent must be seg-0's OWN sampled latent fill (100), not
    # seg-1's or seg-2's.
    assert torch.equal(tails[1], torch.full_like(tails[1], 100.0))
    # seg-2's tail_latent must be seg-1's OWN sampled latent fill (200).
    assert torch.equal(tails[2], torch.full_like(tails[2], 200.0))


def test_continuation_segments_lock_onto_previous_tail_not_original_anchor():
    # A chain continuation must lock onto the PREVIOUS segment's own tail
    # (rolling hand-off) -- never a persistent anchor pinned to the chain's
    # original first frame. That original-anchor scheme is what caused every
    # continuation shot to visually reset to the first image.
    doc = _document(n_segments=3, frames=13, continuation={"source": None, "overlap_frames": 4, "stitch": True})
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    decode, _ = _fake_decode_factory()
    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_decode=decode, fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    # No separate anchor is ever threaded -- neither for the i2v opener nor for
    # any chain continuation; the previous segment's tail IS the start.
    assert build.captured_anchors == [None, None, None]

    # seg-0's decode fill values are base=10 (frames 0..12); seg-1 must lock
    # onto seg-0's LAST frame (value 22), not seg-0's FIRST frame (value 10).
    expected_seg0_tail = torch.tensor([[10 + 12] * 3], dtype=torch.float32) / 255.0
    original_first_frame = torch.tensor([[10 + 0] * 3], dtype=torch.float32) / 255.0
    assert torch.allclose(captured[1][:, 0, 0, :], expected_seg0_tail)
    assert not torch.allclose(captured[1][:, 0, 0, :], original_first_frame)

    # seg-1's decode fill values are base=20; seg-2 must roll forward onto
    # seg-1's own tail (value 32), not fall back to seg-0's original frame.
    expected_seg1_tail = torch.tensor([[20 + 12] * 3], dtype=torch.float32) / 255.0
    assert torch.allclose(captured[2][:, 0, 0, :], expected_seg1_tail)
    assert not torch.allclose(captured[2][:, 0, 0, :], original_first_frame)


def _capture_emitted_frame_counts():
    """Patch encode_frames_to_mp4 to record each emitted clip's frame count."""
    counts: List[int] = []

    def fake_encode(frames, path, fps):
        counts.append(int(frames.shape[0]))
        Path(path).write_bytes(b"fake")

    return counts, patch(
        "src.pipelines.pipes.generator.chain_video_wan22.main.encode_frames_to_mp4",
        side_effect=fake_encode,
    )


def test_continuation_output_trims_the_locked_handoff_overlap():
    # A continuation's emitted clip drops the leading context prefix -- now the
    # number of pixel frames actually locked at the front, i.e. tail_count
    # (the previous segment's hand-off tail), not a separate anchor+motion
    # formula. Default motion_latent_count=1, overlap_frames=4 -> tail_count =
    # min(4, 1) = 1; a 13-frame window emits 12. The opener is never trimmed.
    doc = _document(n_segments=3, frames=13, continuation={"source": None, "overlap_frames": 4, "stitch": True})
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    decode, _ = _fake_decode_factory()
    build, _ = _fake_build_i2v_concat_factory()
    counts, enc_patch = _capture_emitted_frame_counts()
    _, p2, p3, p4 = _patches(fake_decode=decode, fake_build=build)
    with enc_patch, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert counts[0] == 13     # opener: full window
    assert counts[1] == 12     # continuation: 13 - 1 locked hand-off frame
    assert counts[2] == 12


def test_context_prefix_scales_with_motion_latent_count():
    # motion_latent_count=2 -> motion_frames = 4*2+1 = 5 caps overlap_frames=8,
    # so tail_count = 5; a 21-frame window emits 16.
    doc = _document(n_segments=2, frames=21, continuation={"source": None, "overlap_frames": 8, "stitch": True})
    pipe = _pipe(document=doc, motion_latent_count=2)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    decode, _ = _fake_decode_factory()
    build, _ = _fake_build_i2v_concat_factory()
    counts, enc_patch = _capture_emitted_frame_counts()
    _, p2, p3, p4 = _patches(fake_decode=decode, fake_build=build)
    with enc_patch, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert counts[0] == 21
    assert counts[1] == 16     # 21 - 5


def test_prev_tail_comes_from_trimmed_real_content():
    # The tail handed to segment N+1 must be the trimmed window's real tail, not
    # the discarded anchor/context region.
    doc = _document(n_segments=3, frames=13, continuation={"source": None, "overlap_frames": 4, "stitch": True})
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    decode, _ = _fake_decode_factory()
    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_decode=decode, fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    # seg-1 (chain) decode base=20, frames 0..12; trimmed to 5..12; its real tail
    # frame (index 12, value 32) seeds seg-2 -- NOT a frame from the trimmed prefix.
    expected = torch.tensor([[20 + 12] * 3], dtype=torch.float32) / 255.0
    assert torch.allclose(captured[2][:, 0, 0, :], expected)


def test_trimmed_continuations_stitch_with_zero_overlap():
    doc = _document(n_segments=3, frames=13, continuation={"source": None, "overlap_frames": 4, "stitch": True})
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, _no_stitch() as mock_stitch:
        pipe.process(pi, lambda o: None)

    assert mock_stitch.call_args.args[1] == 0


def test_untrimmable_segment_keeps_full_output_and_overlap():
    # When the context prefix would leave <2 frames, trimming is skipped and the
    # stitcher falls back to the overlap-drop path (guard against over-trimming a
    # short segment). overlap_frames=12 with motion_latent_count=4 (motion_frames
    # =13 >= 12) -> tail_count = 12; prefix+1 == 13 == frame count -> no trim.
    doc = _document(n_segments=2, frames=13, continuation={"source": None, "overlap_frames": 12, "stitch": True})
    pipe = _pipe(document=doc, motion_latent_count=4)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    decode, _ = _fake_decode_factory()
    build, _ = _fake_build_i2v_concat_factory()
    counts, enc_patch = _capture_emitted_frame_counts()
    _, p2, p3, p4 = _patches(fake_decode=decode, fake_build=build)
    with enc_patch, p2, p3, p4, _no_stitch() as mock_stitch:
        pipe.process(pi, lambda o: None)

    assert counts[1] == 13                         # not trimmed
    assert mock_stitch.call_args.args[1] == 12     # overlap-drop retained


def test_chain_after_t2v_opener_locks_onto_openers_tail_not_first_frame():
    # A t2v opener has no uploaded image; the following chain segment must lock
    # onto the opener's own generated TAIL, not persist a separate anchor
    # pinned to the opener's first generated frame.
    doc = _document(n_segments=2, start_on_seg0=False)
    pipe = _pipe(document=doc)
    t2v_bundle = _bundle(in_dim=16, dual=False, variant="wan22_t2v")
    i2v_bundle = _bundle(in_dim=36, variant="wan22_i2v_14b")
    pi = _inputs(model=i2v_bundle, model_t2v=t2v_bundle, conditioning=_cond(2))

    decode, _ = _fake_decode_factory()
    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_decode=decode, fake_build=build)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    # Only the chain segment conditions (t2v opener has no concat); no separate
    # anchor is threaded.
    assert len(build.captured_anchors) == 1
    assert build.captured_anchors[0] is None

    # seg-0's decode fill values are base=10 (frames 0..12); the chain segment's
    # start must be seg-0's LAST frame (value 22), not its first (value 10).
    expected_tail = torch.tensor([[10 + 12] * 3], dtype=torch.float32) / 255.0
    original_first_frame = torch.tensor([[10 + 0] * 3], dtype=torch.float32) / 255.0
    assert torch.allclose(captured[0][:, 0, 0, :], expected_tail)
    assert not torch.allclose(captured[0][:, 0, 0, :], original_first_frame)


def test_last_frame_continuation_source_uses_single_frame_overlap():
    doc = _document(n_segments=2, frames=13, continuation={"source": "last_frame", "overlap_frames": 4, "stitch": True})
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    build, captured = _fake_build_i2v_concat_factory()
    p1, p2, p3, p4 = _patches(fake_build=build)
    with p1, p2, p3, p4, _no_stitch() as mock_stitch:
        pipe.process(pi, lambda o: None)

    assert captured[1].shape[0] == 1  # 'last_frame' source caps the motion tail to 1
    # The continuation is context-trimmed, so it no longer reproduces the tail:
    # the stitcher concatenates with zero overlap.
    assert mock_stitch.call_args.args[1] == 0


# -- LoRA patch / unpatch ---------------------------------------------------

def test_lora_reacquire_only_on_stack_change():
    doc = _document(n_segments=4, frames=13)
    doc["segments"][0]["loras"] = None  # base i2v experts, no acquire
    doc["segments"][1]["loras"] = {"high": [{"model": "/l/motion.safetensors", "strength": 0.8}], "low": []}
    doc["segments"][2]["loras"] = {"high": [{"model": "/l/motion.safetensors", "strength": 0.8}], "low": []}  # same -> no reacquire
    doc["segments"][3]["loras"] = {"high": [], "low": []}  # different (empty) -> reacquire "none"

    pipe = _pipe(document=doc)
    models = _FakeModels()
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(4), image=[torch.rand(8, 8, 3)], MODELS=models)

    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    high_calls = [c for c in models.calls if c[0] == "native/dit//m/wan_i2v_high.safetensors"]
    # seg0 base (no call), seg1 acquire, seg2 same fp (no call), seg3 acquire.
    assert len(high_calls) == 2
    assert "motion.safetensors@0.8" in high_calls[0][1]
    assert high_calls[1][1].endswith("|none")


def test_lora_unpatch_restores_base_experts_without_acquire():
    # A LoRA'd segment followed by a plain one must swap the experts back to the
    # set's base bundle (NOT re-acquire, NOT leak the LoRA).
    doc = _document(n_segments=2, frames=13)
    doc["segments"][0]["loras"] = {"high": [{"model": "/l/x.safetensors", "strength": 0.9}], "low": []}
    doc["segments"][1]["loras"] = None  # plain -> back to base

    pipe = _pipe(document=doc)
    models = _FakeModels()
    bundle = _bundle(in_dim=36)
    pi = _inputs(model=bundle, conditioning=_cond(2), image=[torch.rand(8, 8, 3)], MODELS=models)

    routers = []
    p1, p2, p3, p4 = _patches(denoise_side_effect=lambda mf, l, c, u, kw: routers.append(mf.router.high))
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    # seg-0 used an acquired (LoRA'd) expert; seg-1 restored the base bundle's.
    assert routers[1] is bundle.high_dit
    assert routers[0] is not bundle.high_dit
    # Exactly one acquire (seg-0); the unpatch to base does NOT acquire.
    assert len([c for c in models.calls if c[0] == "native/dit//m/wan_i2v_high.safetensors"]) == 1


def test_lora_reacquire_requires_dit_path_when_used():
    doc = _document(n_segments=1, frames=13)
    doc["segments"][0]["loras"] = {"high": [{"model": "/l/x.safetensors", "strength": 1.0}], "low": []}
    pipe = _pipe(document=doc, i2v_high_noise_model=None)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])
    with pytest.raises(ValueError, match="high_noise_model"):
        pipe.process(pi, lambda o: None)


# -- per-segment LoRA override composes onto (never replaces) base ---

def test_base_only_no_override_unchanged():
    # No segment ever sets a LoRA override -- the base bundle's own stack (a
    # non-empty preset-level stack here) is used throughout, no acquire calls.
    doc = _document(n_segments=2, frames=13)
    pipe = _pipe(document=doc)
    models = _FakeModels()
    bundle = _bundle(in_dim=36, loras_high=[{"model": "/l/lightning_high.safetensors", "strength": 1.0}])
    pi = _inputs(model=bundle, conditioning=_cond(2), image=[torch.rand(8, 8, 3)], MODELS=models)

    routers = []
    p1, p2, p3, p4 = _patches(denoise_side_effect=lambda mf, l, c, u, kw: routers.append(mf.router.high))
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert routers[0] is bundle.high_dit and routers[1] is bundle.high_dit
    assert len([c for c in models.calls if c[0] == "native/dit//m/wan_i2v_high.safetensors"]) == 0


def test_segment_override_composes_onto_base_stack():
    # The Fast profile's Lightning speed pair is the base "loras_high" stack;
    # a segment layers a motion LoRA on top -- both must reach the DiT acquire.
    doc = _document(n_segments=1, frames=13)
    doc["segments"][0]["loras"] = {"high": [{"model": "/l/motion.safetensors", "strength": 0.8}], "low": []}
    pipe = _pipe(document=doc)
    models = _FakeModels()
    bundle = _bundle(in_dim=36, loras_high=[{"model": "/l/lightning_high.safetensors", "strength": 1.0}])
    pi = _inputs(model=bundle, conditioning=_cond(1), image=[torch.rand(8, 8, 3)], MODELS=models)

    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    high_calls = [c for c in models.calls if c[0] == "native/dit//m/wan_i2v_high.safetensors"]
    assert len(high_calls) == 1
    fp = high_calls[0][1]
    assert "lightning_high.safetensors@1.0" in fp
    assert "motion.safetensors@0.8" in fp


def test_segment_override_same_file_segment_weight_wins():
    # An override entry sharing a file_path with a base entry replaces that
    # base entry's weight -- it does not stack alongside it.
    doc = _document(n_segments=1, frames=13)
    doc["segments"][0]["loras"] = {"high": [{"model": "/l/lightning_high.safetensors", "strength": 0.4}], "low": []}
    pipe = _pipe(document=doc)
    models = _FakeModels()
    bundle = _bundle(in_dim=36, loras_high=[{"model": "/l/lightning_high.safetensors", "strength": 1.0}])
    pi = _inputs(model=bundle, conditioning=_cond(1), image=[torch.rand(8, 8, 3)], MODELS=models)

    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    fp = next(c for c in models.calls if c[0] == "native/dit//m/wan_i2v_high.safetensors")[1]
    assert fp.endswith("lightning_high.safetensors@0.4")  # segment's weight, not the base's 1.0
    assert fp.count("lightning_high.safetensors@") == 1   # not duplicated


def test_segment_empty_list_override_keeps_base_stack():
    # An explicit [] for an expert is "no ADDITIONAL segment LoRAs", not "wipe
    # the base stack" -- the composed stack must still be exactly the base one.
    doc = _document(n_segments=1, frames=13)
    doc["segments"][0]["loras"] = {"high": [], "low": []}
    pipe = _pipe(document=doc)
    models = _FakeModels()
    bundle = _bundle(in_dim=36, loras_high=[{"model": "/l/lightning_high.safetensors", "strength": 1.0}])
    pi = _inputs(model=bundle, conditioning=_cond(1), image=[torch.rand(8, 8, 3)], MODELS=models)

    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    fp = next(c for c in models.calls if c[0] == "native/dit//m/wan_i2v_high.safetensors")[1]
    assert fp.endswith("lightning_high.safetensors@1.0")
    assert "none" not in fp.rsplit("|", 1)[-1]


def test_fingerprint_reflects_composed_stack_not_override_alone():
    # Two different BASE stacks with the IDENTICAL segment override must
    # acquire under DIFFERENT fingerprints -- otherwise the model-lifecycle
    # cache would return one preset's composed DiT for the other's request
    # (stale reuse across genuinely different composed stacks).
    doc = _document(n_segments=1, frames=13)
    doc["segments"][0]["loras"] = {"high": [{"model": "/l/motion.safetensors", "strength": 0.8}], "low": []}

    def run_with_base(base_lora_path):
        pipe = _pipe(document=doc)
        models = _FakeModels()
        bundle = _bundle(in_dim=36, loras_high=[{"model": base_lora_path, "strength": 1.0}])
        pi = _inputs(model=bundle, conditioning=_cond(1), image=[torch.rand(8, 8, 3)], MODELS=models)
        p1, p2, p3, p4 = _patches()
        with p1, p2, p3, p4, _no_stitch():
            pipe.process(pi, lambda o: None)
        return next(c for c in models.calls if c[0] == "native/dit//m/wan_i2v_high.safetensors")[1]

    fp_a = run_with_base("/l/lightning_high.safetensors")
    fp_b = run_with_base("/l/other_base.safetensors")
    assert fp_a != fp_b


# -- seed policy ----------------------------------------------------------

def test_seed_policy_base_plus_index_unless_pinned():
    doc = _document(n_segments=3, frames=13)
    doc["settings"]["seed"] = 500
    doc["segments"][1]["seed"] = 999  # pinned override
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(3), image=[torch.rand(8, 8, 3)])

    emitted = []
    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: emitted.append(o))

    seg_seed_param = next(o for o in emitted if isinstance(o, ParamGenerationOutput) and o.name == "segment_seed")
    assert seg_seed_param.values == [500, 999, 502]
    base_seed_param = next(o for o in emitted if isinstance(o, ParamGenerationOutput) and o.name == "seed")
    assert base_seed_param.values == [500]


# -- per-segment steps/cfg overrides ---------------------------------------

def test_per_segment_steps_and_cfg_reach_denoise():
    doc = _document(n_segments=2, frames=13)
    doc["segments"][1]["steps"] = 7
    doc["segments"][1]["cfg"] = 2.5
    pipe = _pipe(document=doc, steps=30, cfg=5.0)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    seen_calls = []

    def record(model_forward, latents, cond, uncond, kw):
        seen_calls.append((kw.get("steps"), kw.get("guidance_scale")))

    p1, p2, p3, p4 = _patches(denoise_side_effect=record)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert seen_calls[0] == (30, 5.0)
    assert seen_calls[1] == (7, 2.5)


# -- decode numerics propagation ----------------------------

def test_nan_decode_raises_at_its_own_segment_not_the_next_ones_sampler():
    # A NaN in segment 0's decode must fail LOUDLY at segment 0's own decode
    # (DecodeNumericsError) -- not get silently clamped to a black tail frame
    # (uint8-cast-from-NaN -> 0, see pixels_3thw_to_uint8_frames) and then
    # resurface several sampling steps into segment 1, misattributed to
    # segment 1's sampler instead of segment 0's decode (its true origin).
    doc = _document(n_segments=2, frames=13)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    def nan_on_first_call(ctx, latent):
        raise DecodeNumericsError()

    p1, p2, p3, p4 = _patches(fake_decode=nan_on_first_call)
    with p1, p2, p3, p4, _no_stitch():
        with pytest.raises(DecodeNumericsError):
            pipe.process(pi, lambda o: None)


def test_sampling_numerics_error_annotated_with_failing_segment():
    # A SamplingNumericsError from denoise() must be attributed to the
    # segment that actually raised it -- a watchdog trip a few steps into
    # segment 1's OWN sampling must never read as segment 0's problem.
    doc = _document(n_segments=2, frames=13)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    calls = {"n": 0}

    def raise_on_second_denoise(model_forward, latents, cond, uncond, kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise SamplingNumericsError(3, "unipc", "sage2")

    p1, p2, p3, p4 = _patches(denoise_side_effect=raise_on_second_denoise)
    with p1, p2, p3, p4, _no_stitch():
        with pytest.raises(SamplingNumericsError) as exc:
            pipe.process(pi, lambda o: None)

    assert exc.value.segment_index == 1
    assert exc.value.segment_label is not None
    assert "segment 1" in str(exc.value)


# -- expert-switch boundary vs per-segment steps -----------------------------

def test_expert_boundary_rebinds_to_each_segments_own_steps():
    # expert_switch_step=2 converts to a DIFFERENT sigma depending on the
    # step count of the schedule it's read against. seg-0 runs at the
    # pipe-level default (30); seg-1 overrides to a distilled 4-step count
    # (this must NOT reuse seg-0's 30-step-derived boundary, or the
    # router picks the wrong expert for most of a short distilled run).
    doc = _document(n_segments=2, frames=13)
    doc["segments"][1]["steps"] = 4
    pipe = _pipe(document=doc, steps=30, expert_switch_step=2)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    boundaries = []
    p1, p2, p3, p4 = _patches(
        denoise_side_effect=lambda mf, l, c, u, kw: boundaries.append(mf.router.boundary)
    )
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert len(boundaries) == 2
    assert boundaries[0] != boundaries[1]
    # Recomputed directly for cross-check: each segment's boundary must match
    # ITS OWN step count's schedule, not the other segment's.
    from src.pipelines.pipes.generator.txt2vid_wan22.main import resolve_expert_boundary
    spec = _bundle(in_dim=36).spec
    sampling_settings = spec.sampling_settings
    expected_30 = resolve_expert_boundary(spec, {"expert_switch_step": 2}, sampling_settings, 30)
    expected_4 = resolve_expert_boundary(spec, {"expert_switch_step": 2}, sampling_settings, 4)
    assert boundaries[0] == pytest.approx(expected_30)
    assert boundaries[1] == pytest.approx(expected_4)


# -- riflex / APG / SLG settings threading -----------------------------------

def test_riflex_default_off_sampling_settings_unaffected():
    doc = _document(n_segments=1, frames=13)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])
    seen = {}

    def record(model_forward, latents, cond, uncond, kw):
        seen["sampling_settings"] = kw.get("sampling_settings")

    p1, p2, p3, p4 = _patches(denoise_side_effect=record)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    ss = seen["sampling_settings"]
    for key in ("apg_eta", "apg_norm_threshold", "apg_momentum", "slg_scale", "slg_layers"):
        assert key not in ss
    assert ss["guidance"] == "cfg"  # base spec key survives the merge


def test_apg_slg_config_overrides_thread_into_sampling_settings():
    doc = _document(n_segments=1, frames=13)
    pipe = _pipe(
        document=doc, apg_eta=0.4, apg_norm_threshold=0.6, apg_momentum=-0.3,
        slg_scale=2.0, slg_layers="4,9", slg_sigma_start=0.7, slg_sigma_end=0.3,
    )
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])
    seen = {}

    def record(model_forward, latents, cond, uncond, kw):
        seen["sampling_settings"] = kw.get("sampling_settings")

    p1, p2, p3, p4 = _patches(denoise_side_effect=record)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    ss = seen["sampling_settings"]
    assert ss["apg_eta"] == 0.4
    assert ss["slg_scale"] == 2.0
    assert ss["slg_layers"] == {4, 9}
    assert ss["slg_sigma_start"] == 0.7
    assert ss["slg_sigma_end"] == 0.3


def test_schedule_settings_config_overrides_thread_into_sampling_settings():
    doc = _document(n_segments=1, frames=13)
    pipe = _pipe(document=doc, schedule="beta", detail_strength=0.15, detail_end=0.8)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])
    seen = {}

    def record(model_forward, latents, cond, uncond, kw):
        seen["sampling_settings"] = kw.get("sampling_settings")

    p1, p2, p3, p4 = _patches(denoise_side_effect=record)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    ss = seen["sampling_settings"]
    assert ss["schedule"] == "beta"
    assert ss["detail_strength"] == 0.15
    assert ss["detail_end"] == 0.8


def test_riflex_enabled_thread_into_router():
    doc = _document(n_segments=1, frames=13)
    pipe = _pipe(document=doc, riflex=True, riflex_trained_frames=6)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])

    seen = {}

    def record(model_forward, latents, cond, uncond, kw):
        seen["router"] = model_forward.router

    p1, p2, p3, p4 = _patches(denoise_side_effect=record)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert seen["router"].riflex == {"enabled": True, "latent_frames_trained": 6}


# -- sampler_options / step_cache ---------------------------------------------

def test_sampler_options_step_cache_absent_reach_denoise_as_none():
    doc = _document(n_segments=1, frames=13)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])
    seen = {}

    def record(model_forward, latents, cond, uncond, kw):
        seen["kw"] = kw

    p1, p2, p3, p4 = _patches(denoise_side_effect=record)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert seen["kw"]["sampler_options"] is None
    assert seen["kw"]["step_cache_options"] is None


def test_sampler_options_step_cache_present_reach_denoise():
    doc = _document(n_segments=1, frames=13)
    sampler_opts = {"eta": 0.7}
    step_cache_opts = {"rel_threshold": 0.15, "max_consecutive_skips": 2}
    pipe = _pipe(document=doc, sampler_options=sampler_opts, step_cache=step_cache_opts)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(1), image=[torch.rand(8, 8, 3)])
    seen = {}

    def record(model_forward, latents, cond, uncond, kw):
        seen["kw"] = kw

    p1, p2, p3, p4 = _patches(denoise_side_effect=record)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None)

    assert seen["kw"]["sampler_options"] == sampler_opts
    assert seen["kw"]["step_cache_options"] == step_cache_opts


# -- cancellation -----------------------------------------------------------

def test_cancellation_after_segment_k_stops_early_no_stitch():
    doc = _document(n_segments=4, frames=13)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(4), image=[torch.rand(8, 8, 3)])

    calls = {"n": 0}

    def is_cancelled():
        calls["n"] += 1
        return calls["n"] > 2  # allow 2 segments through, cancel before the 3rd

    p1, p2, p3, p4 = _patches()
    with p1, p2, p3, p4, patch("src.pipelines.pipes.generator.chain_video_wan22.main.stitch_segments") as mock_stitch:
        result = pipe.process(pi, lambda o: None, is_cancelled=is_cancelled)

    mock_stitch.assert_not_called()
    assert len(result.output["video"]) == 2


def test_denoise_receives_the_managers_is_cancelled_probe_mid_segment():
    # A cancel observed mid-segment (i.e. inside denoise()'s own step loop,
    # not just between segments) must abort promptly -- this only works if
    # the segment loop's denoise() call is actually handed the manager's
    # is_cancelled probe.
    doc = _document(n_segments=2, frames=13)
    pipe = _pipe(document=doc)
    pi = _inputs(model=_bundle(in_dim=36), conditioning=_cond(2), image=[torch.rand(8, 8, 3)])

    probe = lambda: False
    captured = {}

    def capture(model_forward, latents, cond, uncond, kw):
        captured["is_cancelled"] = kw.get("is_cancelled")

    p1, p2, p3, p4 = _patches(denoise_side_effect=capture)
    with p1, p2, p3, p4, _no_stitch():
        pipe.process(pi, lambda o: None, is_cancelled=probe)

    assert captured["is_cancelled"] is probe


# -- metadata -----------------------------------------------------------

def test_metadata_has_both_model_sets_conditioning_and_video_output():
    inputs = {i.name: i for i in GeneratorWanChainVideoPipe.inputs()}
    # Both model bundles are OPTIONAL now (a chain may need only one set).
    assert inputs["model"].io_type == IOType.MODEL and not inputs["model"].required
    assert inputs["model_t2v"].io_type == IOType.MODEL and not inputs["model_t2v"].required
    assert inputs["conditioning"].io_type == IOType.CONDITIONING and inputs["conditioning"].is_array
    assert inputs["image"].io_type == IOType.IMAGE and not inputs["image"].required
    assert inputs["end_image"].io_type == IOType.IMAGE and not inputs["end_image"].required
    assert GeneratorWanChainVideoPipe.outputs()[0].io_type == IOType.VIDEO


def test_sampler_choices():
    spec = next(s for s in GeneratorWanChainVideoPipe.configuration() if s.name == "sampler")
    assert set(spec.choices) == {
        "euler", "dpmpp_2m", "dpmpp_2m_sde", "dpmpp_3m", "res_multistep", "unipc", "lcm",
    }
