from __future__ import annotations

from typing import List, Sequence, Tuple

from PIL import Image

from src.pipelines.pipes._shared.imaging.fit import PAD_FILL, pad_layout

SHEET_FILL = PAD_FILL
MAX_PANELS = 6

Size = Tuple[int, int]
Box = Tuple[int, int, int, int]

_GRIDS = {1: (1, 1), 2: (2, 1), 3: (2, 2), 4: (2, 2), 5: (3, 2), 6: (3, 2)}


def sheet_grid(count: int) -> Tuple[int, int]:
    if count not in _GRIDS:
        raise ValueError(f"A reference sheet holds 1 to {MAX_PANELS} pictures, not {count}")
    return _GRIDS[count]


def sheet_spacing(size: Size) -> int:
    return max(2, round(min(size) / 64))


def _split(total: int, parts: int) -> List[int]:
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def panel_boxes(count: int, size: Size) -> List[Box]:
    cols, rows = sheet_grid(count)
    width, height = int(size[0]), int(size[1])
    spacing = sheet_spacing((width, height))
    widths = _split(width - 2 * spacing - (cols - 1) * spacing, cols)
    heights = _split(height - 2 * spacing - (rows - 1) * spacing, rows)
    boxes: List[Box] = []
    top = spacing
    for row in range(rows):
        left = spacing
        for col in range(cols):
            if len(boxes) < count:
                boxes.append((left, top, widths[col], heights[row]))
            left += widths[col] + spacing
        top += heights[row] + spacing
    return boxes


def _paste_contained(canvas: Image.Image, image: Image.Image, box: Box) -> None:
    left, top, width, height = box
    source = image.convert("RGBA")
    dx, dy, nw, nh = pad_layout(source.size, (width, height))
    inner = source if source.size == (nw, nh) else source.resize((nw, nh), Image.LANCZOS)
    canvas.paste(inner, (left + dx, top + dy), inner)


def compose_sheet(images: Sequence[Image.Image], size: Size) -> Image.Image:
    size = (int(size[0]), int(size[1]))
    canvas = Image.new("RGB", size, SHEET_FILL)
    for image, box in zip(images, panel_boxes(len(images), size)):
        _paste_contained(canvas, image, box)
    return canvas


def fit_sheet(image: Image.Image, size: Size) -> Image.Image:
    size = (int(size[0]), int(size[1]))
    canvas = Image.new("RGB", size, SHEET_FILL)
    _paste_contained(canvas, image, (0, 0, size[0], size[1]))
    return canvas
