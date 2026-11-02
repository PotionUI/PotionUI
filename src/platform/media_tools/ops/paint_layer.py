from __future__ import annotations

from PIL import Image

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.probe import has_alpha_mode
from src.platform.media_tools.types import EDITOR_SCOPE, ToolMedia, ToolSpec, UnsupportedMedia

SPEC = dict(
    id="paint_layer",
    label="Paint",
    icon="brush",
    accepts=("image",),
    produces="same",
    cardinality="one_to_one",
    name_suffix="-paint",
    description="Painted strokes on top",
    scopes=(EDITOR_SCOPE,),
    params=[
        {
            "name": "layer",
            "type": "media",
            "label": "Layer",
            "description": "Transparent PNG of the strokes",
            "accepts": ["image"],
        },
    ],
)


class PaintLayer(MediaTool):
    def image(self, image: Image.Image, params: dict) -> Image.Image:
        layer_media = params.get("layer")
        if not isinstance(layer_media, ToolMedia):
            raise UnsupportedMedia("Paint needs its layer attached")
        with Image.open(layer_media.path) as opened:
            layer = opened.convert("RGBA")
        if layer.size != image.size:
            layer = layer.resize(image.size, Image.Resampling.LANCZOS)
        keep_alpha = has_alpha_mode(image)
        painted = Image.alpha_composite(image.convert("RGBA"), layer)
        result = painted if keep_alpha else painted.convert("RGB")
        icc = image.info.get("icc_profile")
        if icc:
            result.info["icc_profile"] = icc
        return result


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=PaintLayer())
