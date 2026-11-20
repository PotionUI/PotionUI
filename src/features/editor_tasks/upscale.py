import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from src.platform.http.errors import DomainError

MAX_OUTPUT_SIDE = 16384
MAX_OUTPUT_PIXELS = MAX_OUTPUT_SIDE * MAX_OUTPUT_SIDE // 4
ALIGN = 8
MIN_SIDE = 64
OVERLAP_SHARE = 0.125
OVERLAP_MIN = 16
OVERLAP_MAX = 128
MIN_FACTOR = 1.0

Box = Tuple[int, int, int, int]


@dataclass(frozen=True)
class Tile:
    index: int
    column: int
    row: int
    source: Box
    output: Box
    feather_left: int
    feather_top: int

    @property
    def source_size(self) -> Tuple[int, int]:
        top, bottom, left, right = self.source
        return right - left, bottom - top

    @property
    def output_size(self) -> Tuple[int, int]:
        top, bottom, left, right = self.output
        return right - left, bottom - top

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "column": self.column,
            "row": self.row,
            "source": list(self.source),
            "output": list(self.output),
            "feather_left": self.feather_left,
            "feather_top": self.feather_top,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Tile":
        return cls(
            index=int(data["index"]),
            column=int(data["column"]),
            row=int(data["row"]),
            source=tuple(int(value) for value in data["source"]),
            output=tuple(int(value) for value in data["output"]),
            feather_left=int(data["feather_left"]),
            feather_top=int(data["feather_top"]),
        )


@dataclass(frozen=True)
class TilePlan:
    source: Tuple[int, int]
    output: Tuple[int, int]
    scale: float
    tiles: Tuple[Tile, ...]


def output_size(
    size: Tuple[int, int], scale: Optional[float] = None, target: Optional[Tuple[int, int]] = None
) -> Tuple[Tuple[int, int], float]:
    width, height = size
    if scale is None and target is None:
        raise DomainError("upscale_factor_required", "Choose how much larger the picture should become", 422)
    if scale is not None and target is not None:
        raise DomainError("upscale_factor_ambiguous", "Send either a scale or a target size, not both", 422)
    factor = float(scale) if scale is not None else min(target[0] / width, target[1] / height)
    out_w, out_h = max(1, round(width * factor)), max(1, round(height * factor))
    if factor <= MIN_FACTOR or (out_w <= width and out_h <= height):
        raise DomainError("upscale_not_larger", "The result would not be larger than the picture", 422)
    if max(out_w, out_h) > MAX_OUTPUT_SIDE or out_w * out_h > MAX_OUTPUT_PIXELS:
        raise DomainError(
            "upscale_too_large",
            f"The result would be {out_w} x {out_h}; the limit is {MAX_OUTPUT_SIDE} px on a side "
            f"and {MAX_OUTPUT_PIXELS // 1_000_000} megapixels",
            422,
        )
    return (out_w, out_h), max(out_w / width, out_h / height)


def _aligned(value: int, limit: int) -> int:
    return min(limit, int(math.ceil(value / ALIGN)) * ALIGN)


def _axis(length: int, side: int, overlap: int) -> List[Tuple[int, int]]:
    if length <= side:
        return [(0, length)]
    count = int(math.ceil((length - overlap) / (side - overlap)))
    span = _aligned(int(math.ceil((length + (count - 1) * overlap) / count)), length)
    step = (length - span) / (count - 1)
    starts = [round(index * step) for index in range(count - 1)] + [length - span]
    return [(start, start + span) for start in starts]


def _sides(width: int, height: int, area: int) -> Tuple[int, int]:
    side = max(MIN_SIDE, math.isqrt(max(1, area)))
    side_x = side_y = side
    if height <= side_y < width:
        side_x = max(side_x, area // height)
    elif width <= side_x < height:
        side_y = max(side_y, area // width)
    return side_x, side_y


def _overlap_for(side_x: int, side_y: int, overlap: Optional[int]) -> int:
    if overlap is None:
        overlap = min(OVERLAP_MAX, max(OVERLAP_MIN, round(OVERLAP_SHARE * min(side_x, side_y))))
    return max(0, min(int(overlap), min(side_x, side_y) // 2))


def _scaled(value: int, length: int, factor_length: int) -> int:
    if value >= length:
        return factor_length
    return round(value * factor_length / length)


def plan_tiles(
    size: Tuple[int, int],
    scale: Optional[float] = None,
    tile_area: int = 1048576,
    overlap: Optional[int] = None,
    output: Optional[Tuple[int, int]] = None,
) -> TilePlan:
    width, height = size
    if output is None:
        output, factor = output_size(size, scale)
    else:
        factor = max(output[0] / width, output[1] / height)
    out_w, out_h = output
    side_x, side_y = _sides(width, height, tile_area)
    gap = _overlap_for(side_x, side_y, overlap)
    columns = _axis(width, side_x, gap)
    rows = _axis(height, side_y, gap)
    out_columns = [(_scaled(left, width, out_w), _scaled(right, width, out_w)) for left, right in columns]
    out_rows = [(_scaled(top, height, out_h), _scaled(bottom, height, out_h)) for top, bottom in rows]
    tiles: List[Tile] = []
    for row, (top, bottom) in enumerate(rows):
        for column, (left, right) in enumerate(columns):
            out_left, out_right = out_columns[column]
            out_top, out_bottom = out_rows[row]
            feather_left = max(0, out_columns[column - 1][1] - out_left) if column else 0
            feather_top = max(0, out_rows[row - 1][1] - out_top) if row else 0
            tiles.append(
                Tile(
                    index=len(tiles),
                    column=column,
                    row=row,
                    source=(top, bottom, left, right),
                    output=(out_top, out_bottom, out_left, out_right),
                    feather_left=feather_left,
                    feather_top=feather_top,
                )
            )
    return TilePlan(source=(width, height), output=(out_w, out_h), scale=factor, tiles=tuple(tiles))


def _ramp(length: int, feather: int) -> np.ndarray:
    ramp = np.ones(length, dtype=np.float32)
    feather = min(feather, length)
    if feather > 0:
        ramp[:feather] = (np.arange(feather, dtype=np.float32) + 0.5) / feather
    return ramp


class Canvas:
    def __init__(self, size: Tuple[int, int]):
        self.size = size
        self.pixels = np.zeros((size[1], size[0], 3), dtype=np.uint8)

    def place(self, tile: Tile, image: Image.Image) -> None:
        top, bottom, left, right = tile.output
        width, height = right - left, bottom - top
        rgb = image.convert("RGB")
        if rgb.size != (width, height):
            rgb = rgb.resize((width, height), Image.Resampling.LANCZOS)
        fresh = np.asarray(rgb)
        target = self.pixels[top:bottom, left:right]
        if tile.feather_left == 0 and tile.feather_top == 0:
            target[...] = fresh
            return
        alpha = _ramp(height, tile.feather_top)[:, None] * _ramp(width, tile.feather_left)[None, :]
        blended = target.astype(np.float32) * (1.0 - alpha[:, :, None]) + fresh.astype(np.float32) * alpha[:, :, None]
        target[...] = np.clip(np.rint(blended), 0, 255).astype(np.uint8)

    def rgba(self) -> np.ndarray:
        height, width = self.pixels.shape[:2]
        out = np.empty((height, width, 4), dtype=np.uint8)
        out[..., :3] = self.pixels
        out[..., 3] = 255
        return out


def compose(plan: TilePlan, results: Sequence[Image.Image]) -> np.ndarray:
    if len(results) != len(plan.tiles):
        raise ValueError("every tile needs a result")
    canvas = Canvas(plan.output)
    for tile, image in zip(plan.tiles, results):
        canvas.place(tile, image)
    return canvas.rgba()
