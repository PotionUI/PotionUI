from __future__ import annotations

from typing import Optional

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.types import (
    FilterFragment,
    InvalidToolParams,
    ToolFailed,
    ToolMedia,
    ToolSpec,
)

DEFAULT_FPS = 24.0
DURATION_TOLERANCE = 0.05
FRAME_EDGE_MARGIN = 0.25

SPEC = dict(
    id="trim",
    label="Trim",
    icon="scissors",
    accepts=("video", "audio"),
    produces="same",
    cardinality="one_to_one",
    editor="trim",
    name_suffix="-trim",
    params=[
        {"name": "start", "type": "float", "label": "Start", "default": 0, "min": 0, "unit": "s"},
        {"name": "end", "type": "float", "label": "End", "min": 0, "unit": "s"},
    ],
)


def validated_window(start: float, end: float, duration: Optional[float]) -> tuple[float, float]:
    if duration is None:
        raise ToolFailed("Could not read the media's duration")
    if start < 0:
        raise InvalidToolParams("start", "The trim cannot start before the beginning")
    if end <= start:
        raise InvalidToolParams("end", "The trim must end after it starts")
    if start >= duration:
        raise InvalidToolParams("start", f"The trim starts ({start:g}s) past the end ({duration:.3f}s)")
    if end > duration + DURATION_TOLERANCE:
        raise InvalidToolParams("end", f"The trim ends ({end:g}s) past the end ({duration:.3f}s)")
    return start, min(end, duration)


def head_tail_frame_count(
    frames: Optional[int], seconds: Optional[float], rate: float, frame_step: int = 1
) -> int:
    step = max(1, int(frame_step or 1))
    if seconds and seconds > 0:
        count = round(seconds * rate)
    else:
        count = int(frames or 1)
    return max(1, (count - 1) // step * step + 1)


def head_tail_window(
    media: ToolMedia,
    keep: str,
    frames: Optional[int] = None,
    seconds: Optional[float] = None,
    fps: float = 0.0,
    frame_step: int = 1,
) -> tuple[float, float]:
    if keep not in ("head", "tail"):
        raise InvalidToolParams("keep", f"keep must be 'head' or 'tail', got {keep!r}")
    if media.duration is None:
        raise ToolFailed("Could not read the media's duration")
    rate = float(fps) if fps and fps > 0 else float(media.fps or DEFAULT_FPS)
    count = head_tail_frame_count(frames, seconds, rate, frame_step)
    window = count / rate
    margin = FRAME_EDGE_MARGIN / rate
    duration = float(media.duration)
    if window >= duration:
        return 0.0, duration
    if keep == "head":
        return 0.0, window - margin
    return max(0.0, duration - window - margin), duration


class Trim(MediaTool):
    window_only = True

    def check_params(self, params: dict) -> None:
        start = params.get("start") or 0.0
        end = params.get("end")
        if end is not None and end <= start:
            raise InvalidToolParams("end", "The trim must end after it starts")

    def video_filter(self, media: ToolMedia, params: dict) -> Optional[FilterFragment]:
        start, end = validated_window(float(params.get("start") or 0.0), float(params["end"]), media.duration)
        return FilterFragment(start=start, end=end, duration=end - start)

    def retime(self, clock, params: dict):
        return clock.trimmed(float(params.get("start") or 0.0), float(params["end"]))

    def still(self, image, params: dict, frame):
        return image


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=Trim())
