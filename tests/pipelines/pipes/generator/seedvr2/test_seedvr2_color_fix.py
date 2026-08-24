"""Tests for the batched color correction (``color_correct_batch``).

The SeedVR2 video path's post-decode tail was dominated by a per-frame CPU
wavelet color-fix over the whole full-HD clip. ``color_correct_batch`` runs the
same math batched (on the GPU when available), so these tests pin that the
batched path is byte-identical to the per-frame :func:`color_correct` on the
same device, plus the ``none``/empty/fallback behaviours. No GPU is touched --
everything runs on CPU (the batched path is device-agnostic; a CUDA request
downgrades to CPU when CUDA is unavailable).
"""

from __future__ import annotations

import json

import numpy as np
import pytest
import torch

from src.pipelines.pipes.generator.seedvr2.color_fix import (
    color_correct,
    color_correct_batch,
)
from src.platform.observability import profiling as profiling_module


@pytest.fixture(autouse=True)
def _isolate_profiling_state(monkeypatch):
    """Every test gets a clean enabled/settings-manager cache, regardless of
    env vars set in the outer shell or a prior test's profiler state."""
    monkeypatch.delenv("POTIONUI_PROFILE", raising=False)
    profiling_module.profiler._settings_manager = None
    profiling_module.reset_enabled_cache()
    yield
    profiling_module.reset_enabled_cache()


def _frames(n: int, h: int = 24, w: int = 32, seed: int = 0):
    rng = np.random.default_rng(seed)
    return [rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8) for _ in range(n)]


@pytest.mark.parametrize("mode", ["wavelet", "adain"])
def test_batch_is_byte_identical_to_per_frame_on_cpu(mode):
    targets = _frames(7, seed=1)
    sources = _frames(7, seed=2)

    batched = color_correct_batch(targets, sources, mode, device="cpu")
    per_frame = [color_correct(t, s, mode) for t, s in zip(targets, sources)]

    assert len(batched) == len(per_frame)
    for b, p in zip(batched, per_frame):
        # Batching corrects each frame independently, so on the SAME device the
        # result must match the per-frame path exactly (not just within tol).
        assert np.array_equal(b, p)


@pytest.mark.parametrize("mode", ["wavelet", "adain"])
def test_batch_chunking_does_not_change_output(mode):
    targets = _frames(9, seed=3)
    sources = _frames(9, seed=4)

    one_chunk = color_correct_batch(targets, sources, mode, device="cpu", max_chunk=64)
    many_chunks = color_correct_batch(targets, sources, mode, device="cpu", max_chunk=2)

    for a, b in zip(one_chunk, many_chunks):
        assert np.array_equal(a, b)


def test_none_mode_returns_targets_unchanged():
    targets = _frames(4, seed=5)
    out = color_correct_batch(targets, _frames(4, seed=6), "none")
    assert len(out) == 4
    for o, t in zip(out, targets):
        assert np.array_equal(o, t)


def test_empty_input_returns_empty():
    assert color_correct_batch([], [], "wavelet") == []


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        color_correct_batch(_frames(3), _frames(2), "wavelet")


def test_cuda_request_downgrades_to_cpu_when_unavailable(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    targets = _frames(3, seed=7)
    sources = _frames(3, seed=8)

    # "cuda" requested but unavailable -> silently runs on CPU, still correct.
    out = color_correct_batch(targets, sources, "wavelet", device="cuda")
    per_frame = [color_correct(t, s, "wavelet") for t, s in zip(targets, sources)]
    for o, p in zip(out, per_frame):
        assert np.array_equal(o, p)


def test_source_resized_to_target_when_shapes_differ():
    # Guards the interpolate branch: a smaller source is bilinearly resized to
    # the target, so the batch call still returns target-shaped frames.
    targets = _frames(2, h=32, w=32, seed=9)
    sources = _frames(2, h=16, w=16, seed=10)
    out = color_correct_batch(targets, sources, "wavelet", device="cpu")
    for o in out:
        assert o.shape == (32, 32, 3)


def test_profiling_emits_breakdown_without_changing_output(monkeypatch, tmp_path):
    """The stage/compute/unstage timing breakdown is opt-in (profiling-gated)
    and must never perturb the actual color-fix math."""
    monkeypatch.setenv("POTIONUI_PROFILE", "1")
    profiling_module.reset_enabled_cache()

    targets = _frames(9, seed=11)
    sources = _frames(9, seed=12)

    unprofiled = color_correct_batch(targets, sources, "wavelet", device="cpu", max_chunk=4)

    profiler = profiling_module.get_profiler()
    profiler.start("test-color-fix-breakdown", tmp_path)
    try:
        profiled = color_correct_batch(targets, sources, "wavelet", device="cpu", max_chunk=4)
    finally:
        profiler.stop("test-color-fix-breakdown")

    for a, b in zip(unprofiled, profiled):
        assert np.array_equal(a, b)

    rows = [
        json.loads(line)
        for line in (tmp_path / "profile.jsonl").read_text().splitlines()
        if line.strip()
    ]
    breakdown = [r for r in rows if r.get("event") == "seedvr2.color_fix.breakdown"]
    assert len(breakdown) == 1
    row = breakdown[0]
    assert row["frames"] == 9
    assert row["mode"] == "wavelet"
    assert row["cpu_fallback_frames"] == 0
    for key in ("stage_seconds", "compute_seconds", "unstage_seconds"):
        assert key in row
        assert row[key] >= 0.0


def test_no_profiling_emits_no_breakdown_event(tmp_path):
    """Profiling off (the default / normal generation path) must not pay for
    or emit the timing breakdown at all."""
    assert profiling_module.profiling_enabled() is False
    targets = _frames(4, seed=13)
    sources = _frames(4, seed=14)

    profiler = profiling_module.get_profiler()
    profiler.start("test-color-fix-no-breakdown", tmp_path)
    try:
        color_correct_batch(targets, sources, "wavelet", device="cpu")
    finally:
        profiler.stop("test-color-fix-no-breakdown")

    assert not (tmp_path / "profile.jsonl").exists()
