import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Union

from src.platform.util.video_transcode import VideoProbe, find_ffmpeg, probe_video

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]
SAMPLE_RATE = 48000
FFMPEG_TIMEOUT_SECONDS = 1800
DEFAULT_FPS = 24.0


class StitchError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClipInfo:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool


def probe_clip(
    path: PathLike, *, probe: Callable[[Path], Optional[VideoProbe]] = probe_video,
) -> Optional[ClipInfo]:
    found = probe(Path(path))
    if found is None or found.width <= 0 or found.height <= 0 or found.duration <= 0:
        return None
    return ClipInfo(
        width=found.width,
        height=found.height,
        fps=found.fps if found.fps > 0 else DEFAULT_FPS,
        duration=found.duration,
        has_audio=found.has_audio,
    )


def concat_command(
    ffmpeg: str, clips: Sequence[PathLike], infos: Sequence[ClipInfo], out_path: PathLike,
) -> List[str]:
    first = infos[0]
    width, height = first.width - first.width % 2, first.height - first.height % 2
    with_audio = any(info.has_audio for info in infos)
    command = [ffmpeg, "-y", "-loglevel", "error"]
    for clip in clips:
        command += ["-i", str(clip)]
    silence_inputs = {}
    for index, info in enumerate(infos):
        if with_audio and not info.has_audio:
            silence_inputs[index] = len(clips) + len(silence_inputs)
            command += [
                "-f", "lavfi", "-t", f"{info.duration:.3f}",
                "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo",
            ]
    filters: List[str] = []
    joined = ""
    for index in range(len(clips)):
        filters.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={first.fps:g},format=yuv420p[v{index}]"
        )
        joined += f"[v{index}]"
        if with_audio:
            source = f"{silence_inputs[index]}:a" if index in silence_inputs else f"{index}:a"
            filters.append(f"[{source}]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo[a{index}]")
            joined += f"[a{index}]"
    filters.append(f"{joined}concat=n={len(clips)}:v=1:a={1 if with_audio else 0}[v]" + ("[a]" if with_audio else ""))
    command += ["-filter_complex", ";".join(filters), "-map", "[v]"]
    if with_audio:
        command += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    command += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(out_path),
    ]
    return command


def _ffmpeg_concat(
    clips: Sequence[PathLike], out_path: Path, *, run: Callable, find: Callable[[], Optional[str]], probe: Callable,
) -> bool:
    ffmpeg = find()
    if ffmpeg is None:
        return False
    infos = [probe(clip) for clip in clips]
    if any(info is None for info in infos):
        return False
    try:
        result = run(concat_command(ffmpeg, clips, infos, out_path), capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as error:
        logger.warning(f"[CLOUD_STITCH] ffmpeg could not join the shots: {type(error).__name__}")
        return False
    if result.returncode != 0 or not out_path.is_file() or out_path.stat().st_size == 0:
        stderr = (result.stderr or b"")[-400:]
        logger.warning(f"[CLOUD_STITCH] ffmpeg could not join the shots: {stderr!r}")
        return False
    return True


def _opencv_concat(clips: Sequence[PathLike], out_path: Path) -> None:
    import cv2

    writer = None
    size = None
    fps = DEFAULT_FPS
    try:
        for clip in clips:
            capture = cv2.VideoCapture(str(clip))
            if not capture.isOpened():
                raise StitchError(f"could not open shot {clip}")
            try:
                if writer is None:
                    fps = capture.get(cv2.CAP_PROP_FPS) or DEFAULT_FPS
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    if writer is None:
                        size = (frame.shape[1], frame.shape[0])
                        writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
                        if not writer.isOpened():
                            raise StitchError("could not write the joined video")
                    if (frame.shape[1], frame.shape[0]) != size:
                        frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
                    writer.write(frame)
            finally:
                capture.release()
    finally:
        if writer is not None:
            writer.release()
    if writer is None or not out_path.is_file() or out_path.stat().st_size == 0:
        raise StitchError("the shots had no frames to join")


def stitch_clips(
    clips: Sequence[PathLike],
    out_path: PathLike,
    *,
    run: Callable = subprocess.run,
    find: Callable[[], Optional[str]] = find_ffmpeg,
    probe: Optional[Callable[[PathLike], Optional[ClipInfo]]] = None,
) -> Path:
    if not clips:
        raise StitchError("there are no shots to join")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if _ffmpeg_concat(clips, out_path, run=run, find=find, probe=probe or probe_clip):
        logger.info(f"[CLOUD_STITCH] joined {len(clips)} shot(s) with ffmpeg")
        return out_path
    logger.warning("[CLOUD_STITCH] ffmpeg was not usable; joining the shots without their sound")
    _opencv_concat(clips, out_path)
    return out_path
