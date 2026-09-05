"""FBCache probe grouping on the MiniMax-H3 packed sequence.

H3 packs video, audio and text rows into ONE sequence, so block-0's output
covers all three at once. Gating on that whole tensor lets the long video
stream (and the pinned text rows, which cannot move at all) hold the drift
ratio under threshold while the handful of audio rows change completely — the
step then skips and replays a stale audio velocity. These tests drive the real
forward and assert on what the cache was actually offered.
"""

from __future__ import annotations

import torch

from src.pipelines.pipes.generator.video_minimax_h3.layout import (
    TEXT_TAG,
    build_packed_sequence,
    build_row_timesteps,
)
from src.pipelines.pipes.generator.video_minimax_h3.main import _MiniMaxH3Forward
from src.platform.runtime.native.sampling.step_cache import FirstBlockCache, GroupedProbe

from .test_minimax_h3_model import (
    TINY_FULL,
    _build_ready,
    _fbcache_forward,
    _fbcache_inputs,
    _tiny_layout,
)

# 60 video rows against 1 audio row and 4 text rows: the shape of a real
# packed sequence, where audio is a rounding error in a pooled mean.
VIDEO_ROWS, AUDIO_ROWS, TEXT_ROWS = 60, 1, 4

# Loose enough that a probe pooled over the whole packed sequence skips a step
# whose audio stream moved 8x (measured 0.075 for that step), tight enough that
# the audio group's own ratio (6.4) cannot.
LOOSE = 0.15


class _ProbeCapture:
    """Stands in for a cache to record the probe the arch builds, without
    letting any step skip."""

    def __init__(self) -> None:
        self.probes: list[GroupedProbe] = []

    def should_skip(self, probe) -> bool:
        self.probes.append(probe)
        return False

    def record_compute(self, probe, output) -> None:
        pass

    def record_skip(self):
        raise AssertionError("capture must never be asked to replay")


def _fixture(seed: int = 7):
    torch.manual_seed(seed)
    layout = _tiny_layout(text_n=TEXT_ROWS, video_n=VIDEO_ROWS, audio_n=AUDIO_ROWS)
    model = _build_ready(TINY_FULL)
    inputs = _fbcache_inputs(TINY_FULL, layout)
    return model, layout, inputs, torch.tensor([0.2, 0.9])


def _with(inputs: dict, **overrides) -> dict:
    return dict(inputs, **overrides)


def test_probe_groups_are_the_generated_rows_only():
    """The video and audio groups hold exactly their own rows of the packed
    sequence; the text rows are in neither, so rows that are pinned for the
    whole trajectory cannot damp the drift ratio."""
    model, layout, inputs, ts = _fixture()
    capture = _ProbeCapture()
    _fbcache_forward(model, layout, inputs, ts, step_cache=capture)

    probe = capture.probes[0]
    assert set(probe.groups) == {"video", "audio"}
    assert probe.groups["video"].shape[1] == VIDEO_ROWS
    assert probe.groups["audio"].shape[1] == AUDIO_ROWS
    seq_len = VIDEO_ROWS + AUDIO_ROWS + TEXT_ROWS
    assert all(t.shape[1] != seq_len for t in probe.groups.values())


def test_probe_rows_match_the_layout_indices():
    """Each group is gathered by the layout's own index tensor — the video
    group must be the video rows, not the first N rows of the sequence."""
    model, layout, inputs, ts = _fixture()
    capture = _ProbeCapture()
    _fbcache_forward(model, layout, inputs, ts, step_cache=capture)
    probe = capture.probes[0]

    # Re-run with the same inputs and gather block-0's output by hand from the
    # full sequence, using a capture that keeps the whole packed tensor.
    full = {}
    original = model.blocks[0].forward

    def keep(*args, **kwargs):
        out = original(*args, **kwargs)
        full.setdefault("h", out)
        return out

    model.blocks[0].forward = keep
    try:
        _fbcache_forward(model, layout, inputs, ts, step_cache=_ProbeCapture())
    finally:
        model.blocks[0].forward = original

    torch.testing.assert_close(probe.groups["video"], full["h"].index_select(1, layout["video_indices"]))
    torch.testing.assert_close(probe.groups["audio"], full["h"].index_select(1, layout["audio_indices"]))


def test_changed_audio_stream_computes_under_a_video_sized_threshold():
    """The defect: only the audio input moves, the 60 video rows barely
    respond, and a probe pooled across the packed sequence reads well under
    the threshold. The audio group must force the step to compute."""
    model, layout, inputs, ts = _fixture()
    cache = FirstBlockCache(rel_threshold=LOOSE, warmup_steps=0)
    _fbcache_forward(model, layout, inputs, ts, step_cache=cache)
    moved = _with(inputs, audio_hidden_states=inputs["audio_hidden_states"] * 8.0)
    _fbcache_forward(model, layout, moved, ts, step_cache=cache)

    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_changed_video_stream_still_computes():
    model, layout, inputs, ts = _fixture()
    cache = FirstBlockCache(rel_threshold=LOOSE, warmup_steps=0)
    _fbcache_forward(model, layout, inputs, ts, step_cache=cache)
    moved = _with(inputs, hidden_states=inputs["hidden_states"] * 1.5)
    _fbcache_forward(model, layout, moved, ts, step_cache=cache)

    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_unchanged_packed_sequence_still_skips_and_replays_both_modalities():
    """Grouping must not cost the cache its reason to exist: a repeated step
    skips, and the replayed pair is byte-identical to the computed one."""
    model, layout, inputs, ts = _fixture()
    cache = FirstBlockCache(rel_threshold=LOOSE, warmup_steps=0)
    with torch.inference_mode():
        first = _fbcache_forward(model, layout, inputs, ts, step_cache=cache)
        second = _fbcache_forward(model, layout, inputs, ts, step_cache=cache)

    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second[0], first[0]) and torch.equal(second[1], first[1])


# --- condition prefixes, through the real forwarding adapter -----------------

# Each modality is laid out as [condition rows, target rows] and only the
# stepper slices the prefix off. Three keyframe blocks against one latent frame
# gives 48 condition video rows to 16 generated; eight condition audio latents
# gives 16 condition audio rows to 2 generated. A probe over the whole modality
# is therefore dominated 4:1 and 9:1 by rows that are frozen for the run.
PATCH = (1, 2, 2)
PREFIX_TEXT_ROWS = 4

# Target rows moved 1.4x read 0.386 (video) and 0.366 (audio) on the generated
# span alone, but only 0.095 and 0.038 pooled across their whole modality.
PREFIX_THRESHOLD = 0.15
TARGET_MOVE = 1.4


def _prefixed_layout(num_audio_latents: int = 1):
    return build_packed_sequence(
        torch.full((PREFIX_TEXT_ROWS,), TEXT_TAG, dtype=torch.long),
        num_latent_frames=1, latent_height=8, latent_width=8,
        num_audio_latents=num_audio_latents, patch_size=PATCH,
        keyframe_anchors=("first", "last", 0), num_condition_audio_latents=8,
    )


def _adapter(layout, seed: int = 5):
    """The real ``_MiniMaxH3Forward`` over a real ``PackedLayout``, plus rows
    and the timestep pair its caller builds."""
    torch.manual_seed(seed)
    model = _build_ready(TINY_FULL)
    forward = _MiniMaxH3Forward(model, layout, torch.randn(1, PREFIX_TEXT_ROWS, TINY_FULL["text_dim"]))
    video_rows = torch.randn(layout.video_indices.numel(), TINY_FULL["in_channels"] * 4)
    audio_rows = torch.randn(layout.audio_indices.numel(), TINY_FULL["audio_in_channels"])
    unique_ts, ts_indices = build_row_timesteps(
        layout.video_indices, layout.audio_indices,
        num_condition_video_rows=layout.num_condition_video_rows,
        num_condition_audio_rows=layout.num_condition_audio_rows,
        num_text_tokens=PREFIX_TEXT_ROWS, video_timestep=0.5, audio_timestep=0.5,
        condition_video_timestep=0.999, condition_audio_timestep=1.0,
    )
    return forward, video_rows, audio_rows, unique_ts, ts_indices


def _moved(rows: torch.Tensor, prefix: int, factor: float = TARGET_MOVE) -> torch.Tensor:
    out = rows.clone()
    out[prefix:] *= factor
    return out


def test_probe_groups_exclude_each_modality_condition_prefix():
    layout = _prefixed_layout()
    forward, video_rows, audio_rows, unique_ts, ts_indices = _adapter(layout)
    capture = _ProbeCapture()
    with torch.inference_mode():
        forward(video_rows, audio_rows, unique_ts, ts_indices, step_cache=capture)

    groups = capture.probes[0].groups
    generated_video = layout.video_indices.numel() - layout.num_condition_video_rows
    generated_audio = layout.audio_indices.numel() - layout.num_condition_audio_rows
    assert groups["video"].shape[1] == generated_video
    assert groups["audio"].shape[1] == generated_audio
    assert groups["video"].shape[1] < layout.video_indices.numel()
    assert groups["audio"].shape[1] < layout.audio_indices.numel()


def test_packed_output_still_covers_every_row():
    """Narrowing the probe must not narrow the forward: both heads still return
    a velocity for the condition prefix as well as the target."""
    layout = _prefixed_layout()
    forward, video_rows, audio_rows, unique_ts, ts_indices = _adapter(layout)
    with torch.inference_mode():
        video_pred, audio_pred = forward(video_rows, audio_rows, unique_ts, ts_indices)

    assert video_pred.shape == video_rows.shape
    assert audio_pred.shape == audio_rows.shape


def test_changed_video_target_computes_under_a_prefix_diluted_threshold():
    """The gap: 48 frozen condition rows pull a moved 16-row target under the
    threshold when the probe spans the whole modality."""
    layout = _prefixed_layout()
    forward, video_rows, audio_rows, unique_ts, ts_indices = _adapter(layout)
    cache = FirstBlockCache(rel_threshold=PREFIX_THRESHOLD, warmup_steps=0)
    with torch.inference_mode():
        forward(video_rows, audio_rows, unique_ts, ts_indices, step_cache=cache)
        forward(_moved(video_rows, layout.num_condition_video_rows), audio_rows,
                unique_ts, ts_indices, step_cache=cache)

    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_changed_audio_target_computes_under_a_prefix_diluted_threshold():
    layout = _prefixed_layout()
    forward, video_rows, audio_rows, unique_ts, ts_indices = _adapter(layout)
    cache = FirstBlockCache(rel_threshold=PREFIX_THRESHOLD, warmup_steps=0)
    with torch.inference_mode():
        forward(video_rows, audio_rows, unique_ts, ts_indices, step_cache=cache)
        forward(video_rows, _moved(audio_rows, layout.num_condition_audio_rows),
                unique_ts, ts_indices, step_cache=cache)

    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_unchanged_targets_still_reuse_the_cached_pair():
    layout = _prefixed_layout()
    forward, video_rows, audio_rows, unique_ts, ts_indices = _adapter(layout)
    cache = FirstBlockCache(rel_threshold=PREFIX_THRESHOLD, warmup_steps=0)
    with torch.inference_mode():
        first = forward(video_rows, audio_rows, unique_ts, ts_indices, step_cache=cache)
        second = forward(video_rows, audio_rows, unique_ts, ts_indices, step_cache=cache)

    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second[0], first[0]) and torch.equal(second[1], first[1])


def test_prefix_only_change_is_judged_by_its_effect_on_the_target():
    """A conditioning row moving is not itself a reason to recompute — the
    cache predicts the TARGET velocity, and here the target's own block-0
    output stays under threshold. The stepper freezes the prefix for the whole
    run, so this never happens mid-trajectory; the case exists to pin what the
    narrowed probe does when it would."""
    layout = _prefixed_layout()
    forward, video_rows, audio_rows, unique_ts, ts_indices = _adapter(layout)
    cache = FirstBlockCache(rel_threshold=PREFIX_THRESHOLD, warmup_steps=0)
    prefix_moved = video_rows.clone()
    prefix_moved[:layout.num_condition_video_rows] *= TARGET_MOVE
    with torch.inference_mode():
        forward(video_rows, audio_rows, unique_ts, ts_indices, step_cache=cache)
        forward(prefix_moved, audio_rows, unique_ts, ts_indices, step_cache=cache)

    assert cache.stats() == {"computed": 1, "skipped": 1}


def test_empty_generated_span_drops_its_group():
    """A layout whose audio is entirely conditioning has no generated audio to
    gate on; the group drops and the video group gates alone."""
    layout = _prefixed_layout(num_audio_latents=0)
    assert layout.audio_indices.numel() == layout.num_condition_audio_rows
    forward, video_rows, audio_rows, unique_ts, ts_indices = _adapter(layout)
    capture = _ProbeCapture()
    with torch.inference_mode():
        forward(video_rows, audio_rows, unique_ts, ts_indices, step_cache=capture)

    assert set(capture.probes[0].groups) == {"video"}
