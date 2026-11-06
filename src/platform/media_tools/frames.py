from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import numpy as np

from src.platform.media_tools.types import ToolContext, ToolFailed, ToolMedia

CHANNELS = 3


def decode_rgb_frames(
    media: ToolMedia,
    ctx: ToolContext,
    width: int,
    height: int,
    *,
    fps: Optional[float] = None,
    max_frames: Optional[int] = None,
    window: Optional[tuple[float, Optional[float]]] = None,
) -> tuple[Path, int]:
    chain = []
    if fps:
        chain.append(f"fps={fps:.6f}")
    chain.append(f"scale={width}:{height}:flags=area")
    dest = ctx.new_path(".rgb", stem="frames")
    args: list[str] = []
    duration = media.duration
    if window is not None:
        start, end = window
        if start > 0:
            args += ["-ss", f"{start:.6f}"]
        if end is not None:
            duration = max(0.0, end - start)
            args += ["-t", f"{duration:.6f}"]
        elif duration is not None:
            duration = max(0.0, duration - start)
    args += ["-i", str(media.path), "-an", "-sn", "-vf", ",".join(chain)]
    if max_frames:
        args += ["-frames:v", str(max_frames)]
    args += ["-pix_fmt", "rgb24", "-f", "rawvideo", str(dest)]
    ctx.ffmpeg(args, duration=duration)
    if not dest.is_file():
        raise ToolFailed("Could not read the video's frames")
    count = dest.stat().st_size // (width * height * CHANNELS)
    if count == 0:
        raise ToolFailed("Could not read the video's frames")
    return dest, count


def load_rgb_frames(path: Path, count: int, width: int, height: int) -> np.ndarray:
    data = np.fromfile(path, dtype=np.uint8, count=count * width * height * CHANNELS)
    return data.reshape(count, height, width, CHANNELS)


def map_rgb_chunks(
    path: Path,
    count: int,
    width: int,
    height: int,
    chunk_frames: int,
    function: Callable[[np.ndarray], np.ndarray],
) -> list[np.ndarray]:
    frame_bytes = width * height * CHANNELS
    results: list[np.ndarray] = []
    with open(path, "rb") as handle:
        for start in range(0, count, chunk_frames):
            size = min(chunk_frames, count - start)
            data = np.fromfile(handle, dtype=np.uint8, count=size * frame_bytes)
            results.append(function(data.reshape(size, height, width, CHANNELS)))
    return results
