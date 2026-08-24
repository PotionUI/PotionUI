"""Tests for the phase 2 throughput changes to generator/seedvr2:

  * ``_auto_batch_size`` — the live-VRAM-targeted 4n+1 temporal batch sizer.
  * ``_window_count`` — the pure profiler-mark window-count derivation.
  * the video path's shrink-on-OOM batch ladder (a fake generator that OOMs
    above a batch size; the pipe must halve the batch and re-run to success).

No real model or GPU is touched — the generator, the video reader and the mp4
encoder are all stubbed, and CUDA is forced unavailable.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image

from src.pipelines.pipes.generator.seedvr2 import main as m
from src.pipelines.pipes.generator.seedvr2.main import (
    GeneratorSeedVR2Pipe,
    _auto_batch_size,
    _spatial_tokens_per_latent_frame,
    _window_count,
    _SEEDVR2_MAX_BATCH,
)
from src.pipelines.contracts import PipeInput


# Latent spatial-token counts for the two calibration anchors.
_TOKENS_512 = _spatial_tokens_per_latent_frame(512, 512)      # tiny clip
_TOKENS_4K = _spatial_tokens_per_latent_frame(3840, 2160)     # the 4K profile


def _auto(free, *, tokens=_TOKENS_512, weights=15.0):
    return _auto_batch_size(
        free, spatial_tokens_per_latent_frame=tokens, weights_gb=weights,
    )


# -- _auto_batch_size (pure) -------------------------------------------------

def test_auto_batch_size_no_vram_signal_keeps_old_default():
    assert _auto(None) == 5
    assert _auto(0.0) == 5


def test_auto_batch_size_grows_with_free_vram():
    small = _auto(16.0)
    big = _auto(32.0)
    assert big > small


def test_auto_batch_size_is_always_4n1_and_clamped():
    for free in (8.0, 12.0, 16.0, 24.0, 32.0, 80.0, 200.0):
        for tokens in (_TOKENS_512, _TOKENS_4K):
            bs = _auto(free, tokens=tokens)
            assert bs % 4 == 1, f"{bs} not on the 4n+1 lattice for free={free}"
            assert 5 <= bs <= _SEEDVR2_MAX_BATCH


def test_auto_batch_size_caps_at_form_max_for_huge_cards():
    assert _auto(1000.0) == _SEEDVR2_MAX_BATCH


def test_auto_batch_size_shrinks_with_output_resolution():
    # The core resolution-awareness fix: at the SAME free VRAM, a 4K clip must
    # pick a strictly smaller temporal batch than a small clip (its per-frame
    # token count, hence activation footprint, is far larger).
    assert _auto(28.5, tokens=_TOKENS_4K) < _auto(28.5, tokens=_TOKENS_512)


def test_auto_batch_size_matches_be91_4k_profile():
    # The 4K profile: ~28.5GB free, 3840x2160 output, 7B DiT (~15.3GB
    # resident). The resolution-blind model picked 29 frames (8 latent) and OOM'd
    # after a ~2min doomed encode+forward; the OOM ladder then landed 13 frames
    # (4 latent). The pre-sizer must now land at that demonstrated-safe 13 up
    # front, never the doomed 29.
    picked = _auto(28.5, tokens=_TOKENS_4K, weights=15.3)
    assert picked == 13


def test_spatial_tokens_scale_with_area():
    # /8 VAE downsample then (2,2) patchify -> (H/16)*(W/16) tokens per frame.
    assert _spatial_tokens_per_latent_frame(512, 512) == (512 // 16) * (512 // 16)
    assert _spatial_tokens_per_latent_frame(3840, 2160) == (3840 // 16) * (2160 // 16)


# -- _window_count (pure geometry) -------------------------------------------

def test_window_count_positive_for_a_real_latent_shape():
    # (B, C, T', Hl, Wl) — a 2-latent-frame 64x64-latent clip.
    assert _window_count((1, 16, 2, 64, 64)) > 0


def test_window_count_zero_on_malformed_shape():
    # Best-effort: a shape it can't unpack must return 0, never raise.
    assert _window_count((1, 2, 3)) == 0


# -- video OOM ladder --------------------------------------------------------

class _FakeGen:
    """Stand-in generator whose ``upscale_video`` OOMs whenever the batch's
    frame count is >= ``oom_at_or_above`` (simulating a card that only fits once
    the temporal batch shrinks). Otherwise it echoes decoded clips of the same
    frame count (2x spatial, matching the real T-preserving contract)."""

    def __init__(self, oom_at_or_above: int):
        self.oom_at_or_above = oom_at_or_above
        self.batch_calls: list[int] = []
        self.released = 0

    def upscale_video(self, clips, prompt_embedding, *, seed, latent_noise_scale,
                      progress_cb=None, is_cancelled=None, tile_size=None, tile_overlap=None):
        bs = int(clips[0].shape[0]) if clips else 0
        self.batch_calls.append(bs)
        if bs >= self.oom_at_or_above:
            raise torch.cuda.OutOfMemoryError("synthetic batch OOM")
        return [np.zeros((c.shape[0], c.shape[1] * 2, c.shape[2] * 2, 3), dtype=np.uint8) for c in clips]

    def release_gpu(self):
        self.released += 1


def _wire(monkeypatch, gen, n_frames=20):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(m, "build_native_generator", lambda bundle, device="cuda": gen)
    frames = [Image.new("RGB", (32, 32)) for _ in range(n_frames)]
    monkeypatch.setattr(
        "src.pipelines.pipes._shared.media.video_read.read_video_frames",
        lambda path: (frames, 24.0),
    )
    monkeypatch.setattr(
        "src.pipelines.pipes._shared.media.video_encode.encode_frames_to_mp4",
        lambda pil_frames, out_path, fps, audio=None: None,
    )


def _video_input():
    bundle = SimpleNamespace(prompt_embedding=torch.zeros(1))
    return PipeInput(input={"video": ["/fake.mp4"], "model": bundle, "seed": [0]})


def _base_config(**over):
    cfg = {
        "scale": 2.0, "target_short_side": 0, "color_correction": "none",
        "latent_noise_scale": 0.0, "input_noise_scale": 0.0, "seed": 0,
        "device": "cuda", "tile_size": 1024, "tile_overlap": 128,
        "batch_size": 9, "temporal_overlap": 0, "prepend_frames": 0,
        "uniform_batch_size": True, "keep_audio": False,
    }
    cfg.update(over)
    return cfg


def test_video_oom_ladder_halves_batch_until_it_fits(monkeypatch):
    gen = _FakeGen(oom_at_or_above=9)         # only batch < 9 fits
    _wire(monkeypatch, gen)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=9))

    out = pipe.process(_video_input(), lambda o: None)

    # First attempt at 9 OOMs, halves to snap(4)=5, which fits.
    assert gen.batch_calls == [9, 5]
    assert gen.released >= 1
    assert "video" in out.output


def test_video_explicit_batch_no_oom_runs_once(monkeypatch):
    gen = _FakeGen(oom_at_or_above=999)       # never OOMs
    _wire(monkeypatch, gen)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=13))

    pipe.process(_video_input(), lambda o: None)

    assert gen.batch_calls == [13]
    assert gen.released == 0


def test_video_batch_zero_auto_sizes_from_free_vram(monkeypatch):
    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    monkeypatch.setattr(m, "free_vram_gb", lambda device: 24.0)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=0))

    pipe.process(_video_input(), lambda o: None)

    # The stub frames are tiny (32x32), so at 24GB free the token-based sizer
    # picks (and clamps at) the form max — one run, no OOM ladder.
    assert gen.batch_calls == [_SEEDVR2_MAX_BATCH]


class _RecordingProfiler:
    """Captures ``mark`` calls so a test can assert which profiler events the
    video path emits (the real profiler writes to a file, not memory)."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def mark(self, event, **fields):
        self.events.append((event, fields))


def test_video_emits_tail_profiler_marks(monkeypatch):
    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    rec = _RecordingProfiler()
    monkeypatch.setattr(m, "get_profiler", lambda: rec)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, color_correction="wavelet"))

    pipe.process(_video_input(), lambda o: None)

    names = {e for e, _ in rec.events}
    # The post-decode tail is now instrumented: stitch/assembly, the (batched)
    # color-fix, and the mp4 re-encode each get their own mark.
    assert "seedvr2.assemble" in names
    assert "seedvr2.color_fix" in names
    assert "seedvr2.encode_mp4" in names


def test_explicit_batch_logs_what_auto_would_pick(monkeypatch):
    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    monkeypatch.setattr(m, "free_vram_gb", lambda device: 24.0)

    logs: list[str] = []

    def _rec(*a, **k):
        try:
            logs.append(a[0] % a[1:] if len(a) > 1 else str(a[0]))
        except Exception:
            logs.append(str(a[0]))

    monkeypatch.setattr(m.logger, "debug", _rec)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5))

    pipe.process(_video_input(), lambda o: None)

    joined = " ".join(logs)
    assert "explicit batch_size=5" in joined
    assert "auto would pick" in joined
    # Tiny stub frames at 24GB free -> auto would pick the clamped form max; the
    # stale-session guard makes that visible next to the explicit 5.
    assert str(_SEEDVR2_MAX_BATCH) in joined


class _CapturingModule:
    def __call__(self, vid, timestep, txt):
        b, _c, t, h, w = vid.shape
        return torch.zeros((b, 16, t, h, w), dtype=vid.dtype)


class _FakeVAE:
    def tiled_encode(self, pixels, tile_size, overlap):
        b, _c, t, h, w = pixels.shape
        return torch.zeros((b, 16, 1 + (t - 1) // 4, h // 8, w // 8), dtype=pixels.dtype)

    def tiled_decode(self, latent, tile_size, overlap):
        b, _c, t, h, w = latent.shape
        return torch.zeros((b, 3, t, h * 8, w * 8), dtype=latent.dtype)


def test_real_upscale_video_emits_marks_and_returns_decoded_clips(monkeypatch):
    # Profiling ON exercises the mark + _sync_if_profiling code paths; CUDA off
    # keeps it CPU (the sync is guarded on cuda availability).
    monkeypatch.setenv("POTIONUI_PROFILE", "1")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(m, "free_vram_gb", lambda device: None)
    from src.platform.observability.profiling import reset_enabled_cache
    reset_enabled_cache()

    gen = m.SeedVR2NativeGenerator.__new__(m.SeedVR2NativeGenerator)
    gen.dit = SimpleNamespace(
        module=_CapturingModule(), compute_dtype=torch.float32,
        estimated_vram_gb=1.0, offload=lambda: None,
    )
    gen.vae = SimpleNamespace(
        module=_FakeVAE(), compute_dtype=torch.float32,
        move_to=lambda device: None, offload=lambda: None, estimated_vram_gb=1.0,
    )
    gen.device_plan = SimpleNamespace(dit_device="cpu", vae_device="cpu")
    gen.placement = None  # -> _resident("dit") True
    gen._build_placement = lambda shape: None
    gen._move_dit_to_gpu = lambda device: None
    gen._stream_dit_to_gpu = lambda device, shape: None
    gen._maybe_compile = lambda: None

    clip = np.zeros((5, 64, 64, 3), dtype=np.uint8)
    out = gen.upscale_video([clip], torch.ones((10, 5120)), seed=0)

    reset_enabled_cache()  # don't leak the enabled flag into other tests
    assert len(out) == 1
    # 5 input frames -> 2 latent frames -> 2 decoded frames; the fake VAE
    # round-trips 64px -> 8 latent px -> 64px (the "upscale" is the pre-encode
    # resize, not the VAE), so the decoded clip is (2, 64, 64, 3).
    assert out[0].shape == (2, 64, 64, 3)
    assert out[0].dtype == np.uint8


def test_video_ladder_gives_up_and_raises_when_even_batch_one_ooms(monkeypatch):
    gen = _FakeGen(oom_at_or_above=1)         # OOMs at every batch, incl. 1
    _wire(monkeypatch, gen)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5))

    with pytest.raises(torch.cuda.OutOfMemoryError):
        pipe.process(_video_input(), lambda o: None)

    # Shrunk 5 -> 1 and only then gave up.
    assert gen.batch_calls[0] == 5
    assert gen.batch_calls[-1] == 1
    assert gen.released >= 1
