"""Tests for the streamed SeedVR2 video path.

The eager pipeline the streaming one replaces is kept verbatim below as
:func:`_eager_reference` -- the oracle every equivalence test compares against.
It is a copy of the pre-streaming ``_upscale_frames`` body, so a divergence in
window planning, reversed padding, overlap blending, prepend trimming or the
output/source pairing shows up as a frame-for-frame mismatch rather than as a
plausible-looking but different clip.

Nothing here loads a model or touches a GPU: the upscaler is a deterministic
pure function of its input clip, the video reader is a list, and the mp4
encoder writes the frames it is handed to a ``.npy`` file so a test can read
back exactly what was published.
"""

from __future__ import annotations

import gc
import subprocess
import tracemalloc
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.generator.seedvr2 import batching as B
from src.pipelines.pipes.generator.seedvr2 import encode as enc
from src.pipelines.pipes.generator.seedvr2 import main as m
from src.pipelines.pipes.generator.seedvr2 import stream as S
from src.pipelines.pipes.generator.seedvr2.main import GeneratorSeedVR2Pipe


# -- fixtures: frames and a deterministic "upscale" --------------------------

def _frame(index: int, size: int = 8) -> np.ndarray:
    """A frame whose every pixel depends on its index, so a misplaced,
    duplicated or dropped frame cannot pass as another."""
    rng = np.random.default_rng(1000 + index)
    return rng.integers(0, 256, size=(size, size, 3), dtype=np.uint8)


def _frames(n: int, size: int = 8) -> list:
    return [_frame(i, size) for i in range(n)]


def _upscale_clip(clip: np.ndarray) -> np.ndarray:
    """Deterministic per-frame transform that preserves T and doubles the
    spatial size, matching the real generator's contract."""
    doubled = np.repeat(np.repeat(clip, 2, axis=1), 2, axis=2)
    return ((doubled.astype(np.uint16) * 3 + 17) % 256).astype(np.uint8)


def _upscale_all(clips):
    return [_upscale_clip(c) for c in clips]


def _upscale_stream(clips):
    for clip in clips:
        yield _upscale_clip(clip)


# -- the eager pipeline, kept verbatim as the oracle -------------------------

def _eager_reference(
    resized_np, batch_size, *, temporal_overlap, prepend_frames, uniform,
    prepare=None, correct=None,
):
    seq = (
        B.pad_reversed(resized_np, prepend_frames, prepend=True)
        if prepend_frames > 0 else list(resized_np)
    )
    windows, overlap = B.plan_batches(len(seq), batch_size, temporal_overlap)
    clips, true_lens = [], []
    for (start, end) in windows:
        frames = seq[start:end]
        padded, true_len = B.pad_batch(frames, batch_size, uniform=uniform)
        if prepare is not None:
            padded = prepare(padded)
        clips.append(np.stack(padded, axis=0))
        true_lens.append(true_len)

    decoded = _upscale_all(clips)

    batch_frames = [
        [arr[i] for i in range(min(tl, arr.shape[0]))]
        for arr, tl in zip(decoded, true_lens)
    ]
    stitched = B.stitch_batches(batch_frames, overlap)
    if prepend_frames > 0:
        stitched = stitched[prepend_frames:]
    if correct is not None:
        sources = [resized_np[min(j, len(resized_np) - 1)] for j in range(len(stitched))]
        return correct(stitched, sources)
    return stitched


def _streamed(resized_np, batch_size, *, temporal_overlap, prepend_frames, uniform,
              prepare=None, correct=None):
    return list(S.stream_output_frames(
        iter(resized_np),
        batch_size=batch_size, temporal_overlap=temporal_overlap,
        prepend_frames=prepend_frames, uniform=uniform,
        upscale_clips=_upscale_stream, prepare_clip=prepare, correct=correct,
    ))


def _assert_same(streamed, eager):
    assert len(streamed) == len(eager)
    for i, (a, b) in enumerate(zip(streamed, eager)):
        assert np.array_equal(a, b), f"frame {i} differs from the eager reference"


# -- geometry: the stream forms reproduce batching.py exactly ----------------

@pytest.mark.parametrize("total", [1, 2, 5, 7, 9, 13, 20, 21, 33])
@pytest.mark.parametrize("batch_size", [1, 5, 9, 13])
@pytest.mark.parametrize("overlap", [0, 1, 2, 4, 8, 13])
def test_iter_windows_matches_plan_batches(total, batch_size, overlap):
    frames = _frames(total, size=2)
    expected, _eff = B.plan_batches(total, batch_size, overlap)

    got = [(start, len(window)) for start, window in S.iter_windows(frames, batch_size, overlap)]

    assert got == [(start, end - start) for start, end in expected]


@pytest.mark.parametrize("total", [1, 2, 3, 8])
@pytest.mark.parametrize("count", [0, 1, 2, 3, 5, 9])
def test_prepend_reversed_stream_matches_pad_reversed(total, count):
    frames = _frames(total, size=2)

    got = list(S.prepend_reversed_stream(iter(frames), count))
    expected = B.pad_reversed(frames, count, prepend=True)

    assert len(got) == len(expected)
    for a, b in zip(got, expected):
        assert np.array_equal(a, b)


@pytest.mark.parametrize("overlap", [0, 1, 2, 3, 5])
def test_overlap_stitcher_matches_stitch_batches(overlap):
    batches = [_frames(6, size=4), _frames(6, size=4), _frames(4, size=4)]
    expected = B.stitch_batches(batches, overlap)

    stitcher = S.OverlapStitcher(overlap)
    got = []
    for batch in batches:
        got.extend(stitcher.push(batch))
    got.extend(stitcher.flush())

    assert len(got) == len(expected)
    for a, b in zip(got, expected):
        assert np.array_equal(a, b)


def test_overlap_stitcher_holds_back_only_the_overlap_tail():
    stitcher = S.OverlapStitcher(3)

    released = stitcher.push(_frames(9, size=2))

    assert len(released) == 6
    assert len(stitcher.flush()) == 3


# -- frame-for-frame equivalence with the eager pipeline ---------------------

@pytest.mark.parametrize("total,batch_size,overlap,prepend,uniform", [
    (20, 9, 0, 0, True),        # plain, non-uniform tail padded to the batch
    (20, 9, 0, 0, False),       # tail padded only to the next 4n+1
    (20, 9, 4, 0, True),        # overlap crossfade
    (20, 9, 4, 3, True),        # overlap + reversed-head prepend
    (20, 5, 2, 5, False),       # prepend longer than the step
    (21, 5, 1, 0, True),        # exact multiple of the step
    (7, 9, 4, 2, True),         # single short batch, uniform-padded
    (3, 5, 2, 6, True),         # prepend overflows the clip
    (1, 1, 0, 0, True),         # degenerate still-image batch
    (13, 13, 13, 0, True),      # overlap >= batch_size -> reset to 0
])
def test_streamed_output_matches_eager_reference(total, batch_size, overlap, prepend, uniform):
    frames = _frames(total)

    streamed = _streamed(
        frames, batch_size, temporal_overlap=overlap, prepend_frames=prepend, uniform=uniform,
    )
    eager = _eager_reference(
        frames, batch_size, temporal_overlap=overlap, prepend_frames=prepend, uniform=uniform,
    )

    _assert_same(streamed, eager)
    assert len(streamed) == total


def test_streamed_output_matches_eager_reference_with_input_noise():
    frames = _frames(20)
    pipe = GeneratorSeedVR2Pipe({})

    def _prepare(padded):
        return [pipe._apply_input_noise(f, 0.4, 7) for f in padded]

    streamed = _streamed(
        frames, 9, temporal_overlap=4, prepend_frames=2, uniform=True, prepare=_prepare,
    )
    eager = _eager_reference(
        frames, 9, temporal_overlap=4, prepend_frames=2, uniform=True, prepare=_prepare,
    )

    _assert_same(streamed, eager)


def test_streamed_output_matches_eager_reference_with_color_correction():
    from src.pipelines.pipes.generator.seedvr2.color_fix import color_correct_batch

    frames = _frames(20)

    def _correct(targets, sources):
        return color_correct_batch(targets, sources, "adain", device="cpu")

    streamed = _streamed(
        frames, 9, temporal_overlap=4, prepend_frames=3, uniform=True, correct=_correct,
    )
    eager = _eager_reference(
        frames, 9, temporal_overlap=4, prepend_frames=3, uniform=True, correct=_correct,
    )

    _assert_same(streamed, eager)


def test_streamed_color_correction_pairs_each_output_with_its_own_source():
    """The source ring must hand frame ``j`` the source at index ``j`` -- an
    off-by-one here still produces a plausible clip, so it is asserted directly
    rather than only through the oracle."""
    frames = _frames(20)
    seen = []

    def _correct(targets, sources):
        seen.extend(sources)
        return targets

    _streamed(frames, 9, temporal_overlap=4, prepend_frames=3, uniform=True, correct=_correct)

    assert len(seen) == len(frames)
    for j, source in enumerate(seen):
        assert np.array_equal(source, frames[j]), f"output {j} was paired with the wrong source"


def test_streamed_cancellation_raises_instead_of_ending_the_stream():
    """A `return` from `_clips` on cancellation would end the generator
    normally -- indistinguishable from a genuinely finished clip -- so the
    caller's `stitcher.flush()` would release the held-back overlap tail and
    publish a cancelled run as if it were complete. Cancellation must instead
    propagate as `SamplingCancelled`, the same exception every other sampling
    loop raises, so a caller can never mistake it for a clean end."""
    from src.platform.runtime.native.errors import SamplingCancelled

    frames = _frames(20)
    state = {"batches": 0}

    def _cancelled():
        return state["batches"] >= 2

    def _on_batch(done):
        state["batches"] = done

    stream = S.stream_output_frames(
        iter(frames), batch_size=9, temporal_overlap=0, prepend_frames=0, uniform=True,
        upscale_clips=_upscale_stream, on_batch=_on_batch, is_cancelled=_cancelled,
    )

    collected = []
    with pytest.raises(SamplingCancelled):
        for frame in stream:
            collected.append(frame)

    # The frames already handed out before cancellation was noticed (streamed
    # straight into ffmpeg in production) are real -- but nothing past them,
    # in particular no flushed overlap tail, is ever yielded.
    assert 0 < len(collected) < len(frames)
    eager_prefix = _eager_reference(
        frames, 9, temporal_overlap=0, prepend_frames=0, uniform=True,
    )[:len(collected)]
    _assert_same(collected, eager_prefix)


def test_streamed_upscaler_is_closed_when_the_consumer_stops_early():
    frames = _frames(20)
    closed = {"value": False}

    def _upscale(clips):
        try:
            for clip in clips:
                yield _upscale_clip(clip)
        finally:
            closed["value"] = True

    stream = S.stream_output_frames(
        iter(frames), batch_size=9, temporal_overlap=0, prepend_frames=0, uniform=True,
        upscale_clips=_upscale,
    )
    next(stream)
    stream.close()

    assert closed["value"], "abandoning the stream must release the upscaler's GPU residency"


# -- the memory bound --------------------------------------------------------

def _peak_bytes(run) -> int:
    gc.collect()
    tracemalloc.start()
    try:
        run()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def _streamed_peak(total: int, size: int = 64) -> int:
    def _run():
        source = (_frame(i, size) for i in range(total))
        for _frame_out in S.stream_output_frames(
            source, batch_size=9, temporal_overlap=4, prepend_frames=2, uniform=True,
            upscale_clips=_upscale_stream,
        ):
            pass

    return _peak_bytes(_run)


def _eager_peak(total: int, size: int = 64) -> int:
    def _run():
        _eager_reference(
            [_frame(i, size) for i in range(total)], 9,
            temporal_overlap=4, prepend_frames=2, uniform=True,
        )

    return _peak_bytes(_run)


def test_streamed_peak_memory_is_independent_of_clip_length():
    """Peak traced bytes must stay flat as the clip grows -- the whole point of
    the streaming seam. numpy's data allocations are traced by tracemalloc, so
    this measures the frame buffers themselves, not a proxy."""
    peaks = {n: _streamed_peak(n) for n in (16, 64, 256)}

    assert peaks[256] < peaks[16] * 1.5, f"streamed peak grew with clip length: {peaks}"
    assert peaks[256] < _eager_peak(256) / 2, (
        f"streamed peak is not materially below the eager pipeline's: {peaks}"
    )


# -- pipe-level: OOM replay, publication and cleanup -------------------------

class _StubSource:
    """Replayable stand-in for ``ResizedFrameSource``."""

    def __init__(self, frames, fps=24.0, video_path="/fake.mp4"):
        self._frames = frames
        self.fps = fps
        self.video_path = video_path
        self.frame_count_hint = len(frames)
        self.frame_shape = (int(frames[0].shape[0]), int(frames[0].shape[1]))
        self.opened = 0
        self.closed = 0

    def probe(self):
        pass

    def iter_frames(self):
        self.opened += 1
        try:
            for frame in self._frames:
                yield frame
        finally:
            self.closed += 1


class _StubGen:
    """Upscaler that OOMs after ``oom_after_clips`` clips of its first attempt."""

    def __init__(self, *, oom_after_clips=None, fail_with=None):
        self.oom_after_clips = oom_after_clips
        self.fail_with = fail_with
        self.attempts = 0
        self.batch_calls = []
        self.released = 0

    def upscale_video(self, clips, prompt_embedding, *, seed, latent_noise_scale,
                      tile_size=None, tile_overlap=None):
        self.attempts += 1
        attempt = self.attempts
        seen = 0
        for clip in clips:
            seen += 1
            if seen == 1:
                self.batch_calls.append(int(clip.shape[0]))
            if self.fail_with is not None and attempt == 1 and seen > 1:
                raise self.fail_with
            if (self.oom_after_clips is not None and attempt == 1
                    and seen > self.oom_after_clips):
                raise torch.cuda.OutOfMemoryError("synthetic streamed OOM")
            yield _upscale_clip(clip)

    def release_gpu(self):
        self.released += 1


def _npy_encoder(frames, out_path, fps, *, codec="libx264", crf=18, audio=None):
    """Stub encoder that persists exactly the frames it was streamed, so a test
    can read back what would have been published.

    It creates the file BEFORE consuming the stream, the way a real streaming
    encoder does: a producer that fails mid-clip must leave a partial file for
    the pipe's cleanup to remove, not nothing at all."""
    Path(str(out_path)).write_bytes(b"partial")
    stacked = np.stack(list(frames), axis=0)
    with open(str(out_path), "wb") as handle:
        np.save(handle, stacked)
    return Path(out_path)


def _wire_pipe(monkeypatch, gen, frames):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(m, "build_native_generator", lambda bundle, device="cuda": gen)
    source = _StubSource(frames)
    monkeypatch.setattr(
        "src.pipelines.pipes.generator.seedvr2.frame_source.ResizedFrameSource",
        lambda path, **kw: source,
    )
    monkeypatch.setattr(
        "src.pipelines.pipes.generator.seedvr2.encode.encode_frames_stream_to_mp4",
        _npy_encoder,
    )
    return source


def _config(**over):
    cfg = {
        "scale": 2.0, "target_short_side": 0, "color_correction": "none",
        "latent_noise_scale": 0.0, "input_noise_scale": 0.0, "seed": 0,
        "device": "cuda", "tile_size": 1024, "tile_overlap": 128,
        "batch_size": 9, "temporal_overlap": 0, "prepend_frames": 0,
        "uniform_batch_size": True, "keep_audio": False,
    }
    cfg.update(over)
    return cfg


def _pipe_input():
    bundle = SimpleNamespace(prompt_embedding=torch.zeros(1))
    return PipeInput(input={"video": ["/fake.mp4"], "model": bundle, "seed": [0]})


def _published_frames(out) -> np.ndarray:
    return np.load(out.output["video"][0])


def test_oom_retry_replays_the_clip_with_no_duplicate_or_lost_frames(monkeypatch):
    frames = _frames(20)

    clean_gen = _StubGen()
    _wire_pipe(monkeypatch, clean_gen, frames)
    clean = GeneratorSeedVR2Pipe(_config(batch_size=5, temporal_overlap=2)).process(
        _pipe_input(), lambda o: None,
    )
    expected = _published_frames(clean)

    retry_gen = _StubGen(oom_after_clips=2)
    source = _wire_pipe(monkeypatch, retry_gen, frames)
    retried = GeneratorSeedVR2Pipe(_config(batch_size=9, temporal_overlap=2)).process(
        _pipe_input(), lambda o: None,
    )
    published = _published_frames(retried)

    assert retry_gen.batch_calls == [9, 5]
    assert source.opened == 2, "the retry must re-open the reader from frame 0"
    assert published.shape[0] == len(frames)
    assert np.array_equal(published, expected), (
        "the replayed attempt must produce exactly the clean run's frames"
    )


def test_failed_attempts_leave_no_output_behind(monkeypatch):
    frames = _frames(20)
    gen = _StubGen(fail_with=RuntimeError("synthetic decode failure"))
    _wire_pipe(monkeypatch, gen, frames)
    pipe = GeneratorSeedVR2Pipe(_config(batch_size=5))

    before = set(Path("/tmp").glob("*.mp4*"))
    with pytest.raises(RuntimeError, match="synthetic decode failure"):
        pipe.process(_pipe_input(), lambda o: None)

    leftovers = set(Path("/tmp").glob("*.mp4*")) - before
    assert not leftovers, f"a failed attempt left files behind: {leftovers}"
    assert gen.released >= 1


def test_oom_retry_leaves_only_the_published_output(monkeypatch):
    frames = _frames(20)
    gen = _StubGen(oom_after_clips=1)
    _wire_pipe(monkeypatch, gen, frames)

    out = GeneratorSeedVR2Pipe(_config(batch_size=9)).process(_pipe_input(), lambda o: None)

    published = Path(out.output["video"][0])
    assert published.exists()
    assert not list(published.parent.glob(f"{published.name}.attempt*"))
    # What survives is the retry's complete clip, never the OOMed attempt's
    # partial file.
    assert np.load(published).shape[0] == len(frames)


def test_streamed_reader_is_released_on_failure(monkeypatch):
    frames = _frames(20)
    gen = _StubGen(fail_with=RuntimeError("synthetic decode failure"))
    source = _wire_pipe(monkeypatch, gen, frames)
    pipe = GeneratorSeedVR2Pipe(_config(batch_size=5))

    with pytest.raises(RuntimeError):
        pipe.process(_pipe_input(), lambda o: None)

    assert source.closed == source.opened, "every opened reader must be closed"


# -- the streaming mp4 encoder ----------------------------------------------

class _FakeFFmpeg:
    """Stands in for a running ffmpeg: collects the piped rawvideo bytes and
    writes them to the output path on a clean close."""

    def __init__(self, out_path, returncode=0, write_output=True):
        self._out_path = Path(out_path)
        self._returncode = returncode
        self._write_output = write_output
        self.chunks = []
        self.killed = False
        self.stdin = self
        self.closed = False
        self.returncode = None

    def write(self, data):
        self.chunks.append(bytes(data))

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._write_output and not self.killed:
            self._out_path.write_bytes(b"".join(self.chunks))

    def wait(self, timeout=None):
        self.returncode = self._returncode
        return self._returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


def _wire_ffmpeg(monkeypatch, *, returncode=0, write_output=True):
    monkeypatch.setattr(enc.shutil, "which", lambda name: f"/usr/bin/{name}")
    created = []

    def _popen(cmd, stdin=None, stdout=None, stderr=None):
        out_path = cmd[-1]
        proc = _FakeFFmpeg(out_path, returncode=returncode, write_output=write_output)
        created.append(proc)
        return proc

    monkeypatch.setattr(enc.subprocess, "Popen", _popen)
    return created


def test_stream_encoder_pipes_every_frame_in_order(monkeypatch, tmp_path):
    created = _wire_ffmpeg(monkeypatch)
    frames = _frames(6, size=4)
    out_path = tmp_path / "out.mp4"

    enc.encode_frames_stream_to_mp4(iter(frames), out_path, 24.0)

    piped = b"".join(created[0].chunks)
    assert piped == b"".join(f.tobytes() for f in frames)
    assert out_path.exists()


def test_stream_encoder_removes_the_partial_file_when_the_producer_fails(monkeypatch, tmp_path):
    created = _wire_ffmpeg(monkeypatch)
    out_path = tmp_path / "out.mp4"

    def _failing():
        yield _frame(0, 4)
        yield _frame(1, 4)
        raise torch.cuda.OutOfMemoryError("synthetic mid-encode OOM")

    with pytest.raises(torch.cuda.OutOfMemoryError):
        enc.encode_frames_stream_to_mp4(_failing(), out_path, 24.0)

    assert created[0].killed, "ffmpeg must be killed when the frame producer fails"
    assert not out_path.exists(), "a failed encode must not leave a playable-looking file"


def test_stream_encoder_removes_the_output_when_ffmpeg_exits_non_zero(monkeypatch, tmp_path):
    _wire_ffmpeg(monkeypatch, returncode=1)
    out_path = tmp_path / "out.mp4"

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        enc.encode_frames_stream_to_mp4(iter(_frames(3, size=4)), out_path, 24.0)

    assert not out_path.exists()


def test_stream_encoder_rejects_a_frame_size_change_mid_stream(monkeypatch, tmp_path):
    _wire_ffmpeg(monkeypatch)
    out_path = tmp_path / "out.mp4"

    with pytest.raises(ValueError, match="frame size changed mid-stream"):
        enc.encode_frames_stream_to_mp4(
            iter([_frame(0, 4), _frame(1, 8)]), out_path, 24.0,
        )

    assert not out_path.exists()


def test_stream_encoder_refuses_audio(monkeypatch, tmp_path):
    _wire_ffmpeg(monkeypatch)

    with pytest.raises(TypeError, match="mux audio"):
        enc.encode_frames_stream_to_mp4(
            iter(_frames(2, size=4)), tmp_path / "out.mp4", 24.0, audio="/some.wav",
        )


def test_stream_encoder_raises_on_an_empty_stream(monkeypatch, tmp_path):
    _wire_ffmpeg(monkeypatch)

    with pytest.raises(ValueError, match="no frames to encode"):
        enc.encode_frames_stream_to_mp4(iter([]), tmp_path / "out.mp4", 24.0)


def test_stream_encoder_uses_the_same_ffmpeg_argv_as_the_eager_encoder(monkeypatch, tmp_path):
    from src.pipelines.pipes._shared.media.video_encode import _build_ffmpeg_args

    seen = {}

    monkeypatch.setattr(enc.shutil, "which", lambda name: f"/usr/bin/{name}")

    def _popen(cmd, stdin=None, stdout=None, stderr=None):
        seen["cmd"] = cmd
        return _FakeFFmpeg(cmd[-1])

    monkeypatch.setattr(enc.subprocess, "Popen", _popen)
    out_path = tmp_path / "out.mp4"

    enc.encode_frames_stream_to_mp4(iter(_frames(2, size=4)), out_path, 24.0)

    assert seen["cmd"] == _build_ffmpeg_args(
        width=4, height=4, fps=24.0, codec="libx264", crf=18,
        out_path=out_path, audio_path=None,
    )


def test_stream_encoder_times_out_and_cleans_up(monkeypatch, tmp_path):
    monkeypatch.setattr(enc.shutil, "which", lambda name: f"/usr/bin/{name}")

    class _Hanging(_FakeFFmpeg):
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)

    def _popen(cmd, stdin=None, stdout=None, stderr=None):
        return _Hanging(cmd[-1])

    monkeypatch.setattr(enc.subprocess, "Popen", _popen)
    out_path = tmp_path / "out.mp4"

    with pytest.raises(RuntimeError, match="timed out"):
        enc.encode_frames_stream_to_mp4(iter(_frames(2, size=4)), out_path, 24.0)

    assert not out_path.exists()


def test_stream_encoder_closes_stderr_handle_on_a_popen_spawn_failure(monkeypatch, tmp_path):
    """The stderr diagnostic file is opened before ffmpeg is spawned; if
    ``Popen`` itself raises (a spawn failure -- missing binary despite the
    ``which`` check above, a permission error, ...) that handle must still be
    closed rather than leaked, and no output file must ever appear."""
    monkeypatch.setattr(enc.shutil, "which", lambda name: f"/usr/bin/{name}")

    created_handles = []

    class _TrackedHandle:
        def __init__(self, real):
            self._real = real
            self.closed = False

        def close(self):
            self.closed = True
            self._real.close()

        def __getattr__(self, name):
            return getattr(self._real, name)

    orig_temporary_file = enc.tempfile.TemporaryFile

    def _tracking_temporary_file(*args, **kwargs):
        handle = _TrackedHandle(orig_temporary_file(*args, **kwargs))
        created_handles.append(handle)
        return handle

    monkeypatch.setattr(enc.tempfile, "TemporaryFile", _tracking_temporary_file)

    def _popen(cmd, stdin=None, stdout=None, stderr=None):
        raise OSError("synthetic spawn failure -- ffmpeg never launched")

    monkeypatch.setattr(enc.subprocess, "Popen", _popen)
    out_path = tmp_path / "out.mp4"

    with pytest.raises(OSError, match="synthetic spawn failure"):
        enc.encode_frames_stream_to_mp4(iter(_frames(2, size=4)), out_path, 24.0)

    assert len(created_handles) == 1
    assert created_handles[0].closed, "the stderr handle must be closed even when Popen itself raises"


class _RaisingCloseFFmpeg(_FakeFFmpeg):
    """A fake ffmpeg whose ``stdin.close()`` always raises -- simulating a
    buffered-write flush failing against an already-broken/dead child.
    Cleanup must swallow this, never let it replace whatever exception is
    actually propagating."""

    def close(self):
        raise OSError("synthetic stdin close failure after kill")


def test_stream_encoder_stdin_close_failure_does_not_mask_the_producer_error(monkeypatch, tmp_path):
    monkeypatch.setattr(enc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(enc.subprocess, "Popen", lambda cmd, **kw: _RaisingCloseFFmpeg(cmd[-1]))
    out_path = tmp_path / "out.mp4"

    def _failing():
        yield _frame(0, 4)
        raise RuntimeError("synthetic producer failure")

    # Without independent, swallowed cleanup steps, the OSError raised by the
    # `finally` block's stdin.close() would replace this RuntimeError.
    with pytest.raises(RuntimeError, match="synthetic producer failure"):
        enc.encode_frames_stream_to_mp4(_failing(), out_path, 24.0)

    assert not out_path.exists()


def test_stream_encoder_stdin_close_failure_does_not_mask_sampling_cancelled(monkeypatch, tmp_path):
    from src.platform.runtime.native.errors import SamplingCancelled

    monkeypatch.setattr(enc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(enc.subprocess, "Popen", lambda cmd, **kw: _RaisingCloseFFmpeg(cmd[-1]))
    out_path = tmp_path / "out.mp4"

    def _cancelled():
        yield _frame(0, 4)
        raise SamplingCancelled()

    # Same as above, but for the cancellation contract specifically: a
    # cleanup-step OSError must not surface in place of SamplingCancelled.
    with pytest.raises(SamplingCancelled):
        enc.encode_frames_stream_to_mp4(_cancelled(), out_path, 24.0)

    assert not out_path.exists()


class _EarlyClosingFFmpeg(_FakeFFmpeg):
    """Simulates a child that closes its stdin (raising ``BrokenPipeError`` on
    write) after ``breaks_after`` frames, then would exit cleanly (returncode
    0) if ever waited on -- the scenario item 3 guards against: an encode the
    child cut short must never be accepted just because it happens to exit
    0."""

    def __init__(self, out_path, breaks_after, **kw):
        super().__init__(out_path, returncode=0, **kw)
        self._breaks_after = breaks_after
        self._writes = 0

    def write(self, data):
        self._writes += 1
        if self._writes > self._breaks_after:
            raise BrokenPipeError("synthetic early pipe close")
        super().write(data)


def test_stream_encoder_raises_when_the_pipe_breaks_before_the_producer_is_exhausted(monkeypatch, tmp_path):
    monkeypatch.setattr(enc.shutil, "which", lambda name: f"/usr/bin/{name}")
    created = []

    def _popen(cmd, stdin=None, stdout=None, stderr=None):
        proc = _EarlyClosingFFmpeg(cmd[-1], breaks_after=1)
        created.append(proc)
        return proc

    monkeypatch.setattr(enc.subprocess, "Popen", _popen)
    out_path = tmp_path / "out.mp4"

    with pytest.raises(RuntimeError, match="closed its input before"):
        enc.encode_frames_stream_to_mp4(iter(_frames(4, size=4)), out_path, 24.0)

    assert not out_path.exists(), "an incomplete encode must never publish a file, even at exit 0"


def test_stream_encoder_succeeds_when_the_pipe_never_breaks(monkeypatch, tmp_path):
    """Control for the fixture above: given the exact frame count the fake
    child accepts without ever breaking the pipe, the encode completes and
    publishes normally."""
    created = []

    def _popen(cmd, stdin=None, stdout=None, stderr=None):
        proc = _EarlyClosingFFmpeg(cmd[-1], breaks_after=999)
        created.append(proc)
        return proc

    monkeypatch.setattr(enc.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(enc.subprocess, "Popen", _popen)
    out_path = tmp_path / "out.mp4"

    result = enc.encode_frames_stream_to_mp4(iter(_frames(4, size=4)), out_path, 24.0)

    assert result == out_path
    assert out_path.exists()
