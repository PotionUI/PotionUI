from __future__ import annotations

from typing import Any, Optional

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from src.pipelines.pipes._shared.imaging.alpha import flatten_onto


def first_image(value: Any) -> Optional[Image.Image]:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        value = Image.fromarray(value)
    return value


def control_canvas(gen: Any, width: int, height: int, guide: Optional[Image.Image]) -> tuple[int, int]:
    if guide is None:
        return width, height
    gw, gh = guide.size
    scale = (width * height / max(1, gw * gh)) ** 0.5
    return gen.snap_resolution(max(1, round(gw * scale)), max(1, round(gh * scale)))


def _pixels(image: Image.Image, width: int, height: int) -> torch.Tensor:
    rgb = flatten_onto(image)
    if rgb.size != (width, height):
        rgb = rgb.resize((width, height), Image.BICUBIC)
    arr = torch.from_numpy(np.asarray(rgb, dtype=np.uint8).copy()).float()
    return arr.permute(2, 0, 1).unsqueeze(0) / 127.5 - 1.0


def _regenerate(mask: Optional[Image.Image], width: int, height: int) -> torch.Tensor:
    if mask is None:
        return torch.ones(1, 1, height, width)
    grey = mask.convert("L")
    if grey.size != (width, height):
        grey = grey.resize((width, height), Image.BILINEAR)
    values = torch.from_numpy(np.asarray(grey, dtype=np.float32) / 255.0)
    return (values >= 0.5).float()[None, None]


def encode_control_context(gen: Any, width: int, height: int, control_image: Optional[Image.Image],
                           inpaint_image: Optional[Image.Image], mask: Optional[Image.Image]) -> torch.Tensor:
    latent_shape = gen.latent_shape_for(width, height)
    lh, lw = latent_shape[-2], latent_shape[-1]
    channels = latent_shape[1]

    def zeros() -> torch.Tensor:
        return torch.zeros(1, channels, 1, lh, lw)

    control = zeros()
    if control_image is not None:
        control = gen.encode_image(_pixels(control_image, width, height)).float().cpu()

    regen = _regenerate(mask, width, height)
    inpaint = zeros()
    if inpaint_image is not None:
        masked = _pixels(inpaint_image, width, height) * (1.0 - regen)
        inpaint = gen.encode_image(masked).float().cpu()

    keep = 1.0 - F.interpolate(regen, size=(lh, lw), mode="nearest")
    return torch.cat([control, keep.unsqueeze(2), inpaint], dim=1)
