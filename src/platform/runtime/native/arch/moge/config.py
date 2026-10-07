from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

__all__ = [
    "BILINEAR",
    "CONV_TRANSPOSE",
    "DEFAULT_RESOLUTION_LEVEL",
    "IGNORED_PREFIXES",
    "IMAGENET_MEAN",
    "IMAGENET_STD",
    "MOGE2_VITL",
    "MoGe2Config",
]

CONV_TRANSPOSE = "conv_transpose"
BILINEAR = "bilinear"

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

DEFAULT_RESOLUTION_LEVEL = 9

IGNORED_PREFIXES = (
    "normal_head.",
    "scale_head.",
    "encoder.backbone.mask_token",
    "encoder.image_mean",
    "encoder.image_std",
)


@dataclass(frozen=True)
class MoGe2Config:
    embed_dim: int = 1024
    depth: int = 24
    num_heads: int = 16
    mlp_hidden: int = 4096
    patch_size: int = 14
    pos_grid: int = 37
    layer_norm_eps: float = 1e-6
    interpolate_offset: float = 0.1
    intermediate_layers: Tuple[int, ...] = (5, 11, 17, 23)
    projection_dim: int = 1024
    neck_dims: Tuple[int, ...] = (1024, 256, 128, 64, 32)
    neck_blocks: Tuple[int, ...] = (0, 2, 2, 2, 0)
    head_blocks: Tuple[int, ...] = (0, 1, 1, 1, 0)
    resamplers: Tuple[str, ...] = (CONV_TRANSPOSE, CONV_TRANSPOSE, CONV_TRANSPOSE, BILINEAR)
    num_tokens_range: Tuple[int, int] = (1200, 3600)
    mask_threshold: float = 0.5

    def num_tokens(self, resolution_level: int = DEFAULT_RESOLUTION_LEVEL) -> int:
        if not 0 <= int(resolution_level) <= 9:
            raise ValueError(f"MoGe resolution level must be between 0 and 9, got {resolution_level}")
        low, high = self.num_tokens_range
        return int(low + (int(resolution_level) / 9) * (high - low))


MOGE2_VITL = MoGe2Config()
