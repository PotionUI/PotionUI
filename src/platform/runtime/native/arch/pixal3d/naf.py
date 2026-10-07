from __future__ import annotations

import math
from pathlib import Path
from typing import Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

__all__ = ["NAF", "NAF_PREFIX", "has_naf_weights", "load_naf", "neighborhood_attention_2d"]

NAF_PREFIX = "naf."

_CHUNK_BYTES = 256 * 1024 * 1024


class _EncBlock(nn.Module):
    def __init__(self, channels: int, kernel_size: int, num_groups: int = 8) -> None:
        super().__init__()
        self.norm1 = nn.GroupNorm(num_groups, channels)
        self.conv1 = nn.Conv2d(channels, channels, kernel_size, padding=kernel_size // 2, padding_mode="reflect")
        self.norm2 = nn.GroupNorm(num_groups, channels)
        self.conv2 = nn.Conv2d(channels, channels, kernel_size, padding=kernel_size // 2, padding_mode="reflect")
        self.activation_fn = nn.SiLU()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.conv1(self.activation_fn(self.norm1(x)))
        return self.conv2(self.activation_fn(self.norm2(x)))


def _encoder(in_channels: int, channels: int, kernel_size: int, num_layers: int = 2) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(in_channels, channels, kernel_size, padding=kernel_size // 2, padding_mode="reflect"),
        *[_EncBlock(channels, kernel_size) for _ in range(num_layers)],
    )


class _RoPE(nn.Module):
    def __init__(self, embed_dim: int, num_heads: int, base: float) -> None:
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = embed_dim // num_heads
        quarter = self.head_dim // 4
        periods = base ** (2 * torch.arange(quarter, dtype=torch.float32) / (self.head_dim // 2))
        self.register_buffer("periods", periods, persistent=True)

    def _angles(self, height: int, width: int) -> torch.Tensor:
        device = self.periods.device
        rows = torch.arange(0.5, height, device=device, dtype=torch.float32) / height
        cols = torch.arange(0.5, width, device=device, dtype=torch.float32) / width
        coords = torch.stack(torch.meshgrid(rows, cols, indexing="ij"), dim=-1).flatten(0, 1) * 2.0 - 1.0
        angles = 2 * math.pi * coords[:, :, None] / self.periods.float()[None, None, :]
        return angles.flatten(1, 2).tile(2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        batch, channels, height, width = x.shape
        heads = x.reshape(batch, self.num_heads, self.head_dim, height * width).transpose(-1, -2)
        angles = self._angles(height, width)
        first, second = heads.chunk(2, dim=-1)
        rotated = heads * torch.cos(angles) + torch.cat([-second, first], dim=-1) * torch.sin(angles)
        return rotated.transpose(-1, -2).reshape(batch, channels, height, width)


class _ImageEncoder(nn.Module):
    def __init__(self, channels: int, heads: int, rope_base: float) -> None:
        super().__init__()
        self.encoder = _encoder(3, channels // 2, 1)
        self.sem_encoder = _encoder(3, channels // 2, 3)
        self.rope = _RoPE(channels, heads, rope_base)

    def forward(self, image: torch.Tensor, output_size: Tuple[int, int]) -> torch.Tensor:
        out_h, out_w = output_size
        if image.shape[-2] > 4 * out_h or image.shape[-1] > 4 * out_w:
            image = F.interpolate(
                image,
                size=(min(image.shape[-2], 4 * out_h, 4 * out_w), min(image.shape[-1], 4 * out_w, 4 * out_h)),
                mode="bilinear",
                align_corners=False,
            )
        features = torch.cat([self.encoder(image), self.sem_encoder(image)], dim=1)
        return self.rope(F.adaptive_avg_pool2d(features, output_size=output_size))


def _window_indices(cells_h: int, cells_w: int, kernel: int, device) -> torch.Tensor:
    rows = (torch.arange(cells_h, device=device) - kernel // 2).clamp(0, cells_h - kernel)[:, None] + torch.arange(
        kernel, device=device
    )
    cols = (torch.arange(cells_w, device=device) - kernel // 2).clamp(0, cells_w - kernel)[:, None] + torch.arange(
        kernel, device=device
    )
    return (rows[:, None, :, None] * cells_w + cols[None, :, None, :]).reshape(cells_h * cells_w, kernel * kernel)


def neighborhood_attention_2d(
    q: torch.Tensor,
    k: torch.Tensor,
    v: torch.Tensor,
    kernel: int = 9,
    scale: Optional[float] = None,
    out_dtype: Optional[torch.dtype] = None,
    chunk_bytes: int = _CHUNK_BYTES,
) -> torch.Tensor:
    height, width, heads, qk_dim = q.shape
    cells_h, cells_w = k.shape[:2]
    if height % cells_h or width % cells_w:
        raise ValueError(
            f"query grid {height}x{width} is not an integer multiple of the key grid {cells_h}x{cells_w}"
        )
    dilation_h, dilation_w = height // cells_h, width // cells_w
    if dilation_h != dilation_w:
        raise ValueError(f"anisotropic dilation {dilation_h}x{dilation_w} is not supported")
    if cells_h < kernel or cells_w < kernel:
        raise ValueError(f"a {kernel}x{kernel} neighbourhood needs at least {kernel} cells per side")
    d = dilation_h
    value_dim = v.shape[-1]
    scale = qk_dim ** -0.5 if scale is None else scale
    window = _window_indices(cells_h, cells_w, kernel, q.device)
    compute = torch.promote_types(q.dtype, torch.float32)
    keys = k.reshape(cells_h * cells_w, heads, qk_dim).to(compute)
    values = v.reshape(cells_h * cells_w, heads, value_dim).to(compute)
    out = torch.empty(height, width, heads, value_dim, device=q.device, dtype=out_dtype or q.dtype)
    taps = kernel * kernel
    per_cell = 4 * (taps * heads * (qk_dim + value_dim) + 2 * heads * d * d * taps + d * d * heads * (qk_dim + value_dim))
    rows_per_chunk = max(1, chunk_bytes // max(1, per_cell * cells_w))
    for start in range(0, cells_h, rows_per_chunk):
        stop = min(cells_h, start + rows_per_chunk)
        rows = stop - start
        block = q[start * d:stop * d].to(compute)
        block = block.reshape(rows, d, cells_w, d, heads, qk_dim).permute(0, 2, 1, 3, 4, 5)
        block = block.reshape(rows * cells_w, d * d, heads, qk_dim)
        index = window[start * cells_w:stop * cells_w]
        scores = torch.einsum("cqnd,cknd->cnqk", block, keys[index]) * scale
        attended = torch.einsum("cnqk,cknd->cqnd", scores.softmax(dim=-1), values[index])
        attended = attended.reshape(rows, cells_w, d, d, heads, value_dim).permute(0, 2, 1, 3, 4, 5)
        out[start * d:stop * d] = attended.reshape(rows * d, width, heads, value_dim).to(out.dtype)
    return out


class NAF(nn.Module):
    def __init__(self, dim: int = 256, heads: int = 4, kernel_size: int = 9, rope_base: float = 100.0) -> None:
        super().__init__()
        self.heads = heads
        self.kernel_size = kernel_size
        self.scale = (dim // heads) ** -0.5
        self.image_encoder = _ImageEncoder(dim, heads, rope_base)

    @property
    def device(self) -> torch.device:
        return self.image_encoder.rope.periods.device

    def forward(
        self,
        image: torch.Tensor,
        features: torch.Tensor,
        output_size: Tuple[int, int],
        out_dtype: Optional[torch.dtype] = None,
    ) -> torch.Tensor:
        dtype = self.image_encoder.rope.periods.dtype
        queries = self.image_encoder(image.to(device=self.device, dtype=dtype), output_size)
        keys = F.adaptive_avg_pool2d(queries, output_size=features.shape[-2:])
        features = features.to(device=self.device)
        batch, channels = features.shape[:2]
        out_h, out_w = output_size
        out = torch.empty(batch, out_h, out_w, channels, device=self.device, dtype=out_dtype or features.dtype)
        for b in range(batch):
            q = queries[b].reshape(self.heads, -1, out_h, out_w).permute(2, 3, 0, 1)
            k = keys[b].reshape(self.heads, -1, *keys.shape[-2:]).permute(2, 3, 0, 1)
            v = features[b].reshape(self.heads, -1, *features.shape[-2:]).permute(2, 3, 0, 1)
            attended = neighborhood_attention_2d(
                q, k, v, kernel=self.kernel_size, scale=self.scale, out_dtype=out.dtype
            )
            out[b] = attended.reshape(out_h, out_w, channels)
        return out


def has_naf_weights(path: str | Path) -> bool:
    from safetensors import safe_open

    with safe_open(str(path), framework="pt") as handle:
        return any(key.startswith(NAF_PREFIX) for key in handle.keys())


def load_naf(path: str | Path) -> NAF:
    from ...io.safetensors_loader import load_torch_file_prefixed

    if not has_naf_weights(path):
        raise ValueError(
            f"{Path(path).name} carries no NAF upsampler weights (no '{NAF_PREFIX}' keys). Pixal3D needs "
            "the DINOv3 file with NAF bundled: dino_v3_L_naf_fp32.safetensors."
        )
    sd, _ = load_torch_file_prefixed(path, NAF_PREFIX, device="cpu")
    sliced = {key[len(NAF_PREFIX):]: value.float() for key, value in sd.items() if key.startswith(NAF_PREFIX)}
    module = NAF()
    result = module.load_state_dict(sliced, strict=False)
    expected = {name for name, _ in module.named_parameters()} | {"image_encoder.rope.periods"}
    unfilled = sorted(expected.intersection(result.missing_keys))
    if unfilled:
        raise ValueError(
            f"{Path(path).name}: {len(unfilled)} NAF weights left unfilled, first few {unfilled[:5]}."
        )
    return module.requires_grad_(False).eval()
