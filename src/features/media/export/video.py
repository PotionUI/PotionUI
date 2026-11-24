from __future__ import annotations

import re
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path
from typing import Callable, Optional

from src.features.media.export.probe import FormatSupport
from src.features.media.export.specs import (
    GIF_COLOURS_BY_LEVEL,
    GIF_DEFAULT_FPS,
    PRORES_PROFILE_BY_LEVEL,
    ConversionCancelled,
    ConvertError,
    FormatUnavailable,
    VideoSpec,
    crf_for,
)

DURATION_PROBE_TIMEOUT_SECONDS = 20
KILL_WAIT_SECONDS = 5
FRAME_PATTERN = "frame_%05d.png"
AUDIO_BITRATE = "192k"

_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_FORMAT_CODEC = {"mp4_h264": "h264", "mp4_h265": "h265", "mp4_av1": "av1"}


def probe_duration(ffmpeg: str, source: Path) -> float:
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DURATION_PROBE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    match = _DURATION.search(result.stderr or "")
    if not match:
        return 0.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def _even(value: int) -> int:
    return max(2, int(value) - int(value) % 2)


def _filters(spec: VideoSpec, extra_fps: Optional[float] = None) -> list[str]:
    filters = []
    fps = spec.fps if spec.fps is not None else extra_fps
    if fps is not None:
        filters.append(f"fps={float(fps):g}")
    if spec.size is not None:
        filters.append(f"scale={_even(spec.size[0])}:{_even(spec.size[1])}:flags=lanczos")
    return filters


def _audio_args(spec: VideoSpec, codec: str) -> list[str]:
    if not spec.include_audio:
        return ["-an"]
    return ["-map", "0:a?", "-c:a", codec] + (["-b:a", AUDIO_BITRATE] if codec == "aac" else [])


def build_command(ffmpeg: str, source: Path, output: Path, spec: VideoSpec, support: FormatSupport) -> list[str]:
    spec.validate()
    if not support.available:
        raise FormatUnavailable(support.reason or f"{spec.fmt} unavailable")
    base = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(source), "-map", "0:v:0"]
    level = spec.quality_level
    filters = _filters(spec)
    tail = ["-progress", "pipe:1", "-nostats", str(output)]
    if spec.fmt in _FORMAT_CODEC:
        codec = _FORMAT_CODEC[spec.fmt]
        crf = crf_for(codec, level)
        encoder = support.encoder
        args = ["-c:v", encoder, "-crf", str(crf), "-pix_fmt", "yuv420p"]
        if codec == "h264":
            args += ["-preset", "medium", "-movflags", "+faststart"]
        elif codec == "h265":
            args += ["-preset", "medium", "-tag:v", "hvc1", "-movflags", "+faststart"]
            args += ["-x265-params", "log-level=error" + (":lossless=1" if level == "lossless" else "")]
        elif encoder == "libsvtav1":
            args += ["-preset", "8", "-movflags", "+faststart"]
        else:
            args += ["-b:v", "0", "-cpu-used", "6", "-row-mt", "1", "-movflags", "+faststart"]
            if level == "lossless":
                args += ["-aom-params", "lossless=1"]
        if filters:
            args += ["-vf", ",".join(filters)]
        return base + args + _audio_args(spec, "aac") + tail
    if spec.fmt == "mov_prores":
        profile = PRORES_PROFILE_BY_LEVEL[level]
        alpha = spec.keep_alpha and profile == 4
        pix_fmt = "yuva444p10le" if alpha else ("yuv444p10le" if profile == 4 else "yuv422p10le")
        args = ["-c:v", "prores_ks", "-profile:v", str(profile), "-vendor", "apl0", "-pix_fmt", pix_fmt]
        if filters:
            args += ["-vf", ",".join(filters)]
        return base + args + _audio_args(spec, "pcm_s16le") + tail
    if spec.fmt == "gif":
        colours = GIF_COLOURS_BY_LEVEL[level]
        chain = ",".join(_filters(spec, extra_fps=GIF_DEFAULT_FPS))
        graph = f"{chain},split[a][b];[a]palettegen=max_colors={colours}[p];[b][p]paletteuse=dither=bayer:bayer_scale=3"
        return base + ["-vf", graph, "-loop", "0", "-an"] + tail
    pix_fmt = "rgba" if spec.keep_alpha else "rgb24"
    args = ["-c:v", "png", "-pix_fmt", pix_fmt, "-compression_level", "6"]
    if filters:
        args += ["-vf", ",".join(filters)]
    return base + args + ["-an", "-progress", "pipe:1", "-nostats", str(output)]


def _parse_progress_line(line: str, duration: float) -> Optional[float]:
    key, _, value = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms"):
        try:
            seconds = int(value) / 1_000_000
        except ValueError:
            return None
        if duration > 0:
            return max(0.0, min(0.99, seconds / duration))
        return None
    if key == "progress" and value == "end":
        return 1.0
    return None


def _terminate(process) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=KILL_WAIT_SECONDS)
    except Exception:
        pass


def _zip_frames(frames_dir: Path, destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_STORED) as archive:
        for frame in sorted(frames_dir.glob("frame_*.png")):
            archive.write(frame, frame.name)


def run_conversion(
    ffmpeg: str,
    source: Path,
    part_path: Path,
    spec: VideoSpec,
    support: FormatSupport,
    cancel: threading.Event,
    on_progress: Callable[[float], None],
    popen: Callable = subprocess.Popen,
    duration: Optional[float] = None,
) -> Path:
    part_path = Path(part_path)
    frames_dir: Optional[Path] = None
    if spec.fmt == "png_sequence_zip":
        frames_dir = part_path.with_name(part_path.name + ".frames")
        frames_dir.mkdir(parents=True, exist_ok=True)
        command = build_command(ffmpeg, source, frames_dir / FRAME_PATTERN, spec, support)
    else:
        command = build_command(ffmpeg, source, part_path, spec, support)
        command[-1:-1] = ["-f", _muxer(spec)]
    total = probe_duration(ffmpeg, source) if duration is None else duration
    process = None
    try:
        if cancel.is_set():
            raise ConversionCancelled()
        process = popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stderr_chunks: list[str] = []
        drain = threading.Thread(target=lambda: stderr_chunks.append(process.stderr.read()), daemon=True)
        drain.start()
        for line in process.stdout:
            if cancel.is_set():
                _terminate(process)
                raise ConversionCancelled()
            fraction = _parse_progress_line(line, total)
            if fraction is not None:
                on_progress(fraction)
        process.wait()
        drain.join(timeout=KILL_WAIT_SECONDS)
        if cancel.is_set():
            raise ConversionCancelled()
        if process.returncode != 0:
            tail = "".join(stderr_chunks).strip().splitlines()[-3:]
            raise ConvertError("ffmpeg failed: " + " | ".join(tail))
        if frames_dir is not None:
            _zip_frames(frames_dir, part_path)
        if not part_path.is_file() or part_path.stat().st_size == 0:
            raise ConvertError("ffmpeg produced no output")
        on_progress(1.0)
        return part_path
    except BaseException:
        if process is not None and process.poll() is None:
            _terminate(process)
        try:
            part_path.unlink()
        except OSError:
            pass
        raise
    finally:
        if frames_dir is not None:
            shutil.rmtree(frames_dir, ignore_errors=True)


def _muxer(spec: VideoSpec) -> str:
    return {"mov_prores": "mov", "gif": "gif"}.get(spec.fmt, "mp4")
