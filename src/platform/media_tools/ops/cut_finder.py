from __future__ import annotations


import numpy as np

from src.platform.imaging.shot_cuts import find_cuts, frame_features
from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.fastpath import run_fragments
from src.platform.media_tools.frames import decode_rgb_frames, open_rgb_frames
from src.platform.media_tools.types import (
    FilterFragment,
    InvalidToolParams,
    ToolContext,
    ToolFailed,
    ToolMedia,
    ToolSpec,
)

ANALYSIS_WIDTH = 64
ANALYSIS_HEIGHT = 36
CHUNK_FRAMES = 512
DEFAULT_FPS = 24.0
MAX_SHOTS = 200

SPEC = dict(
    id="cut_finder",
    label="Find cuts",
    icon="split",
    accepts=("video",),
    produces="video",
    cardinality="one_to_many",
    name_suffix="-shot",
    params=[
        {"name": "sensitivity", "type": "float", "label": "Sensitivity", "default": 50, "min": 0, "max": 100, "step": 5},
        {"name": "min_shot_seconds", "type": "float", "label": "Shortest shot", "default": 0.5, "min": 0.1, "max": 30, "step": 0.1, "unit": "s"},
    ],
)


def shot_boundaries(cut_frames: list[int], fps: float, duration: float) -> list[tuple[float, float]]:
    times = [0.0] + [(frame - 0.5) / fps for frame in cut_frames] + [duration]
    return list(zip(times[:-1], times[1:]))


class CutFinder(MediaTool):
    def check_params(self, params: dict) -> None:
        if float(params.get("min_shot_seconds", 0.5)) <= 0:
            raise InvalidToolParams("min_shot_seconds", "The shortest shot must be longer than zero")

    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        shots: list[ToolMedia] = []
        for media in inputs:
            shots.extend(self._split(media, params, ctx))
        return shots

    def _split(self, media: ToolMedia, params: dict, ctx: ToolContext) -> list[ToolMedia]:
        self.check_params(params)
        if media.duration is None:
            raise ToolFailed("Could not read the video's duration")
        fps = float(media.fps) if media.fps and media.fps > 0 else DEFAULT_FPS
        cuts = self._detect(media, fps, params, ctx.scoped(0.0, 0.3))
        if not cuts:
            raise ToolFailed("No cuts were found; try a higher sensitivity")
        windows = shot_boundaries(cuts, fps, media.duration)
        if len(windows) > MAX_SHOTS:
            raise InvalidToolParams(
                "sensitivity",
                f"Found {len(windows)} shots, over the {MAX_SHOTS}-shot limit; lower the sensitivity",
            )
        produced: list[ToolMedia] = []
        try:
            for index, (start, end) in enumerate(windows):
                stage = ctx.scoped(0.3 + 0.7 * index / len(windows), 0.3 + 0.7 * (index + 1) / len(windows))
                fragment = FilterFragment(start=start, end=end, duration=end - start)
                produced.append(run_fragments(media, [fragment], stage))
        except BaseException:
            for shot in produced:
                shot.path.unlink(missing_ok=True)
            raise
        return produced

    def _detect(self, media: ToolMedia, fps: float, params: dict, ctx: ToolContext) -> list[int]:
        path, count = decode_rgb_frames(media, ctx, ANALYSIS_WIDTH, ANALYSIS_HEIGHT, fps=fps)
        try:
            frames = open_rgb_frames(path, count, ANALYSIS_WIDTH, ANALYSIS_HEIGHT)
            parts = [frame_features(frames[start:start + CHUNK_FRAMES]) for start in range(0, count, CHUNK_FRAMES)]
            del frames
        finally:
            path.unlink(missing_ok=True)
        return find_cuts(
            np.concatenate(parts),
            fps,
            float(params.get("sensitivity", 50.0)),
            float(params.get("min_shot_seconds", 0.5)),
        )


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=CutFinder())
