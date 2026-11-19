import math
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image, ImageFilter

from src.platform.imaging.regions import composite_outpaint, resample_image

MAX_SIDE = 8192
MAX_PIXELS = 36_000_000
MAX_GROWTH = 0.8
MIN_STEP = 64
OVERLAP_FLOOR = 24
OVERLAP_SHARE = 0.05
OVERLAP_CEIL = 96
BLEND_FLOOR = 8
BLEND_SHARE = 0.02
BLEND_CEIL = 32
NEW_AREA = 255

Rect = Tuple[int, int, int, int]


class ExpandError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Canvas:
    width: int
    height: int
    x: int
    y: int
    source_width: int
    source_height: int

    @property
    def known(self) -> Rect:
        return self.x, self.y, self.x + self.source_width, self.y + self.source_height

    def to_dict(self) -> Dict[str, int]:
        return {
            "width": self.width,
            "height": self.height,
            "x": self.x,
            "y": self.y,
            "source_width": self.source_width,
            "source_height": self.source_height,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Canvas":
        return cls(**{key: int(data[key]) for key in ("width", "height", "x", "y", "source_width", "source_height")})


@dataclass(frozen=True)
class Pass:
    window: Rect
    known: Rect

    def to_dict(self) -> Dict[str, List[int]]:
        return {"window": list(self.window), "known": list(self.known)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Pass":
        return cls(tuple(int(v) for v in data["window"]), tuple(int(v) for v in data["known"]))

    @property
    def size(self) -> Tuple[int, int]:
        left, top, right, bottom = self.window
        return right - left, bottom - top

    def known_local(self) -> Rect:
        left, top, _, _ = self.window
        k_left, k_top, k_right, k_bottom = self.known
        return k_left - left, k_top - top, k_right - left, k_bottom - top


def contain_size(source: Tuple[int, int], ratio: Tuple[float, float]) -> Tuple[int, int]:
    width, height = source
    wanted = ratio[0] / ratio[1]
    if width / height < wanted:
        return max(width, math.ceil(height * wanted)), height
    return width, max(height, math.ceil(width / wanted))


def make_canvas(source: Tuple[int, int], width: int, height: int, x: int, y: int) -> Canvas:
    source_width, source_height = source
    if width < source_width or height < source_height:
        raise ExpandError("expand_smaller", "The new canvas has to be at least as large as the picture")
    if width > MAX_SIDE or height > MAX_SIDE or width * height > MAX_PIXELS:
        raise ExpandError("expand_too_large", "The new canvas is too large")
    if width == source_width and height == source_height:
        raise ExpandError("expand_same", "The picture already has that size")
    return Canvas(
        width=width,
        height=height,
        x=min(max(0, x), width - source_width),
        y=min(max(0, y), height - source_height),
        source_width=source_width,
        source_height=source_height,
    )


def _grow_axis(low: int, high: int, limit: int, growth: float) -> Tuple[int, int]:
    need_low, need_high = low, limit - high
    cap = max(MIN_STEP, math.ceil(growth * (high - low)))
    if need_low + need_high <= cap:
        return 0, limit
    take_low = min(need_low, round(cap * need_low / (need_low + need_high)))
    take_high = min(need_high, cap - take_low)
    take_low = min(need_low, cap - take_high)
    return low - take_low, high + take_high


def plan_passes(canvas: Canvas, growth: float = MAX_GROWTH) -> List[Pass]:
    left, top, right, bottom = canvas.known
    passes: List[Pass] = []
    full = (0, 0, canvas.width, canvas.height)
    while (left, top, right, bottom) != full:
        grown_left, grown_right = _grow_axis(left, right, canvas.width, growth)
        grown_top, grown_bottom = _grow_axis(top, bottom, canvas.height, growth)
        window = (grown_left, grown_top, grown_right, grown_bottom)
        passes.append(Pass(window=window, known=(left, top, right, bottom)))
        left, top, right, bottom = window
    return passes


def overlap_for(window_size: Tuple[int, int]) -> int:
    return max(OVERLAP_FLOOR, min(OVERLAP_CEIL, int(round(OVERLAP_SHARE * min(window_size)))))


def blend_for(window_size: Tuple[int, int]) -> int:
    return max(BLEND_FLOOR, min(BLEND_CEIL, int(round(BLEND_SHARE * min(window_size)))))


def blank_canvas(source: np.ndarray, canvas: Canvas) -> np.ndarray:
    out = np.zeros((canvas.height, canvas.width, 3), dtype=np.uint8)
    left, top, right, bottom = canvas.known
    out[top:bottom, left:right] = source
    return out


def fill_unknown(window: np.ndarray, known: Rect) -> np.ndarray:
    left, top, right, bottom = known
    height, width = window.shape[:2]
    core = window[top:bottom, left:right]
    padded = np.pad(core, ((top, height - bottom), (left, width - right), (0, 0)), mode="edge")
    radius = max(height, width) / 24
    blurred = np.asarray(Image.fromarray(padded).filter(ImageFilter.GaussianBlur(radius))).astype(np.float32)
    import cv2

    reach = cv2.distanceTransform(new_area((height, width), known).astype(np.uint8), cv2.DIST_L2, 3)
    t = np.clip(reach / max(1.0, radius), 0.0, 1.0)
    take = (t * t * (3.0 - 2.0 * t))[:, :, None]
    out = np.rint(padded.astype(np.float32) + (blurred - padded.astype(np.float32)) * take).astype(np.uint8)
    out[top:bottom, left:right] = core
    return out


def new_area(shape: Tuple[int, int], known: Rect) -> np.ndarray:
    left, top, right, bottom = known
    region = np.ones(shape[:2], dtype=bool)
    region[top:bottom, left:right] = False
    return region


def soft_mask(shape: Tuple[int, int], known: Rect, overlap: int) -> np.ndarray:
    import cv2

    region = new_area(shape, known)
    distance = cv2.distanceTransform((~region).astype(np.uint8), cv2.DIST_L2, 3)
    t = np.clip(distance / max(1, overlap), 0.0, 1.0)
    ramp = 1.0 - t * t * (3.0 - 2.0 * t)
    return np.rint(np.where(region, 1.0, ramp) * 255).astype(np.uint8)


def pass_input(canvas_rgb: np.ndarray, step: Pass) -> np.ndarray:
    left, top, right, bottom = step.window
    return fill_unknown(canvas_rgb[top:bottom, left:right], step.known_local())


def stitch_pass(base: np.ndarray, content: Image.Image, step: Pass) -> np.ndarray:
    width, height = step.size
    fresh = resample_image(np.asarray(content.convert("RGB")), width, height)
    region = new_area(base.shape, step.known_local()).astype(np.uint8) * NEW_AREA
    return composite_outpaint(base, fresh, region, blend_for(step.size))


def place(canvas_rgb: np.ndarray, window_rgb: np.ndarray, step: Pass) -> None:
    left, top, right, bottom = step.window
    canvas_rgb[top:bottom, left:right] = window_rgb


def patch_of(canvas_rgb: np.ndarray, canvas: Canvas) -> np.ndarray:
    alpha = np.full(canvas_rgb.shape[:2], 255, dtype=np.uint8)
    left, top, right, bottom = canvas.known
    alpha[top:bottom, left:right] = 0
    return np.dstack([canvas_rgb, alpha])

