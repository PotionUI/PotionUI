from __future__ import annotations

from typing import Optional

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.types import FilterFragment, ToolMedia, ToolSpec

MAX_ATEMPO = 2.0
MIN_ATEMPO = 0.5
PITCH_SAMPLE_RATE = 48000

SPEC = dict(
    id="speed",
    label="Speed",
    icon="gauge",
    accepts=("video", "audio"),
    produces="same",
    cardinality="one_to_one",
    name_suffix="-speed",
    params=[
        {"name": "speed", "type": "float", "label": "Speed", "default": 2, "min": 0.25, "max": 4, "step": 0.25, "unit": "x"},
        {"name": "keep_pitch", "type": "checkbox", "label": "Keep pitch", "default": True},
    ],
)


def tempo_factors(speed: float) -> list[float]:
    factors: list[float] = []
    remaining = speed
    while remaining > MAX_ATEMPO:
        factors.append(MAX_ATEMPO)
        remaining /= MAX_ATEMPO
    while remaining < MIN_ATEMPO:
        factors.append(MIN_ATEMPO)
        remaining /= MIN_ATEMPO
    factors.append(remaining)
    return factors


def audio_filters(speed: float, keep_pitch: bool) -> list[str]:
    if keep_pitch:
        return [f"atempo={factor:.6g}" for factor in tempo_factors(speed)]
    return [
        f"aresample={PITCH_SAMPLE_RATE}",
        f"asetrate={PITCH_SAMPLE_RATE}*{speed:.6g}",
        f"aresample={PITCH_SAMPLE_RATE}",
    ]


class Speed(MediaTool):
    def video_filter(self, media: ToolMedia, params: dict) -> Optional[FilterFragment]:
        speed = float(params["speed"])
        fragment = FilterFragment(duration=media.duration / speed if media.duration else None)
        if media.kind == "video":
            fragment.vf = [f"setpts=PTS/{speed:.6g}"]
            if media.fps and media.fps > 0:
                fragment.vf.append(f"fps={media.fps:g}")
        if media.has_audio is not False:
            fragment.af = audio_filters(speed, bool(params.get("keep_pitch", True)))
        return fragment

    def retime(self, clock, params: dict):
        return clock.sped(float(params["speed"]))

    def still(self, image, params: dict, frame):
        return image


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=Speed())
