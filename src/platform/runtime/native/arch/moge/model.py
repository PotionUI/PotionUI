from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

from .config import BILINEAR, CONV_TRANSPOSE, DEFAULT_RESOLUTION_LEVEL, IMAGENET_MEAN, IMAGENET_STD, MoGe2Config
from .geometry import fov_from_focal, normalized_view_plane_uv, recover_focal

__all__ = ["FovEstimate", "MoGe2Model"]


@dataclass(frozen=True)
class FovEstimate:
    fov_x_deg: float
    fov_y_deg: float
    focal: float


class _PatchEmbed(nn.Module):
    def __init__(self, patch_size: int, embed_dim: int) -> None:
        super().__init__()
        self.proj = nn.Conv2d(3, embed_dim, kernel_size=patch_size, stride=patch_size)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x).flatten(2).transpose(1, 2)


class _Attention(nn.Module):
    def __init__(self, dim: int, num_heads: int) -> None:
        super().__init__()
        self.num_heads = num_heads
        self.qkv = nn.Linear(dim, dim * 3, bias=True)
        self.proj = nn.Linear(dim, dim, bias=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch, tokens, channels = x.shape
        qkv = self.qkv(x).reshape(batch, tokens, 3, self.num_heads, channels // self.num_heads).permute(2, 0, 3, 1, 4)
        q, k, v = qkv.unbind(0)
        x = F.scaled_dot_product_attention(q, k, v)
        return self.proj(x.permute(0, 2, 1, 3).reshape(batch, tokens, channels))


class _LayerScale(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.gamma = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x * self.gamma


class _Mlp(nn.Module):
    def __init__(self, dim: int, hidden: int) -> None:
        super().__init__()
        self.fc1 = nn.Linear(dim, hidden)
        self.act = nn.GELU()
        self.fc2 = nn.Linear(hidden, dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc2(self.act(self.fc1(x)))


class _Block(nn.Module):
    def __init__(self, config: MoGe2Config) -> None:
        super().__init__()
        dim = config.embed_dim
        self.norm1 = nn.LayerNorm(dim, eps=config.layer_norm_eps)
        self.attn = _Attention(dim, config.num_heads)
        self.ls1 = _LayerScale(dim)
        self.norm2 = nn.LayerNorm(dim, eps=config.layer_norm_eps)
        self.mlp = _Mlp(dim, config.mlp_hidden)
        self.ls2 = _LayerScale(dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.ls1(self.attn(self.norm1(x)))
        return x + self.ls2(self.mlp(self.norm2(x)))


class _DINOv2(nn.Module):
    def __init__(self, config: MoGe2Config) -> None:
        super().__init__()
        self.config = config
        dim = config.embed_dim
        self.patch_embed = _PatchEmbed(config.patch_size, dim)
        self.cls_token = nn.Parameter(torch.zeros(1, 1, dim))
        self.pos_embed = nn.Parameter(torch.zeros(1, 1 + config.pos_grid ** 2, dim))
        self.blocks = nn.ModuleList([_Block(config) for _ in range(config.depth)])
        self.norm = nn.LayerNorm(dim, eps=config.layer_norm_eps)

    def _position_embedding(self, num_patches: int, height: int, width: int, dtype: torch.dtype) -> torch.Tensor:
        grid = self.config.pos_grid
        if num_patches == grid * grid and height == width:
            return self.pos_embed.to(dtype)
        pos_embed = self.pos_embed.float()
        rows, cols = height // self.config.patch_size, width // self.config.patch_size
        offset = self.config.interpolate_offset
        patch = F.interpolate(
            pos_embed[:, 1:].reshape(1, grid, grid, -1).permute(0, 3, 1, 2),
            scale_factor=((rows + offset) / grid, (cols + offset) / grid),
            mode="bicubic",
            antialias=False,
        )
        if tuple(patch.shape[-2:]) != (rows, cols):
            raise ValueError(f"position grid interpolated to {tuple(patch.shape[-2:])}, expected {(rows, cols)}")
        patch = patch.permute(0, 2, 3, 1).flatten(1, 2)
        return torch.cat([pos_embed[:, :1], patch], dim=1).to(dtype)

    def intermediate_layers(
        self, image: torch.Tensor, layers: Sequence[int]
    ) -> List[Tuple[torch.Tensor, torch.Tensor]]:
        height, width = image.shape[-2:]
        x = self.patch_embed(image)
        x = torch.cat([self.cls_token.to(x.dtype).expand(x.shape[0], -1, -1), x], dim=1)
        x = x + self._position_embedding(x.shape[1] - 1, height, width, x.dtype)
        wanted = set(layers)
        outputs = []
        for index, block in enumerate(self.blocks):
            x = block(x)
            if index in wanted:
                normed = self.norm(x)
                outputs.append((normed[:, 1:], normed[:, 0]))
            if index >= max(wanted):
                break
        return outputs


class _Encoder(nn.Module):
    def __init__(self, config: MoGe2Config) -> None:
        super().__init__()
        self.config = config
        self.backbone = _DINOv2(config)
        self.output_projections = nn.ModuleList(
            [nn.Conv2d(config.embed_dim, config.projection_dim, kernel_size=1) for _ in config.intermediate_layers]
        )

    def forward(self, image: torch.Tensor, rows: int, cols: int) -> torch.Tensor:
        patch = self.config.patch_size
        image = F.interpolate(image, (rows * patch, cols * patch), mode="bilinear", align_corners=False, antialias=True)
        mean = torch.tensor(IMAGENET_MEAN, device=image.device, dtype=image.dtype).view(1, 3, 1, 1)
        std = torch.tensor(IMAGENET_STD, device=image.device, dtype=image.dtype).view(1, 3, 1, 1)
        image = (image - mean) / std
        features = self.backbone.intermediate_layers(image, self.config.intermediate_layers)
        return torch.stack(
            [
                projection(tokens.permute(0, 2, 1).unflatten(2, (rows, cols)).contiguous())
                for projection, (tokens, _cls) in zip(self.output_projections, features)
            ],
            dim=1,
        ).sum(dim=1)


def _conv3x3(c_in: int, c_out: int) -> nn.Conv2d:
    return nn.Conv2d(c_in, c_out, kernel_size=3, padding=1, padding_mode="replicate")


class _ResidualConvBlock(nn.Module):
    def __init__(self, channels: int) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Identity(), nn.ReLU(), _conv3x3(channels, channels),
            nn.Identity(), nn.ReLU(), _conv3x3(channels, channels),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.layers(x) + x


class _Resampler(nn.Sequential):
    def __init__(self, c_in: int, c_out: int, kind: str) -> None:
        if kind == CONV_TRANSPOSE:
            super().__init__(nn.ConvTranspose2d(c_in, c_out, kernel_size=2, stride=2), _conv3x3(c_out, c_out))
        elif kind == BILINEAR:
            super().__init__(nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False), _conv3x3(c_in, c_out))
        else:
            raise ValueError(f"unsupported MoGe resampler {kind!r}")


def _conv1x1(c_in: Optional[int], c_out: Optional[int]) -> nn.Module:
    if c_in is None or c_out is None:
        return nn.Identity()
    return nn.Conv2d(c_in, c_out, kernel_size=1)


class _ConvStack(nn.Module):
    def __init__(
        self,
        dim_in: Sequence[Optional[int]],
        dims: Sequence[int],
        dim_out: Sequence[Optional[int]],
        blocks: Sequence[int],
        resamplers: Sequence[str],
    ) -> None:
        super().__init__()
        self.input_blocks = nn.ModuleList([_conv1x1(c_in, dim) for c_in, dim in zip(dim_in, dims)])
        self.resamplers = nn.ModuleList(
            [_Resampler(prev, succ, kind) for prev, succ, kind in zip(dims[:-1], dims[1:], resamplers)]
        )
        self.res_blocks = nn.ModuleList(
            [nn.Sequential(*[_ResidualConvBlock(dim) for _ in range(count)]) for dim, count in zip(dims, blocks)]
        )
        self.output_blocks = nn.ModuleList([_conv1x1(dim, c_out) for dim, c_out in zip(dims, dim_out)])

    def forward(self, features: Sequence[torch.Tensor]) -> List[torch.Tensor]:
        outputs = []
        x = None
        for level in range(len(self.res_blocks)):
            feature = self.input_blocks[level](features[level])
            x = feature if x is None else x + feature
            x = self.res_blocks[level](x)
            outputs.append(self.output_blocks[level](x))
            if level < len(self.resamplers):
                x = self.resamplers[level](x)
        return outputs


class MoGe2Model(nn.Module):
    def __init__(self, config: MoGe2Config) -> None:
        super().__init__()
        self.config = config
        levels = len(config.neck_dims)
        dims = list(config.neck_dims)
        self.encoder = _Encoder(config)
        self.neck = _ConvStack(
            [config.projection_dim + 2, *([2] * (levels - 1))], dims, [None] * levels,
            config.neck_blocks, config.resamplers,
        )
        self.points_head = _ConvStack(
            dims, dims, [*([None] * (levels - 1)), 3], config.head_blocks, config.resamplers,
        )
        self.mask_head = _ConvStack(
            dims, dims, [*([None] * (levels - 1)), 1], config.head_blocks, config.resamplers,
        )

    def forward(self, image: torch.Tensor, num_tokens: int) -> Tuple[torch.Tensor, torch.Tensor]:
        batch, _, height, width = image.shape
        aspect_ratio = width / height
        rows = round((num_tokens / aspect_ratio) ** 0.5)
        cols = round((num_tokens * aspect_ratio) ** 0.5)
        features: List[torch.Tensor] = []
        top = self.encoder(image, rows, cols)
        for level in range(len(self.config.neck_dims)):
            uv = normalized_view_plane_uv(
                cols * 2 ** level, rows * 2 ** level, aspect_ratio=aspect_ratio, dtype=image.dtype, device=image.device
            )
            uv = uv.permute(2, 0, 1).unsqueeze(0).expand(batch, -1, -1, -1)
            features.append(torch.cat([top, uv], dim=1) if level == 0 else uv)
        features = self.neck(features)
        points = F.interpolate(self.points_head(features)[-1], (height, width), mode="bilinear", align_corners=False)
        mask = F.interpolate(self.mask_head(features)[-1], (height, width), mode="bilinear", align_corners=False)
        points = points.permute(0, 2, 3, 1).float()
        xy, z = points.split([2, 1], dim=-1)
        z = torch.exp(z)
        return torch.cat([xy * z, z], dim=-1), mask.squeeze(1).sigmoid().float()

    @torch.no_grad()
    def estimate_fov(
        self, image: torch.Tensor, resolution_level: int = DEFAULT_RESOLUTION_LEVEL
    ) -> List[Optional[FovEstimate]]:
        if image.dim() == 3:
            image = image.unsqueeze(0)
        parameter = next(self.parameters())
        image = image.to(device=parameter.device, dtype=parameter.dtype)
        height, width = image.shape[-2:]
        points, mask = self(image, self.config.num_tokens(resolution_level))
        estimates: List[Optional[FovEstimate]] = []
        for focal in recover_focal(points, mask > self.config.mask_threshold):
            if focal is None:
                estimates.append(None)
                continue
            fov_x, fov_y = fov_from_focal(focal, width / height)
            estimates.append(FovEstimate(fov_x, fov_y, focal) if math.isfinite(fov_x) else None)
        return estimates
