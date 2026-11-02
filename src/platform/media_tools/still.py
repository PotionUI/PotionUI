from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from PIL import Image

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.chain import Step, check_accepts, check_requires
from src.platform.media_tools.fastpath import encode_to, prepare_image
from src.platform.media_tools.types import (
    FilterFragment,
    MediaToolError,
    ToolContext,
    ToolFailed,
    ToolMedia,
    ToolSpec,
    UnsupportedMedia,
)

DEFAULT_FPS = 24.0
END_BACKOFF_SECONDS = 0.5
EXCERPT_ID = "preview_excerpt"
SCALE_ID = "preview_scale"
PREVIEW_SUFFIX = ".mp4"
PREVIEW_AUDIO_BITRATE = "128k"

Memo = Callable[[tuple, Callable[[], Any]], Any]


def _no_memo(key: tuple, compute: Callable[[], Any]) -> Any:
    return compute()


@dataclass(frozen=True)
class Clock:
    offset: float = 0.0
    rate: float = 1.0
    length: Optional[float] = None

    def source_time(self, time: float) -> float:
        return self.offset + time * self.rate

    def output_time(self, source: float) -> float:
        return (source - self.offset) / self.rate

    def trimmed(self, start: float, end: Optional[float]) -> "Clock":
        stop = end if self.length is None or end is None else min(end, self.length)
        length = None if stop is None else max(0.0, stop - start)
        return Clock(offset=self.source_time(start), rate=self.rate, length=length)

    def sped(self, speed: float) -> "Clock":
        length = None if self.length is None else self.length / speed
        return Clock(offset=self.offset, rate=self.rate * speed, length=length)

    def source_window(self) -> tuple[float, Optional[float]]:
        end = None if self.length is None else self.source_time(self.length)
        return self.offset, end


@dataclass
class StillFrame:
    source: ToolMedia
    time: float
    clock: Clock
    media: ToolMedia
    ctx: ToolContext
    memo: Memo = field(default=_no_memo)

    @property
    def fps(self) -> float:
        return float(self.media.fps or self.source.fps or DEFAULT_FPS)

    @property
    def index(self) -> int:
        return max(0, round(self.time * self.fps))


def frame_index(media: ToolMedia, time: float) -> int:
    fps = float(media.fps or DEFAULT_FPS)
    index = max(0, round(time * fps))
    if media.duration:
        index = min(index, max(0, math.ceil(media.duration * fps) - 1))
    return index


def read_frame(media: ToolMedia, time: float, ctx: ToolContext) -> tuple[Image.Image, float]:
    if media.kind == "image":
        return prepare_image(media.path), 0.0
    if media.kind != "video":
        raise UnsupportedMedia(f"A {media.kind} file has no frames to preview")
    fps = float(media.fps or DEFAULT_FPS)
    index = frame_index(media, time)
    snapped = index / fps
    seeks = [max(0.0, (index - 0.5) / fps)]
    if media.duration:
        seeks.append(max(0.0, media.duration - END_BACKOFF_SECONDS))
    seeks.append(0.0)
    dest = ctx.new_path(".png", stem="frame")
    for seek in seeks:
        ctx.ffmpeg([
            "-ss", f"{seek:.6f}", "-i", str(media.path), "-map", "0:v:0",
            "-frames:v", "1", "-pix_fmt", "rgb24", str(dest),
        ])
        if dest.is_file() and dest.stat().st_size > 0:
            return prepare_image(dest), snapped
    raise ToolFailed("Could not read a frame from the video")


def _tag(exc: MediaToolError, index: int) -> None:
    if getattr(exc, "step_index", None) is None:
        exc.step_index = index


def render_still(
    steps: Sequence[Step],
    source: ToolMedia,
    image: Image.Image,
    time: float,
    ctx: ToolContext,
    memo: Optional[Memo] = None,
) -> Image.Image:
    clock = Clock(length=source.duration)
    media = source
    for index, (spec, params) in enumerate(steps):
        try:
            check_accepts(spec, media)
            check_requires(spec, media)
            tool = spec.impl
            if tool is None or spec.cardinality == "many_to_one":
                raise UnsupportedMedia(f"{spec.label} has no frame preview")
            clock = tool.retime(clock, params)
            frame = StillFrame(
                source=source,
                time=max(0.0, clock.output_time(time)),
                clock=clock,
                media=media,
                ctx=ctx,
                memo=memo or _no_memo,
            )
            result = tool.still(image, params, frame)
            if result is None:
                raise UnsupportedMedia(f"{spec.label} has no frame preview")
            media = tool.advance(media, params).with_changes(
                kind=spec.output_kind(media.kind), width=result.width, height=result.height,
            )
        except MediaToolError as exc:
            _tag(exc, index)
            raise
        image = result
    return image


class _Excerpt(MediaTool):
    def __init__(self, start: float, end: Optional[float]):
        self.start = start
        self.end = end

    def video_filter(self, media: ToolMedia, params: dict) -> Optional[FilterFragment]:
        end = self.end if self.end is not None else media.duration
        duration = None if end is None else max(0.0, end - self.start)
        return FilterFragment(start=self.start, end=self.end, duration=duration)


class _Scale(MediaTool):
    def __init__(self, height: int, crf: int):
        self.height = height
        self.crf = crf

    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        return [self._scale(media, ctx) for media in inputs]

    def _scale(self, media: ToolMedia, ctx: ToolContext) -> ToolMedia:
        if media.height and media.height > self.height:
            size = f"scale=-2:{self.height}"
        else:
            size = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        out_path = ctx.new_path(PREVIEW_SUFFIX, stem="preview")
        args = ["-i", str(media.path), "-map", "0:v:0"]
        if media.has_audio is False:
            args += ["-an"]
        else:
            args += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", PREVIEW_AUDIO_BITRATE]
        args += [
            "-vf", f"{size},setsar=1", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(self.crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path),
        ]
        return encode_to(ctx, args, out_path, "video", media.duration)


def excerpt_window(
    steps: Sequence[Step], media: ToolMedia, start: float, duration: float
) -> tuple[float, float]:
    clock = Clock(length=media.duration)
    for spec, params in steps:
        if spec.cardinality == "one_to_one" and spec.impl is not None:
            clock = spec.impl.retime(clock, params)
    begin = max(0.0, clock.output_time(start))
    span = duration
    if clock.length is not None:
        begin = min(begin, max(0.0, clock.length - duration))
        span = min(duration, max(0.0, clock.length - begin))
    low = clock.source_time(begin)
    high = clock.source_time(begin + span)
    if media.duration is not None:
        high = min(high, media.duration)
    if high <= low:
        raise UnsupportedMedia("There is nothing left to preview after the trim")
    return low, high


def excerpt_steps(
    steps: Sequence[Step], media: ToolMedia, start: float, duration: float, height: int, crf: int
) -> list[Step]:
    low, high = excerpt_window(steps, media, start, duration)
    excerpt = ToolSpec(id=EXCERPT_ID, label="Preview", accepts=("video",), impl=_Excerpt(low, high))
    plan: list[Step] = [(excerpt, {})]
    for spec, params in steps:
        if spec.cardinality != "one_to_one" or spec.impl is None or spec.impl.window_only:
            continue
        plan.append((spec, params))
    plan.append((ToolSpec(id=SCALE_ID, label="Preview", accepts=("video",), impl=_Scale(height, crf)), {}))
    return plan
