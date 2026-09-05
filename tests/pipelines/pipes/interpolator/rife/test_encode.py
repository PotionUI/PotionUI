"""StreamingMp4Writer teardown contract, exercised without ffmpeg/cv2: the
subprocess handshake is what regressed in the field (communicate() flushes
stdin itself — pre-closing it raised "flush of closed file" on every
successful encode), so these tests stub Popen with real pipe processes."""

import subprocess

import numpy as np
import pytest

from src.pipelines.pipes.interpolator.rife import encode


@pytest.fixture
def ffmpeg_stub(monkeypatch):
    def _install(argv):
        monkeypatch.setattr(encode.shutil, "which", lambda _: "/usr/bin/ffmpeg")
        real_popen = subprocess.Popen
        monkeypatch.setattr(
            encode.subprocess, "Popen", lambda cmd, **kw: real_popen(argv, **kw)
        )
    return _install


def test_close_after_successful_writes(ffmpeg_stub):
    ffmpeg_stub(["cat"])
    writer = encode.StreamingMp4Writer("/dev/null", 64, 64, 24.0)

    writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.close()

    assert writer._proc.returncode == 0


def test_nonzero_exit_raises_with_message(ffmpeg_stub):
    ffmpeg_stub(["false"])
    writer = encode.StreamingMp4Writer("/dev/null", 4, 4, 24.0)

    with pytest.raises(RuntimeError, match="exit 1"):
        writer.write(np.zeros((4, 4, 3), dtype=np.uint8))
        writer.close()


def test_odd_dimensions_rejected(ffmpeg_stub):
    ffmpeg_stub(["cat"])
    with pytest.raises(ValueError, match="even dimensions"):
        encode.StreamingMp4Writer("/dev/null", 63, 64, 24.0)


# -- abort() / context manager, against a real process ------------------------
# `cat` with nothing written to it and stdin left open blocks forever on read,
# so it stands in for an ffmpeg encode that must be cut short.

def test_abort_terminates_a_running_process(ffmpeg_stub):
    ffmpeg_stub(["cat"])
    writer = encode.StreamingMp4Writer("/dev/null", 64, 64, 24.0)
    assert writer._proc.poll() is None

    writer.abort()

    assert writer._proc.poll() is not None  # reaped, not left as a zombie


def test_abort_is_idempotent(ffmpeg_stub):
    ffmpeg_stub(["cat"])
    writer = encode.StreamingMp4Writer("/dev/null", 64, 64, 24.0)

    writer.abort()
    writer.abort()  # must not raise the second time

    assert writer._proc.poll() is not None


def test_abort_after_close_is_a_noop(ffmpeg_stub):
    ffmpeg_stub(["cat"])
    writer = encode.StreamingMp4Writer("/dev/null", 64, 64, 24.0)
    writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.close()

    writer.abort()  # must not raise or hang once the process has already exited

    assert writer._proc.returncode == 0


def test_context_manager_closes_on_clean_exit(ffmpeg_stub):
    ffmpeg_stub(["cat"])
    with encode.StreamingMp4Writer("/dev/null", 64, 64, 24.0) as writer:
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))

    assert writer._proc.returncode == 0


def test_context_manager_aborts_on_exception(ffmpeg_stub):
    ffmpeg_stub(["cat"])
    holder = {}
    with pytest.raises(ValueError, match="boom"):
        with encode.StreamingMp4Writer("/dev/null", 64, 64, 24.0) as writer:
            holder["writer"] = writer
            raise ValueError("boom")

    assert holder["writer"]._proc.poll() is not None  # aborted, not left running


# -- close()/abort() timeout-escalation, against a scripted fake process ------
# These exercise the reap-after-kill path directly: waiting 600s for a real
# `close()` timeout is not practical in a test.

class _FakeProc:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.waits = 0
        self.returncode = None
        self.stdin = None

    def poll(self):
        return self.returncode

    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.waits += 1
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
        return self.returncode


def test_close_timeout_kills_and_reaps():
    writer = object.__new__(encode.StreamingMp4Writer)
    writer._proc = _FakeProc()

    with pytest.raises(RuntimeError, match="timed out"):
        writer.close()

    assert writer._proc.killed
    assert writer._proc.waits >= 1  # reaped after the kill, not left running


def test_abort_escalates_to_kill_when_terminate_does_not_stop_it():
    writer = object.__new__(encode.StreamingMp4Writer)
    proc = _FakeProc()
    writer._proc = proc

    writer.abort()

    assert proc.terminated
    assert proc.killed


def test_abort_on_already_exited_process_skips_terminate_and_kill():
    writer = object.__new__(encode.StreamingMp4Writer)
    proc = _FakeProc()
    proc.returncode = 0
    writer._proc = proc

    writer.abort()

    assert not proc.terminated
    assert not proc.killed


# -- mux_audio_from_source: never leaves a partial file behind ---------------

def test_mux_audio_from_source_removes_partial_output_on_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(encode, "has_audio_stream", lambda _source: True)
    out_path = tmp_path / "muxed.mp4"

    def fake_run(cmd, capture_output, timeout):
        out_path.write_bytes(b"partial")  # ffmpeg wrote something before failing
        return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(encode.subprocess, "run", fake_run)

    result = encode.mux_audio_from_source("video.mp4", "source.mp4", str(out_path))

    assert result is False
    assert not out_path.exists()


def test_mux_audio_from_source_removes_partial_output_on_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(encode, "has_audio_stream", lambda _source: True)
    out_path = tmp_path / "muxed.mp4"
    out_path.write_bytes(b"partial")

    def fake_run(cmd, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(encode.subprocess, "run", fake_run)

    result = encode.mux_audio_from_source("video.mp4", "source.mp4", str(out_path))

    assert result is False
    assert not out_path.exists()
