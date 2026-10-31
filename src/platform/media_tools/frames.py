from __future__ import annotations

from pathlib import Path
from typing import Optional

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
) -> tuple[Path, int]:
    chain = []
    if fps:
        chain.append(f"fps={fps:.6f}")
    chain.append(f"scale={width}:{height}:flags=area")
    dest = ctx.new_path(".rgb", stem="frames")
    args = ["-i", str(media.path), "-an", "-sn", "-vf", ",".join(chain)]
    if max_frames:
        args += ["-frames:v", str(max_frames)]
    args += ["-pix_fmt", "rgb24", "-f", "rawvideo", str(dest)]
    ctx.ffmpeg(args, duration=media.duration)
    if not dest.is_file():
        raise ToolFailed("Could not read the video's frames")
    count = dest.stat().st_size // (width * height * CHANNELS)
    if count == 0:
        raise ToolFailed("Could not read the video's frames")
    return dest, count


def open_rgb_frames(path: Path, count: int, width: int, height: int) -> np.ndarray:
    return np.memmap(path, dtype=np.uint8, mode="r", shape=(count, height, width, CHANNELS))
