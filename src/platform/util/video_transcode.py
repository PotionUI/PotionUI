import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT_SECONDS = 600
FFPROBE_TIMEOUT_SECONDS = 30

CONTAINERS = ("webm", "mp4")

VIDEO_CODECS = {"webm": "vp9", "mp4": "h264"}

DEFAULT_FPS = 24
MAX_FPS = 30

_WEBM_LADDER = ((34, 1.0), (38, 1.0), (42, 0.8), (46, 0.65), (50, 0.5))
_MP4_LADDER = ((26, 1.0), (30, 1.0), (34, 0.8), (38, 0.65), (42, 0.5))


class VideoTranscodeError(RuntimeError):
    pass


class FfmpegUnavailableError(VideoTranscodeError):
    pass


@dataclass(frozen=True)
class VideoProbe:
    duration: float
    width: int
    height: int
    fps: float
    video_codec: str
    has_audio: bool
    size_bytes: int


@dataclass(frozen=True)
class TranscodeSpec:
    container: str
    start: float = 0.0
    length: Optional[float] = None
    max_width: int = 1280
    fps: Optional[float] = None
    crf: Optional[int] = None
    width_scale: float = 1.0
    keep_audio: bool = False


@dataclass(frozen=True)
class TranscodeResult:
    path: Path
    bytes: int
    crf: int
    width_scale: float
    probe: Optional[VideoProbe]


def find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def find_ffprobe() -> Optional[str]:
    return shutil.which("ffprobe")


def _parse_rate(value: str) -> float:
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def parse_probe(payload: dict, size_bytes: int) -> Optional[VideoProbe]:
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        return None
    fmt = payload.get("format") or {}
    try:
        duration = float(video.get("duration") or fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return VideoProbe(
        duration=duration,
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        fps=_parse_rate(str(video.get("avg_frame_rate") or video.get("r_frame_rate") or "0")),
        video_codec=str(video.get("codec_name") or ""),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        size_bytes=size_bytes,
    )


def probe_video(path: Path) -> Optional[VideoProbe]:
    ffprobe = find_ffprobe()
    if ffprobe is None:
        return None
    command = [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=FFPROBE_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return parse_probe(payload, Path(path).stat().st_size)


def _even(value: int) -> int:
    return max(2, value - (value % 2))


def target_fps(source_fps: float, requested: Optional[float] = None) -> float:
    if requested:
        return float(requested)
    if source_fps and source_fps > 0:
        return min(float(source_fps), float(MAX_FPS))
    return float(DEFAULT_FPS)


def scale_filter(max_width: int, width_scale: float) -> str:
    width = _even(int(max_width * width_scale))
    return f"scale='min({width},iw)':-2:flags=lanczos"


def build_transcode_command(ffmpeg: str, src: Path, dest: Path, spec: TranscodeSpec) -> List[str]:
    if spec.container not in CONTAINERS:
        raise VideoTranscodeError(f"unsupported container: {spec.container}")

    crf = spec.crf if spec.crf is not None else quality_ladder(spec.container)[0][0]
    command: List[str] = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if spec.start > 0:
        command += ["-ss", f"{spec.start:.3f}"]
    command += ["-i", str(src)]
    if spec.length is not None:
        command += ["-t", f"{spec.length:.3f}"]

    fps = target_fps(0.0, spec.fps)
    filters = f"{scale_filter(spec.max_width, spec.width_scale)},fps={fps:g}"
    command += ["-map", "0:v:0", "-vf", filters, "-fps_mode", "cfr", "-map_metadata", "-1"]

    if spec.container == "webm":
        command += [
            "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(crf),
            "-row-mt", "1", "-deadline", "good", "-cpu-used", "2",
            "-pix_fmt", "yuv420p",
        ]
    else:
        command += [
            "-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        ]

    if spec.keep_audio:
        command += ["-map", "0:a:0?", "-c:a", "libopus" if spec.container == "webm" else "aac", "-b:a", "96k"]
    else:
        command += ["-an"]

    command += ["-f", spec.container, str(dest)]
    return command


def quality_ladder(container: str) -> Sequence[tuple]:
    return _WEBM_LADDER if container == "webm" else _MP4_LADDER


def _run(command: List[str], dest: Path) -> None:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=FFMPEG_TIMEOUT_SECONDS
        )
    except FileNotFoundError:
        raise FfmpegUnavailableError("ffmpeg is not available on this server")
    except subprocess.TimeoutExpired:
        _discard(dest)
        raise VideoTranscodeError("ffmpeg timed out")
    if result.returncode != 0:
        logger.error("ffmpeg exited %s: %s", result.returncode, (result.stderr or "")[-2000:])
        _discard(dest)
        raise VideoTranscodeError("ffmpeg failed to encode the video")
    if not dest.exists() or dest.stat().st_size == 0:
        _discard(dest)
        raise VideoTranscodeError("ffmpeg produced an empty file")


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def transcode_video(
    src: Path,
    dest: Path,
    spec: TranscodeSpec,
    max_bytes: Optional[int] = None,
) -> TranscodeResult:
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise FfmpegUnavailableError("ffmpeg is not available on this server")

    source_probe = probe_video(src)
    fps = target_fps(source_probe.fps if source_probe else 0.0, spec.fps)

    ladder = quality_ladder(spec.container)
    steps = [(spec.crf if spec.crf is not None else ladder[0][0], spec.width_scale)]
    if max_bytes is not None:
        floor = spec.crf if spec.crf is not None else ladder[0][0]
        steps = [(crf, scale) for crf, scale in ladder if crf >= floor and scale <= spec.width_scale] or steps

    last_size = 0
    for crf, scale in steps:
        attempt = TranscodeSpec(
            container=spec.container,
            start=spec.start,
            length=spec.length,
            max_width=spec.max_width,
            fps=fps,
            crf=crf,
            width_scale=scale,
            keep_audio=spec.keep_audio,
        )
        _run(build_transcode_command(ffmpeg, src, dest, attempt), dest)
        last_size = dest.stat().st_size
        if max_bytes is None or last_size <= max_bytes:
            return TranscodeResult(path=dest, bytes=last_size, crf=crf, width_scale=scale, probe=probe_video(dest))

    _discard(dest)
    raise VideoTranscodeError(f"could not fit the video within {max_bytes} bytes (smallest attempt was {last_size})")

