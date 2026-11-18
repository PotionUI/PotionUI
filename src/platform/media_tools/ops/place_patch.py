from __future__ import annotations

from PIL import Image

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.layer_opacity import OPACITY_PARAM, read_opacity, with_opacity
from src.platform.media_tools.probe import has_alpha_mode
from src.platform.media_tools.types import EDITOR_SCOPE, ToolMedia, ToolSpec, UnsupportedMedia

SPEC = dict(
    id="place_patch",
    label="Generated edit",
    icon="edit",
    accepts=("image",),
    produces="same",
    cardinality="one_to_one",
    name_suffix="-edit",
    description="A generated patch placed on the picture",
    scopes=(EDITOR_SCOPE,),
    params=[
        {
            "name": "patch",
            "type": "media",
            "label": "Patch",
            "description": "RGBA PNG made by an editor run",
            "accepts": ["image"],
        },
        {"name": "x", "type": "float", "label": "X", "default": 0, "min": 0, "max": 1, "step": 0.0001},
        {"name": "y", "type": "float", "label": "Y", "default": 0, "min": 0, "max": 1, "step": 0.0001},
        {"name": "width", "type": "float", "label": "Width", "default": 1, "min": 0.0001, "max": 1, "step": 0.0001},
        {"name": "height", "type": "float", "label": "Height", "default": 1, "min": 0.0001, "max": 1, "step": 0.0001},
        OPACITY_PARAM,
    ],
)


class PlacePatch(MediaTool):
    def image(self, image: Image.Image, params: dict) -> Image.Image:
        patch_media = params.get("patch")
        if not isinstance(patch_media, ToolMedia):
            raise UnsupportedMedia("The edit needs its patch attached")
        with Image.open(patch_media.path) as opened:
            patch = opened.convert("RGBA")
        width, height = image.size
        left = min(width - 1, max(0, round(float(params.get("x", 0)) * width)))
        top = min(height - 1, max(0, round(float(params.get("y", 0)) * height)))
        span_w = min(width - left, max(1, round(float(params.get("width", 1)) * width)))
        span_h = min(height - top, max(1, round(float(params.get("height", 1)) * height)))
        if patch.size != (span_w, span_h):
            patch = patch.resize((span_w, span_h), Image.Resampling.LANCZOS)
        patch = with_opacity(patch, read_opacity(params))
        keep_alpha = has_alpha_mode(image)
        canvas = image.convert("RGBA")
        canvas.alpha_composite(patch, dest=(left, top))
        result = canvas if keep_alpha else canvas.convert("RGB")
        icc = image.info.get("icc_profile")
        if icc:
            result.info["icc_profile"] = icc
        return result


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=PlacePatch())
