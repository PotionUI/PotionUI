# Vendored from ByteDance's SeedVR — https://github.com/ByteDance-Seed/SeedVR
# Upstream path: models/video_vae_v3/modules/attn_video_vae.py @ unknown;
# vendored ~2025 (moved into vendor/seedvr2/ from
# src/platform/runtime/native/vae/seedvr2_causal_video.py as part of the
# license-relocation workstream, BE-97).
# License: Apache-2.0 (see LICENSE).

"""SeedVR2 causal video VAE building blocks, ported from ByteDance's SeedVR
``models/video_vae_v3/modules/attn_video_vae.py`` (the
``VideoAutoencoderKLWrapper`` -> ``VideoAutoencoderKL`` stack: diffusers
``AutoencoderKL`` blocks *inflated* from 2D to causal-3D -- ``ResnetBlock3D``,
``DownEncoderBlock3D``/``UpDecoderBlock3D``, ``UNetMidBlock3D`` with a diffusers
``Attention`` mid-block, and ``InflatedCausalConv3d`` in place of every conv).

**Key layout is diffusers ``AutoencoderKL`` verbatim.** The real checkpoint
(``models/vae/ema_vae_fp16.safetensors``: 250 tensors, ``encoder.*``/``decoder.*``
only, no ``quant_conv``/``post_quant_conv``) stores *already-inflated 5D* conv
weights (e.g. ``encoder.conv_in.weight`` is ``[128,3,3,3,3]``). SeedVR's
load-time 2D->3D inflation only fires for 4D source weights, so for this
checkpoint it is a no-op -- native 3D convs load the 5D weights strict, no
rename map (mirrors ``causal_3d.py``'s "Key parity" arrangement: the causal
conv IS a plain ``operations.Conv3d`` whose params sit at the exact
state-dict path, with the causal-padding amount stashed as a plain attribute
and applied by a module-level forward helper -- there is no wrapper submodule).

**Causal padding is first-frame REPLICATE, not zeros** (this is the one real
math difference from ``causal_3d.py``, which zero-left-pads). SeedVR's
``extend_head`` prepends ``2 * temporal_padding`` copies of the first frame
before a temporal conv with its temporal padding removed; ``GroupNorm`` is
applied *per frame* (reshape ``(B,C,T,H,W) -> (B*T,C,H,W)``), i.e. frames are
normalized independently -- both replicated faithfully here (see
``_causal_conv3d_forward`` / ``_causal_group_norm``). For a still image
(``T=1``) these reduce to a plain conv/GroupNorm, so the image path is exact.

**Temporal streaming (SeedVR's ``MemoryState``/``set_causal_slicing`` chunked
conv cache) is intentionally NOT ported** -- this vendor pass targets images
(``T=1``) and short whole-clips processed in one shot, which corresponds to
SeedVR's ``MemoryState.DISABLED`` path (no cross-call memory).

The top-level ``SeedVR2CausalVideoVAE`` class
(``src/platform/runtime/native/vae/seedvr2_causal_video.py``) extends
``NativeArchModule`` (PotionUI's own loader contract), owns the config
constants + latent scaling, and delegates spatial tiling to PotionUI's own
shared ``tiling`` module — none of which is ByteDance-derived — so it stays in
src and imports these building blocks from here.
"""

from __future__ import annotations

from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F


def _causal_conv3d(
    in_channels: int,
    out_channels: int,
    kernel_size: int | tuple[int, int, int],
    *,
    stride: int | tuple[int, int, int] = 1,
    temporal_padding: int = 0,
    spatial_padding: int = 0,
    bias: bool = True,
    operations: Any,
) -> nn.Module:
    """Build a SeedVR ``InflatedCausalConv3d`` equivalent: a native 3D conv
    with *spatial-only* padding baked in; the temporal padding count is stashed
    for :func:`_causal_conv3d_forward` to apply as a first-frame replicate."""
    if isinstance(kernel_size, int):
        kernel_size = (kernel_size, kernel_size, kernel_size)
    conv = operations.Conv3d(
        in_channels, out_channels, kernel_size,
        stride=stride, padding=(0, spatial_padding, spatial_padding), bias=bias,
    )
    conv._seedvr_temporal_pad = temporal_padding
    return conv


def _causal_conv3d_forward(conv: nn.Module, x: torch.Tensor) -> torch.Tensor:
    """Apply a causal conv built by :func:`_causal_conv3d`: front-pad the time
    axis with ``2 * temporal_pad`` replicas of the first frame (SeedVR's
    ``extend_head(times=temporal_padding*2)``), then convolve. This is the
    ``MemoryState.DISABLED`` path -- no cross-call streaming cache."""
    pad = conv._seedvr_temporal_pad * 2
    if pad > 0:
        x = torch.cat([x[:, :, :1].repeat(1, 1, pad, 1, 1), x], dim=2)
    return conv(x)


def _causal_group_norm(norm: nn.Module, x: torch.Tensor) -> torch.Tensor:
    """GroupNorm applied per frame (SeedVR's ``causal_norm_wrapper`` for
    GroupNorm): reshape ``(B,C,T,H,W) -> (B*T,C,H,W)`` so each frame normalizes
    independently, then reshape back. For ``T=1`` this equals a plain 5D
    GroupNorm; for video it deliberately excludes the temporal axis."""
    b, c, t, h, w = x.shape
    x = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
    x = norm(x)
    return x.reshape(b, t, c, h, w).permute(0, 2, 1, 3, 4)


class ResnetBlock3D(nn.Module):
    """Causal-inflated diffusers ``ResnetBlock2D`` (``time_receptive_field
    "full"``: both convs are temporal-causal 3x3x3). No temb, no in-block
    up/down (those live in the separate down/up samplers)."""

    def __init__(self, in_channels: int, out_channels: int, *, groups: int = 32,
                 eps: float = 1e-6, operations: Any) -> None:
        super().__init__()
        self.norm1 = operations.GroupNorm(groups, in_channels, eps=eps, affine=True)
        self.conv1 = _causal_conv3d(in_channels, out_channels, 3, temporal_padding=1,
                                    spatial_padding=1, operations=operations)
        self.norm2 = operations.GroupNorm(groups, out_channels, eps=eps, affine=True)
        self.dropout = nn.Dropout(0.0)
        self.conv2 = _causal_conv3d(out_channels, out_channels, 3, temporal_padding=1,
                                    spatial_padding=1, operations=operations)
        self.nonlinearity = nn.SiLU()
        self.conv_shortcut = (
            _causal_conv3d(in_channels, out_channels, 1, operations=operations)
            if in_channels != out_channels else None
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        h = _causal_group_norm(self.norm1, x)
        h = self.nonlinearity(h)
        h = _causal_conv3d_forward(self.conv1, h)
        h = _causal_group_norm(self.norm2, h)
        h = self.nonlinearity(h)
        h = self.dropout(h)
        h = _causal_conv3d_forward(self.conv2, h)
        shortcut = x if self.conv_shortcut is None else _causal_conv3d_forward(self.conv_shortcut, x)
        return shortcut + h  # output_scale_factor == 1.0


class AttentionBlock3D(nn.Module):
    """Per-frame spatial self-attention: SeedVR runs a diffusers ``Attention``
    (deprecated-attn-block, single head, group_norm + residual) over each
    frame's ``H*W`` positions (reshape ``(B,C,T,H,W) -> (B*T,C,H,W)``). Keys are
    diffusers-native: ``group_norm`` / ``to_q`` / ``to_k`` / ``to_v`` /
    ``to_out.0``."""

    def __init__(self, channels: int, *, groups: int = 32, eps: float = 1e-6, operations: Any) -> None:
        super().__init__()
        self.channels = channels
        self.group_norm = operations.GroupNorm(groups, channels, eps=eps, affine=True)
        self.to_q = operations.Linear(channels, channels, bias=True)
        self.to_k = operations.Linear(channels, channels, bias=True)
        self.to_v = operations.Linear(channels, channels, bias=True)
        self.to_out = nn.ModuleList([operations.Linear(channels, channels, bias=True)])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, c, t, h, w = x.shape
        x2 = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)  # per-frame
        residual = x2

        seq = x2.reshape(b * t, c, h * w).transpose(1, 2)  # (bt, hw, c)
        seq = self.group_norm(seq.transpose(1, 2)).transpose(1, 2)
        q = self.to_q(seq)
        k = self.to_k(seq)
        v = self.to_v(seq)
        # single-head attention over the hw positions (diffusers heads==channels//head_dim==1)
        q = q.unsqueeze(1)
        k = k.unsqueeze(1)
        v = v.unsqueeze(1)
        out = F.scaled_dot_product_attention(q, k, v, dropout_p=0.0, is_causal=False)
        out = out.squeeze(1)  # (bt, hw, c)
        out = self.to_out[0](out)
        out = out.transpose(1, 2).reshape(b * t, c, h, w)
        out = out + residual  # residual_connection, rescale_output_factor == 1.0

        return out.reshape(b, t, c, h, w).permute(0, 2, 1, 3, 4)


class Downsample3D(nn.Module):
    """Strided causal conv downsample (SeedVR ``Downsample3D``): asymmetric
    spatial pad ``(0,1,0,1)`` then a stride-2 causal conv. ``temporal_down``
    convs carry temporal kernel 3 (causal); spatial-only convs use temporal
    kernel 1 (no temporal padding)."""

    def __init__(self, channels: int, *, temporal_down: bool, operations: Any) -> None:
        super().__init__()
        temporal_kernel = 3 if temporal_down else 1
        temporal_stride = 2 if temporal_down else 1
        self.conv = _causal_conv3d(
            channels, channels, (temporal_kernel, 3, 3),
            stride=(temporal_stride, 2, 2),
            temporal_padding=1 if temporal_down else 0, spatial_padding=0,
            operations=operations,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.pad(x, (0, 1, 0, 1))  # spatial asymmetric pad (w_right, h_bottom)
        return _causal_conv3d_forward(self.conv, x)


class Upsample3D(nn.Module):
    """MAGViT-v2 depth-to-space causal upsample (SeedVR ``Upsample3D``): a
    1x1x1 ``upscale_conv`` expands channels by ``spatial_ratio^2 *
    temporal_ratio``, a pixel-shuffle rearranges them into space/time, the
    duplicated head frame is dropped on temporal upsamples, then a causal 3x3x3
    ``conv`` cleans up."""

    def __init__(self, channels: int, *, temporal_up: bool, operations: Any) -> None:
        super().__init__()
        self.temporal_ratio = 2 if temporal_up else 1
        self.spatial_ratio = 2
        upscale = self.spatial_ratio ** 2 * self.temporal_ratio
        # upscale_conv is a plain (non-causal) 1x1x1 conv in SeedVR.
        self.upscale_conv = operations.Conv3d(channels, channels * upscale, kernel_size=1)
        self.conv = _causal_conv3d(channels, channels, 3, temporal_padding=1,
                                   spatial_padding=1, operations=operations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.upscale_conv(x)
        # "b (x y z c) f h w -> b c (f z) (h x) (w y)" (channel fastest-var: c,z,y,x)
        b, _, f, h, w = x.shape
        sx = self.spatial_ratio
        sy = self.spatial_ratio
        tz = self.temporal_ratio
        c = x.shape[1] // (sx * sy * tz)
        x = x.view(b, sx, sy, tz, c, f, h, w)
        x = x.permute(0, 4, 5, 3, 6, 1, 7, 2).reshape(b, c, f * tz, h * sx, w * sy)
        if self.temporal_ratio == 2 and x.shape[2] > 0:
            # remove_head: drop the duplicated first-frame the temporal expansion
            # introduced (keep frame 0, drop frame 1, keep the rest).
            x = torch.cat([x[:, :, :1], x[:, :, 2:]], dim=2)
        return _causal_conv3d_forward(self.conv, x)


class DownEncoderBlock3D(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, *, num_layers: int,
                 groups: int, temporal_down: bool, add_downsample: bool, operations: Any) -> None:
        super().__init__()
        self.resnets = nn.ModuleList([
            ResnetBlock3D(in_channels if i == 0 else out_channels, out_channels,
                          groups=groups, operations=operations)
            for i in range(num_layers)
        ])
        self.downsamplers = (
            nn.ModuleList([Downsample3D(out_channels, temporal_down=temporal_down, operations=operations)])
            if add_downsample else None
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for resnet in self.resnets:
            x = resnet(x)
        if self.downsamplers is not None:
            for down in self.downsamplers:
                x = down(x)
        return x


class UpDecoderBlock3D(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, *, num_layers: int,
                 groups: int, temporal_up: bool, add_upsample: bool, operations: Any) -> None:
        super().__init__()
        self.resnets = nn.ModuleList([
            ResnetBlock3D(in_channels if i == 0 else out_channels, out_channels,
                          groups=groups, operations=operations)
            for i in range(num_layers)
        ])
        self.upsamplers = (
            nn.ModuleList([Upsample3D(out_channels, temporal_up=temporal_up, operations=operations)])
            if add_upsample else None
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for resnet in self.resnets:
            x = resnet(x)
        if self.upsamplers is not None:
            for up in self.upsamplers:
                x = up(x)
        return x


class UNetMidBlock3D(nn.Module):
    """SeedVR mid-block: resnet, then a single [attention, resnet] pair."""

    def __init__(self, channels: int, *, groups: int, operations: Any) -> None:
        super().__init__()
        self.resnets = nn.ModuleList([
            ResnetBlock3D(channels, channels, groups=groups, operations=operations),
            ResnetBlock3D(channels, channels, groups=groups, operations=operations),
        ])
        self.attentions = nn.ModuleList([
            AttentionBlock3D(channels, groups=groups, operations=operations),
        ])

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.resnets[0](x)
        for attn, resnet in zip(self.attentions, self.resnets[1:]):
            x = attn(x)
            x = resnet(x)
        return x


class Encoder3D(nn.Module):
    def __init__(self, *, in_channels: int, latent_channels: int, block_out_channels: tuple[int, ...],
                 layers_per_block: int, norm_num_groups: int, temporal_down_num: int, operations: Any) -> None:
        super().__init__()
        self.conv_in = _causal_conv3d(in_channels, block_out_channels[0], 3,
                                      temporal_padding=1, spatial_padding=1, operations=operations)

        self.down_blocks = nn.ModuleList()
        out_ch = block_out_channels[0]
        n = len(block_out_channels)
        for i in range(n):
            in_ch = out_ch
            out_ch = block_out_channels[i]
            is_final = i == n - 1
            # SeedVR: is_temporal_down_block = i >= n - temporal_down_num - 1
            temporal_down = i >= n - temporal_down_num - 1
            self.down_blocks.append(DownEncoderBlock3D(
                in_ch, out_ch, num_layers=layers_per_block, groups=norm_num_groups,
                temporal_down=temporal_down, add_downsample=not is_final, operations=operations,
            ))

        self.mid_block = UNetMidBlock3D(block_out_channels[-1], groups=norm_num_groups, operations=operations)
        self.conv_norm_out = operations.GroupNorm(norm_num_groups, block_out_channels[-1], eps=1e-6, affine=True)
        self.conv_act = nn.SiLU()
        # double_z: 2 * latent_channels (mean + logvar).
        self.conv_out = _causal_conv3d(block_out_channels[-1], 2 * latent_channels, 3,
                                       temporal_padding=1, spatial_padding=1, operations=operations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = _causal_conv3d_forward(self.conv_in, x)
        for block in self.down_blocks:
            x = block(x)
        x = self.mid_block(x)
        x = _causal_group_norm(self.conv_norm_out, x)
        x = self.conv_act(x)
        return _causal_conv3d_forward(self.conv_out, x)


class Decoder3D(nn.Module):
    def __init__(self, *, out_channels: int, latent_channels: int, block_out_channels: tuple[int, ...],
                 layers_per_block: int, norm_num_groups: int, temporal_up_num: int, operations: Any) -> None:
        super().__init__()
        self.conv_in = _causal_conv3d(latent_channels, block_out_channels[-1], 3,
                                      temporal_padding=1, spatial_padding=1, operations=operations)
        self.mid_block = UNetMidBlock3D(block_out_channels[-1], groups=norm_num_groups, operations=operations)

        self.up_blocks = nn.ModuleList()
        reversed_ch = list(reversed(block_out_channels))
        n = len(block_out_channels)
        out_ch = reversed_ch[0]
        for i in range(n):
            prev_out = out_ch
            out_ch = reversed_ch[i]
            is_final = i == n - 1
            # SeedVR: is_temporal_up_block = i < temporal_up_num
            temporal_up = i < temporal_up_num
            self.up_blocks.append(UpDecoderBlock3D(
                prev_out, out_ch, num_layers=layers_per_block + 1, groups=norm_num_groups,
                temporal_up=temporal_up, add_upsample=not is_final, operations=operations,
            ))

        self.conv_norm_out = operations.GroupNorm(norm_num_groups, block_out_channels[0], eps=1e-6, affine=True)
        self.conv_act = nn.SiLU()
        self.conv_out = _causal_conv3d(block_out_channels[0], out_channels, 3,
                                       temporal_padding=1, spatial_padding=1, operations=operations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = _causal_conv3d_forward(self.conv_in, x)
        x = self.mid_block(x)
        for block in self.up_blocks:
            x = block(x)
        x = _causal_group_norm(self.conv_norm_out, x)
        x = self.conv_act(x)
        return _causal_conv3d_forward(self.conv_out, x)
