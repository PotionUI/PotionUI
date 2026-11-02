from __future__ import annotations

from typing import Optional

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.types import FilterFragment, ToolMedia, ToolSpec

SPEC = dict(
    id="mute",
    label="Mute",
    icon="editor-mute",
    accepts=("video",),
    produces="same",
    cardinality="one_to_one",
    group="audio",
    name_suffix="-muted",
    description="Drop the sound",
    requires={"has_audio": True},
    params=[],
)


class Mute(MediaTool):
    def video_filter(self, media: ToolMedia, params: dict) -> Optional[FilterFragment]:
        return FilterFragment(drop_audio=True, copy_video=True)

    def still(self, image, params: dict, frame):
        return image


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=Mute())
