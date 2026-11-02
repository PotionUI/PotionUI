from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from src.platform.media_tools.types import FilterFragment, ToolContext, ToolMedia

if TYPE_CHECKING:
    from PIL import Image

    from src.platform.media_tools.still import Clock, StillFrame


class MediaTool:
    window_only = False

    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        from src.platform.media_tools.fastpath import run_single

        return [run_single(self, media, params, ctx) for media in inputs]

    def video_filter(self, media: ToolMedia, params: dict) -> Optional[FilterFragment]:
        return None

    def image(self, image: "Image.Image", params: dict) -> "Image.Image":
        raise NotImplementedError

    def check_params(self, params: dict) -> None:
        return None

    def has_image_path(self) -> bool:
        return type(self).image is not MediaTool.image

    def retime(self, clock: "Clock", params: dict) -> "Clock":
        return clock

    def still(self, image: "Image.Image", params: dict, frame: "StillFrame") -> Optional["Image.Image"]:
        if self.has_image_path():
            return self.image(image, params)
        return None

    def advance(self, media: ToolMedia, params: dict) -> ToolMedia:
        from src.platform.media_tools.fastpath import advance_media

        if media.kind == "image":
            return media
        fragment = self.video_filter(media, params)
        return advance_media(media, fragment) if fragment is not None else media
