from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageFilter


ANCHORS = ("center", "left", "right", "top", "bottom")
FILLS = ("blur", "edge", "gray")
MAX_RATIO = 8.0
MAX_GROW = 8192


def pixel_margins(left: int, top: int, right: int, bottom: int) -> tuple[int, int, int, int]:
    margins = tuple(max(0, int(v)) for v in (left, top, right, bottom))
    if not any(margins):
        raise ValueError("Expand at least one side of the picture")
    return margins


def parse_aspect(aspect: str) -> float:
    try:
        w, h = (float(part) for part in str(aspect).split(":"))
    except ValueError as error:
        raise ValueError(f"Unknown aspect ratio {aspect!r}; use W:H such as 16:9") from error
    if w <= 0 or h <= 0 or not 1 / MAX_RATIO <= w / h <= MAX_RATIO:
        raise ValueError(f"Unknown aspect ratio {aspect!r}; use W:H such as 16:9")
    return w / h


def aspect_margins(width: int, height: int, aspect: str, anchor: str = "center") -> tuple[int, int, int, int]:
    if anchor not in ANCHORS:
        raise ValueError(f"Unknown anchor {anchor!r}")
    target = parse_aspect(aspect)
    if width / height < target:
        grow, horizontal = round(height * target) - width, True
    else:
        grow, horizontal = round(width / target) - height, False
    grow = min(max(0, grow), MAX_GROW)
    if not grow:
        raise ValueError("The picture already has that shape")
    along = "left" if horizontal else "top"
    other_end = "right" if horizontal else "bottom"
    near = 0 if anchor == along else grow if anchor == other_end else grow // 2
    far = grow - near
    return (near, 0, far, 0) if horizontal else (0, near, 0, far)


def build_canvas(image: Image.Image, margins: tuple[int, int, int, int], fill: str = "blur") -> Image.Image:
    if fill not in FILLS:
        raise ValueError(f"Unknown fill {fill!r}")
    left, top, right, bottom = margins
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    if fill == "gray":
        canvas = np.full((height + top + bottom, width + left + right, 3), 127, dtype=np.uint8)
    else:
        canvas = np.pad(rgb, ((top, bottom), (left, right), (0, 0)), mode="edge")
        if fill == "blur":
            radius = max(canvas.shape[:2]) / 24
            canvas = np.asarray(Image.fromarray(canvas).filter(ImageFilter.GaussianBlur(radius)))
        canvas = np.array(canvas)
    canvas[top:top + height, left:left + width] = rgb
    return Image.fromarray(canvas)


def build_region_mask(size: tuple[int, int], margins: tuple[int, int, int, int]) -> Image.Image:
    width, height = size
    left, top, right, bottom = margins
    mask = np.full((height + top + bottom, width + left + right), 255, dtype=np.uint8)
    mask[top:top + height, left:left + width] = 0
    return Image.fromarray(mask)


def build_mask(size: tuple[int, int], margins: tuple[int, int, int, int], overlap: int = 48) -> Image.Image:
    region = np.asarray(build_region_mask(size, margins)) > 0
    overlap = max(0, int(overlap))
    if not overlap or region.all():
        return Image.fromarray(region.astype(np.uint8) * 255)
    into_source = cv2.distanceTransform((~region).astype(np.uint8), cv2.DIST_L2, 3)
    t = np.clip(into_source / overlap, 0.0, 1.0)
    ramp = 1.0 - t * t * (3.0 - 2.0 * t)
    return Image.fromarray(np.rint(np.where(region, 1.0, ramp) * 255).astype(np.uint8))
