from __future__ import annotations

from typing import Any, Optional

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from src.pipelines.pipes._shared.imaging.control_guide import control_canvas, first_image, guide_pixels


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
        control = gen.encode_image(guide_pixels(control_image, width, height)).float().cpu()

    regen = _regenerate(mask, width, height)
    inpaint = zeros()
    if inpaint_image is not None:
        masked = guide_pixels(inpaint_image, width, height) * (1.0 - regen)
        inpaint = gen.encode_image(masked).float().cpu()

    keep = 1.0 - F.interpolate(regen, size=(lh, lw), mode="nearest")
    return torch.cat([control, keep.unsqueeze(2), inpaint], dim=1)
