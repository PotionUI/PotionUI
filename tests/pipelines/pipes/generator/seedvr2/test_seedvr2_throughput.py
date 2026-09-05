"""Tests for the phase 2 throughput changes to generator/seedvr2:

  * ``_auto_batch_size`` — the live-VRAM-targeted 4n+1 temporal batch sizer.
  * ``_window_count`` — the pure profiler-mark window-count derivation.
  * the video path's shrink-on-OOM batch ladder (a fake generator that OOMs
    above a batch size; the pipe must halve the batch and re-run to success).

No real model or GPU is touched — the generator, the video reader and the mp4
encoder are all stubbed, and CUDA is forced unavailable.
"""

from __future__ import annotations

import glob
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from src.pipelines.pipes.generator.seedvr2 import batching as B
from src.pipelines.pipes.generator.seedvr2 import main as m
from src.pipelines.pipes.generator.seedvr2.main import (
    GeneratorSeedVR2Pipe,
    _auto_batch_size,
    _count_windows,
    _spatial_tokens_per_latent_frame,
    _window_count,
    _SEEDVR2_MAX_BATCH,
)
from src.pipelines.contracts import PipeInput
from src.platform.runtime.native.errors import SamplingCancelled


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


# -- _count_windows (progress-bar estimate, constant space) ------------------

def test_count_windows_matches_plan_batches_for_a_grid_of_small_hints():
    for total in range(0, 60):
        for batch_size in (1, 2, 3, 5, 9, 13):
            for overlap in (0, 1, 2, 4, 6, 8, 12, 20):
                windows, _ = B.plan_batches(total, batch_size, overlap)
                assert _count_windows(total, batch_size, overlap) == len(windows), (
                    f"total={total} batch_size={batch_size} overlap={overlap}"
                )


def test_count_windows_never_materialises_the_window_list(monkeypatch):
    def _must_not_be_called(*a, **kw):
        raise AssertionError("_estimate_batches must not call plan_batches")

    monkeypatch.setattr(B, "plan_batches", _must_not_be_called)

    # A container frame count in the millions is exactly the case that made
    # materialising `plan_batches`' full window list expensive; the estimate
    # must come back cheaply and without ever touching `plan_batches`.
    n = GeneratorSeedVR2Pipe._estimate_batches(10_000_000, 9, 4, 0)
    assert n == 2_000_000


def test_count_windows_constant_space_for_a_huge_hint():
    import tracemalloc

    tracemalloc.start()
    try:
        n = _count_windows(10_000_000, 9, 4)
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    # 10M frames at batch 9 (step 5 with overlap 4) is 2,000,000 windows --
    # a materialised list of that many (start, end) tuples is tens of
    # megabytes; a closed-form count stays in the tens/hundreds of bytes.
    assert n == 2_000_000
    assert peak < 4096


# -- video OOM ladder --------------------------------------------------------

class _FakeGen:
    """Stand-in generator whose ``upscale_video`` OOMs whenever the batch's
    frame count is >= ``oom_at_or_above`` (simulating a card that only fits once
    the temporal batch shrinks). Otherwise it echoes decoded clips of the same
    frame count (2x spatial, matching the real T-preserving contract).

    ``upscale_video`` is a generator, like the real one: it pulls one clip at a
    time from the streamed producer and yields its decoded clip."""

    def __init__(self, oom_at_or_above: int):
        self.oom_at_or_above = oom_at_or_above
        self.batch_calls: list[int] = []
        self.released = 0

    def upscale_video(self, clips, prompt_embedding, *, seed, latent_noise_scale,
                      tile_size=None, tile_overlap=None):
        first = True
        for clip in clips:
            if first:
                bs = int(clip.shape[0])
                self.batch_calls.append(bs)
                if bs >= self.oom_at_or_above:
                    raise torch.cuda.OutOfMemoryError("synthetic batch OOM")
                first = False
            yield np.zeros(
                (clip.shape[0], clip.shape[1] * 2, clip.shape[2] * 2, 3), dtype=np.uint8,
            )

    def release_gpu(self):
        self.released += 1


class _FakeSource:
    """Replayable stand-in for ``ResizedFrameSource``: a fixed list of resized
    frames, re-iterable from index 0 as often as an OOM retry needs."""

    def __init__(self, frames, fps=24.0, video_path="/fake.mp4"):
        self._frames = frames
        self.fps = fps
        self.video_path = video_path
        self.frame_count_hint = len(frames)
        self.frame_shape = (int(frames[0].shape[0]), int(frames[0].shape[1]))
        self.opened = 0

    def probe(self):
        pass

    def iter_frames(self):
        self.opened += 1
        for frame in self._frames:
            yield frame


def _stub_stream_encode(frames, out_path, fps, *, codec="libx264", crf=18, audio=None):
    """Consume the streamed frames and write a marker file, so the pipe's
    move-into-place step has a real file to publish."""
    count = sum(1 for _ in frames)
    Path(out_path).write_text(f"video:{count}")
    return Path(out_path)


def _wire(monkeypatch, gen, n_frames=20):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(m, "build_native_generator", lambda bundle, device="cuda": gen)
    frames = [np.zeros((32, 32, 3), dtype=np.uint8) for _ in range(n_frames)]
    source = _FakeSource(frames)
    monkeypatch.setattr(
        "src.pipelines.pipes.generator.seedvr2.frame_source.ResizedFrameSource",
        lambda path, **kw: source,
    )
    monkeypatch.setattr(
        "src.pipelines.pipes.generator.seedvr2.encode.encode_frames_stream_to_mp4",
        _stub_stream_encode,
    )
    return source


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
    out = list(gen.upscale_video([clip], torch.ones((10, 5120)), seed=0))

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


# -- video path: audio mux outcome wiring ------------------------------------
#
# `_process_video` delegates the video/audio split to
# `seedvr2.encode.encode_video_with_audio`; these tests check the PIPE's side
# of that boundary -- that a mux failure surfaces as a durable warning output
# and that the profiler mark carries the actual outcome, not the requested
# `keep_audio` bool -- by faking the whole encode/mux function, not ffmpeg.

def _wire_fake_audio_outcome(monkeypatch, *, audio_outcome, omitted_reason=None):
    from src.pipelines.pipes.generator.seedvr2 import encode as enc

    def _fake(frames_arr, out_path, fps, *, source_audio_path, keep_audio, encode_video, **_kw):
        encode_video(frames_arr, out_path, fps=fps, audio=None)
        video_path = out_path
        if audio_outcome == enc.AUDIO_MUXED:
            video_path = f"{out_path}.audio.mp4"
            Path(video_path).write_text("muxed")
        return enc.VideoAudioResult(
            video_path=video_path, audio_outcome=audio_outcome, omitted_reason=omitted_reason,
        )

    monkeypatch.setattr(enc, "encode_video_with_audio", _fake)
    return enc


def test_video_mux_failure_emits_durable_warning_and_reports_actual_outcome(monkeypatch):
    from src.pipelines.outputs import ProgressGenerationOutput

    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    enc = _wire_fake_audio_outcome(
        monkeypatch, audio_outcome="mux_failed", omitted_reason="synthetic mux failure",
    )
    rec = _RecordingProfiler()
    monkeypatch.setattr(m, "get_profiler", lambda: rec)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=True))

    outputs = []
    pipe.process(_video_input(), outputs.append)

    warnings = [
        o for o in outputs
        if isinstance(o, ProgressGenerationOutput) and o.icon and o.icon.name == "alert-triangle"
    ]
    assert warnings, "a mux failure must surface a durable alert-triangle progress warning"

    encode_marks = [fields for event, fields in rec.events if event == "seedvr2.encode_mp4"]
    assert encode_marks and encode_marks[0]["audio"] == enc.AUDIO_MUX_FAILED
    assert encode_marks[0]["audio"] != True  # noqa: E712 -- must not regress to the requested bool


def test_video_mux_success_reports_muxed_with_no_warning(monkeypatch):
    from src.pipelines.outputs import ProgressGenerationOutput

    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    enc = _wire_fake_audio_outcome(monkeypatch, audio_outcome="muxed")
    rec = _RecordingProfiler()
    monkeypatch.setattr(m, "get_profiler", lambda: rec)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=True))

    outputs = []
    out = pipe.process(_video_input(), outputs.append)

    warnings = [
        o for o in outputs
        if isinstance(o, ProgressGenerationOutput) and o.icon and o.icon.name == "alert-triangle"
    ]
    assert not warnings

    encode_marks = [fields for event, fields in rec.events if event == "seedvr2.encode_mp4"]
    assert encode_marks and encode_marks[0]["audio"] == enc.AUDIO_MUXED
    # The MUXED file, not the video-only one, is what gets moved into place.
    published = Path(out.output["video"][0])
    assert published.read_text() == "muxed"


# -- video path: file-set ownership through the real encode/mux/probe code --
#
# Unlike `_wire_fake_audio_outcome` above (which fakes the whole
# `encode_video_with_audio` function), these fake only the OS boundary
# (`subprocess.run`, `shutil.which`) that `mux_audio_into_video` and
# `probe_audio_stream` call, so the REAL classification and file-ownership
# logic in `encode.py` runs end to end through `_process_video`'s attempt/
# publish machinery -- and the exact set of files left on disk afterward is
# asserted, not just the returned path.

def _fake_ffmpeg_mux(*, succeeds: bool, writes_partial_on_failure: bool = False):
    def _run(cmd, **kwargs):
        out_path = cmd[-1]
        if succeeds:
            Path(out_path).write_text("muxed")
            return SimpleNamespace(returncode=0, stderr=b"")
        if writes_partial_on_failure:
            Path(out_path).write_text("partial")
        return SimpleNamespace(returncode=1, stderr=b"synthetic encoder error")
    return _run


def _wire_real_audio_boundary(monkeypatch, *, ffprobe_present: bool, has_audio_stream: bool,
                              mux_succeeds: bool = True, mux_writes_partial_on_failure: bool = False):
    """Leaves `encode_video_with_audio`/`mux_audio_into_video`/`probe_audio_stream`
    as the REAL implementations; fakes only what they shell out to."""
    from src.pipelines.pipes.generator.seedvr2 import encode as enc

    monkeypatch.setattr(
        enc.shutil, "which", lambda name: (f"/usr/bin/{name}" if name != "ffprobe" or ffprobe_present else None),
    )

    def _run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            streams = [{"codec_type": "audio"}] if has_audio_stream else []
            return SimpleNamespace(returncode=0, stdout=f'{{"streams": {streams!r}}}'.replace("'", '"'))
        return _fake_ffmpeg_mux(succeeds=mux_succeeds, writes_partial_on_failure=mux_writes_partial_on_failure)(cmd, **kwargs)

    monkeypatch.setattr(enc.subprocess, "run", _run)
    return enc


def test_video_mux_success_leaves_only_the_final_file_on_disk(monkeypatch):
    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    _wire_real_audio_boundary(monkeypatch, ffprobe_present=True, has_audio_stream=True, mux_succeeds=True)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=True))

    out = pipe.process(_video_input(), lambda o: None)

    final_path = out.output["video"][0]
    assert glob.glob(f"{final_path}*") == [final_path]
    assert Path(final_path).read_text() == "muxed"


def test_video_mux_failure_leaves_only_the_silent_video_with_warning(monkeypatch):
    from src.pipelines.outputs import ProgressGenerationOutput

    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    _wire_real_audio_boundary(
        monkeypatch, ffprobe_present=True, has_audio_stream=True,
        mux_succeeds=False, mux_writes_partial_on_failure=True,
    )
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=True))

    outputs = []
    out = pipe.process(_video_input(), outputs.append)

    final_path = out.output["video"][0]
    # Only the published (silent) video remains -- the partial `.audio.mp4`
    # ffmpeg wrote before failing is gone, and so is the pre-publish attempt file.
    assert glob.glob(f"{final_path}*") == [final_path]
    assert Path(final_path).read_text().startswith("video:")

    warnings = [
        o for o in outputs
        if isinstance(o, ProgressGenerationOutput) and o.icon and o.icon.name == "alert-triangle"
    ]
    assert warnings


def test_video_probe_failure_leaves_only_the_silent_video_with_warning(monkeypatch):
    from src.pipelines.outputs import ProgressGenerationOutput

    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    # ffprobe missing entirely -- an INCONCLUSIVE probe, not a confirmed-silent
    # source; `has_audio_stream` is irrelevant here since ffprobe never runs.
    _wire_real_audio_boundary(monkeypatch, ffprobe_present=False, has_audio_stream=True)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=True))

    outputs = []
    out = pipe.process(_video_input(), outputs.append)

    final_path = out.output["video"][0]
    # No mux was ever attempted (nothing safe to hand ffmpeg) -- only the
    # video-only attempt survives, published under `final_path`.
    assert glob.glob(f"{final_path}*") == [final_path]
    assert Path(final_path).read_text().startswith("video:")

    warnings = [
        o for o in outputs
        if isinstance(o, ProgressGenerationOutput) and o.icon and o.icon.name == "alert-triangle"
    ]
    assert warnings


def test_video_confirmed_silent_source_leaves_only_the_silent_video_no_warning(monkeypatch):
    from src.pipelines.outputs import ProgressGenerationOutput

    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    _wire_real_audio_boundary(monkeypatch, ffprobe_present=True, has_audio_stream=False)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=True))

    outputs = []
    out = pipe.process(_video_input(), outputs.append)

    final_path = out.output["video"][0]
    assert glob.glob(f"{final_path}*") == [final_path]

    warnings = [
        o for o in outputs
        if isinstance(o, ProgressGenerationOutput) and o.icon and o.icon.name == "alert-triangle"
    ]
    assert not warnings  # a confirmed-silent source is not a failure


def test_video_encoder_failure_leaves_no_temp_files(monkeypatch):
    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)

    def _failing_stream_encode(frames, out_path, fps, *, codec="libx264", crf=18, audio=None):
        for _ in frames:  # drain the generator like a real encoder would
            pass
        raise RuntimeError("ffmpeg failed (exit 1): synthetic video defect")

    monkeypatch.setattr(
        "src.pipelines.pipes.generator.seedvr2.encode.encode_frames_stream_to_mp4",
        _failing_stream_encode,
    )

    created: list[str] = []
    import tempfile
    orig_ntf = tempfile.NamedTemporaryFile

    def _tracking_ntf(*args, **kwargs):
        f = orig_ntf(*args, **kwargs)
        created.append(f.name)
        return f

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _tracking_ntf)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=True))

    with pytest.raises(RuntimeError, match="synthetic video defect"):
        pipe.process(_video_input(), lambda o: None)

    final_path = created[0]  # the first NamedTemporaryFile _process_video creates
    assert glob.glob(f"{final_path}*") == []


# -- video path: cancellation and publish-step cleanup ownership -------------
#
# `_clips` (stream.py) used to end its generator normally on cancellation, which
# `stream_output_frames` cannot tell apart from a genuinely finished clip: the
# held-back overlap tail gets flushed, the encoder sees a clean EOF, and
# `_process_video` publishes the truncated clip as if it were complete. These
# fixtures drive the real `process()` (fake generator/source/encoder, no ffmpeg)
# and assert the fix end to end: cancellation propagates as `SamplingCancelled`,
# nothing reaches the gallery, and no attempt/placeholder file survives.

def _track_tempfiles(monkeypatch):
    """Record every path `tempfile.NamedTemporaryFile` hands out during the
    test, so the assertions below can name the exact `final_path` the pipe
    picked without reaching into its internals."""
    import tempfile

    created: list[str] = []
    orig_ntf = tempfile.NamedTemporaryFile

    def _tracking_ntf(*args, **kwargs):
        f = orig_ntf(*args, **kwargs)
        created.append(f.name)
        return f

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _tracking_ntf)
    return created


def _no_gallery_video(outputs) -> bool:
    from src.pipelines.outputs import GalleryGenerationOutput

    return not any(
        isinstance(o, GalleryGenerationOutput) and o.videos for o in outputs
    )


def test_video_cancellation_after_a_batch_raises_and_publishes_nothing(monkeypatch):
    # batch_size=5 over 20 frames -> 4 windows (0:5, 5:10, 10:15, 15:20), so a
    # cancellation flipped after the first is a genuine mid-stream cancel, not
    # the last-batch edge case covered separately below.
    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen, n_frames=20)
    created = _track_tempfiles(monkeypatch)

    calls = {"n": 0}

    def _is_cancelled():
        # A ONE-SHOT signal (true on exactly the second per-batch check, false
        # everywhere else) isolates `_clips`' own check: a monotone "stays
        # cancelled forever" flag would also be caught by the separate
        # pre-publish check below, whichever one has the bug.
        calls["n"] += 1
        return calls["n"] == 2

    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=False))
    outputs = []

    with pytest.raises(SamplingCancelled):
        pipe.process(_video_input(), outputs.append, is_cancelled=_is_cancelled)

    assert _no_gallery_video(outputs)
    assert gen.released >= 1
    final_path = created[0]
    assert glob.glob(f"{final_path}*") == []


def test_video_cancellation_during_final_batch_raises_and_publishes_nothing(monkeypatch):
    # 10 frames at batch_size=5 -> exactly 2 windows, so both of `_clips`' own
    # per-batch checks pass (calls 1-2 return False) and the whole clip finishes
    # encoding; cancellation is only ever observed at the NEW check right before
    # publication (call 3) -- proving that checkpoint, not `_clips`', is what
    # catches a cancellation raised during the final batch's own decode/encode.
    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen, n_frames=10)
    created = _track_tempfiles(monkeypatch)

    calls = {"n": 0}

    def _is_cancelled():
        calls["n"] += 1
        return calls["n"] > 2

    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=False))
    outputs = []

    with pytest.raises(SamplingCancelled):
        pipe.process(_video_input(), outputs.append, is_cancelled=_is_cancelled)

    assert calls["n"] == 3, "the pre-publish check must be the one that caught this"
    assert _no_gallery_video(outputs)
    assert gen.released >= 1
    final_path = created[0]
    assert glob.glob(f"{final_path}*") == []


def test_video_publish_failure_leaves_no_temp_survivors(monkeypatch):
    import os as os_mod

    gen = _FakeGen(oom_at_or_above=999)
    _wire(monkeypatch, gen)
    created = _track_tempfiles(monkeypatch)

    def _failing_replace(src, dst):
        raise OSError("synthetic replace failure")

    monkeypatch.setattr(os_mod, "replace", _failing_replace)
    pipe = GeneratorSeedVR2Pipe(_base_config(batch_size=5, keep_audio=False))

    with pytest.raises(OSError, match="synthetic replace failure"):
        pipe.process(_video_input(), lambda o: None)

    # A publish failure (os.replace itself raising, here simulating a
    # cross-device rename or a disk error) must leave neither the attempt file
    # nor the empty final placeholder behind.
    final_path = created[0]
    assert glob.glob(f"{final_path}*") == []
    assert gen.released >= 1
