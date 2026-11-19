from __future__ import annotations

from PIL import Image

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.layer_opacity import OPACITY_PARAM, read_opacity, with_opacity
from src.platform.imaging.pixels import MAX_IMAGE_DIMENSION
from src.platform.media_tools.probe import has_alpha_mode
from src.platform.media_tools.types import EDITOR_SCOPE, InvalidToolParams, ToolMedia, ToolSpec, UnsupportedMedia

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
        {"name": "canvas_width", "type": "integer", "label": "Canvas width", "default": 0, "min": 0, "max": MAX_IMAGE_DIMENSION, "unit": "px", "description": "0 keeps the picture's size; otherwise the picture is first placed on a canvas this wide"},
        {"name": "canvas_height", "type": "integer", "label": "Canvas height", "default": 0, "min": 0, "max": MAX_IMAGE_DIMENSION, "unit": "px"},
        {"name": "canvas_x", "type": "integer", "label": "Picture X on canvas", "default": 0, "min": 0, "max": MAX_IMAGE_DIMENSION, "unit": "px"},
        {"name": "canvas_y", "type": "integer", "label": "Picture Y on canvas", "default": 0, "min": 0, "max": MAX_IMAGE_DIMENSION, "unit": "px"},
    ],
)


def grown(image: Image.Image, params: dict) -> Image.Image:
    width, height = int(params.get("canvas_width") or 0), int(params.get("canvas_height") or 0)
    if not width and not height:
        return image
    if width < image.width or height < image.height or max(width, height) > MAX_IMAGE_DIMENSION:
        raise InvalidToolParams("canvas_width", "The canvas has to be at least as large as the picture and within the size limit")
    left = min(width - image.width, max(0, int(params.get("canvas_x") or 0)))
    top = min(height - image.height, max(0, int(params.get("canvas_y") or 0)))
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.paste(image.convert("RGBA"), (left, top))
    return canvas


class PlacePatch(MediaTool):
    def image(self, image: Image.Image, params: dict) -> Image.Image:
        patch_media = params.get("patch")
        if not isinstance(patch_media, ToolMedia):
            raise UnsupportedMedia("The edit needs its patch attached")
        with Image.open(patch_media.path) as opened:
            patch = opened.convert("RGBA")
        keep_alpha = has_alpha_mode(image)
        image = grown(image, params)
        width, height = image.size
        left = min(width - 1, max(0, round(float(params.get("x", 0)) * width)))
        top = min(height - 1, max(0, round(float(params.get("y", 0)) * height)))
        span_w = min(width - left, max(1, round(float(params.get("width", 1)) * width)))
        span_h = min(height - top, max(1, round(float(params.get("height", 1)) * height)))
        if patch.size != (span_w, span_h):
            patch = patch.resize((span_w, span_h), Image.Resampling.LANCZOS)
        patch = with_opacity(patch, read_opacity(params))
        canvas = image.convert("RGBA")
        canvas.alpha_composite(patch, dest=(left, top))
        result = canvas if keep_alpha else canvas.convert("RGB")
        icc = image.info.get("icc_profile")
        if icc:
            result.info["icc_profile"] = icc
        return result


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=PlacePatch())
