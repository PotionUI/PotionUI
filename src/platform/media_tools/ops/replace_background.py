from __future__ import annotations

import numpy as np
from PIL import Image

from src.platform.imaging.pixels import parse_hex_color
from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.layer_opacity import OPACITY_PARAM, read_opacity
from src.platform.media_tools.probe import has_alpha_mode
from src.platform.media_tools.types import EDITOR_SCOPE, InvalidToolParams, ToolMedia, ToolSpec, UnsupportedMedia

KINDS = ("color", "picture", "transparent")

SPEC = dict(
    id="replace_background",
    label="Replace background",
    icon="image",
    accepts=("image",),
    produces="same",
    cardinality="one_to_one",
    name_suffix="-background",
    description="Put a colour, a picture or transparency behind the subject, using a matte",
    scopes=(EDITOR_SCOPE,),
    params=[
        {
            "name": "kind",
            "type": "select",
            "label": "Background",
            "default": "color",
            "options": [
                {"label": "Color", "value": "color"},
                {"label": "Picture", "value": "picture"},
                {"label": "Transparent", "value": "transparent"},
            ],
        },
        {
            "name": "color",
            "type": "color",
            "label": "Color",
            "default": "#ffffff",
            "when": {"field": "kind", "equals": "color"},
        },
        {
            "name": "picture",
            "type": "media",
            "label": "Picture",
            "accepts": ["image"],
            "when": {"field": "kind", "equals": "picture"},
        },
        {
            "name": "matte",
            "type": "media",
            "label": "Matte",
            "description": "Grayscale mask, white where the background is",
            "accepts": ["image"],
        },
        OPACITY_PARAM,
    ],
)


def _open(media: object, what: str, mode: str) -> Image.Image:
    if not isinstance(media, ToolMedia):
        raise UnsupportedMedia(f"The {what} needs to be attached")
    with Image.open(media.path) as opened:
        return opened.convert(mode)


def cover_fit(picture: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    scale = max(width / picture.width, height / picture.height)
    scaled = (max(width, round(picture.width * scale)), max(height, round(picture.height * scale)))
    fitted = picture.resize(scaled, Image.Resampling.LANCZOS)
    left = (scaled[0] - width) // 2
    top = (scaled[1] - height) // 2
    return fitted.crop((left, top, left + width, top + height))


class ReplaceBackground(MediaTool):
    def check_params(self, params: dict) -> None:
        kind = str(params.get("kind") or "color")
        if kind not in KINDS:
            raise InvalidToolParams("kind", "Background must be color, picture or transparent")
        if kind == "color":
            try:
                parse_hex_color(str(params.get("color") or "#ffffff"))
            except ValueError:
                raise InvalidToolParams("color", "Color must be a hex colour such as #ffffff")

    def image(self, image: Image.Image, params: dict) -> Image.Image:
        self.check_params(params)
        kind = str(params.get("kind") or "color")
        base = image.convert("RGBA")
        width, height = base.size
        matte = _open(params.get("matte"), "matte", "L")
        if matte.size != base.size:
            matte = matte.resize(base.size, Image.Resampling.BILINEAR)
        weight = np.asarray(matte, dtype=np.float32) / 255.0 * read_opacity(params)
        pixels = np.asarray(base, dtype=np.float32)
        alpha = pixels[..., 3]
        if kind == "transparent":
            out = np.dstack([pixels[..., :3], alpha * (1.0 - weight)])
            return Image.fromarray(np.clip(np.rint(out), 0, 255).astype(np.uint8), "RGBA")
        if kind == "color":
            fill = np.empty((height, width, 3), dtype=np.float32)
            fill[:] = parse_hex_color(str(params.get("color") or "#ffffff"))
            cover = weight
        else:
            layer = np.asarray(
                cover_fit(_open(params.get("picture"), "background picture", "RGBA"), base.size), dtype=np.float32
            )
            fill = layer[..., :3]
            cover = weight * layer[..., 3] / 255.0
        rgb = pixels[..., :3] * (1.0 - cover[..., None]) + fill * cover[..., None]
        out_alpha = alpha + (255.0 - alpha) * cover if kind == "picture" else alpha
        result = Image.fromarray(np.clip(np.rint(np.dstack([rgb, out_alpha])), 0, 255).astype(np.uint8), "RGBA")
        if not has_alpha_mode(image):
            result = result.convert("RGB")
        icc = image.info.get("icc_profile")
        if icc:
            result.info["icc_profile"] = icc
        return result


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=ReplaceBackground())
