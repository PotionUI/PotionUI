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
