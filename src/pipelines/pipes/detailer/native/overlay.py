from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SIGNAL = (91, 157, 255)
SIGNAL_SHADOW = (25, 43, 71)
SKIPPED_GREY = (154, 154, 154)
BACKDROP_ALPHA = 179
CONTOUR_ALPHA = 153
LIGHT_ALPHA = 190
DIM_STRENGTH = 0.25
BRACKET_FRACTION = 0.18
FOCUS_BRACKET_WIDTH = 3
LIGHT_BRACKET_WIDTH = 2
DASH_PERIOD = 6
LABEL_SCALE = 0.022
LABEL_MIN_SIZE = 12
TAG_MAX_SIZE = 14
PILL_GAP = 4
MISSING_SCORE = "-"
SEPARATOR = " · "


@dataclass
class OverlayFace:
    box: Tuple[int, int, int, int]
    mask: Optional[Image.Image] = None
    label: str = ""
    tag: str = ""
    skipped: bool = False
    done: bool = False
    focus: bool = False


def face_label(index: int, confidence: Optional[float], size: Tuple[int, int], mask_mode: str) -> str:
    score = f"{confidence:.2f}" if confidence is not None else MISSING_SCORE
    return f"FACE {index} · {score} · {size[0]}x{size[1]} · {mask_mode.upper()}"


def step_suffix(label: str, step: int, total: int) -> str:
    if total <= 0:
        return label
    return f"{label} · step {step}/{total}"


def wrap_label(label: str) -> List[str]:
    parts = label.split(SEPARATOR)
    if len(parts) < 2:
        return [label]
    split = (len(parts) + 1) // 2
    return [SEPARATOR.join(parts[:split]), SEPARATOR.join(parts[split:])]


def _overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _within(rect, size) -> bool:
    return rect[0] >= 0 and rect[1] >= 0 and rect[2] <= size[0] and rect[3] <= size[1]


def place_label(image_size, box, pill_size, obstacles=(), gap: int = PILL_GAP):
    width, height = image_size
    pill_w, pill_h = pill_size
    x1, y1, x2, y2 = (int(v) for v in box)
    aligned_x = min(max(0, x1), max(0, width - pill_w))
    aligned_y = min(max(0, y1), max(0, height - pill_h))
    candidates = (
        (aligned_x, y1 - pill_h - gap),
        (aligned_x, y2 + gap),
        (x2 + gap, aligned_y),
        (x1 - pill_w - gap, aligned_y),
    )
    for x, y in candidates:
        rect = (x, y, x + pill_w, y + pill_h)
        if not _within(rect, (width, height)):
            continue
        if any(_overlaps(rect, tuple(int(v) for v in o)) for o in obstacles):
            continue
        return x, y
    return None


def _font(image_size: Tuple[int, int], cap: Optional[int] = None) -> ImageFont.ImageFont:
    size = max(LABEL_MIN_SIZE, int(round(min(image_size) * LABEL_SCALE)))
    if cap is not None:
        size = min(size, cap)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _focused(faces: Sequence[OverlayFace]) -> List[OverlayFace]:
    return [f for f in faces if f.focus and f.mask is not None]


def _union_mask(size, faces: Sequence[OverlayFace], transform=None) -> Image.Image:
    union = Image.new("L", size, 0)
    for face in faces:
        source = face.mask if transform is None else transform(face.mask)
        patch = Image.new("L", size, 0)
        patch.paste(source, (face.box[0], face.box[1]))
        union = ImageChops.lighter(union, patch)
    return union


def _edges(mask: Image.Image) -> Image.Image:
    hard = mask.point(lambda v: 255 if v > 127 else 0)
    return hard.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3))


def _dashed(layer: Image.Image, alpha: int) -> Image.Image:
    arr = np.asarray(layer, dtype=np.uint8).astype(np.float32)
    rows, cols = np.indices(arr.shape)
    arr[((cols + rows) // DASH_PERIOD) % 2 == 1] = 0.0
    return Image.fromarray((arr * (alpha / 255.0)).astype(np.uint8), mode="L")


def _dim_outside(base: Image.Image, lit: Image.Image, strength: float) -> Image.Image:
    if strength <= 0.0:
        return base
    dark = Image.blend(base, Image.new("RGB", base.size, (0, 0, 0)), strength)
    return Image.composite(base, dark, lit)


def _bracket_segments(box, inset: int):
    x1, y1, x2, y2 = (int(v) for v in box)
    x1, y1, x2, y2 = x1 + inset, y1 + inset, x2 - inset, y2 - inset
    if x2 <= x1 or y2 <= y1:
        return []
    length = max(4, int(round(min(x2 - x1, y2 - y1) * BRACKET_FRACTION)))
    segments = []
    for cx, cy, dx, dy in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
        segments.append(((cx, cy), (cx + dx * length, cy)))
        segments.append(((cx, cy), (cx, cy + dy * length)))
    return segments


def _draw_focus_brackets(draw, box) -> None:
    segments = _bracket_segments(box, FOCUS_BRACKET_WIDTH)
    for width, fill in ((FOCUS_BRACKET_WIDTH + 2, SIGNAL_SHADOW + (255,)),
                        (FOCUS_BRACKET_WIDTH, SIGNAL + (255,))):
        for start, end in segments:
            draw.line([start, end], fill=fill, width=width)


def _draw_light_brackets(draw, box, colour) -> None:
    for start, end in _bracket_segments(box, LIGHT_BRACKET_WIDTH):
        draw.line([start, end], fill=colour + (LIGHT_ALPHA,), width=LIGHT_BRACKET_WIDTH)


def _text_size(draw, text, font):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top, left, top


def _draw_pill(draw, origin, pill_size) -> None:
    x, y = origin
    draw.rounded_rectangle(
        [x, y, x + pill_size[0], y + pill_size[1]],
        radius=max(2, pill_size[1] // 3), fill=(0, 0, 0, BACKDROP_ALPHA),
    )


def _draw_tag(draw, box, text, colour, font) -> None:
    text_w, text_h, left, top = _text_size(draw, text, font)
    pad = max(2, text_h // 3)
    pill_size = (text_w + pad * 2, text_h + pad * 2)
    origin = (int(box[0]) + 2, int(box[1]) + 2)
    _draw_pill(draw, origin, pill_size)
    draw.text((origin[0] + pad - left, origin[1] + pad - top), text, font=font, fill=colour + (255,))


def _draw_check_tag(draw, box, font) -> None:
    unit = max(8, _text_size(draw, "0", font)[1])
    pill_size = (int(unit * 1.8), int(unit * 1.6))
    origin = (int(box[0]) + 2, int(box[1]) + 2)
    _draw_pill(draw, origin, pill_size)
    x, y = origin
    draw.line(
        [(x + unit * 0.45, y + unit * 0.8), (x + unit * 0.75, y + unit * 1.15),
         (x + unit * 1.3, y + unit * 0.45)],
        fill=SIGNAL + (255,), width=max(2, unit // 5), joint="curve",
    )


def _draw_focus_label(draw, image_size, font, face: OverlayFace, obstacles) -> None:
    lines = [face.label]
    text_w, text_h, left, top = _text_size(draw, face.label, font)
    pad_x = max(4, text_h // 2)
    pad_y = max(2, text_h // 3)
    pill_size = (text_w + pad_x * 2, text_h + pad_y * 2)
    origin = place_label(image_size, face.box, pill_size, obstacles)

    if origin is None:
        lines = wrap_label(face.label)
        widths = [_text_size(draw, line, font) for line in lines]
        text_w = max(w for w, _, _, _ in widths)
        line_h = max(h for _, h, _, _ in widths)
        pill_size = (text_w + pad_x * 2, line_h * len(lines) + pad_y * (len(lines) + 1))
        origin = place_label(image_size, face.box, pill_size, obstacles)

    if origin is None:
        origin = (min(max(0, int(face.box[0])), max(0, image_size[0] - pill_size[0])),
                  max(0, int(face.box[1]) - pill_size[1] - PILL_GAP))

    _draw_pill(draw, origin, pill_size)
    colour = SKIPPED_GREY if face.skipped else SIGNAL
    y = origin[1] + pad_y
    for line in lines:
        width, height, left, top = _text_size(draw, line, font)
        draw.text((origin[0] + pad_x - left, y - top), line, font=font, fill=colour + (255,))
        y += height + pad_y


def render_overlay(base: Image.Image, faces: Sequence[OverlayFace], *,
                   dim: float = DIM_STRENGTH, protect: Sequence[Sequence[int]] = ()) -> Image.Image:
    focused = _focused(faces)
    frame = base.convert("RGB")
    if focused:
        lit = _union_mask(frame.size, [f for f in faces if f.mask is not None and not f.skipped])
        frame = _dim_outside(frame, lit, dim)

    layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    if focused:
        contour = _dashed(_union_mask(frame.size, focused, transform=_edges), CONTOUR_ALPHA)
        layer.paste(Image.new("RGBA", frame.size, SIGNAL + (255,)), (0, 0), contour)

    draw = ImageDraw.Draw(layer)
    label_font = _font(frame.size)
    tag_font = _font(frame.size, cap=TAG_MAX_SIZE)

    for face in faces:
        if face.focus:
            _draw_focus_brackets(draw, face.box)
            continue
        if face.tag:
            _draw_light_brackets(draw, face.box, SKIPPED_GREY if face.skipped else SIGNAL)

    for face in faces:
        if face.focus and face.label:
            obstacles = [other.box for other in faces if other is not face] + list(protect)
            _draw_focus_label(draw, frame.size, label_font, face, obstacles)
        elif face.done:
            _draw_check_tag(draw, face.box, tag_font)
        elif face.tag:
            _draw_tag(draw, face.box, face.tag, SKIPPED_GREY if face.skipped else SIGNAL, tag_font)

    return Image.alpha_composite(frame.convert("RGBA"), layer).convert("RGB")
