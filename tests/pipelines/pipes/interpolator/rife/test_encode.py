"""StreamingMp4Writer teardown contract, exercised without ffmpeg/cv2: the
subprocess handshake is what regressed in the field (communicate() flushes
stdin itself — pre-closing it raised "flush of closed file" on every
successful encode), so these tests stub Popen with real pipe processes."""

import subprocess
import threading

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

class _NoOpStderrTail:
    def join(self, timeout=None):
        pass

    def tail(self):
        return b""


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
    writer._stderr_tail = _NoOpStderrTail()

    with pytest.raises(RuntimeError, match="timed out"):
        writer.close()

    assert writer._proc.killed
    assert writer._proc.waits >= 1  # reaped after the kill, not left running


def test_abort_escalates_to_kill_when_terminate_does_not_stop_it():
    writer = object.__new__(encode.StreamingMp4Writer)
    proc = _FakeProc()
    writer._proc = proc
    writer._stderr_tail = _NoOpStderrTail()

    writer.abort()

    assert proc.terminated
    assert proc.killed


def test_abort_on_already_exited_process_skips_terminate_and_kill():
    writer = object.__new__(encode.StreamingMp4Writer)
    proc = _FakeProc()
    proc.returncode = 0
    writer._proc = proc
    writer._stderr_tail = _NoOpStderrTail()

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


# -- _StderrTail: continuously drained, bounded retention --------------------

class _FiniteStream:
    """A readable stream that yields `chunks` one at a time, then EOF."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read(self, n=-1):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_stderr_tail_drains_continuously_and_stays_bounded():
    chunks = [b"E" * 4096 for _ in range(50)]  # 200KB total, far past the bound
    tail = encode._StderrTail(_FiniteStream(chunks), max_bytes=8192)

    tail.join(timeout=5)

    assert len(tail.tail()) == 8192
    assert tail.tail() == b"E" * 8192  # only the LAST 8KB survive


# -- stderr can never back-pressure stdin; abort() never blocks on a flush ---
# A tiny fake process/pipe pair reproducing the exact coupling a real one has
# (its OWN stderr write blocking stops it reading stdin) without spawning a
# real, potentially-unkillable subprocess. Every risky call is run through
# `_run_with_timeout` so a regression here FAILS (cleanly, within the bound)
# instead of hanging the test process.

def _run_with_timeout(fn, timeout):
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised by the caller below
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=timeout)
    return not thread.is_alive(), box


class _BoundedPipe:
    """An in-memory model of a real OS pipe's bounded kernel buffer: write()
    blocks (with real partial-write semantics) once `capacity` unread bytes
    are buffered; read() unblocks any blocked writer; close() breaks a
    blocked writer immediately, matching a dead reader raising EPIPE."""

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
    """The writer's view of a `_BoundedPipe`, matching the subset of a real
    ``Popen.stdin``'s interface `StreamingMp4Writer` uses. `close()` flushes
    one more pending write before marking itself closed, mirroring a real
    buffered stdin -- exactly the call `abort()` must never let block."""

    def __init__(self, channel):
        self._channel = channel
        self.closed = False

    def write(self, data):
        return self._channel.write(data)

    def close(self):
        if self.closed:
            return
        self._channel.write(b"F")  # the pending flush
        self._channel.close()  # then EOF, like closing a real file descriptor
        self.closed = True


class _StderrEnd:
    def __init__(self, channel):
        self._channel = channel

    def read(self, n: int = 65536) -> bytes:
        return self._channel.read(n)


class _FloodingFakeProc:
    """Models an ffmpeg child that writes `stderr_bytes` to stderr BEFORE
    ever reading a byte of stdin -- the ordering that deadlocks a real
    process against an undrained stderr pipe. The flood runs on a background
    thread the same way a real ffmpeg's own stderr write only blocks ITSELF,
    never the parent directly."""

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
            self._stderr_channel.write(stderr_bytes)  # blocks here if undrained
        except BrokenPipeError:
            self._returncode = -1
            return
        while True:  # only reaches here once the flood is fully written
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


def test_stderr_flood_does_not_block_writes_and_close_completes(monkeypatch):
    monkeypatch.setattr(encode.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    # 200KB of chatter, well past any real pipe's ~64KB kernel buffer, and a
    # stdin capacity small enough that a single frame needs the child
    # actually reading -- so this frame's write() only completes once the
    # flood has been drained and the child moves on to stdin.
    proc = _FloodingFakeProc(stderr_bytes=b"E" * 200_000, stdin_capacity=4096, stderr_capacity=65536)
    monkeypatch.setattr(encode.subprocess, "Popen", lambda *a, **k: proc)

    writer = encode.StreamingMp4Writer("/dev/null", 64, 64, 24.0)

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
    """Models a completely unresponsive ffmpeg: never reads stdin, never
    writes stderr, never exits on its own -- only `terminate()`/`kill()`
    breaks it, exactly like killing a real hung process breaks its pipes.
    Stdin capacity is tiny so a pending flush write blocks immediately
    unless the child (or `terminate()`) has already unblocked it."""

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
    proc._stdin_channel.write(b"x")  # fills the tiny channel: the next write blocks
    writer = object.__new__(encode.StreamingMp4Writer)
    writer._proc = proc
    writer._stderr_tail = encode._StderrTail(proc.stderr)

    completed, box = _run_with_timeout(writer.abort, timeout=encode._ABORT_WAIT_TIMEOUT + 5)

    assert completed, "abort() blocked -- it touched stdin before terminating the child"
    if "error" in box:
        raise box["error"]
    assert proc._returncode == -15
