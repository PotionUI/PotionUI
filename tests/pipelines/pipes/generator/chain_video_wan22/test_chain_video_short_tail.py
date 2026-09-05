"""Regression tests for the available-tail clamp (DIR-07 rework 1): a chain
continuation must never assume it received more of the previous segment's
tail than that segment actually emitted -- a short opener (e.g. a 1-frame
`t2v` shot) leaves less real context than `tail_count` alone would assume,
and the pre-decode trim, the stitch-time join fallback, and the latent seam
splice must all agree on the clamped amount.

Self-contained (duplicates the small fixture subset it needs from
test_chain_video_generator.py) rather than importing that module, which
another lane is concurrently editing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List
from unittest.mock import patch

import numpy as np
import torch

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.generator.chain_video_wan22.main import GeneratorWanChainVideoPipe


# -- fakes (mirrors test_chain_video_generator.py's subset used here) -------

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


def _bundle(in_dim=36, dual=True, variant="wan22_i2v_14b"):
    vae = SimpleNamespace(
        compute_dtype=torch.float32, move_to=lambda d: None, offload=lambda: None,
        module=SimpleNamespace(encode=lambda px: torch.zeros(1, 16, (px.shape[2] - 1) // 4 + 1, 2, 2)),
    )
    spec = _FakeSpec(variant=variant)
    b = SimpleNamespace(
        high_dit=_fake_dit(in_dim), low_dit=_fake_dit(in_dim) if dual else None,
        vae=vae, is_dual_expert=dual, spec=spec, loras_high=[], loras_low=[],
    )
    b.high_dit.spec = spec
    return b


def _segment(seg_id, frames=13, prompt="a", **over):
    seg = {
        "id": seg_id, "prompt": prompt, "negative_prompt": "", "start": None, "end": None,
        "frames": frames, "seed": None, "steps": None, "cfg": None, "loras": None,
    }
    seg.update(over)
    return seg


def _document(n_segments, frames, continuation=None):
    """A prompt-only chain (no media -- segment 0 is always a fresh t2v
    opener). `frames` may be an int (applied to every segment) or a
    per-segment list."""
    if isinstance(frames, int):
        frames = [frames] * n_segments
    segments = [_segment(f"seg-{i}", frames=frames[i]) for i in range(n_segments)]
    return {
        "schema_version": 1, "mode": "director",
        "settings": {"fps": 16, "duration": None, "resolution": "", "seed": 12345, "continuation": continuation},
        "segments": segments, "media": [], "audio": [], "ic_lora": [],
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


def _inputs(model=None, model_t2v=None, conditioning=None, **extra):
    inp = {"conditioning": conditioning if conditioning is not None else _cond(2)}
    if model is not None:
        inp["model"] = model
    if model_t2v is not None:
        inp["model_t2v"] = model_t2v
    inp.update(extra)
    return PipeInput(input=inp)


def _fake_decode_factory():
    """Deterministic decode fake: frame values encode (segment_index,
    frame_index) so boundary pixels can be asserted exactly."""
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

    def fake_build(start_frames, vae_encode, *, length, height, width, latents_mean, latents_std,
                    end_frames=None, anchor_frames=None, anchor_strength=1.0, tail_latent=None,
                    device="cpu", dtype=torch.float32):
        captured.append(start_frames.clone())
        t_lat = (length - 1) // 4 + 1
        return torch.zeros(1, 20, t_lat, height // 8, width // 8, dtype=dtype)

    return fake_build, captured


def _patches(fake_decode=None, fake_build=None):
    if fake_decode is None:
        fake_decode, _ = _fake_decode_factory()
    if fake_build is None:
        fake_build, _ = _fake_build_i2v_concat_factory()

    def fake_denoise(model_forward, latents, cond, uncond, **kw):
        return latents

    return (
        patch("src.pipelines.pipes.generator.chain_video_wan22.main.denoise", side_effect=fake_denoise),
        patch("src.pipelines.pipes.generator.chain_video_wan22.main._decode_video", side_effect=fake_decode),
        patch("src.pipelines.pipes.generator.chain_video_wan22.main.build_i2v_concat", side_effect=fake_build),
    )


def _stitch_with_recorded_frames():
    """A stitch_segments side_effect that runs the REAL crossfade/concat
    algorithm (stitch.py) against what the generator actually emitted, off
    of in-memory frame arrays keyed by each segment's temp path (no real
    ffmpeg/cv2; ffmpeg's stream-copy path is forced off)."""
    frames_by_path: Dict[str, np.ndarray] = {}

    def fake_encode(frames, path, fps):
        frames_by_path[str(path)] = frames

    def real_stitch_side_effect(paths, overlaps, out_path, fps):
        from src.pipelines.pipes.generator.chain_video_wan22.stitch import stitch_segments as real_stitch
        with patch("src.pipelines.pipes.generator.chain_video_wan22.stitch.shutil.which", return_value=None):
            return real_stitch(
                [str(p) for p in paths], overlaps, out_path, fps,
                frame_reader=lambda p: iter(frames_by_path[str(p)]),
                encode=lambda frames, out, fps: frames_by_path.__setitem__("__stitched__", frames),
            )

    return frames_by_path, fake_encode, real_stitch_side_effect


# -- short opener (available-tail clamp) -------------------------------------

def test_short_opener_continuation_clamps_context_to_what_was_actually_emitted():
    # seg-0: a 1-frame t2v opener -- only ONE real frame of tail exists to
    # hand off, far less than tail_count (12, from overlap_frames=12 /
    # motion_latent_count=4). seg-1: a 13-frame chain continuation. Before
    # the available-tail clamp, main.py assumed context_prefix=tail_count=12
    # regardless of what seg-0 actually produced: the trim guard saw
    # 13 > 12 + 1 as FALSE (no pre-decode trim), so the untrimmed join
    # fallback planned to drop 12 frames at stitch time against only the 1
    # frame seg-0 actually contributed -- crossfading 1 accumulated frame
    # against a 12-frame head and discarding the rest (2 frames total instead
    # of 13, 11 real frames of new content silently lost).
    doc = _document(n_segments=2, frames=[1, 13],
                     continuation={"source": None, "overlap_frames": 12, "stitch": True})
    pipe = _pipe(document=doc, motion_latent_count=4)
    pi = _inputs(model=_bundle(in_dim=36), model_t2v=_bundle(in_dim=16, dual=False), conditioning=_cond(2))

    frames_by_path, fake_encode, real_stitch = _stitch_with_recorded_frames()
    p2, p3, p4 = _patches()
    with patch("src.pipelines.pipes.generator.chain_video_wan22.main.encode_frames_to_mp4", side_effect=fake_encode), \
         p2, p3, p4, \
         patch("src.pipelines.pipes.generator.chain_video_wan22.main.stitch_segments", side_effect=real_stitch):
        pipe.process(pi, lambda o: None)

    stitched = frames_by_path["__stitched__"]
    # seg-0: base=10, 1 frame (value 10). seg-1: base=20, 13 frames
    # (20..32). context_frames = min(12, 1) = 1 -> the trim guard now sees
    # 13 > 1 + 1 = 2 (True): seg-1 is pre-decode trimmed by 1, not 12,
    # emitting 12 frames (21..32); its join is then 0 (already trimmed).
    # Total: 1 + 12 = 13, no content silently discarded.
    assert stitched.shape[0] == 13
    assert stitched[0, 0, 0, 0] == 10   # seg-0's only frame, untouched
    assert stitched[1, 0, 0, 0] == 21   # seg-1's first frame AFTER its 1-frame trim
    assert stitched[12, 0, 0, 0] == 32  # seg-1's own last frame


def test_short_opener_before_a_fresh_cut_is_unaffected_control():
    # Same 1-frame opener, but seg-1 is a forced FRESH cut (not a
    # continuation): the short opener must have no bearing on this join at
    # all -- it stays 0 regardless of how little tail the opener produced.
    doc = _document(n_segments=2, frames=[1, 13],
                     continuation={"source": None, "overlap_frames": 12, "stitch": True})
    doc["segments"][1]["sub_type"] = "t2v"
    pipe = _pipe(document=doc, motion_latent_count=4)
    pi = _inputs(model_t2v=_bundle(in_dim=16, dual=False), conditioning=_cond(2))

    frames_by_path, fake_encode, real_stitch = _stitch_with_recorded_frames()
    p2, p3, p4 = _patches()
    with patch("src.pipelines.pipes.generator.chain_video_wan22.main.encode_frames_to_mp4", side_effect=fake_encode), \
         p2, p3, p4, \
         patch("src.pipelines.pipes.generator.chain_video_wan22.main.stitch_segments", side_effect=real_stitch):
        pipe.process(pi, lambda o: None)

    stitched = frames_by_path["__stitched__"]
    assert stitched.shape[0] == 14  # 1 + 13, nothing dropped
    assert stitched[0, 0, 0, 0] == 10   # seg-0's only frame
    assert stitched[1, 0, 0, 0] == 20   # seg-1's first frame, untouched -- no crossfade
