"""Tests for `generator/seedvr2/encode.py`'s video/audio failure split.

Every scenario is exercised with fake `encode_video`/`mux_audio`/`probe_audio`
callables -- no ffmpeg, no ffprobe. The file-set tests use real (tiny) files
under `tmp_path` so `encode_video_with_audio`'s claimed file ownership --
"exactly one surviving file, whichever it is" -- is checked against the real
filesystem, not just the returned path.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.pipelines.pipes.generator.seedvr2.encode import (
    AUDIO_MUXED,
    AUDIO_MUX_FAILED,
    AUDIO_NOT_REQUESTED,
    AUDIO_PROBE_FAILED,
    AUDIO_SILENT_SOURCE,
    AudioProbeResult,
    AUDIO_PROBE_PRESENT,
    AUDIO_PROBE_SILENT,
    _stderr_tail,
    _STDERR_TAIL_BYTES,
    encode_video_with_audio,
)

_FRAMES = np.zeros((2, 4, 4, 3), dtype=np.uint8)


class _Recorder:
    """Records calls made to a fake callable and returns/raises as configured."""

    def __init__(self, *, raises: Exception | None = None, returns=None):
        self.calls: list[tuple] = []
        self._raises = raises
        self._returns = returns

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._raises is not None:
            raise self._raises
        return self._returns


def _probe(status, reason=None):
    return lambda path: AudioProbeResult(status, reason)


def test_video_encoder_failure_propagates_and_never_attempts_mux():
    encode_video = _Recorder(raises=RuntimeError("ffmpeg failed (exit 1): bad rawvideo geometry"))
    mux_audio = _Recorder()

    with pytest.raises(RuntimeError, match="bad rawvideo geometry"):
        encode_video_with_audio(
            _FRAMES, "/tmp/out.mp4", 24.0,
            source_audio_path="/tmp/src.mp4", keep_audio=True,
            encode_video=encode_video, mux_audio=mux_audio,
            probe_audio=_probe(AUDIO_PROBE_PRESENT),
        )

    assert len(encode_video.calls) == 1
    assert mux_audio.calls == []  # never reached -- video failures are never treated as audio problems


def test_video_encoder_failure_propagates_even_when_audio_not_requested():
    encode_video = _Recorder(raises=RuntimeError("ffmpeg timed out after 600s"))

    with pytest.raises(RuntimeError, match="timed out"):
        encode_video_with_audio(
            _FRAMES, "/tmp/out.mp4", 24.0,
            source_audio_path=None, keep_audio=False,
            encode_video=encode_video, mux_audio=_Recorder(),
        )


def test_keep_audio_false_skips_mux_and_probe_entirely():
    encode_video = _Recorder()
    mux_audio = _Recorder()
    probe_audio = _Recorder(returns=AudioProbeResult(AUDIO_PROBE_PRESENT))

    result = encode_video_with_audio(
        _FRAMES, "/tmp/out.mp4", 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=False,
        encode_video=encode_video, mux_audio=mux_audio, probe_audio=probe_audio,
    )

    assert result.audio_outcome == AUDIO_NOT_REQUESTED
    assert result.video_path == "/tmp/out.mp4"
    assert mux_audio.calls == []
    assert probe_audio.calls == []  # not even probed -- audio was never wanted


def test_confirmed_silent_source_is_not_treated_as_a_failure():
    encode_video = _Recorder()
    mux_audio = _Recorder()

    result = encode_video_with_audio(
        _FRAMES, "/tmp/out.mp4", 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=encode_video, mux_audio=mux_audio,
        probe_audio=_probe(AUDIO_PROBE_SILENT),
    )

    assert result.audio_outcome == AUDIO_SILENT_SOURCE
    assert result.omitted_reason is None
    assert result.video_path == "/tmp/out.mp4"
    assert mux_audio.calls == []  # confirmed no audio stream -- never invoked


def test_failed_probe_falls_back_with_reason_and_never_attempts_mux():
    # The bug this whole rework fixes: a probe that could not determine
    # whether audio exists (missing ffprobe, timeout, corrupt source, ...)
    # must NOT be silently folded into "no audio" -- it needs its own outcome
    # and a recorded reason, and must never be handed to mux_audio.
    encode_video = _Recorder()
    mux_audio = _Recorder()

    result = encode_video_with_audio(
        _FRAMES, "/tmp/out.mp4", 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=encode_video, mux_audio=mux_audio,
        probe_audio=_probe(AUDIO_PROBE_FAILED, "ffprobe not found on PATH"),
    )

    assert result.audio_outcome == AUDIO_PROBE_FAILED
    assert result.omitted_reason == "ffprobe not found on PATH"
    assert result.video_path == "/tmp/out.mp4"
    assert mux_audio.calls == []


def test_genuine_mux_failure_falls_back_to_video_only_with_reason():
    encode_video = _Recorder()
    mux_audio = _Recorder(raises=RuntimeError("ffmpeg audio mux failed (exit 1): bad audio codec"))

    result = encode_video_with_audio(
        _FRAMES, "/tmp/out.mp4", 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=encode_video, mux_audio=mux_audio,
        probe_audio=_probe(AUDIO_PROBE_PRESENT),
    )

    assert result.audio_outcome == AUDIO_MUX_FAILED
    assert "bad audio codec" in result.omitted_reason
    assert result.video_path == "/tmp/out.mp4"  # the already-successful silent video, not lost
    assert len(mux_audio.calls) == 1


def test_successful_passthrough_reports_audio_muxed():
    encode_video = _Recorder()
    mux_audio = _Recorder()

    result = encode_video_with_audio(
        _FRAMES, "/tmp/out.mp4", 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=encode_video, mux_audio=mux_audio,
        probe_audio=_probe(AUDIO_PROBE_PRESENT),
    )

    assert result.audio_outcome == AUDIO_MUXED
    assert result.video_path == "/tmp/out.mp4.audio.mp4"
    assert mux_audio.calls == [(("/tmp/out.mp4", "/tmp/src.mp4", "/tmp/out.mp4.audio.mp4"), {})]


# -- probe_audio_stream: three-way classification ----------------------------
#
# Fakes `shutil.which`/`subprocess.run` directly -- no real ffprobe binary.

def test_probe_reports_present_when_ffprobe_finds_an_audio_stream(monkeypatch):
    import src.pipelines.pipes.generator.seedvr2.encode as enc

    monkeypatch.setattr(enc.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        enc.subprocess, "run",
        lambda cmd, **kw: SimpleNamespaceResult(returncode=0, stdout='{"streams": [{"codec_type": "audio"}]}'),
    )

    result = enc.probe_audio_stream("/tmp/src.mp4")
    assert result.status == AUDIO_PROBE_PRESENT
    assert result.reason is None


def test_probe_reports_confirmed_silent_when_no_streams(monkeypatch):
    import src.pipelines.pipes.generator.seedvr2.encode as enc

    monkeypatch.setattr(enc.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        enc.subprocess, "run",
        lambda cmd, **kw: SimpleNamespaceResult(returncode=0, stdout='{"streams": []}'),
    )

    result = enc.probe_audio_stream("/tmp/src.mp4")
    assert result.status == AUDIO_PROBE_SILENT
    assert result.reason is None


def test_probe_reports_failed_when_ffprobe_missing(monkeypatch):
    import src.pipelines.pipes.generator.seedvr2.encode as enc

    monkeypatch.setattr(enc.shutil, "which", lambda name: None)

    result = enc.probe_audio_stream("/tmp/src.mp4")
    assert result.status == AUDIO_PROBE_FAILED
    assert "not found" in result.reason


def test_probe_reports_failed_on_nonzero_exit(monkeypatch):
    import src.pipelines.pipes.generator.seedvr2.encode as enc

    monkeypatch.setattr(enc.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        enc.subprocess, "run",
        lambda cmd, **kw: SimpleNamespaceResult(returncode=1, stdout=""),
    )

    result = enc.probe_audio_stream("/tmp/corrupt.mp4")
    assert result.status == AUDIO_PROBE_FAILED
    assert "exited 1" in result.reason


def test_probe_reports_failed_on_timeout(monkeypatch):
    import src.pipelines.pipes.generator.seedvr2.encode as enc

    def _raise_timeout(cmd, **kw):
        raise enc.subprocess.TimeoutExpired(cmd=cmd, timeout=10)

    monkeypatch.setattr(enc.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(enc.subprocess, "run", _raise_timeout)

    result = enc.probe_audio_stream("/tmp/src.mp4")
    assert result.status == AUDIO_PROBE_FAILED
    assert "timed out" in result.reason


def test_probe_reports_failed_on_unparseable_output(monkeypatch):
    import src.pipelines.pipes.generator.seedvr2.encode as enc

    monkeypatch.setattr(enc.shutil, "which", lambda name: "/usr/bin/ffprobe")
    monkeypatch.setattr(
        enc.subprocess, "run",
        lambda cmd, **kw: SimpleNamespaceResult(returncode=0, stdout="not json"),
    )

    result = enc.probe_audio_stream("/tmp/src.mp4")
    assert result.status == AUDIO_PROBE_FAILED
    assert "unparseable" in result.reason


class SimpleNamespaceResult:
    """Minimal stand-in for `subprocess.CompletedProcess` (only the two
    attributes `probe_audio_stream` reads)."""

    def __init__(self, *, returncode: int, stdout: str):
        self.returncode = returncode
        self.stdout = stdout


# -- file-set ownership: real files under tmp_path ---------------------------
#
# `encode_video_with_audio` claims to own the full lifecycle of the two files
# it can create (the silent intermediate, the muxed sibling): exactly one file
# survives each outcome, whichever the caller is told to use. These write real
# (tiny) files instead of asserting only on the returned path, so a leak that
# the pure-mock tests above couldn't see (e.g. a forgotten unlink) shows up as
# an unexpected extra file on disk.

def _fake_encode_video_writes_file(frames, out_path, fps, *, audio=None):
    with open(out_path, "w") as f:
        f.write("video")


def _listing(tmp_path):
    return sorted(p.name for p in tmp_path.iterdir())


def test_files_after_mux_success_only_the_muxed_file_remains(tmp_path):
    out_path = tmp_path / "out.mp4"

    def _fake_mux(video_only, source, dest):
        with open(dest, "w") as f:
            f.write("muxed")

    result = encode_video_with_audio(
        _FRAMES, str(out_path), 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=_fake_encode_video_writes_file, mux_audio=_fake_mux,
        probe_audio=_probe(AUDIO_PROBE_PRESENT),
    )

    assert result.audio_outcome == AUDIO_MUXED
    assert _listing(tmp_path) == ["out.mp4.audio.mp4"]  # the silent intermediate is gone


def test_files_after_mux_failure_only_the_silent_video_remains(tmp_path):
    out_path = tmp_path / "out.mp4"

    def _fake_mux_leaves_partial_then_fails(video_only, source, dest):
        # A realistic ffmpeg failure: some bytes land before it exits non-zero.
        # `mux_audio`'s contract (enforced for real by `mux_audio_into_video`,
        # see the dedicated test below) is to remove that partial file itself
        # before raising -- `encode_video_with_audio` never touches this path.
        with open(dest, "w") as f:
            f.write("partial")
        import os
        os.unlink(dest)
        raise RuntimeError("ffmpeg audio mux failed (exit 1): bad audio codec")

    result = encode_video_with_audio(
        _FRAMES, str(out_path), 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=_fake_encode_video_writes_file, mux_audio=_fake_mux_leaves_partial_then_fails,
        probe_audio=_probe(AUDIO_PROBE_PRESENT),
    )

    assert result.audio_outcome == AUDIO_MUX_FAILED
    # `mux_audio` is expected to clean up its own partial file on failure --
    # `encode_video_with_audio` never touches `out_path + ".audio.mp4"` itself.
    assert _listing(tmp_path) == ["out.mp4"]


def test_mux_audio_into_video_removes_partial_output_on_ffmpeg_failure(tmp_path, monkeypatch):
    import src.pipelines.pipes.generator.seedvr2.encode as enc

    out_path = tmp_path / "muxed.mp4"

    class _FakeCompleted:
        returncode = 1
        stderr = b"encoder error"

    def _fake_run(cmd, **kwargs):
        # ffmpeg wrote a few bytes before failing -- realistic partial output.
        out_path.write_text("partial")
        return _FakeCompleted()

    monkeypatch.setattr(enc.subprocess, "run", _fake_run)

    with pytest.raises(RuntimeError, match="ffmpeg audio mux failed"):
        enc.mux_audio_into_video(tmp_path / "video.mp4", tmp_path / "src.mp4", out_path)

    assert not out_path.exists()


def test_files_after_probe_failure_only_the_silent_video_remains(tmp_path):
    out_path = tmp_path / "out.mp4"
    mux_audio = _Recorder()

    result = encode_video_with_audio(
        _FRAMES, str(out_path), 24.0,
        source_audio_path="/tmp/src.mp4", keep_audio=True,
        encode_video=_fake_encode_video_writes_file, mux_audio=mux_audio,
        probe_audio=_probe(AUDIO_PROBE_FAILED, "ffprobe timed out"),
    )

    assert result.audio_outcome == AUDIO_PROBE_FAILED
    assert mux_audio.calls == []
    assert _listing(tmp_path) == ["out.mp4"]


def test_files_after_genuine_encode_failure_no_leftovers(tmp_path):
    out_path = tmp_path / "out.mp4"

    def _fake_encode_video_fails(frames, path, fps, *, audio=None):
        # A realistic streamed-encoder failure removes its own partial file
        # (see `encode_frames_stream_to_mp4`) before propagating.
        raise RuntimeError("ffmpeg failed (exit 1): bad rawvideo geometry")

    with pytest.raises(RuntimeError, match="bad rawvideo geometry"):
        encode_video_with_audio(
            _FRAMES, str(out_path), 24.0,
            source_audio_path="/tmp/src.mp4", keep_audio=True,
            encode_video=_fake_encode_video_fails, mux_audio=_Recorder(),
            probe_audio=_probe(AUDIO_PROBE_PRESENT),
        )

    assert _listing(tmp_path) == []


# -- _stderr_tail: bounded diagnostic read ------------------------------------

class _FakeDiagFile:
    """An in-memory stand-in for the stderr `tempfile.TemporaryFile` handle,
    tracking every `read()` size so a test can prove `_stderr_tail` never
    reads more than its bound -- a real file object gives no such hook."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self.read_sizes: list = []

    def seek(self, offset, whence=0):
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = len(self._data) + offset
        else:
            raise ValueError(f"unsupported whence {whence}")
        return self._pos

    def tell(self):
        return self._pos

    def read(self, size=-1):
        self.read_sizes.append(size)
        if size is None or size < 0:
            chunk = self._data[self._pos:]
        else:
            chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


def test_stderr_tail_reads_at_most_the_bounded_window_from_a_large_file():
    # A diagnostic file far larger than the bound, with distinct markers at
    # the start and the end.
    payload = b"START-OF-LOG\n" + b"x" * (2 * 1024 * 1024) + b"\nEND-OF-LOG\n"
    handle = _FakeDiagFile(payload)

    tail = _stderr_tail(handle)

    assert "END-OF-LOG" in tail
    assert "START-OF-LOG" not in tail
    assert len(tail.encode("utf-8", errors="replace")) <= _STDERR_TAIL_BYTES
    # Every read() call asked for a bounded size -- never the whole 2MB+ file.
    assert handle.read_sizes
    assert all(0 <= size <= _STDERR_TAIL_BYTES for size in handle.read_sizes)


def test_stderr_tail_returns_the_whole_file_when_it_is_smaller_than_the_bound():
    handle = _FakeDiagFile(b"short diagnostic output\n")

    assert _stderr_tail(handle) == "short diagnostic output\n"
