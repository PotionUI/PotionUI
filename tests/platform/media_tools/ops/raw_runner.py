from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from src.platform.media_tools import ToolCancelled, ToolContext, ToolMedia


class RawRunner:
    def __init__(self, frames: Optional[np.ndarray] = None, fail_encode: Optional[int] = None, error: Optional[Exception] = None):
        self.frames = frames
        self.fail_encode = fail_encode
        self.error = error or ToolCancelled()
        self.commands: list[list[str]] = []

    def run(self, args, *, duration=None, on_progress=None, is_cancelled=None):
        args = list(args)
        self.commands.append(args)
        target = Path(args[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        if "rawvideo" in args:
            size = args[args.index("-vf") + 1].split("scale=")[1].split(":")
            width, height = int(size[0]), int(size[1])
            target.write_bytes(self._resized(width, height).tobytes())
        else:
            target.write_bytes(b"partial")
            if self.fail_encode is not None and len(self.encodes()) == self.fail_encode:
                raise self.error
            target.write_bytes(b"encoded")

    def _resized(self, width: int, height: int) -> np.ndarray:
        count = self.frames.shape[0]
        rows = np.linspace(0, self.frames.shape[1] - 1, height).astype(int)
        cols = np.linspace(0, self.frames.shape[2] - 1, width).astype(int)
        return self.frames[:, rows][:, :, cols].reshape(count, height, width, 3)

    def encodes(self) -> list[list[str]]:
        return [command for command in self.commands if "rawvideo" not in command]

    def decodes(self) -> list[list[str]]:
        return [command for command in self.commands if "rawvideo" in command]


def video_media(tmp_path, duration=2.0, fps=24.0, width=64, height=36) -> ToolMedia:
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"source")
    return ToolMedia(path=path, kind="video", width=width, height=height, duration=duration, fps=fps, has_audio=False)


def raw_context(tmp_path, frames, **runner_options) -> ToolContext:
    def prober(path, kind=None):
        return ToolMedia(path=Path(path), kind="video", width=64, height=36, duration=1.0, fps=24.0)

    return ToolContext(tmp_path / "scratch", runner=RawRunner(frames, **runner_options), prober=prober)
