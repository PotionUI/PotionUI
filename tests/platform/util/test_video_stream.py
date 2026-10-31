
import subprocess
import sys
import threading

import numpy as np
import pytest

from src.platform.util import video_stream as encode


_READ_STDIN = [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"]
_EXIT_ONE = [sys.executable, "-c", "import sys; sys.exit(1)"]


@pytest.fixture
def ffmpeg_stub(monkeypatch):
    def _install(argv):
        monkeypatch.setattr(encode, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
        real_popen = subprocess.Popen
        monkeypatch.setattr(
            encode.subprocess, "Popen", lambda cmd, **kw: real_popen(argv, **kw)
        )
    return _install


def test_close_after_successful_writes(ffmpeg_stub, tmp_path):
    ffmpeg_stub(_READ_STDIN)
    writer = encode.StreamingMp4Writer(str(tmp_path / "out.mp4"), 64, 64, 24.0)

    writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.close()

    assert writer._proc.returncode == 0


def test_nonzero_exit_raises_with_message(ffmpeg_stub, tmp_path):
    ffmpeg_stub(_EXIT_ONE)
    writer = encode.StreamingMp4Writer(str(tmp_path / "out.mp4"), 4, 4, 24.0)

    with pytest.raises(RuntimeError, match="exit 1"):
        writer.write(np.zeros((4, 4, 3), dtype=np.uint8))
        writer.close()


def test_odd_dimensions_rejected(ffmpeg_stub, tmp_path):
    ffmpeg_stub(_READ_STDIN)
    with pytest.raises(ValueError, match="even dimensions"):
        encode.StreamingMp4Writer(str(tmp_path / "out.mp4"), 63, 64, 24.0)



def test_abort_terminates_a_running_process(ffmpeg_stub, tmp_path):
    ffmpeg_stub(_READ_STDIN)
    writer = encode.StreamingMp4Writer(str(tmp_path / "out.mp4"), 64, 64, 24.0)
    assert writer._proc.poll() is None

    writer.abort()

    assert writer._proc.poll() is not None


def test_abort_is_idempotent(ffmpeg_stub, tmp_path):
    ffmpeg_stub(_READ_STDIN)
    writer = encode.StreamingMp4Writer(str(tmp_path / "out.mp4"), 64, 64, 24.0)

    writer.abort()
    writer.abort()

    assert writer._proc.poll() is not None


def test_abort_after_close_is_a_noop(ffmpeg_stub, tmp_path):
    ffmpeg_stub(_READ_STDIN)
    writer = encode.StreamingMp4Writer(str(tmp_path / "out.mp4"), 64, 64, 24.0)
    writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.close()

    writer.abort()

    assert writer._proc.returncode == 0


def test_context_manager_closes_on_clean_exit(ffmpeg_stub, tmp_path):
    ffmpeg_stub(_READ_STDIN)
    with encode.StreamingMp4Writer(str(tmp_path / "out.mp4"), 64, 64, 24.0) as writer:
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))

    assert writer._proc.returncode == 0


def test_context_manager_aborts_on_exception(ffmpeg_stub, tmp_path):
    ffmpeg_stub(_READ_STDIN)
    holder = {}
    with pytest.raises(ValueError, match="boom"):
        with encode.StreamingMp4Writer(str(tmp_path / "out.mp4"), 64, 64, 24.0) as writer:
            holder["writer"] = writer
            raise ValueError("boom")

    assert holder["writer"]._proc.poll() is not None



class _NoOpStderrTail:
    def __init__(self):
        self.finished = False

    def join(self, timeout=None):
        pass

    def tail(self):
        return b""

    def mark_finished(self):
        self.finished = True


class _FakeProc:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.waits = 0
        self.returncode = None
        self.stdin = None
        self.stderr = None

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
    writer._stderr_tail = _NoOpStderrTail()

    with pytest.raises(RuntimeError, match="timed out"):
        writer.close()

    assert writer._proc.killed
    assert writer._proc.waits >= 1


def test_abort_kills_a_running_process_without_waiting_for_terminate():
    writer = object.__new__(encode.StreamingMp4Writer)
    proc = _FakeProc()
    writer._proc = proc
    writer._stderr_tail = _NoOpStderrTail()

    writer.abort()

    assert proc.killed
    assert not proc.terminated


def test_abort_on_already_exited_process_skips_terminate_and_kill():
    writer = object.__new__(encode.StreamingMp4Writer)
    proc = _FakeProc()
    proc.returncode = 0
    writer._proc = proc
    writer._stderr_tail = _NoOpStderrTail()

    writer.abort()

    assert not proc.terminated
    assert not proc.killed



def test_mux_audio_from_source_removes_partial_output_on_nonzero_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(encode, "has_audio_stream", lambda _source: True)
    out_path = tmp_path / "muxed.mp4"

    def fake_run(cmd, capture_output, timeout):
        out_path.write_bytes(b"partial")
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



class _FiniteStream:

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, n=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_stderr_tail_drains_continuously_and_stays_bounded():
    chunks = [b"E" * 4096 for _ in range(50)]
    tail = encode._StderrTail(_FiniteStream(chunks), max_bytes=8192)

    tail.join(timeout=5)

    assert len(tail.tail()) == 8192
    assert tail.tail() == b"E" * 8192



def _run_with_timeout(fn, timeout):
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    return not thread.is_alive(), box


class _BoundedPipe:

    def __init__(self, capacity):
        self._capacity = capacity
        self._buf = bytearray()
        self._cv = threading.Condition()
        self._closed = False

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        written = 0
        with self._cv:
            while written < len(view):
                if self._closed:
                    raise BrokenPipeError("pipe closed")
                room = self._capacity - len(self._buf)
                if room <= 0:
                    self._cv.wait(timeout=0.02)
                    continue
                chunk = bytes(view[written:written + room])
                self._buf.extend(chunk)
                written += len(chunk)
                self._cv.notify_all()
        return written

    def read(self, n: int = 65536) -> bytes:
        with self._cv:
            while not self._buf and not self._closed:
                self._cv.wait(timeout=0.02)
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
            self._cv.notify_all()
            return chunk

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()


class _StdinEnd:

    def __init__(self, channel):
        self._channel = channel
        self.closed = False

    def write(self, data):
        return self._channel.write(data)

    def close(self):
        if self.closed:
            return
        self._channel.write(b"F")
        self._channel.close()
        self.closed = True


class _StderrEnd:
    def __init__(self, channel):
        self._channel = channel
        self.closed = False

    def read(self, n: int = 65536) -> bytes:
        return self._channel.read(n)

    def close(self):
        self.closed = True


class _FloodingFakeProc:

    def __init__(self, stderr_bytes: bytes, stdin_capacity: int, stderr_capacity: int):
        self._stdin_channel = _BoundedPipe(stdin_capacity)
        self._stderr_channel = _BoundedPipe(stderr_capacity)
        self.stdin = _StdinEnd(self._stdin_channel)
        self.stderr = _StderrEnd(self._stderr_channel)
        self.stdout = None
        self._returncode = None
        self._child_thread = threading.Thread(
            target=self._run_child, args=(stderr_bytes,), daemon=True,
        )
        self._child_thread.start()

    def _run_child(self, stderr_bytes: bytes) -> None:
        try:
            self._stderr_channel.write(stderr_bytes)
        except BrokenPipeError:
            self._returncode = -1
            return
        while True:
            chunk = self._stdin_channel.read()
            if not chunk and self._stdin_channel._closed:
                break
        self._stderr_channel.close()
        self._returncode = 0

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self._child_thread.join(timeout=timeout)
        if self._child_thread.is_alive():
            raise subprocess.TimeoutExpired(cmd="fake-ffmpeg", timeout=timeout)
        return self._returncode

    def terminate(self):
        if self._returncode is None:
            self._returncode = -15
        self._stdin_channel.close()
        self._stderr_channel.close()

    def kill(self):
        self.terminate()


def test_stderr_flood_does_not_block_writes_and_close_completes(monkeypatch, tmp_path):
    monkeypatch.setattr(encode, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    proc = _FloodingFakeProc(stderr_bytes=b"E" * 200_000, stdin_capacity=4096, stderr_capacity=65536)
    monkeypatch.setattr(encode.subprocess, "Popen", lambda *a, **k: proc)

    writer = encode.StreamingMp4Writer(str(tmp_path / "out.mp4"), 64, 64, 24.0)

    completed, box = _run_with_timeout(
        lambda: writer.write(np.zeros((64, 64, 3), dtype=np.uint8)), timeout=5,
    )
    assert completed, "write() blocked -- stderr is not being drained continuously"
    if "error" in box:
        raise box["error"]

    completed, box = _run_with_timeout(writer.close, timeout=5)
    assert completed, "close() blocked waiting on the flooding child"
    if "error" in box:
        raise box["error"]

    assert len(writer._stderr_tail.tail()) <= encode._STDERR_TAIL_BYTES


class _StuckFakeProc:

    def __init__(self, stdin_capacity: int = 1):
        self._stdin_channel = _BoundedPipe(stdin_capacity)
        self._stderr_channel = _BoundedPipe(65536)
        self.stdin = _StdinEnd(self._stdin_channel)
        self.stderr = _StderrEnd(self._stderr_channel)
        self.stdout = None
        self._returncode = None

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(cmd="fake-stuck-ffmpeg", timeout=timeout)

    def terminate(self):
        self._returncode = -15
        self._stdin_channel.close()
        self._stderr_channel.close()

    def kill(self):
        self.terminate()


def test_abort_completes_within_the_bounded_wait_when_stdin_flush_would_block():
    proc = _StuckFakeProc(stdin_capacity=1)
    proc._stdin_channel.write(b"x")
    writer = object.__new__(encode.StreamingMp4Writer)
    writer._proc = proc
    writer._stderr_tail = encode._StderrTail(proc.stderr)

    completed, box = _run_with_timeout(writer.abort, timeout=encode._ABORT_WAIT_TIMEOUT + 5)

    assert completed, "abort() blocked -- it touched stdin before terminating the child"
    if "error" in box:
        raise box["error"]
    assert proc._returncode == -15



class _TrackedStream:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _EOFStream(_TrackedStream):

    def __init__(self, chunks=()):
        super().__init__()
        self._chunks = list(chunks)

    def read(self, n: int = 65536) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _BlockingStream(_TrackedStream):

    def read(self, n: int = 65536) -> bytes:
        threading.Event().wait()


class _CleanExitProc:

    def __init__(self, returncode: int, stderr):
        self.stdin = _TrackedStream()
        self.stderr = stderr
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


def _finalize_writer(proc):
    writer = object.__new__(encode.StreamingMp4Writer)
    writer._proc = proc
    writer._stderr_tail = encode._StderrTail(proc.stderr)
    return writer


def test_close_success_finalizes_both_streams_and_the_reader_actually_exits():
    proc = _CleanExitProc(returncode=0, stderr=_EOFStream())
    writer = _finalize_writer(proc)

    writer.close()

    assert proc.stdin.closed
    assert proc.stderr.closed
    assert writer._stderr_tail.join(timeout=5)


def test_close_nonzero_exit_still_finalizes_streams_before_raising():
    proc = _CleanExitProc(returncode=1, stderr=_EOFStream(chunks=[b"boom"]))
    writer = _finalize_writer(proc)

    with pytest.raises(RuntimeError, match="exit 1"):
        writer.close()

    assert proc.stdin.closed
    assert proc.stderr.closed
    assert writer._stderr_tail.join(timeout=5)


class _TimesOutOnceProc:

    def __init__(self, stderr):
        self.stdin = _TrackedStream()
        self.stderr = stderr
        self.returncode = None
        self._raised_once = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._raised_once:
            self._raised_once = True
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_close_timeout_still_finalizes_streams_before_the_original_error():
    proc = _TimesOutOnceProc(stderr=_EOFStream())
    writer = _finalize_writer(proc)

    with pytest.raises(RuntimeError, match="timed out"):
        writer.close()

    assert proc.stdin.closed
    assert proc.stderr.closed
    assert writer._stderr_tail.join(timeout=5)


class _StillRunningProc:

    def __init__(self, stderr):
        self.stdin = _TrackedStream()
        self.stderr = stderr
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


def test_abort_finalizes_both_streams_and_the_reader_actually_exits():
    proc = _StillRunningProc(stderr=_EOFStream())
    writer = _finalize_writer(proc)

    writer.abort()

    assert proc.stdin.closed
    assert proc.stderr.closed
    assert writer._stderr_tail.join(timeout=5)


def test_raise_with_stderr_finalizes_both_streams_before_raising():
    proc = _CleanExitProc(returncode=1, stderr=_EOFStream(chunks=[b"died"]))
    writer = _finalize_writer(proc)

    with pytest.raises(RuntimeError, match="ffmpeg died mid-encode"):
        writer._raise_with_stderr("ffmpeg died mid-encode")

    assert proc.stdin.closed
    assert proc.stderr.closed
    assert writer._stderr_tail.join(timeout=5)



def test_join_timeout_control_never_reports_the_reader_finished(monkeypatch):
    monkeypatch.setattr(encode, "_ABORT_WAIT_TIMEOUT", 0.2)
    proc = _StillRunningProc(stderr=_BlockingStream())
    writer = _finalize_writer(proc)

    completed, box = _run_with_timeout(writer.abort, timeout=5)

    assert completed, "abort() blocked past its own bounded wait"
    if "error" in box:
        raise box["error"]
    assert writer._stderr_tail.join(timeout=0) is False
    assert proc.stdin.closed
    assert proc.stderr.closed


class _Completed:
    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self.stderr = stderr


def _mux_runner(monkeypatch, outcomes, out_path, calls):
    queue = list(outcomes)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        returncode = queue.pop(0)
        if returncode == 0:
            out_path.write_bytes(b"muxed")
        else:
            out_path.write_bytes(b"partial")
        return _Completed(returncode, b"boom")

    monkeypatch.setattr(encode, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(encode.subprocess, "run", fake_run)


def test_mux_audio_prefers_stream_copy_and_stops_on_success(tmp_path, monkeypatch):
    out_path = tmp_path / "out.mp4"
    calls = []
    _mux_runner(monkeypatch, [0], out_path, calls)

    encode.mux_audio("v.mp4", "s.mp4", out_path, shortest=False, prefer_copy=True)

    assert len(calls) == 1
    assert calls[0][calls[0].index("-c:a") + 1] == "copy"
    assert "-shortest" not in calls[0]
    assert out_path.exists()


def test_mux_audio_falls_back_to_aac_when_copy_fails(tmp_path, monkeypatch):
    out_path = tmp_path / "out.mp4"
    calls = []
    _mux_runner(monkeypatch, [1, 0], out_path, calls)

    encode.mux_audio("v.mp4", "s.mp4", out_path, shortest=False, prefer_copy=True)

    assert [c[c.index("-c:a") + 1] for c in calls] == ["copy", "aac"]
    assert out_path.read_bytes() == b"muxed"


def test_mux_audio_without_prefer_copy_is_aac_only_and_honours_shortest(tmp_path, monkeypatch):
    out_path = tmp_path / "out.mp4"
    calls = []
    _mux_runner(monkeypatch, [0], out_path, calls)

    encode.mux_audio("v.mp4", "s.mp4", out_path, shortest=True, prefer_copy=False)

    assert len(calls) == 1
    assert calls[0][calls[0].index("-c:a") + 1] == "aac"
    assert "-shortest" in calls[0]


def test_mux_audio_raises_and_removes_partial_when_every_attempt_fails(tmp_path, monkeypatch):
    out_path = tmp_path / "out.mp4"
    calls = []
    _mux_runner(monkeypatch, [1, 1], out_path, calls)

    with pytest.raises(RuntimeError, match="exit 1"):
        encode.mux_audio("v.mp4", "s.mp4", out_path, shortest=False, prefer_copy=True)

    assert not out_path.exists()


def test_mux_audio_rejects_a_zero_byte_result(tmp_path, monkeypatch):
    out_path = tmp_path / "out.mp4"

    def fake_run(cmd, **kwargs):
        out_path.write_bytes(b"")
        return _Completed(0)

    monkeypatch.setattr(encode, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(encode.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="no muxed output"):
        encode.mux_audio("v.mp4", "s.mp4", out_path, shortest=True, prefer_copy=False)

    assert not out_path.exists()


class _Probed:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _probe_with(monkeypatch, *, ffprobe="/usr/bin/ffprobe", result=None, error=None):
    monkeypatch.setattr(encode, "find_ffprobe", lambda: ffprobe)

    def fake_run(cmd, **kwargs):
        if error is not None:
            raise error
        return result

    monkeypatch.setattr(encode.subprocess, "run", fake_run)


def test_probe_reports_present_when_ffprobe_finds_an_audio_stream(monkeypatch):
    _probe_with(monkeypatch, result=_Probed(stdout='{"streams": [{"codec_type": "audio"}]}'))

    result = encode.probe_audio_stream("src.mp4")

    assert result.status == encode.AUDIO_PROBE_PRESENT
    assert result.reason is None


def test_probe_reports_silent_when_there_are_no_streams(monkeypatch):
    _probe_with(monkeypatch, result=_Probed(stdout='{"streams": []}'))

    result = encode.probe_audio_stream("src.mp4")

    assert result.status == encode.AUDIO_PROBE_SILENT
    assert result.reason is None


def test_probe_reports_failed_when_ffprobe_is_missing(monkeypatch):
    _probe_with(monkeypatch, ffprobe=None)

    result = encode.probe_audio_stream("src.mp4")

    assert result.status == encode.AUDIO_PROBE_FAILED
    assert "not found" in result.reason


def test_probe_reports_failed_on_nonzero_exit(monkeypatch):
    _probe_with(monkeypatch, result=_Probed(returncode=1))

    result = encode.probe_audio_stream("src.mp4")

    assert result.status == encode.AUDIO_PROBE_FAILED
    assert "exited 1" in result.reason


def test_probe_reports_failed_on_timeout(monkeypatch):
    _probe_with(monkeypatch, error=subprocess.TimeoutExpired(cmd="ffprobe", timeout=10))

    result = encode.probe_audio_stream("src.mp4")

    assert result.status == encode.AUDIO_PROBE_FAILED
    assert "timed out" in result.reason


def test_probe_reports_failed_when_ffprobe_cannot_run(monkeypatch):
    _probe_with(monkeypatch, error=OSError("denied"))

    result = encode.probe_audio_stream("src.mp4")

    assert result.status == encode.AUDIO_PROBE_FAILED
    assert "denied" in result.reason


def test_probe_reports_failed_on_unparseable_output(monkeypatch):
    _probe_with(monkeypatch, result=_Probed(stdout="not json"))

    result = encode.probe_audio_stream("src.mp4")

    assert result.status == encode.AUDIO_PROBE_FAILED
    assert "unparseable" in result.reason


def test_has_audio_stream_is_true_only_for_a_confirmed_audio_track(monkeypatch):
    _probe_with(monkeypatch, result=_Probed(stdout='{"streams": [{"codec_type": "audio"}]}'))
    assert encode.has_audio_stream("src.mp4") is True

    _probe_with(monkeypatch, result=_Probed(stdout='{"streams": []}'))
    assert encode.has_audio_stream("src.mp4") is False

    _probe_with(monkeypatch, result=_Probed(returncode=1))
    assert encode.has_audio_stream("src.mp4") is False


def test_require_ffmpeg_returns_the_binary_or_raises(monkeypatch):
    monkeypatch.setattr(encode, "find_ffmpeg", lambda: "/opt/ffmpeg")
    assert encode.require_ffmpeg() == "/opt/ffmpeg"

    monkeypatch.setattr(encode, "find_ffmpeg", lambda: None)
    with pytest.raises(encode.FFmpegNotFoundError, match="ffmpeg binary not found"):
        encode.require_ffmpeg()


def test_mux_audio_raises_and_removes_partial_when_ffmpeg_cannot_run(tmp_path, monkeypatch):
    out_path = tmp_path / "out.mp4"
    out_path.write_bytes(b"partial")

    def fake_run(cmd, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(encode, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(encode.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="could not run"):
        encode.mux_audio("v.mp4", "s.mp4", out_path, shortest=False, prefer_copy=True)

    assert not out_path.exists()


def test_mux_audio_raises_and_removes_partial_on_timeout(tmp_path, monkeypatch):
    out_path = tmp_path / "out.mp4"
    out_path.write_bytes(b"partial")

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=600)

    monkeypatch.setattr(encode, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(encode.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="timed out"):
        encode.mux_audio("v.mp4", "s.mp4", out_path, shortest=True, prefer_copy=False)

    assert not out_path.exists()


def test_mux_audio_raises_ffmpeg_not_found_without_running_anything(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(encode, "find_ffmpeg", lambda: None)
    monkeypatch.setattr(encode.subprocess, "run", lambda *a, **k: ran.append(a))

    with pytest.raises(encode.FFmpegNotFoundError):
        encode.mux_audio("v.mp4", "s.mp4", tmp_path / "out.mp4", shortest=False, prefer_copy=True)

    assert ran == []


def test_mux_audio_from_source_returns_true_on_success(tmp_path, monkeypatch):
    out_path = tmp_path / "out.mp4"
    calls = []
    _mux_runner(monkeypatch, [0], out_path, calls)
    monkeypatch.setattr(encode, "has_audio_stream", lambda _source: True)

    assert encode.mux_audio_from_source("v.mp4", "s.mp4", out_path) is True
    assert out_path.read_bytes() == b"muxed"


def test_mux_audio_from_source_skips_ffmpeg_when_the_source_has_no_audio(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(encode, "has_audio_stream", lambda _source: False)
    monkeypatch.setattr(encode.subprocess, "run", lambda *a, **k: ran.append(a))

    assert encode.mux_audio_from_source("v.mp4", "s.mp4", tmp_path / "out.mp4") is False
    assert ran == []


def test_mux_audio_from_source_returns_false_when_ffmpeg_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(encode, "has_audio_stream", lambda _source: True)
    monkeypatch.setattr(encode, "find_ffmpeg", lambda: None)

    assert encode.mux_audio_from_source("v.mp4", "s.mp4", tmp_path / "out.mp4") is False


def test_mux_audio_from_source_returns_false_for_a_zero_byte_result_and_removes_it(tmp_path, monkeypatch):
    out_path = tmp_path / "out.mp4"

    def fake_run(cmd, **kwargs):
        out_path.write_bytes(b"")
        return _Completed(0)

    monkeypatch.setattr(encode, "has_audio_stream", lambda _source: True)
    monkeypatch.setattr(encode, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(encode.subprocess, "run", fake_run)

    assert encode.mux_audio_from_source("v.mp4", "s.mp4", out_path) is False
    assert not out_path.exists()


class _Banner:
    def __init__(self, stderr, returncode=1):
        self.stderr = stderr
        self.returncode = returncode
        self.stdout = ""


def _banner_probe(monkeypatch, stderr):
    monkeypatch.setattr(encode, "find_ffprobe", lambda: None)
    monkeypatch.setattr(encode, "find_ffmpeg", lambda: "/usr/bin/ffmpeg")
    monkeypatch.setattr(encode.subprocess, "run", lambda cmd, **kwargs: _Banner(stderr))


def test_probe_without_ffprobe_reads_an_audio_stream_from_the_ffmpeg_banner(monkeypatch):
    _banner_probe(monkeypatch, "Input #0, mov\n  Stream #0:0[0x1](und): Video: h264, yuv420p\n  Stream #0:1[0x2](und): Audio: aac, 44100 Hz\n")

    assert encode.probe_audio_stream("src.mp4").status == encode.AUDIO_PROBE_PRESENT
    assert encode.has_audio_stream("src.mp4") is True


def test_probe_without_ffprobe_reports_silent_for_a_banner_with_only_video(monkeypatch):
    _banner_probe(monkeypatch, "Input #0, mov\n  Stream #0:0[0x1](und): Video: h264, yuv420p\n")

    assert encode.probe_audio_stream("src.mp4").status == encode.AUDIO_PROBE_SILENT


def test_probe_without_ffprobe_stays_failed_when_the_banner_lists_no_streams(monkeypatch):
    _banner_probe(monkeypatch, "src.mp4: No such file or directory\n")

    assert encode.probe_audio_stream("src.mp4").status == encode.AUDIO_PROBE_FAILED


def test_an_audio_title_tag_is_not_mistaken_for_a_stream(monkeypatch):
    _banner_probe(monkeypatch, "  Metadata:\n    title           : Audio: demo\n  Stream #0:0: Video: h264\n")

    assert encode.probe_audio_stream("src.mp4").status == encode.AUDIO_PROBE_SILENT


def test_abort_on_a_writer_blocked_on_stdin_returns_fast_and_removes_the_partial_file(ffmpeg_stub, tmp_path):
    import time

    ffmpeg_stub(_READ_STDIN)
    out = tmp_path / "out.mp4"
    writer = encode.StreamingMp4Writer(str(out), 64, 64, 24.0)
    out.write_bytes(b"partial")
    started = time.monotonic()

    writer.abort()

    assert time.monotonic() - started < 5
    assert writer._proc.poll() is not None
    assert not out.exists()


def test_abort_keeps_a_file_that_was_already_finished(ffmpeg_stub, tmp_path):
    ffmpeg_stub(_READ_STDIN)
    out = tmp_path / "out.mp4"
    writer = encode.StreamingMp4Writer(str(out), 64, 64, 24.0)
    writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.close()
    out.write_bytes(b"done")

    writer.abort()

    assert out.exists()
