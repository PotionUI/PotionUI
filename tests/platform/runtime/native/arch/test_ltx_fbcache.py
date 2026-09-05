"""FBCache integration on the LTX-2 AV arch (dual video/audio stream).

Output caching caches the WHOLE variadic forward return, so both streams are
byte-identical on a skip; the video stream's block-0 output is the probe.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

from .test_ltx_forward import TINY_19B, TINY_25, _av_inputs, _build, _extras


def _eq(a, b) -> bool:
    if isinstance(a, torch.Tensor):
        return torch.equal(a, b)
    if a is None:
        return b is None
    return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))


def _fwd(m, x, ts, ctx, **kw):
    with torch.inference_mode():
        return m.forward(x, ts, ctx, **kw)


def test_step_cache_none_is_byte_identical():
    m = _build(TINY_19B)
    vx, ax, ctx, ts = _av_inputs(TINY_19B)
    base = _fwd(m, [vx, ax], ts, ctx)
    assert _eq(base, _fwd(m, [vx, ax], ts, ctx, step_cache=None))


def test_identical_inputs_skip_returns_cached_output_both_streams():
    m = _build(TINY_19B)
    vx, ax, ctx, ts = _av_inputs(TINY_19B)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    first = _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 0}
    second = _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    # both video and audio streams are byte-identical to the computed step.
    assert isinstance(second, list) and len(second) == 2
    assert _eq(second, first)


def test_different_inputs_do_not_skip():
    m = _build(TINY_19B)
    vx, ax, ctx, ts = _av_inputs(TINY_19B)
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    _fwd(m, [vx * 5.0, ax], ts, ctx, step_cache=cache)  # different video stream
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_skip_avoids_later_blocks():
    m = _build(TINY_19B)  # num_layers=2 -> transformer_blocks[-1] is block 1
    vx, ax, ctx, ts = _av_inputs(TINY_19B)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    last = m.transformer_blocks[-1]
    orig = last.forward
    calls = {"n": 0}

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    last.forward = counting
    _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    assert calls["n"] == 1
    _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    assert calls["n"] == 1


def test_video_only_forward_caches_tensor():
    # audio absent -> forward returns a bare tensor; caching/skip must handle it.
    m = _build(TINY_19B)
    vx, _, ctx, ts = _av_inputs(TINY_19B)
    cache = FirstBlockCache(rel_threshold=0.5, warmup_steps=0)
    first = _fwd(m, [vx], ts, ctx, step_cache=cache)
    second = _fwd(m, [vx], ts, ctx, step_cache=cache)
    assert isinstance(second, torch.Tensor)
    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert torch.equal(second, first)


def test_audio_only_change_does_not_skip():
    """S7: the probe used to be the video stream alone. LTX's audio stream can
    carry an independently-scheduled timestep, so audio state can drift while
    the video probe stays stable — with a video-only probe this would go
    undetected and reuse a stale audio velocity. Verify the cache actually
    recomputes when ONLY the audio input changes (video/timestep held fixed)."""
    m = _build(TINY_19B)
    vx, ax, ctx, ts = _av_inputs(TINY_19B)
    cache = FirstBlockCache(rel_threshold=0.01, warmup_steps=0)
    _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    _fwd(m, [vx, ax * 5.0], ts, ctx, step_cache=cache)  # only audio differs
    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_resolution_change_forces_compute():
    m = _build(TINY_19B)
    vx, ax, ctx, ts = _av_inputs(TINY_19B)
    cache = FirstBlockCache(rel_threshold=0.9, warmup_steps=0)
    _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    vx2 = torch.randn(1, TINY_19B["in_channels"], 2, 4, 2)  # different H
    _fwd(m, [vx2, ax], ts, ctx, step_cache=cache)
    assert cache.stats()["skipped"] == 0


# --- per-modality gating -----------------------------------------------------

# A long video grid against a single audio row — the ratio a real AV generation
# runs at. Loose enough that ONE probe pooled over both streams skips a step
# whose audio moved 8x (measured 0.12 for that step), tight enough that the
# audio group's own ratio (7.6) cannot.
LOOSE = 0.15


def _lopsided(cfg):
    """Inputs whose video stream dwarfs its audio stream."""
    _, _, ctx, ts = _av_inputs(cfg)
    return torch.randn(1, cfg["in_channels"], 2, 8, 8), torch.randn(1, 8, 1, 16), ctx, ts


class _ProbeCapture:
    """Records the probe the arch builds without letting any step skip."""

    def __init__(self):
        self.probes = []

    def should_skip(self, probe):
        self.probes.append(probe)
        return False

    def record_compute(self, probe, output):
        pass

    def record_skip(self):
        raise AssertionError("capture must never be asked to replay")


def test_probe_splits_the_two_streams():
    m = _build(TINY_19B)
    vx, ax, ctx, ts = _lopsided(TINY_19B)
    capture = _ProbeCapture()
    _fwd(m, [vx, ax], ts, ctx, step_cache=capture)

    groups = capture.probes[0].groups
    assert set(groups) == {"video", "audio"}
    assert groups["video"].shape[1] > groups["audio"].shape[1] * 100


def test_changed_audio_stream_computes_under_a_video_sized_threshold():
    """The defect one flattened probe leaves open: only the audio input moves,
    the video grid barely responds, and the pooled ratio reads under the
    threshold. The audio group must force the step to compute."""
    torch.manual_seed(11)
    m = _build(TINY_19B)
    vx, ax, ctx, ts = _lopsided(TINY_19B)
    cache = FirstBlockCache(rel_threshold=LOOSE, warmup_steps=0)
    _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    _fwd(m, [vx, ax * 8.0], ts, ctx, step_cache=cache)

    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_changed_video_stream_computes_under_the_same_threshold():
    torch.manual_seed(11)
    m = _build(TINY_19B)
    vx, ax, ctx, ts = _lopsided(TINY_19B)
    cache = FirstBlockCache(rel_threshold=LOOSE, warmup_steps=0)
    _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    _fwd(m, [vx * 1.5, ax], ts, ctx, step_cache=cache)

    assert cache.stats() == {"computed": 2, "skipped": 0}


def test_unchanged_streams_still_skip_at_the_same_threshold():
    """Grouping must not cost the cache its reason to exist."""
    torch.manual_seed(11)
    m = _build(TINY_19B)
    vx, ax, ctx, ts = _lopsided(TINY_19B)
    cache = FirstBlockCache(rel_threshold=LOOSE, warmup_steps=0)
    first = _fwd(m, [vx, ax], ts, ctx, step_cache=cache)
    second = _fwd(m, [vx, ax], ts, ctx, step_cache=cache)

    assert cache.stats() == {"computed": 1, "skipped": 1}
    assert _eq(second, first)


def test_audio_absent_leaves_one_group():
    """No audio track: the audio group is empty and drops away, leaving the
    video group gating alone exactly as a single-stream arch does."""
    m = _build(TINY_19B)
    vx, _, ctx, ts = _av_inputs(TINY_19B)
    capture = _ProbeCapture()
    _fwd(m, [vx], ts, ctx, step_cache=capture)

    assert set(capture.probes[0].groups) == {"video"}


def test_appended_conditioning_tokens_are_outside_the_video_group():
    """Keyframe / IC-LoRA conditioning tokens ride at the tail of the video
    stream but are not generated output, and they are pinned for the whole
    trajectory — counting them would damp every step's drift."""
    m = _build(TINY_25)
    vx, ax, ctx, ts = _av_inputs(TINY_25)
    extra_tokens, extra_coords = _extras(TINY_25)
    plain, conditioned = _ProbeCapture(), _ProbeCapture()

    _fwd(m, [vx, ax], ts, ctx, sigma=ts, audio_sigma=ts, step_cache=plain)
    _fwd(m, [vx, ax], ts, ctx, sigma=ts, audio_sigma=ts, step_cache=conditioned,
         extra_video_tokens=extra_tokens, extra_video_pixel_coords=extra_coords)

    base_rows = plain.probes[0].groups["video"].shape[1]
    assert conditioned.probes[0].groups["video"].shape[1] == base_rows
