# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/models/sparse_structure_vae.py
# (SparseStructureDecoder, ResBlock3d, UpsampleBlock3d, norm_layer) and
# trellis2/modules/spatial.py (pixel_shuffle_3d). SparseStructureEncoder and
# DownsampleBlock3d are not ported — this piece is decoder-only. Adapted to the
# native ``operations`` seam (torch.nn.Conv3d/LayerNorm/GroupNorm ->
# operations.Conv3d/LayerNorm/GroupNorm); submodule names/nesting kept IDENTICAL
# to upstream so a checkpoint's decoder state dict loads with no key remapping.
"""SS VAE decoder — ``SSVAEDecoder`` (``NativeArchModule``).

Dense 3D conv decoder: a latent ``[B, latent_channels, r, r, r]`` grid up through
a channel/resolution schedule (``ResBlock3d`` at each level, ``UpsampleBlock3d``
[3D pixel-shuffle, factor 2] between levels) to an occupancy logit volume
``[B, out_channels, r * 2**(len(channels)-1), ..., ...]``.

Forward-call contract: ``forward(x)`` where ``x`` is ``[B, latent_channels, r,
r, r]``; returns ``[B, out_channels, R, R, R]`` (``R`` per the channel
schedule's upsample count). The caller applies ``> 0`` to get an occupancy mask
(this module returns raw logits, matching upstream).
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ...base import NativeArchModule
from .config import SSVAEDecoderConfig

Tensor = torch.Tensor


def _manual_cast(t: Tensor, dtype: torch.dtype) -> Tensor:
    if not torch.is_autocast_enabled():
        return t.type(dtype)
    return t


def _make_norm32(norm_type: str, channels: int, operations, device=None, dtype=None) -> nn.Module:
    """``GroupNorm32``/``ChannelLayerNorm32``: a real subclass of the operations
    norm (so ``weight``/``bias`` land flat on the returned module, matching
    upstream's key space) whose ``forward`` runs the norm in float32 --
    ``ChannelLayerNorm32`` additionally permutes channels to the last dim first
    (``nn.LayerNorm`` normalizes over the trailing dims, ``nn.GroupNorm`` is
    already channel-first)."""
    if norm_type == "group":
        class _GroupNorm32(operations.GroupNorm):
            def forward(self, x: Tensor) -> Tensor:
                x_dtype = x.dtype
                o = super().forward(_manual_cast(x, torch.float32))
                return _manual_cast(o, x_dtype)

        return _GroupNorm32(32, channels, device=device, dtype=dtype)

    if norm_type == "layer":
        class _ChannelLayerNorm32(operations.LayerNorm):
            def forward(self, x: Tensor) -> Tensor:
                dim = x.dim()
                x = x.permute(0, *range(2, dim), 1).contiguous()
                x_dtype = x.dtype
                x = super().forward(_manual_cast(x, torch.float32))
                x = _manual_cast(x, x_dtype)
                return x.permute(0, dim - 1, *range(1, dim - 1)).contiguous()

        return _ChannelLayerNorm32(channels, device=device, dtype=dtype)

    raise ValueError(f"unsupported norm_type {norm_type!r}")


def _pixel_shuffle_3d(x: Tensor, scale_factor: int) -> Tensor:
    B, C, H, W, D = x.shape
    C_ = C // scale_factor**3
    x = x.reshape(B, C_, scale_factor, scale_factor, scale_factor, H, W, D)
    x = x.permute(0, 1, 5, 2, 6, 3, 7, 4)
    return x.reshape(B, C_, H * scale_factor, W * scale_factor, D * scale_factor)


class _ResBlock3d(nn.Module):
    def __init__(self, channels: int, out_channels: int, norm_type: str, operations,
                 device=None, dtype=None) -> None:
        super().__init__()
        self.out_channels = out_channels or channels
        self.norm1 = _make_norm32(norm_type, channels, operations, device, dtype)
        self.norm2 = _make_norm32(norm_type, self.out_channels, operations, device, dtype)
        self.conv1 = operations.Conv3d(channels, self.out_channels, 3, padding=1, device=device, dtype=dtype)
        self.conv2 = operations.Conv3d(self.out_channels, self.out_channels, 3, padding=1, device=device, dtype=dtype)
        self.skip_connection = (
            operations.Conv3d(channels, self.out_channels, 1, device=device, dtype=dtype)
            if channels != self.out_channels else nn.Identity()
        )

    def forward(self, x: Tensor) -> Tensor:
        h = self.norm1(x)
        h = F.silu(h)
        h = self.conv1(h)
        h = self.norm2(h)
        h = F.silu(h)
        h = self.conv2(h)
        return h + self.skip_connection(x)


class _UpsampleBlock3d(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.conv = operations.Conv3d(in_channels, out_channels * 8, 3, padding=1, device=device, dtype=dtype)

    def forward(self, x: Tensor) -> Tensor:
        return _pixel_shuffle_3d(self.conv(x), 2)


class SSVAEDecoder(NativeArchModule):
    """``SparseStructureDecoder``: Conv3d(latent -> channels[0]) -> middle
    ResBlock3d stack -> per-level ResBlock3d(s) + UpsampleBlock3d -> out Conv3d."""

    def __init__(self, config: SSVAEDecoderConfig, operations, device=None, dtype=None) -> None:
        super().__init__()
        self.config = config
        channels = config.channels

        self.input_layer = operations.Conv3d(
            config.latent_channels, channels[0], 3, padding=1, device=device, dtype=dtype,
        )
        self.middle_block = nn.Sequential(*[
            _ResBlock3d(channels[0], channels[0], config.norm_type, operations, device=device, dtype=dtype)
            for _ in range(config.num_res_blocks_middle)
        ])

        blocks: list[nn.Module] = []
        for i, ch in enumerate(channels):
            blocks.extend([
                _ResBlock3d(ch, ch, config.norm_type, operations, device=device, dtype=dtype)
                for _ in range(config.num_res_blocks)
            ])
            if i < len(channels) - 1:
                blocks.append(_UpsampleBlock3d(ch, channels[i + 1], operations, device=device, dtype=dtype))
        self.blocks = nn.ModuleList(blocks)

        self.out_layer = nn.Sequential(
            _make_norm32(config.norm_type, channels[-1], operations, device=device, dtype=dtype),
            nn.SiLU(),
            operations.Conv3d(channels[-1], config.out_channels, 3, padding=1, device=device, dtype=dtype),
        )

    # -- foundation contract ------------------------------------------------

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "SSVAEDecoder":
        cfg = dict(config)
        cfg["channels"] = tuple(cfg["channels"])
        return cls(SSVAEDecoderConfig(**cfg), operations=operations)

    def post_load(self) -> None:
        """No computed/derived buffers to recompute (pure conv/norm stack)."""

    # -- forward --------------------------------------------------------------

    def forward(self, x: Tensor) -> Tensor:
        h = self.input_layer(x)
        h = self.middle_block(h)
        for block in self.blocks:
            h = block(h)
        return self.out_layer(h)
