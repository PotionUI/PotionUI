from __future__ import annotations

from PIL import Image

from src.platform.media_tools.types import UnsupportedMedia

OPACITY_PARAM = {"name": "opacity", "type": "float", "label": "Opacity", "default": 1, "min": 0, "max": 1, "step": 0.01}


def read_opacity(params: dict) -> float:
    try:
        value = float(params.get("opacity", 1))
    except (TypeError, ValueError):
        raise UnsupportedMedia("Opacity must be a number between 0 and 1")
    if value != value:
        raise UnsupportedMedia("Opacity must be a number between 0 and 1")
    return min(1.0, max(0.0, value))


def with_opacity(layer: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 1:
        return layer
    red, green, blue, alpha = layer.convert("RGBA").split()
    alpha = alpha.point([round(level * opacity) for level in range(256)])
    return Image.merge("RGBA", (red, green, blue, alpha))
