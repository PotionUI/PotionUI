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
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from src.pipelines.pipes._shared.media.video_encode import has_audio_stream

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
