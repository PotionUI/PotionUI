from __future__ import annotations

import math
import shutil

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.fastpath import AUDIO_OUTPUT_SUFFIXES, FALLBACK_AUDIO_SUFFIX, run_fragments
from src.platform.media_tools.types import (
    FilterFragment,
    InvalidToolParams,
    ToolContext,
    ToolFailed,
    ToolMedia,
    ToolSpec,
)

MAX_SPLIT_PARTS = 200
EVERY = "every"
AT = "at"

SPEC = dict(
    id="split",
    label="Split",
    icon="split",
    accepts=("audio", "video"),
    produces="same",
    cardinality="one_to_many",
    editor="split",
    name_suffix="-part",
    description="Cut into separate parts",
    params=[
        {
            "name": "mode",
            "type": "select",
            "label": "Split",
            "default": EVERY,
            "options": [
                {"label": "Every few seconds", "value": EVERY},
                {"label": "At a time", "value": AT},
            ],
        },
        {
            "name": "part_seconds",
            "type": "float",
            "label": "Part length",
            "min": 0.1,
            "unit": "s",
            "when": {"field": "mode", "equals": EVERY},
        },
        {
            "name": "at",
            "type": "float",
            "label": "At",
            "description": "Where the second part starts",
            "min": 0,
            "unit": "s",
            "when": {"field": "mode", "equals": AT},
        },
    ],
)


def _every(media: ToolMedia, part_seconds: float) -> list[float]:
    if not math.isfinite(part_seconds) or part_seconds <= 0:
        raise InvalidToolParams("part_seconds", "The part length must be a positive number of seconds")
    if part_seconds >= media.duration:
        raise InvalidToolParams(
            "part_seconds",
            f"The part length ({part_seconds:g}s) is not shorter than the media "
            f"({media.duration:.3f}s), so there is nothing to split",
        )
    part_count = math.ceil(media.duration / part_seconds)
    if part_count > MAX_SPLIT_PARTS:
        raise InvalidToolParams(
            "part_seconds",
            f"Splitting into {part_count} parts exceeds the {MAX_SPLIT_PARTS}-part limit",
        )
    return [part_seconds * index for index in range(1, part_count)]


def _at(media: ToolMedia, at: float) -> list[float]:
    if not math.isfinite(at) or at <= 0 or at >= media.duration:
        raise InvalidToolParams(
            "at", f"The split point ({at:g}s) must be inside the media (0 to {media.duration:.3f}s)"
        )
    return [at]


class Split(MediaTool):
    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        parts: list[ToolMedia] = []
        for media in inputs:
            parts.extend(self._split(media, params, ctx))
        return parts

    def still(self, image, params: dict, frame):
        return image

    def _split(self, media: ToolMedia, params: dict, ctx: ToolContext) -> list[ToolMedia]:
        if media.duration is None:
            raise ToolFailed("Could not read the media's duration")
        if params.get("mode", EVERY) == AT:
            return self._split_exact(media, _at(media, float(params["at"])), ctx)
        part_seconds = float(params["part_seconds"])
        cuts = _every(media, part_seconds)
        if media.kind == "video":
            return self._split_exact(media, cuts, ctx)
        return self._split_audio(media, part_seconds, ctx)

    def _split_exact(self, media: ToolMedia, cuts: list[float], ctx: ToolContext) -> list[ToolMedia]:
        bounds = [0.0, *cuts, media.duration]
        count = len(bounds) - 1
        parts: list[ToolMedia] = []
        try:
            for index in range(count):
                start, end = bounds[index], bounds[index + 1]
                last = index == count - 1
                fragment = FilterFragment(start=start, end=None if last else end, duration=end - start)
                parts.append(run_fragments(media, [fragment], ctx.scoped(index / count, (index + 1) / count)))
        except BaseException:
            for part in parts:
                part.path.unlink(missing_ok=True)
            raise
        return parts

    def _split_audio(self, media: ToolMedia, part_seconds: float, ctx: ToolContext) -> list[ToolMedia]:
        suffix = media.path.suffix.lower()
        if suffix not in AUDIO_OUTPUT_SUFFIXES:
            suffix = FALLBACK_AUDIO_SUFFIX
        parts_dir = ctx.new_path("", stem="parts")
        parts_dir.mkdir(parents=True, exist_ok=True)
        pattern = parts_dir / f"part_%03d{suffix}"
        try:
            ctx.ffmpeg(
                [
                    "-i", str(media.path),
                    "-f", "segment",
                    "-segment_time", f"{part_seconds:.6f}",
                    "-reset_timestamps", "1",
                    "-vn",
                    str(pattern),
                ],
                duration=media.duration,
            )
            produced = sorted(parts_dir.iterdir())
            if not produced or any(part.stat().st_size == 0 for part in produced):
                raise ToolFailed("The encoder produced an incomplete split")
            return [ctx.probe(part, "audio") for part in produced]
        except BaseException:
            shutil.rmtree(parts_dir, ignore_errors=True)
            raise


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=Split())
