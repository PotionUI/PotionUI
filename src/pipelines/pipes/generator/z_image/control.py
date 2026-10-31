from __future__ import annotations

from typing import Any, Optional

import torch
from PIL import Image

from src.pipelines.pipes._shared.imaging.control_guide import control_canvas, first_image, guide_pixels


def encode_control_context(gen: Any, width: int, height: int, control_image: Optional[Image.Image]) -> torch.Tensor:
    latent_shape = gen.latent_shape_for(width, height)
    channels, lh, lw = latent_shape[1], latent_shape[-2], latent_shape[-1]
    control = torch.zeros(1, channels, lh, lw)
    if control_image is not None:
        control = gen.encode_image(guide_pixels(control_image, width, height)).float().cpu()
    return torch.cat([control, torch.zeros(1, 1, lh, lw), torch.zeros(1, channels, lh, lw)], dim=1)
