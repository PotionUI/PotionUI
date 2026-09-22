"""Wan 2.2 causal 3D VAE, vendored from ComfyUI's ``comfy/ldm/wan/vae2_2.py``.

Separate file, not a config variant of ``causal_3d.py`` -- the actual diff is
not "same architecture, different numbers" the way ``flux_ae``/``flux2_ae``
were. ComfyUI itself keeps ``vae2_2.py`` as its own file for the same reason:
it introduces a genuinely different block structure --

  * ``patchify``/``unpatchify`` (2x2 space-to-depth before the encoder and
    after the decoder -- ``encoder.conv1`` takes 12 channels, not 3).
  * ``Down_ResidualBlock``/``Up_ResidualBlock`` wrapping *both* a stack of
    plain ``ResidualBlock``s *and* a parameter-free average-pooling shortcut
    path (``AvgDown3D``/``DupUp3D``) that mixes back in at the block's output
    -- Wan 2.1 has no such shortcut path.
  * An asymmetric channel width: the encoder base width (``dim=160``) and
    decoder base width (``dec_dim=256``) differ -- verified against the real
    ``wan2.2_vae.safetensors`` header (``encoder.conv1.weight`` shape
    ``[160,12,...]`` vs ``decoder.conv1.weight`` shape ``[1024,48,...]`` where
    ``1024 = dec_dim * dim_mult[-1] = 256*4``).

``ResidualBlock``, ``AttentionBlock``, ``RMS_norm`` and the causal-conv
plumbing (``_causal_conv3d``/``_conv3d_forward``/``_is_causal_conv3d``) ARE
identical to Wan 2.1's -- ComfyUI's own ``vae2_2.py`` imports them from
``.vae`` rather than redefining them, and this module does the same (imports
from ``causal_3d.py``) rather than duplicating.

**Detection**: the nested ``decoder.upsamples.0.upsamples.0.residual.2.weight``
key (an ``Up_ResidualBlock`` inside another ``nn.Sequential`` of
``Up_ResidualBlock``s) is exactly the signature ``detect_causal3d_vae_config``
already excludes Wan 2.1 detection on -- see ``detect/vae_detect.py``.

**Latent format**: ComfyUI's ``comfy/latent_formats.py`` ``Wan22`` class
*inherits* ``Wan21``'s ``process_in``/``process_out`` (which read
``self.latents_mean``/``self.latents_std``) but its own ``__init__`` never
sets those two attributes -- only ``scale_factor = 1.0``. There is no
per-channel Wan 2.2 mean/std anywhere in ComfyUI to port; verified by reading
the class, not assumed. So Wan 2.2 normalization is plain
``latent * scale_factor`` (``scale_factor=1.0``, i.e. no-op) -- do not invent
48-length mean/std constants to "complete" this; there aren't any upstream.

**Qwen-Image-2.1**: same module shape, different hyper-parameters -- every
class below is parametrised (``dim``/``dec_dim``/``z_dim``/``dim_mult``/
``num_res_blocks``/``temporal_downsample``/``image_channels``/``patch_size``/
``temporal_kernel``), with the constants two paragraphs down as the Wan 2.2
defaults. See ``AutoEncoderCausal3D_2_2``'s docstring and
``detect_qwen_image21_vae_config``.
"""

from __future__ import annotations

import logging
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule
from .causal_3d import (
    AttentionBlock,
    ResidualBlock,
    RMS_norm,
    _causal_conv3d,
    _conv3d_forward,
    _is_causal_conv3d,
)

logger = logging.getLogger(__name__)

# Wan 2.2 VAE architecture constants, verified against the real
# wan2.2_vae.safetensors header: encoder dim=160 (encoder.conv1 out-channels),
# decoder dim=256 (decoder.conv1 out=1024=256*dim_mult[-1]), z_dim=48
# (conv2 channels), dim_mult/temporal flags mirror ComfyUI's ddconfig
# (comfy/sd.py's Wan 2.2 branch) exactly. Also the class defaults below --
# ``AutoEncoderCausal3D_2_2(operations=...)`` with no other kwargs builds
# exactly this shape. Qwen-Image-2.1's VAE reuses every class in this module
# with a different set of these same knobs (``dim``/``dec_dim``/``z_dim``/
# ``dim_mult``/``temporal_downsample``/``image_channels``/``patch_size``/
# ``temporal_kernel``) -- see ``detect_qwen_image21_vae_config`` -- rather
# than a second copy of this file: same nested-``upsamples.upsamples`` block
# structure (patchify/average-pool shortcuts/asymmetric encoder-decoder
# width), verified against ``Comfy-Org/Qwen-Image-2.1``'s
# ``qwen_image_2.1_vae_bf16.safetensors`` header (238 tensors, same key names
# as this module's submodules) -- ComfyUI itself reuses ``vae2_2.WanVAE`` for
# it. The only real differences are RGBA (not RGB) images, no 2x2 patchify,
# and ``temporal_kernel=1`` instead of 3 (every "3x3 spatial" causal conv's
# temporal axis shrinks to 1 -- no causal history needed, ``padding =
# temporal_kernel // 2`` -> 0).
_ENC_DIM = 160
_DEC_DIM = 256
_Z_DIM = 48
_DIM_MULT = (1, 2, 4, 4)
_NUM_RES_BLOCKS = 2
_ATTN_SCALES: tuple[float, ...] = ()
_TEMPORAL_DOWNSAMPLE = (False, True, True)
_IMAGE_CHANNELS = 3
_PATCH_SIZE = 2
_TEMPORAL_KERNEL = 3
_CACHE_T = 2

LATENT_CHANNELS = _Z_DIM
# See module docstring: ComfyUI's Wan22 latent format has no per-channel
# mean/std -- plain scale_factor normalization only. Qwen-Image-2.1's
# per-channel latents_mean/latents_std live on the DiT's ModelSpec
# (``latent_format``), read generically by ``engine.py`` -- not here (see
# ``causal_3d.py``'s LATENTS_MEAN/LATENTS_STD for the sibling family that
# HAS a fixed per-channel format; this one and Wan 2.2 don't).
LATENT_SCALE_FACTOR = 1.0


def _patchify(x: torch.Tensor, patch_size: int = 2) -> torch.Tensor:
    """2x2 space-to-depth on the spatial dims of a (B,C,T,H,W) tensor."""
    if patch_size == 1:
        return x
    b, c, t, h, w = x.shape
    p = patch_size
    x = x.view(b, c, t, h // p, p, w // p, p)
    x = x.permute(0, 1, 4, 6, 2, 3, 5).contiguous()
    return x.view(b, c * p * p, t, h // p, w // p)


def _unpatchify(x: torch.Tensor, patch_size: int = 2) -> torch.Tensor:
    """Inverse of :func:`_patchify`."""
    if patch_size == 1:
        return x
    b, c_pp, t, h, w = x.shape
    p = patch_size
    c = c_pp // (p * p)
    x = x.view(b, c, p, p, t, h, w)
    x = x.permute(0, 1, 4, 5, 2, 6, 3).contiguous()
    return x.view(b, c, t, h * p, w * p)


class Resample2(nn.Module):
    """Wan 2.2's ``Resample``: unlike Wan 2.1's, ``upsample3d``'s spatial conv
    keeps the channel count fixed (``dim -> dim``, not ``dim -> dim//2`` --
    the channel change happens via the parallel ``DupUp3D`` shortcut instead)."""

    def __init__(self, dim: int, mode: str, *, operations: Any, temporal_kernel: int = _TEMPORAL_KERNEL) -> None:
        super().__init__()
        self.mode = mode

        if mode in ("upsample2d", "upsample3d"):
            self.resample = nn.Sequential(
                nn.Upsample(scale_factor=(2.0, 2.0), mode="nearest-exact"),
                operations.Conv2d(dim, dim, 3, padding=1),
            )
            if mode == "upsample3d":
                t_pad = temporal_kernel // 2
                self.time_conv = _causal_conv3d(
                    dim, dim * 2, (temporal_kernel, 1, 1), padding=(t_pad, 0, 0), operations=operations,
                )
        elif mode in ("downsample2d", "downsample3d"):
            self.resample = nn.Sequential(
                nn.ZeroPad2d((0, 1, 0, 1)),
                operations.Conv2d(dim, dim, 3, stride=2),
            )
            if mode == "downsample3d":
                self.time_conv = _causal_conv3d(
                    dim, dim, (temporal_kernel, 1, 1), stride=(2, 1, 1), operations=operations,
                )
        else:
            self.resample = nn.Identity()

    def forward(
        self, x: torch.Tensor, feat_cache: list | None = None, feat_idx: list[int] = [0],  # noqa: B006 (ComfyUI parity)
    ) -> torch.Tensor:
        b, c, t, h, w = x.shape

        if self.mode == "upsample3d" and feat_cache is not None:
            idx = feat_idx[0]
            if feat_cache[idx] is None:
                feat_cache[idx] = "Rep"
                feat_idx[0] += 1
            else:
                cache_x = x[:, :, -_CACHE_T:, :, :].clone()
                prev = feat_cache[idx]
                if cache_x.shape[2] < 2 and prev is not None and prev != "Rep":
                    cache_x = torch.cat([prev[:, :, -1, :, :].unsqueeze(2).to(cache_x.device), cache_x], dim=2)
                if cache_x.shape[2] < 2 and prev == "Rep":
                    cache_x = torch.cat([torch.zeros_like(cache_x), cache_x], dim=2)
                x = _conv3d_forward(self.time_conv, x) if prev == "Rep" else _conv3d_forward(self.time_conv, x, cache_x=prev)
                feat_cache[idx] = cache_x
                feat_idx[0] += 1

                x = x.reshape(b, 2, c, t, h, w)
                x = torch.stack((x[:, 0], x[:, 1]), dim=3)
                x = x.reshape(b, c, t * 2, h, w)

        t = x.shape[2]
        x = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
        x = self.resample(x)
        x = x.reshape(b, t, x.shape[1], x.shape[2], x.shape[3]).permute(0, 2, 1, 3, 4)

        if self.mode == "downsample3d" and feat_cache is not None:
            idx = feat_idx[0]
            if feat_cache[idx] is None:
                feat_cache[idx] = x.clone()
                feat_idx[0] += 1
            else:
                cache_x = x[:, :, -1:, :, :].clone()
                x = _conv3d_forward(self.time_conv, torch.cat([feat_cache[idx][:, :, -1:, :, :], x], dim=2))
                feat_cache[idx] = cache_x
                feat_idx[0] += 1
        return x


class AvgDown3D(nn.Module):
    """Parameter-free average-pooling shortcut (space+time block-mean).
    No learned weights -- contributes no state-dict keys."""

    def __init__(self, in_channels: int, out_channels: int, factor_t: int, factor_s: int = 1) -> None:
        super().__init__()
        self.out_channels = out_channels
        self.factor_t = factor_t
        self.factor_s = factor_s
        self.factor = factor_t * factor_s * factor_s
        self.group_size = in_channels * self.factor // out_channels

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        pad_t = (self.factor_t - x.shape[2] % self.factor_t) % self.factor_t
        x = F.pad(x, (0, 0, 0, 0, pad_t, 0))
        b, c, t, h, w = x.shape
        x = x.view(b, c, t // self.factor_t, self.factor_t, h // self.factor_s, self.factor_s, w // self.factor_s, self.factor_s)
        x = x.permute(0, 1, 3, 5, 7, 2, 4, 6).contiguous()
        x = x.view(b, c * self.factor, t // self.factor_t, h // self.factor_s, w // self.factor_s)
        x = x.view(b, self.out_channels, self.group_size, t // self.factor_t, h // self.factor_s, w // self.factor_s)
        return x.mean(dim=2)


class DupUp3D(nn.Module):
    """Parameter-free nearest-neighbor upsampling shortcut (inverse of
    ``AvgDown3D``: repeat-interleave + reshape). No learned weights."""

    def __init__(self, in_channels: int, out_channels: int, factor_t: int, factor_s: int = 1) -> None:
        super().__init__()
        self.out_channels = out_channels
        self.factor_t = factor_t
        self.factor_s = factor_s
        self.factor = factor_t * factor_s * factor_s
        self.repeats = out_channels * self.factor // in_channels

    def forward(self, x: torch.Tensor, first_chunk: bool = False) -> torch.Tensor:
        x = x.repeat_interleave(self.repeats, dim=1)
        x = x.view(x.size(0), self.out_channels, self.factor_t, self.factor_s, self.factor_s, x.size(2), x.size(3), x.size(4))
        x = x.permute(0, 1, 5, 2, 6, 3, 7, 4).contiguous()
        x = x.view(x.size(0), self.out_channels, x.size(2) * self.factor_t, x.size(4) * self.factor_s, x.size(6) * self.factor_s)
        if first_chunk:
            x = x[:, :, self.factor_t - 1:, :, :]
        return x


class Down_ResidualBlock(nn.Module):
    def __init__(
        self, in_dim: int, out_dim: int, mult: int, *, temporal_downsample: bool, down_flag: bool, operations: Any,
        temporal_kernel: int = _TEMPORAL_KERNEL,
    ) -> None:
        super().__init__()
        self.avg_shortcut = AvgDown3D(
            in_dim, out_dim, factor_t=2 if temporal_downsample else 1, factor_s=2 if down_flag else 1,
        )

        downsamples: list[nn.Module] = []
        cur_in = in_dim
        for _ in range(mult):
            downsamples.append(ResidualBlock(cur_in, out_dim, operations=operations, temporal_kernel=temporal_kernel))
            cur_in = out_dim
        if down_flag:
            mode = "downsample3d" if temporal_downsample else "downsample2d"
            downsamples.append(Resample2(out_dim, mode, operations=operations, temporal_kernel=temporal_kernel))
        self.downsamples = nn.Sequential(*downsamples)

    def forward(self, x: torch.Tensor, feat_cache: list | None, feat_idx: list[int]) -> torch.Tensor:
        x_copy = x
        h = x
        for layer in self.downsamples:
            h = layer(h, feat_cache, feat_idx)
        return h + self.avg_shortcut(x_copy)


class Up_ResidualBlock(nn.Module):
    def __init__(
        self, in_dim: int, out_dim: int, mult: int, *, temporal_upsample: bool, up_flag: bool, operations: Any,
        temporal_kernel: int = _TEMPORAL_KERNEL,
    ) -> None:
        super().__init__()
        self.avg_shortcut = (
            DupUp3D(in_dim, out_dim, factor_t=2 if temporal_upsample else 1, factor_s=2 if up_flag else 1)
            if up_flag else None
        )

        upsamples: list[nn.Module] = []
        cur_in = in_dim
        for _ in range(mult):
            upsamples.append(ResidualBlock(cur_in, out_dim, operations=operations, temporal_kernel=temporal_kernel))
            cur_in = out_dim
        if up_flag:
            mode = "upsample3d" if temporal_upsample else "upsample2d"
            upsamples.append(Resample2(out_dim, mode, operations=operations, temporal_kernel=temporal_kernel))
        self.upsamples = nn.Sequential(*upsamples)

    def forward(
        self, x: torch.Tensor, feat_cache: list | None, feat_idx: list[int], first_chunk: bool = False,
    ) -> torch.Tensor:
        x_main = x
        for layer in self.upsamples:
            x_main = layer(x_main, feat_cache, feat_idx)
        if self.avg_shortcut is not None:
            return x_main + self.avg_shortcut(x, first_chunk)
        return x_main


def _run_seq_with_cache(container: nn.Sequential, x: torch.Tensor, feat_cache, feat_idx, first_chunk: bool = False):
    for layer in container:
        if isinstance(layer, (Down_ResidualBlock,)):
            x = layer(x, feat_cache, feat_idx)
        elif isinstance(layer, Up_ResidualBlock):
            x = layer(x, feat_cache, feat_idx, first_chunk)
        elif isinstance(layer, (ResidualBlock, Resample2)):
            x = layer(x, feat_cache, feat_idx) if feat_cache is not None else layer(x)
        elif _is_causal_conv3d(layer):
            if feat_cache is None:
                x = _conv3d_forward(layer, x)
                continue
            idx = feat_idx[0]
            cache_x = x[:, :, -_CACHE_T:, :, :].clone()
            prev = feat_cache[idx]
            if cache_x.shape[2] < 2 and prev is not None:
                cache_x = torch.cat([prev[:, :, -1, :, :].unsqueeze(2).to(cache_x.device), cache_x], dim=2)
            x = _conv3d_forward(layer, x, cache_x=prev)
            feat_cache[idx] = cache_x
            feat_idx[0] += 1
        else:
            x = layer(x)
    return x


class Encoder3d(nn.Module):
    def __init__(
        self, *, operations: Any,
        dim: int = _ENC_DIM, dim_mult: tuple[int, ...] = _DIM_MULT, num_res_blocks: int = _NUM_RES_BLOCKS,
        temporal_downsample: tuple[bool, ...] = _TEMPORAL_DOWNSAMPLE, patchified_channels: int,
        z_dim: int = _Z_DIM, temporal_kernel: int = _TEMPORAL_KERNEL,
    ) -> None:
        super().__init__()
        dims = [dim * m for m in (1,) + dim_mult]
        t_pad = temporal_kernel // 2
        self.conv1 = _causal_conv3d(
            patchified_channels, dims[0], (temporal_kernel, 3, 3), padding=(t_pad, 1, 1), operations=operations,
        )

        downsamples: list[nn.Module] = []
        out_dim = dims[0]
        for i, (in_dim, out_dim) in enumerate(zip(dims[:-1], dims[1:])):
            t_down = temporal_downsample[i] if i < len(temporal_downsample) else False
            downsamples.append(Down_ResidualBlock(
                in_dim, out_dim, num_res_blocks, temporal_downsample=t_down,
                down_flag=i != len(dim_mult) - 1, operations=operations, temporal_kernel=temporal_kernel,
            ))
        self.downsamples = nn.Sequential(*downsamples)

        self.middle = nn.Sequential(
            ResidualBlock(out_dim, out_dim, operations=operations, temporal_kernel=temporal_kernel),
            AttentionBlock(out_dim, operations=operations),
            ResidualBlock(out_dim, out_dim, operations=operations, temporal_kernel=temporal_kernel),
        )
        self.head = nn.Sequential(
            RMS_norm(out_dim, images=False),
            nn.SiLU(),
            _causal_conv3d(out_dim, z_dim * 2, (temporal_kernel, 3, 3), padding=(t_pad, 1, 1), operations=operations),
        )

    def forward(self, x: torch.Tensor, feat_cache: list | None = None, feat_idx: list[int] = [0]) -> torch.Tensor:  # noqa: B006
        if feat_cache is not None:
            idx = feat_idx[0]
            cache_x = x[:, :, -_CACHE_T:, :, :].clone()
            prev = feat_cache[idx]
            if cache_x.shape[2] < 2 and prev is not None:
                cache_x = torch.cat([prev[:, :, -1, :, :].unsqueeze(2).to(cache_x.device), cache_x], dim=2)
            x = _conv3d_forward(self.conv1, x, cache_x=prev)
            feat_cache[idx] = cache_x
            feat_idx[0] += 1
        else:
            x = _conv3d_forward(self.conv1, x)

        x = _run_seq_with_cache(self.downsamples, x, feat_cache, feat_idx)
        x = _run_seq_with_cache(self.middle, x, feat_cache, feat_idx)
        x = _run_seq_with_cache(self.head, x, feat_cache, feat_idx)
        return x


class Decoder3d(nn.Module):
    def __init__(
        self, *, operations: Any,
        dec_dim: int = _DEC_DIM, dim_mult: tuple[int, ...] = _DIM_MULT, num_res_blocks: int = _NUM_RES_BLOCKS,
        temporal_downsample: tuple[bool, ...] = _TEMPORAL_DOWNSAMPLE, patchified_channels: int,
        z_dim: int = _Z_DIM, temporal_kernel: int = _TEMPORAL_KERNEL,
    ) -> None:
        super().__init__()
        dims = [dec_dim * m for m in (dim_mult[-1],) + dim_mult[::-1]]
        t_pad = temporal_kernel // 2
        self.conv1 = _causal_conv3d(
            z_dim, dims[0], (temporal_kernel, 3, 3), padding=(t_pad, 1, 1), operations=operations,
        )

        self.middle = nn.Sequential(
            ResidualBlock(dims[0], dims[0], operations=operations, temporal_kernel=temporal_kernel),
            AttentionBlock(dims[0], operations=operations),
            ResidualBlock(dims[0], dims[0], operations=operations, temporal_kernel=temporal_kernel),
        )

        upsamples: list[nn.Module] = []
        out_dim = dims[0]
        temporal_upsample = temporal_downsample[::-1]
        for i, (in_dim, out_dim) in enumerate(zip(dims[:-1], dims[1:])):
            t_up = temporal_upsample[i] if i < len(temporal_upsample) else False
            upsamples.append(Up_ResidualBlock(
                in_dim, out_dim, num_res_blocks + 1, temporal_upsample=t_up,
                up_flag=i != len(dim_mult) - 1, operations=operations, temporal_kernel=temporal_kernel,
            ))
        self.upsamples = nn.Sequential(*upsamples)

        self.head = nn.Sequential(
            RMS_norm(out_dim, images=False),
            nn.SiLU(),
            _causal_conv3d(
                out_dim, patchified_channels, (temporal_kernel, 3, 3), padding=(t_pad, 1, 1), operations=operations,
            ),
        )

    def forward(
        self, x: torch.Tensor, feat_cache: list | None = None, feat_idx: list[int] = [0], first_chunk: bool = False,
    ) -> torch.Tensor:
        if feat_cache is not None:
            idx = feat_idx[0]
            cache_x = x[:, :, -_CACHE_T:, :, :].clone()
            prev = feat_cache[idx]
            if cache_x.shape[2] < 2 and prev is not None:
                cache_x = torch.cat([prev[:, :, -1, :, :].unsqueeze(2).to(cache_x.device), cache_x], dim=2)
            x = _conv3d_forward(self.conv1, x, cache_x=prev)
            feat_cache[idx] = cache_x
            feat_idx[0] += 1
        else:
            x = _conv3d_forward(self.conv1, x)

        x = _run_seq_with_cache(self.middle, x, feat_cache, feat_idx)
        x = _run_seq_with_cache(self.upsamples, x, feat_cache, feat_idx, first_chunk)
        x = _run_seq_with_cache(self.head, x, feat_cache, feat_idx)
        return x


def _count_causal_conv3d_v2(module: nn.Module) -> int:
    return sum(1 for m in module.modules() if _is_causal_conv3d(m))


class AutoEncoderCausal3D_2_2(NativeArchModule):
    """Wan 2.2 causal 3D VAE (48ch, patchified, average-pool shortcuts --
    see module docstring) by default; also builds Qwen-Image-2.1's VAE (64ch
    RGBA, no patchify, ``temporal_kernel=1``) when constructed with that
    shape's kwargs (``from_config``, below). Same ``encode``/``decode``
    (video) + ``encode_image``/``decode_image`` (T=1 convenience) API shape
    as ``causal_3d.AutoEncoderCausal3D``.
    """

    def __init__(
        self, *, operations: Any,
        dim: int = _ENC_DIM, dec_dim: int = _DEC_DIM, z_dim: int = _Z_DIM, dim_mult: tuple[int, ...] = _DIM_MULT,
        num_res_blocks: int = _NUM_RES_BLOCKS, temporal_downsample: tuple[bool, ...] = _TEMPORAL_DOWNSAMPLE,
        image_channels: int = _IMAGE_CHANNELS, patch_size: int = _PATCH_SIZE, temporal_kernel: int = _TEMPORAL_KERNEL,
        pad_channel_value: float | None = None,
    ) -> None:
        super().__init__()
        self.patch_size = patch_size
        self.image_channels = image_channels
        self.pad_channel_value = pad_channel_value
        patchified_channels = image_channels * patch_size * patch_size
        self.encoder = Encoder3d(
            operations=operations, dim=dim, dim_mult=dim_mult, num_res_blocks=num_res_blocks,
            temporal_downsample=temporal_downsample, patchified_channels=patchified_channels, z_dim=z_dim,
            temporal_kernel=temporal_kernel,
        )
        self.conv1 = _causal_conv3d(z_dim * 2, z_dim * 2, 1, operations=operations)
        self.conv2 = _causal_conv3d(z_dim, z_dim, 1, operations=operations)
        self.decoder = Decoder3d(
            operations=operations, dec_dim=dec_dim, dim_mult=dim_mult, num_res_blocks=num_res_blocks,
            temporal_downsample=temporal_downsample, patchified_channels=patchified_channels, z_dim=z_dim,
            temporal_kernel=temporal_kernel,
        )

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "AutoEncoderCausal3D_2_2":
        image_channels = config.get("in_channels", _IMAGE_CHANNELS)
        return cls(
            operations=operations,
            dim=config.get("dim", _ENC_DIM),
            dec_dim=config.get("dec_dim", _DEC_DIM),
            z_dim=config.get("latent_channels", _Z_DIM),
            dim_mult=tuple(config.get("dim_mult", _DIM_MULT)),
            num_res_blocks=config.get("num_res_blocks", _NUM_RES_BLOCKS),
            temporal_downsample=tuple(config.get("temporal_downsample", _TEMPORAL_DOWNSAMPLE)),
            image_channels=image_channels,
            patch_size=config.get("patch_size", _PATCH_SIZE),
            temporal_kernel=config.get("temporal_kernel", _TEMPORAL_KERNEL),
            pad_channel_value=1.0 if image_channels == 4 else None,
        )

    def post_load(self) -> None:
        # No computed buffers: same reasoning as causal_3d.AutoEncoderCausal3D
        # -- RMS_norm's params are real loaded weights, causal padding is
        # computed per-forward, and AvgDown3D/DupUp3D carry no parameters.
        return None

    def _pad_missing_channel(self, x: torch.Tensor) -> torch.Tensor:
        """Fill a caller-omitted channel (RGB in on an RGBA VAE) with
        :attr:`pad_channel_value`; no-op when the channel count already
        matches or padding isn't configured for this variant."""
        if self.pad_channel_value is None or x.shape[1] != self.image_channels - 1:
            return x
        pad = torch.full_like(x[:, :1], self.pad_channel_value)
        return torch.cat([x, pad], dim=1)

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """``x``: (B, image_channels, T, H, W) pixels in [-1, 1]; a caller may
        instead pass ``image_channels - 1`` channels (RGB on an RGBA VAE --
        see :attr:`pad_channel_value`). Returns (B, z_dim, T', H/16, W/16)."""
        x = self._pad_missing_channel(x)
        x = _patchify(x, patch_size=self.patch_size)
        t = x.shape[2]
        n_chunks = 1 + (t - 1) // 4
        feat_cache = [None] * _count_causal_conv3d_v2(self.encoder)

        out = None
        for i in range(n_chunks):
            idx = [0]
            if i == 0:
                chunk = self.encoder(x[:, :, :1], feat_cache=feat_cache, feat_idx=idx)
            else:
                chunk = self.encoder(x[:, :, 1 + 4 * (i - 1):1 + 4 * i], feat_cache=feat_cache, feat_idx=idx)
            out = chunk if out is None else torch.cat([out, chunk], dim=2)

        mu, _logvar = _conv3d_forward(self.conv1, out).chunk(2, dim=1)
        return mu

    def new_feat_cache(self) -> list:
        """A fresh causal-conv cache for :meth:`decode`, sized for the decoder.
        See ``AutoEncoderCausal3D.new_feat_cache`` (``causal_3d.py``) -- same
        contract, mutated in place across successive :meth:`decode` calls to
        carry causal state across temporal chunks (``vae/tiling.py::
        chunked_decode_causal3d``). Engine-side wiring is out of scope here.
        """
        return [None] * _count_causal_conv3d_v2(self.decoder)

    def decode(
        self, z: torch.Tensor, feat_cache: list | None = None, first_chunk: bool = True, rgb_only: bool = False,
    ) -> torch.Tensor:
        """``z``: (B, z_dim, T, H, W). Returns (B, image_channels, T', H*16, W*16)
        pixels in [-1, 1] -- RGBA (4 channels), unmodified, for an RGBA VAE
        (see ``rgb_only`` to instead get RGB; this method never composites the
        alpha channel silently).

        ``feat_cache``: pass an external cache from :meth:`new_feat_cache`
        (mutated in place) to decode ``z`` as one temporal chunk of a longer
        clip; ``None`` (default) builds and discards a cache internally, as
        before -- see ``AutoEncoderCausal3D.decode`` for why no alignment is
        needed on the chunk boundaries.

        ``first_chunk``: True only for the call that decodes the GLOBALLY
        first latent frame of the clip (default True -- correct for a single
        whole-clip decode, where ``i == 0`` of the internal loop below always
        is that frame). It controls ``DupUp3D``'s leading-frame trim (the
        first latent frame's temporal upsample doesn't duplicate backward,
        every later group does) and is threaded through to ``Up_ResidualBlock``
        as ``first_chunk and i == 0`` -- so when externally chunking, pass
        ``first_chunk=False`` for every call except the one carrying the
        clip's actual first latent frame, or the trim will fire again on that
        chunk's own local frame 0 and drop a real output frame.

        ``rgb_only``: drop the trailing (alpha) channel and return only the
        first 3, for a caller that wants plain RGB from an RGBA VAE and
        doesn't want to reach into the output itself.
        """
        n_chunks = z.shape[2]
        if feat_cache is None:
            feat_cache = [None] * _count_causal_conv3d_v2(self.decoder)

        x = _conv3d_forward(self.conv2, z)
        out = None
        for i in range(n_chunks):
            idx = [0]
            chunk = self.decoder(
                x[:, :, i:i + 1], feat_cache=feat_cache, feat_idx=idx, first_chunk=(first_chunk and i == 0),
            )
            out = chunk if out is None else torch.cat([out, chunk], dim=2)
        out = _unpatchify(out, patch_size=self.patch_size)
        return out[:, :3] if rgb_only else out

    def encode_image(self, pixels: torch.Tensor) -> torch.Tensor:
        """``pixels``: (B, image_channels, H, W) in [-1, 1]; a caller may
        instead pass ``image_channels - 1`` channels (see :meth:`encode`).
        Returns (B, z_dim, H/16, W/16)."""
        return self.encode(pixels.unsqueeze(2)).squeeze(2)

    def decode_image(self, latent: torch.Tensor, rgb_only: bool = False) -> torch.Tensor:
        """``latent``: (B, z_dim, H, W).
        Returns (B, image_channels, H*16, W*16) in [-1, 1] (see :meth:`decode`)."""
        return self.decode(latent.unsqueeze(2), rgb_only=rgb_only).squeeze(2)
