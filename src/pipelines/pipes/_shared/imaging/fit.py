from __future__ import annotations

from typing import Tuple

from PIL import Image

FIT_MODES = ("crop", "pad")
PAD_FILL = (128, 128, 128)

Size = Tuple[int, int]


def crop_box(src_size: Size, target_size: Size) -> Tuple[int, int, int, int]:
    sw, sh = src_size
    tw, th = target_size
    if sw * th > sh * tw:
        cw = max(1, min(sw, round(sh * tw / th)))
        left = (sw - cw) // 2
        return left, 0, left + cw, sh
    ch = max(1, min(sh, round(sw * th / tw)))
    top = (sh - ch) // 2
    return 0, top, sw, top + ch


def pad_layout(src_size: Size, target_size: Size) -> Tuple[int, int, int, int]:
    sw, sh = src_size
    tw, th = target_size
    scale = min(tw / sw, th / sh)
    nw = max(1, min(tw, round(sw * scale)))
    nh = max(1, min(th, round(sh * scale)))
    return (tw - nw) // 2, (th - nh) // 2, nw, nh


def fit_image(image: Image.Image, target_size: Size, fit: str) -> Image.Image:
    target_size = (int(target_size[0]), int(target_size[1]))
    if fit == "crop":
        out = image.crop(crop_box(image.size, target_size))
        return out if out.size == target_size else out.resize(target_size, Image.LANCZOS)
    if fit == "pad":
        left, top, nw, nh = pad_layout(image.size, target_size)
        inner = image if image.size == (nw, nh) else image.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", target_size, PAD_FILL)
        canvas.paste(inner.convert("RGB"), (left, top))
        return canvas
    raise ValueError(f"Unknown fit mode: {fit}")
