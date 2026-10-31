from __future__ import annotations

import json
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

import numpy as np

from src.platform.observability.logger import logger
from src.platform.util.video_transcode import find_ffmpeg, find_ffprobe

_ABORT_WAIT_TIMEOUT = 10
_KILL_REAP_TIMEOUT = 3
_STDERR_TAIL_BYTES = 8192
_MUX_TIMEOUT = 600
_PROBE_TIMEOUT = 10

_BANNER_AUDIO_RE = re.compile(r"Stream #\d+:\d+[^:\n]*:\s*Audio:")
_BANNER_STREAM_RE = re.compile(r"Stream #\d+:\d+")

AUDIO_PROBE_PRESENT = "present"
AUDIO_PROBE_SILENT = "silent"
AUDIO_PROBE_FAILED = "probe_failed"


class FFmpegNotFoundError(RuntimeError):
    pass


def require_ffmpeg() -> str:
    found = find_ffmpeg()
    if found is None:
        raise FFmpegNotFoundError(
            "ffmpeg binary not found on PATH and no bundled imageio-ffmpeg binary is available -- "
            "required to encode video output. Install ffmpeg (e.g. `apt install ffmpeg`, "
            "`winget install ffmpeg`) and retry."
        )
    return found


@dataclass(frozen=True)
class AudioProbeResult:
    status: str
    reason: Optional[str] = None


def probe_audio_stream(path: Union[str, Path]) -> AudioProbeResult:
    result = _ffprobe_audio_stream(path)
    if result.status != AUDIO_PROBE_FAILED:
        return result
    fallback = _banner_audio_stream(path)
    return fallback if fallback.status != AUDIO_PROBE_FAILED else result

def _banner_audio_stream(path: Union[str, Path]) -> AudioProbeResult:
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        return AudioProbeResult(AUDIO_PROBE_FAILED, "ffmpeg not found")
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-i", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=_PROBE_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return AudioProbeResult(AUDIO_PROBE_FAILED, f"ffmpeg banner probe failed: {exc}")
    banner = getattr(result, "stderr", None) or ""
    if not isinstance(banner, str):
        return AudioProbeResult(AUDIO_PROBE_FAILED, "ffmpeg banner unreadable")
    if _BANNER_AUDIO_RE.search(banner):
        return AudioProbeResult(AUDIO_PROBE_PRESENT)
    if _BANNER_STREAM_RE.search(banner):
        return AudioProbeResult(AUDIO_PROBE_SILENT)
    return AudioProbeResult(AUDIO_PROBE_FAILED, "ffmpeg listed no streams")

def _ffprobe_audio_stream(path: Union[str, Path]) -> AudioProbeResult:
    ffprobe = find_ffprobe()
    if ffprobe is None:
        return AudioProbeResult(AUDIO_PROBE_FAILED, "ffprobe not found on PATH")
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "quiet", "-print_format", "json",
                "-show_streams", "-select_streams", "a",
                str(path),
            ],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return AudioProbeResult(AUDIO_PROBE_FAILED, "ffprobe timed out")
    except OSError as exc:
        return AudioProbeResult(AUDIO_PROBE_FAILED, f"ffprobe could not run: {exc}")

    if result.returncode != 0:
        return AudioProbeResult(AUDIO_PROBE_FAILED, f"ffprobe exited {result.returncode}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return AudioProbeResult(AUDIO_PROBE_FAILED, "ffprobe returned unparseable output")

    if data.get("streams"):
        return AudioProbeResult(AUDIO_PROBE_PRESENT)
    return AudioProbeResult(AUDIO_PROBE_SILENT)


def has_audio_stream(path: Union[str, Path]) -> bool:
    return probe_audio_stream(path).status == AUDIO_PROBE_PRESENT


def mux_audio(
    video_only: Union[str, Path],
    source: Union[str, Path],
    out_path: Union[str, Path],
    *,
    shortest: bool,
    prefer_copy: bool,
) -> None:
    ffmpeg = require_ffmpeg()
    out = Path(out_path)
    audio_modes = [["-c:a", "aac", "-b:a", "192k"]]
    if prefer_copy:
        audio_modes.insert(0, ["-c:a", "copy"])
    failure = "ffmpeg audio mux failed"
    for audio_args in audio_modes:
        cmd = [
            ffmpeg, "-y",
            "-i", str(video_only), "-i", str(source),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", *audio_args,
        ]
        if shortest:
            cmd.append("-shortest")
        cmd.append(str(out))
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=_MUX_TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            out.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg audio mux timed out after {_MUX_TIMEOUT}s: {source}") from exc
        except OSError as exc:
            out.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg audio mux could not run: {exc}") from exc
        if result.returncode == 0:
            if not out.exists() or out.stat().st_size == 0:
                out.unlink(missing_ok=True)
                raise RuntimeError(f"ffmpeg reported success but produced no muxed output at {out_path}")
            return
        stderr = result.stderr.decode("utf-8", errors="replace")
        failure = f"ffmpeg audio mux failed (exit {result.returncode}): {stderr[-2000:]}"
        logger.warning("[VIDEO STREAM] audio mux via '%s' failed (exit %d)", " ".join(audio_args), result.returncode)
        out.unlink(missing_ok=True)
    raise RuntimeError(failure)


def mux_audio_from_source(
    video_only: Union[str, Path], source: Union[str, Path], out_path: Union[str, Path],
) -> bool:
    if not has_audio_stream(source):
        return False
    try:
        mux_audio(video_only, source, out_path, shortest=False, prefer_copy=True)
    except RuntimeError as exc:
        logger.warning("[VIDEO STREAM] audio mux failed (%s) -- keeping video-only output", exc)
        return False
    return True


class _StderrTail:

    def __init__(self, stream, max_bytes: int = _STDERR_TAIL_BYTES) -> None:
        self._max_bytes = max_bytes
        self._buf = bytearray()
        self._lock = threading.Lock()
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
        except (OSError, ValueError):
            pass

    def tail(self) -> bytes:
        with self._lock:
            return bytes(self._buf)

    def join(self, timeout: Optional[float] = None) -> bool:
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()


class StreamingMp4Writer:

    def __init__(self, out_path: Union[str, Path], width: int, height: int,
                 fps: float, codec: str = "libx264", crf: int = 18):
        ffmpeg = require_ffmpeg()
        self._out_path = Path(out_path)
        self.width = int(width)
        self.height = int(height)
        if self.width % 2 or self.height % 2:
            raise ValueError(
                f"StreamingMp4Writer needs even dimensions for yuv420p, got {self.width}x{self.height}"
            )
        cmd = [
            ffmpeg, "-y",
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
                self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            returncode = self._proc.wait(timeout=600)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._reap()
            raise RuntimeError("ffmpeg encode timed out after 600s")
        finally:
            self._finalize_streams()
        if returncode != 0:
            msg = self._stderr_tail.tail().decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"ffmpeg encode failed (exit {returncode}): {msg}")

    def abort(self) -> None:
        was_running = self._proc.poll() is None
        if was_running and self._proc.poll() is None:
            self._proc.kill()
        self._reap()
        self._finalize_streams()
        failed = was_running or (self._proc.returncode not in (0, None))
        out_path = getattr(self, "_out_path", None)
        if failed and out_path is not None:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _reap(self) -> None:
        try:
            self._proc.wait(timeout=_KILL_REAP_TIMEOUT)
        except subprocess.TimeoutExpired:
            pass

    def _finalize_streams(self) -> bool:
        joined = self._stderr_tail.join(timeout=_ABORT_WAIT_TIMEOUT)
        if not joined:
            logger.warning(
                "[VIDEO STREAM] stderr drain thread did not exit within "
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

