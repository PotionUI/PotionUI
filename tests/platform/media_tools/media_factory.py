from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from src.platform.util.video_transcode import find_ffmpeg


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


def run_ffmpeg(*args: str) -> None:
    subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-nostdin", "-y", "-loglevel", "error", *args],
        check=True,
        capture_output=True,
    )


def video_frame_count(path: Path) -> int:
    result = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    return int(re.findall(r"frame=\s*(\d+)", result.stderr)[-1])


def video_stream_line(path: Path) -> str:
    result = subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)], capture_output=True, text=True,
    )
    return next(line for line in result.stderr.splitlines() if "Video:" in line)


def decode_video_rgb(path: Path, width: int, height: int) -> np.ndarray:
    command = [
        find_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
        "-map", "0:v:0", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ]
    raw = subprocess.run(command, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width, 3).astype(np.float64)


def make_video(
    path: Path,
    seconds: float = 1.0,
    size: tuple[int, int] = (64, 48),
    fps: int = 10,
    audio: bool = True,
    freq: int = 440,
) -> Path:
    frames = round(seconds * fps)
    args = ["-f", "lavfi", "-i", f"testsrc=size={size[0]}x{size[1]}:rate={fps}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=44100:duration={seconds}"]
    args += ["-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "64k", "-shortest"]
    args += [str(path)]
    run_ffmpeg(*args)
    produced = video_frame_count(path)
    assert produced == frames, f"fixture clip {path.name} has {produced} frames, expected {frames}"
    return path


def make_audio(path: Path, seconds: float = 2.0, freq: int = 440) -> Path:
    run_ffmpeg("-f", "lavfi", "-i", f"sine=frequency={freq}:sample_rate=22050:duration={seconds}", str(path))
    return path


def make_image(
    path: Path,
    size: tuple[int, int] = (40, 30),
    mode: str = "RGB",
    color: tuple | str | int = (200, 40, 40),
) -> Path:
    Image.new(mode, size, color).save(path)
    return path
