"""FBCache integration on the LTX-2 AV arch (dual video/audio stream).

Output caching caches the WHOLE variadic forward return, so both streams are
byte-identical on a skip; the video stream's block-0 output is the probe.
"""

from __future__ import annotations

import torch

from src.platform.runtime.native.sampling.step_cache import FirstBlockCache

from .test_ltx_forward import TINY_19B, _av_inputs, _build


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
