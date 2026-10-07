from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Sequence, Union

import numpy as np
import torch

from ...sparse3d import SparseTensor
from ..trellis2.conditioner import IMAGENET_MEAN, IMAGENET_STD
from ..trellis2.config import DINO_V3_VIT_L16
from .config import StageConditioning
from .projection import dense_grid_coords, projection_grid, sample_bilinear

__all__ = [
    "ConditioningView",
    "EncodedView",
    "ProjectedCondition",
    "encode_views",
    "project_features",
    "stage_condition",
    "view_pixels",
]

_GLOBAL_TOKENS = 1 + DINO_V3_VIT_L16.num_register_tokens


class ProjectedCondition(NamedTuple):
    tokens: torch.Tensor
    proj: Union[torch.Tensor, SparseTensor]

    @property
    def dtype(self) -> torch.dtype:
        return self.tokens.dtype

    def negative(self) -> "ProjectedCondition":
        if isinstance(self.proj, SparseTensor):
            proj = self.proj.replace(torch.zeros_like(self.proj.feats))
        else:
            proj = torch.zeros_like(self.proj)
        return ProjectedCondition(torch.zeros_like(self.tokens), proj)


@dataclass
class ConditioningView:
    image: object
    camera: torch.Tensor


@dataclass
class EncodedView:
    camera: torch.Tensor
    pixels: Dict[int, torch.Tensor] = field(default_factory=dict)
    tokens: Dict[int, torch.Tensor] = field(default_factory=dict)
    patches: Dict[int, torch.Tensor] = field(default_factory=dict)


def view_pixels(image, size: int) -> torch.Tensor:
    from PIL import Image

    resized = image.resize((size, size), Image.LANCZOS).convert("RGB")
    return torch.from_numpy(np.asarray(resized, dtype=np.float32) / 255.0).permute(2, 0, 1)


def encode_views(
    conditioner, views: Sequence[ConditioningView], sizes: Sequence[int], device
) -> List[EncodedView]:
    encoded = [EncodedView(camera=view.camera.to(device)) for view in views]
    mean = torch.tensor(IMAGENET_MEAN).view(1, 3, 1, 1)
    std = torch.tensor(IMAGENET_STD).view(1, 3, 1, 1)
    for size in sizes:
        pixels = torch.stack([view_pixels(view.image, size) for view in views])
        features = conditioner.encode((pixels - mean) / std, size)
        grid = size // DINO_V3_VIT_L16.patch_size
        for index, view in enumerate(encoded):
            view.pixels[size] = pixels[index].to(device)
            view.tokens[size] = features[index, :_GLOBAL_TOKENS]
            view.patches[size] = features[index, _GLOBAL_TOKENS:].reshape(grid, grid, -1)
    return encoded


def project_features(
    encoded: Sequence[EncodedView],
    coords: torch.Tensor,
    resolution: int,
    stage: StageConditioning,
    fov: float,
    naf=None,
) -> torch.Tensor:
    if stage.naf_size is not None and naf is None:
        raise ValueError("this stage samples NAF-upsampled features, but no NAF upsampler is loaded")
    total: Optional[torch.Tensor] = None
    for view in encoded:
        patches = view.patches[stage.image_size]
        grid = projection_grid(coords.to(patches.device), resolution, view.camera, fov, stage.image_size)
        parts = [sample_bilinear(patches, grid)]
        if stage.naf_size is not None:
            upsampled = naf(
                view.pixels[stage.image_size].unsqueeze(0),
                patches.permute(2, 0, 1).unsqueeze(0),
                (stage.naf_size, stage.naf_size),
                out_dtype=patches.dtype,
            )[0]
            parts.append(sample_bilinear(upsampled, grid.to(upsampled.device)))
            del upsampled
        feats = torch.cat(parts, dim=-1)
        total = feats if total is None else total.add_(feats)
    return total / len(encoded)


def _tokens(encoded: Sequence[EncodedView], size: int) -> torch.Tensor:
    total = encoded[0].tokens[size].float().clone()
    for view in encoded[1:]:
        total += view.tokens[size].float()
    return (total / len(encoded)).unsqueeze(0)


def stage_condition(
    encoded: Sequence[EncodedView],
    stage: StageConditioning,
    fov: float,
    *,
    coords: Optional[torch.Tensor] = None,
    resolution: int,
    naf=None,
    dtype: Optional[torch.dtype] = None,
) -> ProjectedCondition:
    dtype = dtype or encoded[0].tokens[stage.image_size].dtype
    tokens = _tokens(encoded, stage.image_size).to(dtype)
    if coords is None:
        dense = dense_grid_coords(resolution, device=tokens.device)
        proj = project_features(encoded, dense, resolution, stage, fov, naf)
        return ProjectedCondition(tokens, proj.to(dtype).unsqueeze(0))
    if int(coords[:, 0].max()) != 0:
        raise ValueError("projected conditioning is built for one object at a time; got a batched coordinate set")
    proj = project_features(encoded, coords[:, 1:], resolution, stage, fov, naf)
    return ProjectedCondition(tokens, SparseTensor(feats=proj.to(device=coords.device, dtype=dtype), coords=coords))
