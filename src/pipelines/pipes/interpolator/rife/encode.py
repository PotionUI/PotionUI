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
from pathlib import Path
from typing import Union

import numpy as np

from src.pipelines.pipes._shared.media.video_encode import (
    FFmpegNotFoundError,
    has_audio_stream,
)
from src.platform.observability.logger import logger


class StreamingMp4Writer:
    """Pipe RGB uint8 frames to ffmpeg's stdin, one at a time, into a
    video-only MP4. All frames must share the writer's ``(width, height)``,
    which must be even (yuv420p)."""

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

    def write(self, frame_rgb: np.ndarray) -> None:
        arr = np.ascontiguousarray(frame_rgb, dtype=np.uint8)
        try:
            self._proc.stdin.write(arr.tobytes())
        except (BrokenPipeError, OSError):
            self._raise_with_stderr("ffmpeg died mid-encode")

    def close(self) -> None:
        # communicate() flushes and closes stdin itself (ffmpeg's EOF); closing
        # stdin beforehand makes that flush raise "flush of closed file".
        try:
            _, stderr = self._proc.communicate(timeout=600)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            raise RuntimeError("ffmpeg encode timed out after 600s")
        if self._proc.returncode != 0:
            msg = stderr.decode("utf-8", errors="replace")[-2000:] if stderr else ""
            raise RuntimeError(f"ffmpeg encode failed (exit {self._proc.returncode}): {msg}")

    def _raise_with_stderr(self, prefix: str) -> None:
        try:
            _, stderr = self._proc.communicate(timeout=10)
        except Exception:
            self._proc.kill()
            stderr = b""
        msg = stderr.decode("utf-8", errors="replace")[-2000:] if stderr else ""
        raise RuntimeError(f"{prefix} (exit {self._proc.returncode}): {msg}")


def mux_audio_from_source(video_only: Union[str, Path], source: Union[str, Path],
                          out_path: Union[str, Path]) -> bool:
    """Copy the video stream of ``video_only`` and the audio of ``source`` into
    ``out_path``, both at their full length. Returns True on success. On any
    failure (no audio stream, ffmpeg error, timeout) leaves ``out_path``
    untouched and returns False -- the caller then keeps the video-only file.

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
            return False
        if result.returncode == 0:
            return True
        logger.warning(
            "[INTERPOLATOR RIFE] audio mux via '%s' failed (exit %d)",
            " ".join(audio_args), result.returncode,
        )
    logger.warning("[INTERPOLATOR RIFE] audio mux failed -- keeping video-only output")
    return False
