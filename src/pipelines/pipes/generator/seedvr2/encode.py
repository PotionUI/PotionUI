"""Video-vs-audio failure classification for the SeedVR2 video path's
re-encode step.

The frame->MP4 encode and the source-audio mux are run as two SEPARATE
ffmpeg invocations (mirroring the split already used by
``interpolator/rife/encode.py``) rather than one combined call: that keeps a
genuine video-encoding defect (bad frames, disk full, an ffmpeg timeout) from
ever being caught by the same ``except`` that also catches an audio-specific
mux failure and silently retried away. Video failures always propagate; only
a failure isolated to the mux step degrades to an audio-less output, and only
then with an explicit, reported reason.

:func:`encode_frames_stream_to_mp4` is the video half of that split for a
STREAMED clip: it pipes frames to ffmpeg as they are produced instead of
requiring the whole decoded clip as one array, so the video path's peak memory
stays bounded by its temporal batch (see :mod:`stream`). It plugs into
:func:`encode_video_with_audio` as ``encode_video``, leaving the mux step and
its failure classification untouched.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional, Union

import numpy as np

from src.pipelines.pipes._shared.media.video_encode import (
    FFmpegNotFoundError,
    _build_ffmpeg_args,
    _pad_to_even,
    has_audio_stream,
)

# The argv builder and the even-dimension pad are shared with the eager
# `encode_frames_to_mp4` rather than restated here: a streamed encode that
# drifted from the eager one's ffmpeg flags would silently change output
# quality/container settings for the same pipe.
_FFMPEG_WAIT_TIMEOUT = 600

# `VideoAudioResult.audio_outcome` values.
AUDIO_MUXED = "muxed"
AUDIO_NOT_REQUESTED = "not_requested"
AUDIO_SILENT_SOURCE = "silent_source"
AUDIO_MUX_FAILED = "mux_failed"


@dataclass(frozen=True)
class VideoAudioResult:
    """Outcome of :func:`encode_video_with_audio` -- the final video path plus
    the ACTUAL audio outcome, never just the requested ``keep_audio`` flag.

    ``audio_outcome`` is one of the ``AUDIO_*`` constants above.
    ``omitted_reason`` is set only for :data:`AUDIO_MUX_FAILED`.
    """
    video_path: str
    audio_outcome: str
    omitted_reason: Optional[str] = None


def mux_audio_into_video(
    video_only: Union[str, Path], source: Union[str, Path], out_path: Union[str, Path],
) -> None:
    """Copy ``video_only``'s video stream and ``source``'s audio stream into
    ``out_path``. Raises ``RuntimeError`` on any mux failure (ffmpeg exit,
    timeout, or a zero-byte result). Callers are expected to have already
    confirmed ``source`` has an audio stream (see :func:`has_audio_stream`) --
    that is a "nothing to mux" case, not a mux failure, and is handled by
    :func:`encode_video_with_audio` before this is ever called.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_only), "-i", str(source),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg audio mux timed out after 600s: {source}") from e

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg audio mux failed (exit {result.returncode}): {stderr[-2000:]}")

    out = Path(out_path)
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"ffmpeg reported success but produced no muxed output at {out_path}")


def encode_video_with_audio(
    frames_arr,
    out_path: Union[str, Path],
    fps: float,
    *,
    source_audio_path: Optional[Union[str, Path]],
    keep_audio: bool,
    encode_video: Callable[..., object],
    mux_audio: Callable[[Union[str, Path], Union[str, Path], Union[str, Path]], None] = mux_audio_into_video,
    has_audio: Callable[[Union[str, Path]], bool] = has_audio_stream,
) -> VideoAudioResult:
    """Encode ``frames_arr`` to ``out_path`` (video only) then, if
    ``keep_audio``, mux in ``source_audio_path``'s audio track as a separate
    step.

    ``encode_video`` is always called first and un-guarded: any exception it
    raises is a video-encoding defect and propagates unchanged, regardless of
    ``keep_audio``. Only once that has succeeded is muxing attempted, so a
    mux failure can never be mistaken for -- or mask -- a video failure.

    A source with no audio stream at all is not a failure: it is reported as
    :data:`AUDIO_SILENT_SOURCE` without ever invoking ``mux_audio``. Only an
    actual ``mux_audio`` exception (ffmpeg error, timeout, empty output) is
    the "genuine mux failure" that falls back to the audio-less video, via
    :data:`AUDIO_MUX_FAILED` with ``omitted_reason`` set.
    """
    encode_video(frames_arr, out_path, fps=fps, audio=None)

    if not keep_audio:
        return VideoAudioResult(video_path=str(out_path), audio_outcome=AUDIO_NOT_REQUESTED)

    if source_audio_path is None or not has_audio(source_audio_path):
        return VideoAudioResult(video_path=str(out_path), audio_outcome=AUDIO_SILENT_SOURCE)

    muxed_path = f"{out_path}.audio.mp4"
    try:
        mux_audio(out_path, source_audio_path, muxed_path)
    except RuntimeError as exc:
        return VideoAudioResult(
            video_path=str(out_path), audio_outcome=AUDIO_MUX_FAILED, omitted_reason=str(exc),
        )
    return VideoAudioResult(video_path=muxed_path, audio_outcome=AUDIO_MUXED)


def _even_frame(frame) -> "np.ndarray":
    """One uint8 ``(H,W,3)`` frame, contiguous and edge-padded to even sides."""
    arr = np.ascontiguousarray(frame, dtype=np.uint8)
    if arr.ndim != 3 or arr.shape[-1] != 3:
        raise ValueError(f"expected a (H,W,3) uint8 frame, got shape {arr.shape}")
    return _pad_to_even(arr[np.newaxis])[0]


def _stderr_tail(handle) -> str:
    try:
        handle.seek(0)
        return handle.read().decode("utf-8", errors="replace")[-2000:]
    except OSError:  # pragma: no cover - a diagnostic read must never mask the failure
        return ""


def _terminate(proc: "subprocess.Popen") -> None:
    if proc.poll() is not None:
        return
    proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:  # pragma: no cover - the kill above already fired
        pass


def encode_frames_stream_to_mp4(
    frames: "Iterable[np.ndarray]",
    out_path: Union[str, Path],
    fps: float,
    *,
    codec: str = "libx264",
    crf: int = 18,
    audio: None = None,
) -> Path:
    """Encode an iterable of uint8 ``(H,W,3)`` RGB frames to ``out_path``,
    writing each frame to ffmpeg's stdin as it is produced.

    Video only: ``audio`` exists to match ``encode_frames_to_mp4``'s keyword and
    must be ``None`` -- audio is muxed as a separate step by
    :func:`encode_video_with_audio`, which is what keeps a mux failure
    distinguishable from a video failure.

    Frame geometry is taken from the first frame; a later frame of a different
    size is a defect, not something to pad around, because ffmpeg is already
    reading a fixed-size rawvideo stream by then. Any failure -- a frame the
    producer could not compute, a non-zero ffmpeg exit, a timeout -- kills the
    process and removes the partial file before propagating, so a failed encode
    never leaves a playable-looking output behind.
    """
    if audio is not None:
        raise TypeError(
            "encode_frames_stream_to_mp4 encodes video only; mux audio with encode_video_with_audio"
        )
    if shutil.which("ffmpeg") is None:
        raise FFmpegNotFoundError(
            "ffmpeg binary not found on PATH -- required to encode video output. "
            "Install ffmpeg (e.g. `apt install ffmpeg`) and retry."
        )

    frames_iter = iter(frames)
    try:
        frame = _even_frame(next(frames_iter))
    except StopIteration:
        raise ValueError("no frames to encode") from None

    height, width = int(frame.shape[0]), int(frame.shape[1])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = _build_ffmpeg_args(
        width=width, height=height, fps=fps, codec=codec, crf=crf,
        out_path=out_path, audio_path=None,
    )

    # ffmpeg's progress chatter goes to a file, not a pipe: a full stderr pipe
    # would block ffmpeg mid-encode while this loop blocks writing to stdin.
    stderr_file = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr_file,
    )
    try:
        while True:
            if frame.shape[0] != height or frame.shape[1] != width:
                raise ValueError(
                    f"frame size changed mid-stream: expected {width}x{height}, "
                    f"got {frame.shape[1]}x{frame.shape[0]}"
                )
            try:
                proc.stdin.write(memoryview(frame.reshape(-1)))
            except BrokenPipeError:
                break
            try:
                frame = _even_frame(next(frames_iter))
            except StopIteration:
                break

        proc.stdin.close()
        try:
            returncode = proc.wait(timeout=_FFMPEG_WAIT_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"ffmpeg timed out after {_FFMPEG_WAIT_TIMEOUT}s encoding {out_path}"
            ) from exc

        if returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed (exit {returncode}) encoding {out_path}: {_stderr_tail(stderr_file)}"
            )
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg reported success but produced no output at {out_path}")
        return out_path
    except BaseException:
        _terminate(proc)
        out_path.unlink(missing_ok=True)
        raise
    finally:
        if proc.stdin is not None and not proc.stdin.closed:
            proc.stdin.close()
        stderr_file.close()
