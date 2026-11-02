from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from PIL import Image

from src.platform.media_tools.base import MediaTool
from src.platform.media_tools.fastpath import prepare_image, save_image
from src.platform.media_tools.probe import has_alpha_mode
from src.platform.media_tools.types import (
    EDITOR_SCOPE,
    InvalidToolParams,
    ToolCancelled,
    ToolContext,
    ToolFailed,
    ToolMedia,
    ToolSpec,
)

MAX_SLICE_CELLS = 64
MIN_CELL_PIXELS = 8
MAX_NAME_LENGTH = 120
MAX_CELL_OUTPUT_PIXELS = 40_000_000
MAX_TOTAL_OUTPUT_PIXELS = 160_000_000
MAX_SOURCE_PIXELS = 100_000_000
ALPHA_FLOOR = 16
RING_SHARE = 0.01
FRACTION_SLACK = 1e-6
PORTRAIT_RATIO = (2, 3)
ASPECTS = ("keep", "square", "portrait")
AUTO = "auto"
TRANSPARENT = "transparent"

_FILL_RE = re.compile(r"^(auto|transparent|#[0-9a-fA-F]{6})$")
_NAME_STRIP_RE = re.compile(r"[\x00-\x1f\x7f/\\]+")

SPEC = dict(
    id="slice",
    label="Slice",
    icon="editor-slice",
    accepts=("image",),
    produces="image",
    cardinality="one_to_many",
    editor="slice",
    name_suffix="-cell",
    description="Cut a sheet into separate images. The last step of a stack",
    selection={"min": 1, "max": 1},
    scopes=(EDITOR_SCOPE,),
    params=[
        {
            "name": "rects",
            "type": "json",
            "label": "Cells",
            "description": f"Up to {MAX_SLICE_CELLS} cells [{{x, y, w, h, name?}}] as 0..1 fractions, in output order",
        },
        {"name": "trim", "type": "checkbox", "label": "Trim background", "default": False},
        {
            "name": "tolerance",
            "type": "integer",
            "label": "Tolerance",
            "default": 12,
            "min": 0,
            "max": 64,
            "when": {"field": "trim", "equals": True},
        },
        {"name": "padding", "type": "integer", "label": "Padding", "default": 0, "min": 0, "max": 512, "unit": "px"},
        {
            "name": "aspect",
            "type": "select",
            "label": "Aspect",
            "default": "keep",
            "options": [{"label": "Keep", "value": "keep"}, {"label": "Square", "value": "square"}, {"label": "Portrait 2:3", "value": "portrait"}],
        },
        {
            "name": "fill",
            "type": "string",
            "label": "Fill",
            "default": AUTO,
            "description": "auto, transparent or #RRGGBB",
        },
    ],
)


@dataclass(frozen=True)
class Cell:
    x: float
    y: float
    w: float
    h: float
    name: Optional[str] = None

    def rect(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def _finite(entry: dict, key: str, index: int) -> float:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidToolParams("rects", f"Cell {index + 1}: {key} must be a number")
    return float(value)


def _clean_name(value: Any, index: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidToolParams("rects", f"Cell {index + 1}: name must be text")
    cleaned = _NAME_STRIP_RE.sub("", value).strip()
    if len(cleaned) > MAX_NAME_LENGTH:
        raise InvalidToolParams("rects", f"Cell {index + 1}: name must be at most {MAX_NAME_LENGTH} characters")
    return cleaned or None


def parse_cells(rects: Any) -> list[Cell]:
    if not isinstance(rects, list) or not 1 <= len(rects) <= MAX_SLICE_CELLS:
        raise InvalidToolParams("rects", f"Slice needs between 1 and {MAX_SLICE_CELLS} cells")
    cells: list[Cell] = []
    for index, entry in enumerate(rects):
        if not isinstance(entry, dict):
            raise InvalidToolParams("rects", f"Cell {index + 1} must be an object with x, y, w and h")
        x, y, w, h = (_finite(entry, key, index) for key in ("x", "y", "w", "h"))
        if w <= 0 or h <= 0:
            raise InvalidToolParams("rects", f"Cell {index + 1} has no area")
        if x < 0 or y < 0 or x + w > 1 + FRACTION_SLACK or y + h > 1 + FRACTION_SLACK:
            raise InvalidToolParams("rects", f"Cell {index + 1} reaches outside the picture")
        cells.append(Cell(x, y, w, h, _clean_name(entry.get("name"), index)))
    return cells


def cell_names(params: dict) -> list[Optional[str]]:
    return [cell.name for cell in parse_cells(params["rects"])]


def pixel_box(cell: Cell, width: int, height: int) -> tuple[int, int, int, int]:
    left = max(0, min(width, int(round(cell.x * width))))
    top = max(0, min(height, int(round(cell.y * height))))
    right = max(0, min(width, int(round((cell.x + cell.w) * width))))
    bottom = max(0, min(height, int(round((cell.y + cell.h) * height))))
    return left, top, right, bottom


def check_geometry(cells: list[Cell], width: int, height: int, params: dict) -> list[tuple[int, int, int, int]]:
    boxes = [pixel_box(cell, width, height) for cell in cells]
    padding = int(params.get("padding") or 0)
    aspect = params.get("aspect") or "keep"
    total = 0
    for index, (left, top, right, bottom) in enumerate(boxes):
        if right - left < MIN_CELL_PIXELS or bottom - top < MIN_CELL_PIXELS:
            raise InvalidToolParams("rects", f"Cell {index + 1} is smaller than {MIN_CELL_PIXELS} px")
        out_w, out_h = _padded_size(right - left, bottom - top, padding, aspect)
        if out_w * out_h > MAX_CELL_OUTPUT_PIXELS:
            raise InvalidToolParams("rects", f"Cell {index + 1} would be larger than {MAX_CELL_OUTPUT_PIXELS // 1_000_000} megapixels")
        total += out_w * out_h
    if total > MAX_TOTAL_OUTPUT_PIXELS:
        raise InvalidToolParams("rects", f"The cells together would be larger than {MAX_TOTAL_OUTPUT_PIXELS // 1_000_000} megapixels")
    return boxes


def _padded_size(width: int, height: int, padding: int, aspect: str) -> tuple[int, int]:
    width, height = width + 2 * padding, height + 2 * padding
    if aspect == "square":
        side = max(width, height)
        return side, side
    if aspect == "portrait":
        num, den = PORTRAIT_RATIO
        if width * den > height * num:
            return width, math.ceil(width * den / num)
        return math.ceil(height * num / den), height
    return width, height


def _sheet_colour(rgba: np.ndarray) -> tuple[int, int, int, int]:
    height, width = rgba.shape[:2]
    edge = max(1, int(round(min(width, height) * RING_SHARE)))
    ring = np.ones((height, width), dtype=bool)
    ring[edge : height - edge, edge : width - edge] = False
    samples = rgba[ring]
    return tuple(int(np.sort(samples[:, channel])[len(samples) >> 1]) for channel in range(4))


def _trim_box(block: np.ndarray, sheet: tuple[int, int, int, int], tolerance: int, use_alpha: bool):
    if use_alpha:
        content = block[:, :, 3] > ALPHA_FLOOR
    else:
        reference = np.array(sheet[:3], dtype=np.int16)
        content = np.abs(block[:, :, :3].astype(np.int16) - reference).max(axis=2) > tolerance
    columns = np.flatnonzero(content.any(axis=0))
    if columns.size == 0:
        return None
    rows = np.flatnonzero(content.any(axis=1))
    return int(columns[0]), int(rows[0]), int(columns[-1]) + 1, int(rows[-1]) + 1


def _fill_colour(fill: str, sheet: tuple[int, int, int, int], use_alpha: bool) -> tuple[int, int, int, int]:
    if fill == TRANSPARENT or (fill == AUTO and use_alpha):
        return 0, 0, 0, 0
    if fill == AUTO:
        return sheet[0], sheet[1], sheet[2], 255
    return int(fill[1:3], 16), int(fill[3:5], 16), int(fill[5:7], 16), 255


class Slice(MediaTool):
    def check_params(self, params: dict) -> None:
        parse_cells(params.get("rects"))
        fill = params.get("fill")
        if fill is not None and not _FILL_RE.match(str(fill)):
            raise InvalidToolParams("fill", "Fill must be auto, transparent or a #RRGGBB colour")

    def still(self, image, params: dict, frame):
        return image

    def run(self, inputs: list[ToolMedia], params: dict, ctx: ToolContext) -> list[ToolMedia]:
        outputs: list[ToolMedia] = []
        for media in inputs:
            outputs.extend(self._slice(media, params, ctx))
        return outputs

    def _slice(self, media: ToolMedia, params: dict, ctx: ToolContext) -> list[ToolMedia]:
        cells = parse_cells(params["rects"])
        if media.width and media.height and media.width * media.height > MAX_SOURCE_PIXELS:
            raise ToolFailed("The picture is too large to slice")
        source = prepare_image(media.path)
        if source.width * source.height > MAX_SOURCE_PIXELS:
            raise ToolFailed("The picture is too large to slice")
        boxes = check_geometry(cells, source.width, source.height, params)
        rgba = np.asarray(source.convert("RGBA"))
        use_alpha = has_alpha_mode(source) and int(rgba[:, :, 3].min()) < 255
        sheet = _sheet_colour(rgba)
        fill = _fill_colour(str(params.get("fill") or AUTO), sheet, use_alpha)
        keeps_alpha = use_alpha or fill[3] == 0
        tolerance = int(params.get("tolerance") or 0)
        trim = bool(params.get("trim"))
        padding = int(params.get("padding") or 0)
        aspect = params.get("aspect") or "keep"
        total = len(boxes)
        written: list[ToolMedia] = []
        try:
            for index, (left, top, right, bottom) in enumerate(boxes):
                if ctx.is_cancelled():
                    raise ToolCancelled()
                step = ctx.scoped(index / total, (index + 1) / total)
                step.progress(0.0, f"Cell {index + 1} of {total}")
                block = rgba[top:bottom, left:right]
                if trim:
                    found = _trim_box(block, sheet, tolerance, use_alpha)
                    if found is not None:
                        block = block[found[1] : found[3], found[0] : found[2]]
                canvas_w, canvas_h = _padded_size(block.shape[1], block.shape[0], padding, aspect)
                canvas = np.empty((canvas_h, canvas_w, 4), dtype=np.uint8)
                canvas[:] = fill
                offset_x = (canvas_w - block.shape[1]) // 2
                offset_y = (canvas_h - block.shape[0]) // 2
                canvas[offset_y : offset_y + block.shape[0], offset_x : offset_x + block.shape[1]] = block
                image = Image.fromarray(canvas, "RGBA")
                if not keeps_alpha:
                    image = image.convert("RGB")
                dest = ctx.new_path(".png", stem=media.path.stem[:40] or "out")
                path = save_image(image, dest)
                written.append(ctx.probe(path, "image"))
                step.progress(1.0, f"Cell {index + 1} of {total}")
        except BaseException:
            for part in written:
                part.path.unlink(missing_ok=True)
            raise
        return written


def spec() -> ToolSpec:
    return ToolSpec(**SPEC, impl=Slice())
