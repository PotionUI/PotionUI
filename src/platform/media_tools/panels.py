from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from PIL import Image

ANALYSIS_SIDE = 768
DEFAULT_TOLERANCE = 12
MAX_TOLERANCE = 64
CLEAN_SHARE = 0.985
ALPHA_FLOOR = 16
MIN_PANEL_AREA = 0.012
MAX_DEPTH = 6
OVERLAP_SHARE = 0.6
ROW_TOLERANCE = 0.04
GRID_SNAP_SHARE = 0.35
MAX_GRID_SIDE = 12

Box = tuple[int, int, int, int]


@dataclass
class Analysis:
    width: int
    height: int
    background: Optional[tuple[int, int, int]]
    is_bg: np.ndarray
    min_gutter: int


@dataclass
class Band:
    a: int
    b: int
    edge: bool


def _round(value: float) -> int:
    return int(np.floor(value + 0.5))


def analyse(image: Image.Image, tolerance: int = DEFAULT_TOLERANCE, max_side: int = ANALYSIS_SIDE) -> Analysis:
    rgba = image.convert("RGBA")
    scale = min(1.0, max_side / max(rgba.width, rgba.height))
    if scale < 1.0:
        rgba = rgba.resize(
            (max(1, _round(rgba.width * scale)), max(1, _round(rgba.height * scale))),
            Image.Resampling.BOX,
        )
    data = np.asarray(rgba, dtype=np.int16)
    height, width = data.shape[:2]
    edge = max(2, _round(min(width, height) * 0.01))
    ring = np.ones((height, width), dtype=bool)
    ring[edge : height - edge, edge : width - edge] = False
    samples = data[ring]
    transparent = int(np.sort(samples[:, 3])[len(samples) >> 1]) < ALPHA_FLOOR
    background = tuple(int(np.sort(samples[:, channel])[len(samples) >> 1]) for channel in range(3))
    is_bg = data[:, :, 3] < ALPHA_FLOOR
    if not transparent:
        difference = np.abs(data[:, :, :3] - np.array(background, dtype=np.int16)).max(axis=2)
        is_bg = is_bg | (difference <= int(tolerance))
    return Analysis(
        width, height, None if transparent else background, is_bg, max(3, _round(max(width, height) * 0.006))
    )


def bands(analysis: Analysis, axis: str, within: Box, clean: float = CLEAN_SHARE) -> list[Band]:
    x0, y0, x1, y1 = within
    region = analysis.is_bg[y0:y1, x0:x1]
    if region.size == 0:
        return []
    lo, hi = (x0, x1) if axis == "x" else (y0, y1)
    profile = region.mean(axis=0 if axis == "x" else 1)
    gutter = np.concatenate(([False], profile >= clean, [False]))
    changes = np.flatnonzero(gutter[1:] != gutter[:-1])
    found: list[Band] = []
    for start, stop in zip(changes[::2], changes[1::2]):
        a, b = lo + int(start), lo + int(stop)
        touches = a == lo or b == hi
        if b - a >= analysis.min_gutter or touches:
            found.append(Band(a, b, touches))
    return found


def content_box(analysis: Analysis, within: Box) -> Optional[Box]:
    x0, y0, x1, y1 = within
    content = ~analysis.is_bg[y0:y1, x0:x1]
    columns = np.flatnonzero(content.any(axis=0))
    if columns.size == 0:
        return None
    rows = np.flatnonzero(content.any(axis=1))
    return x0 + int(columns[0]), y0 + int(rows[0]), x0 + int(columns[-1]) + 1, y0 + int(rows[-1]) + 1


def _leaves(analysis: Analysis, within: Box, depth: int = 0) -> list[Box]:
    box = content_box(analysis, within)
    if box is None:
        return []
    area = (box[2] - box[0]) * (box[3] - box[1]) / (analysis.width * analysis.height)
    if depth > MAX_DEPTH or area < MIN_PANEL_AREA:
        return [box]
    for axis in ("y", "x"):
        inner = [band for band in bands(analysis, axis, box) if not band.edge]
        if not inner:
            continue
        lo, hi = (box[0], box[2]) if axis == "x" else (box[1], box[3])
        cuts = [lo, *[edge for band in inner for edge in (band.a, band.b)], hi]
        out: list[Box] = []
        for index in range(0, len(cuts), 2):
            piece = (
                (cuts[index], box[1], cuts[index + 1], box[3])
                if axis == "x"
                else (box[0], cuts[index], box[2], cuts[index + 1])
            )
            out.extend(_leaves(analysis, piece, depth + 1))
        return out
    return [box]


def _union(first: Box, second: Box) -> Box:
    return min(first[0], second[0]), min(first[1], second[1]), max(first[2], second[2]), max(first[3], second[3])


def _gap(first_lo: int, first_hi: int, second_lo: int, second_hi: int) -> int:
    return max(0, max(first_lo, second_lo) - min(first_hi, second_hi))


def _overlap_share(first_lo: int, first_hi: int, second_lo: int, second_hi: int) -> float:
    shared = min(first_hi, second_hi) - max(first_lo, second_lo)
    shortest = min(first_hi - first_lo, second_hi - second_lo)
    return shared / shortest if shortest > 0 and shared > 0 else 0.0


def _near(first: Box, second: Box, reach: int) -> bool:
    return (
        _gap(first[0], first[2], second[0], second[2]) <= reach
        and _gap(first[1], first[3], second[1], second[3]) <= reach
    )


def _attach_specks(analysis: Analysis, leaves: list[Box]) -> list[Box]:
    total = analysis.width * analysis.height
    def is_big(box: Box) -> bool:
        return (box[2] - box[0]) * (box[3] - box[1]) / total >= MIN_PANEL_AREA

    big = [box for box in leaves if is_big(box)]
    for box in [box for box in leaves if not is_big(box)]:
        reach = 2 * analysis.min_gutter
        hosts = [index for index, host in enumerate(big) if _near(box, host, reach)]
        if hosts:
            nearest = min(
                hosts,
                key=lambda index: _gap(box[0], box[2], big[index][0], big[index][2])
                + _gap(box[1], box[3], big[index][1], big[index][3]),
            )
            big[nearest] = _union(big[nearest], box)
    return big


def _merge_close(analysis: Analysis, leaves: list[Box]) -> list[Box]:
    boxes = list(leaves)
    merged = True
    while merged:
        merged = False
        for first in range(len(boxes)):
            for second in range(first + 1, len(boxes)):
                a, b = boxes[first], boxes[second]
                across_x = _gap(a[0], a[2], b[0], b[2]) < analysis.min_gutter
                across_y = _gap(a[1], a[3], b[1], b[3]) < analysis.min_gutter
                joined = (across_x and _overlap_share(a[1], a[3], b[1], b[3]) > OVERLAP_SHARE) or (
                    across_y and _overlap_share(a[0], a[2], b[0], b[2]) > OVERLAP_SHARE
                )
                if joined:
                    boxes[first] = _union(a, b)
                    del boxes[second]
                    merged = True
                    break
            if merged:
                break
    return boxes


def _reading_order(analysis: Analysis, boxes: list[Box]) -> list[Box]:
    rows: list[list[Box]] = []
    row_top = 0
    reach = ROW_TOLERANCE * analysis.height
    for box in sorted(boxes, key=lambda item: (item[1], item[0])):
        if not rows or box[1] - row_top > reach:
            rows.append([box])
            row_top = box[1]
        else:
            rows[-1].append(box)
    return [box for row in rows for box in sorted(row, key=lambda item: item[0])]


def find_panels(analysis: Analysis) -> list[Box]:
    leaves = _leaves(analysis, (0, 0, analysis.width, analysis.height))
    panels = _merge_close(analysis, _attach_specks(analysis, leaves))
    return _reading_order(analysis, panels)


def _lines(analysis: Analysis, axis: str, region: Box, count: int) -> list[tuple[int, int]]:
    lo, hi = (region[0], region[2]) if axis == "x" else (region[1], region[3])
    step = (hi - lo) / count
    inner = [band for band in bands(analysis, axis, region) if not band.edge]
    lines = []
    for index in range(1, count):
        position = lo + step * index
        near = [band for band in inner if abs((band.a + band.b) / 2 - position) <= GRID_SNAP_SHARE * step]
        if near:
            pick = min(near, key=lambda band: abs((band.a + band.b) / 2 - position))
            lines.append((pick.a, pick.b))
        else:
            cut = _round(position)
            lines.append((cut, cut))
    starts = [lo, *[line[1] for line in lines]]
    stops = [*[line[0] for line in lines], hi]
    return list(zip(starts, stops))


def grid_cells(analysis: Analysis, columns: int, rows: int) -> list[Box]:
    box = content_box(analysis, (0, 0, analysis.width, analysis.height))
    if box is None:
        return []
    cells: list[Box] = []
    for top, bottom in _lines(analysis, "y", box, rows):
        for left, right in _lines(analysis, "x", (box[0], top, box[2], bottom), columns):
            cells.append((left, top, right, bottom))
    return cells


def _fractions(analysis: Analysis, box: Box) -> dict:
    return {
        "x": box[0] / analysis.width,
        "y": box[1] / analysis.height,
        "w": (box[2] - box[0]) / analysis.width,
        "h": (box[3] - box[1]) / analysis.height,
    }


def _band_fractions(found: Sequence[Band], size: int) -> list[dict]:
    return [{"a": band.a / size, "b": band.b / size, "edge": band.edge} for band in found]


def detect(
    image: Image.Image,
    tolerance: int = DEFAULT_TOLERANCE,
    grid: Optional[tuple[int, int]] = None,
    source_size: Optional[tuple[int, int]] = None,
) -> dict:
    analysis = analyse(image, tolerance)
    width, height = source_size or image.size
    whole: Box = (0, 0, analysis.width, analysis.height)
    result = {
        "width": width,
        "height": height,
        "background": None if analysis.background is None else "#%02x%02x%02x" % analysis.background,
        "gutters": {
            "x": _band_fractions(bands(analysis, "x", whole), analysis.width),
            "y": _band_fractions(bands(analysis, "y", whole), analysis.height),
        },
        "panels": [_fractions(analysis, box) for box in find_panels(analysis)],
    }
    if grid is not None:
        result["grid_cells"] = [_fractions(analysis, box) for box in grid_cells(analysis, *grid)]
    return result
