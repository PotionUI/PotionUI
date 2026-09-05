"""Incremental (streaming) MP4 encoding for the RIFE interpolator pipe.

The generic `_shared/media/video_encode.encode_frames_to_mp4` takes the WHOLE
frame array at once; RIFE interpolation must not hold the interpolated clip in
memory (it is `factor`x longer than the source), so frames are piped to ffmpeg
one at a time as they are produced. Audio is muxed in a second copy-only pass
from the source video, reusing `has_audio_stream` from the shared helper and
mirroring the source's silent-failure behaviour (a failed mux keeps the
video-only output rather than raising).
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional, Union

import numpy as np

from src.pipelines.pipes._shared.media.video_encode import (
    FFmpegNotFoundError,
    has_audio_stream,
)
from src.platform.observability.logger import logger

_ABORT_WAIT_TIMEOUT = 10
#: How much of ffmpeg's stderr chatter to retain for an error message.
_STDERR_TAIL_BYTES = 8192


class _StderrTail:
    """Continuously drains a pipe on a background thread, RETAINING only the
    last `max_bytes` (default :data:`_STDERR_TAIL_BYTES`). Each individual
    ``read()`` is chunked at 64 KiB -- a separate, transient buffer that is
    never itself retained -- so the bound above describes the tail this
    keeps, not this thread's whole working set.

    ffmpeg logs progress/diagnostics to stderr as it runs; a plain
    ``stderr=PIPE`` left unread until :meth:`close`/:meth:`abort` fills the
    OS pipe buffer once a chatty encode outruns it, which blocks ffmpeg's own
    write(2) to its stderr -- and a process blocked writing its OWN stderr
    stops reading stdin too, so ``write()`` (this writer's) blocks right
    behind it, before any cancellation check or timeout ever runs. Draining
    continuously on a separate thread removes that coupling entirely."""

    def __init__(self, stream, max_bytes: int = _STDERR_TAIL_BYTES) -> None:
        self._max_bytes = max_bytes
        self._buf = bytearray()
        self._lock = threading.Lock()
        # Set from INSIDE `_drain`'s own `finally`, never assigned by a
        # caller -- this must reflect whether the drain loop actually
        # exited, not merely that finalisation was requested. A join that
        # times out leaves this False; only the thread itself sets it True.
        self.finished = False
        self._thread = threading.Thread(
            target=self._drain, args=(stream,), daemon=True,
        )
        self._thread.start()

    def _drain(self, stream) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                with self._lock:
                    self._buf.extend(chunk)
                    overflow = len(self._buf) - self._max_bytes
                    if overflow > 0:
                        del self._buf[:overflow]
        except (OSError, ValueError):  # pragma: no cover - stream torn down mid-read
            pass
        finally:
            self.finished = True

    def tail(self) -> bytes:
        with self._lock:
            return bytes(self._buf)

    def join(self, timeout: Optional[float] = None) -> bool:
        """Wait up to `timeout` for the drain thread to exit. Returns
        whether it actually did -- a caller must not treat a timed-out join
        as the thread having finished; check :attr:`finished` (or
        :meth:`is_alive`) for the real outcome."""
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def is_alive(self) -> bool:
        return self._thread.is_alive()


class StreamingMp4Writer:
    """Pipe RGB uint8 frames to ffmpeg's stdin, one at a time, into a
    video-only MP4. All frames must share the writer's ``(width, height)``,
    which must be even (yuv420p).

    Usable as a context manager: a clean exit calls :meth:`close` (raises on a
    bad encode), an exception in the ``with`` block calls :meth:`abort`
    instead so a cancelled or failed clip never leaves the ffmpeg child
    running."""

    def __init__(self, out_path: Union[str, Path], width: int, height: int,
                 fps: float, codec: str = "libx264", crf: int = 18):
        if shutil.which("ffmpeg") is None:
            raise FFmpegNotFoundError(
                "ffmpeg binary not found on PATH -- required to encode video output."
            )
        self.width = int(width)
        self.height = int(height)
        if self.width % 2 or self.height % 2:
            raise ValueError(
                f"StreamingMp4Writer needs even dimensions for yuv420p, got {self.width}x{self.height}"
            )
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{self.width}x{self.height}", "-r", str(fps),
            "-i", "-", "-an",
            "-c:v", codec, "-pix_fmt", "yuv420p", "-crf", str(crf),
            "-movflags", "+faststart",
            str(out_path),
        ]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        # Drained continuously from the moment the process exists -- see
        # `_StderrTail`'s docstring for why this can never be deferred to
        # close()/abort() time.
        self._stderr_tail = _StderrTail(self._proc.stderr)

    def write(self, frame_rgb: np.ndarray) -> None:
        arr = np.ascontiguousarray(frame_rgb, dtype=np.uint8)
        try:
            self._proc.stdin.write(arr.tobytes())
        except (BrokenPipeError, OSError):
            self._raise_with_stderr("ffmpeg died mid-encode")

    def close(self) -> None:
        try:
            if self._proc.stdin is not None and not self._proc.stdin.closed:
                self._proc.stdin.close()  # signals EOF; ffmpeg finishes and exits
        except (BrokenPipeError, OSError):
            pass
        try:
            returncode = self._proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._reap()
            raise RuntimeError("ffmpeg encode timed out after 600s")
        finally:
            # Reached on the success path AND the timeout's raise above (a
            # `finally` runs before an exception in the `except` clause it
            # guards propagates) -- ffmpeg has already been waited/killed/
            # reaped by this point either way, so it is safe to close the
            # streams here.
            self._finalize_streams()
        if returncode != 0:
            msg = self._stderr_tail.tail().decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"ffmpeg encode failed (exit {returncode}): {msg}")

    def abort(self) -> None:
        """Stop the ffmpeg child without waiting for a clean encode.

        Idempotent -- safe to call more than once, and safe to call after
        :meth:`close` (a no-op once the process has already exited). Used when
        the clip this writer belongs to is cancelled or fails.

        Terminates (escalating to a kill on timeout) BEFORE touching stdin --
        closing our write end first would flush any bytes Python has buffered
        but not yet handed to the OS, a write that blocks for as long as the
        child is alive and not reading. Terminating first guarantees that
        flush either completes immediately (the child drains what remains of
        stdin as it exits) or fails immediately with a broken-pipe error once
        the child is gone -- it can never block waiting on a reader that is
        exactly the thing an abort gives up on waiting for."""
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=_ABORT_WAIT_TIMEOUT)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._reap()
        self._finalize_streams()

    def _reap(self) -> None:
        try:
            self._proc.wait(timeout=_ABORT_WAIT_TIMEOUT)
        except subprocess.TimeoutExpired:  # pragma: no cover - a killed process not reaping is a hang, not a retry case
            pass

    def _finalize_streams(self) -> bool:
        """Release everything this writer's process streams hold, once
        ffmpeg itself has stopped producing (the caller has already waited,
        killed and reaped it). The ONE shared path reached from every
        terminal operation -- close()'s success path AND its timeout branch,
        abort(), and _raise_with_stderr()'s mid-encode failure -- so a
        retained writer/Popen never keeps stdin or stderr open past its own
        terminal call.

        Each step is independent and best-effort: the drain-thread join and
        each stream's close run in their own try/scope, so one failing can
        never skip the others or mask whatever exception the caller is
        already raising around this call.

        Returns whether the drain thread actually exited within the bounded
        wait -- ``_StderrTail.join()``'s own result, reported rather than
        assumed. A caller never treats a timed-out join as the reader having
        finished: :attr:`_StderrTail.finished` is set only by the thread
        itself, from inside :meth:`_StderrTail._drain`'s own ``finally``."""
        joined = self._stderr_tail.join(timeout=_ABORT_WAIT_TIMEOUT)
        if not joined:
            logger.warning(
                "[INTERPOLATOR RIFE] stderr drain thread did not exit within "
                "the %ss bounded wait", _ABORT_WAIT_TIMEOUT,
            )
        try:
            if self._proc.stdin is not None and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            if self._proc.stderr is not None and not self._proc.stderr.closed:
                self._proc.stderr.close()
        except OSError:
            pass
        return joined

    def _raise_with_stderr(self, prefix: str) -> None:
        if self._proc.poll() is None:
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._reap()
        self._finalize_streams()
        msg = self._stderr_tail.tail().decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"{prefix} (exit {self._proc.returncode}): {msg}")

    def __enter__(self) -> "StreamingMp4Writer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()
        else:
            self.abort()


def mux_audio_from_source(video_only: Union[str, Path], source: Union[str, Path],
                          out_path: Union[str, Path]) -> bool:
    """Copy the video stream of ``video_only`` and the audio of ``source`` into
    ``out_path``, both at their full length. Returns True on success. On any
    failure (no audio stream, ffmpeg error, timeout, non-zero exit) removes
    whatever ``out_path`` may hold and returns False -- the caller then keeps
    the video-only file; a caller never has to know a failed attempt left a
    partial file at ``out_path``.

    Deliberately no ``-shortest``: the interpolated video already runs the
    source's length, so trimming to the shorter stream only ever cuts the audio.
    The audio is stream-copied when the codec allows (an AAC re-encode pads the
    track out to its 1024-sample frame grid, which moves the duration); the
    re-encode is the fallback for codecs MP4 cannot hold as-is."""
    if not has_audio_stream(source):
        return False
    for audio_args in (["-c:a", "copy"], ["-c:a", "aac", "-b:a", "192k"]):
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_only), "-i", str(source),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", *audio_args,
            str(out_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=600)
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("[INTERPOLATOR RIFE] audio mux failed (%s) -- keeping video-only output", exc)
            Path(out_path).unlink(missing_ok=True)
            return False
        if result.returncode == 0:
            return True
        logger.warning(
            "[INTERPOLATOR RIFE] audio mux via '%s' failed (exit %d)",
            " ".join(audio_args), result.returncode,
        )
        Path(out_path).unlink(missing_ok=True)
    logger.warning("[INTERPOLATOR RIFE] audio mux failed -- keeping video-only output")
    return False
