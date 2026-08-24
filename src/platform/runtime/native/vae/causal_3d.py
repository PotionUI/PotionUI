"""Causal 3D VAE, vendored from ComfyUI's ``comfy/ldm/wan/vae.py`` (the Wan 2.1
shape -- ``CausalConv3d``/``RMS_norm``/``Resample``/``ResidualBlock``/
``AttentionBlock``/``Encoder3d``/``Decoder3d``/``WanVAE``).

**Why this lives under ``vae/`` and not a Wan-specific package**: the local
``models/vae/qwen_image_vae.safetensors`` is architecturally identical to the
Wan 2.1 VAE, verified by header dump against ``comfy/sd.py``'s VAE dispatch --
same signature key (``decoder.middle.0.residual.0.gamma``), same absence of
the nested ``upsamples.upsamples.*`` keys that distinguish the Wan 2.2
variant, same shapes (``dim=96``, ``z_dim=16``, ``dim_mult=[1,2,4,4]``,
``temperal_downsample=[False, True, True]``, image_channels=3). ComfyUI has no
dedicated "Qwen-Image VAE" class at all -- Qwen-Image's checkpoint is a
plain Wan-2.1-shaped VAE, and its latent format (``comfy/latent_formats.py``'s
``Wan21`` -- 16-channel per-channel ``latents_mean``/``latents_std``,
``scale_factor=1.0``) is Wan's too. This module is therefore genuinely shared
between the Krea-2/Qwen-Image image slice and the later Wan 2.2 video slice,
matching the engine plan's intent for ``vae/causal_3d.py``.

**Key parity**: ComfyUI's ``CausalConv3d`` *subclasses* ``Conv3d`` directly, so
a causal conv's checkpoint keys are exactly the conv's own (``conv1.weight``,
``residual.2.weight``, ...) -- there is no wrapper level. Since our ``operations``
namespace is chosen per-load (not known at class-definition time, unlike
ComfyUI's static ``import comfy.ops``), we can't subclass it the same way.
Instead, ``_causal_conv3d`` builds a plain ``operations.Conv3d`` and stashes
the causal-padding amount as a plain attribute (``_causal_time_pad``); a
module-level ``_conv3d_forward`` helper applies the causal left-pad before
calling it. This keeps every parameter at the *exact* same state-dict path
ComfyUI uses -- no wrapper submodule, no rename map needed. ``head``/``middle``
are ``nn.Sequential`` (matching ComfyUI's own containers byte-for-byte in
naming) even though the forward pass iterates them manually (a causal conv
needs the extra ``cache_x`` argument a plain ``Sequential.forward`` can't pass).

**Image vs. video**: the underlying architecture treats an image as a
single-frame video (``T=1``). ``AutoEncoderCausal3D.encode_image``/
``decode_image`` are the image-slice entry points (insert/remove the ``T=1``
axis, giving a plain ``(B, 16, H/8, W/8)`` latent with no temporal dim);
``encode``/``decode`` are the raw ``(B,C,T,H,W)`` video-shaped API for the
future multi-frame Wan/LTX slices, including the chunked ``feat_cache`` decode
path ported from ComfyUI (unused, and never triggered, for a single frame --
``encode``/``decode`` only build a cache when more than one temporal chunk is
needed, which a single image never is).

**Krea-2 coordination**: the squeezed ``(B, 16, H, W)`` shape is not a guess --
it's what ``arch/krea2/model.py:Krea2.build_stream_inputs`` already expects
(``latent: Tensor`` shaped ``(B, C, H, W)``, patchified straight into image
tokens with no temporal axis to strip). ``encode_image``/``decode_image``
plug directly into that contract.

**Tiling**: ``vae/tiling.py``'s ``tiled_encode_causal3d`` /
``tiled_decode_causal3d`` are the 3D-aware spatial encode/decode paths for this
module -- they tile only the H/W axes of the 5D ``(B,C,T,H,W)`` tensor and pass
the full temporal axis through each tile whole, so the ``feat_cache`` temporal
chunking below is untouched (spatial tiling only). Encode engages on an OOM-retry
in the Wan i2v generator (the untiled encode of a 480p+ start frame otherwise
OOMs; encode costs ~2x decode). Decode engages in ``engine.decode`` -- proactively
when the estimated fp32 3D-conv decode spike won't fit live free VRAM (it scales
with output pixels and exceeds a 31GB card past ~1024², caps 12GB cards at 512²),
and as an OOM-retry backstop, both with a shrink-on-OOM tile loop. The generic 2D
``tiled_decode``/``tiled_encode`` in that module are 4D-only and must NOT be
called on this VAE's 5D tensors (they'd silently produce wrong shapes).
"""

from __future__ import annotations

import logging
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule

logger = logging.getLogger(__name__)

# Wan 2.1 / Qwen-Image VAE architecture constants (verified against the real
# qwen_image_vae.safetensors header: dim=96 from decoder.head.0.gamma.shape[0],
# z_dim=16 from conv2's channel count, dim_mult from the down/up block channel
# progression, temperal_downsample from which of the three downsample stages
# carry a time_conv).
_DIM = 96
_Z_DIM = 16
_DIM_MULT = (1, 2, 4, 4)
_NUM_RES_BLOCKS = 2
_ATTN_SCALES: tuple[float, ...] = ()
_TEMPORAL_DOWNSAMPLE = (False, True, True)
_IMAGE_CHANNELS = 3

# Temporal cache window kept between chunks -- ComfyUI's CACHE_T.
_CACHE_T = 2

LATENT_CHANNELS = _Z_DIM
# Wan21 latent format (comfy/latent_formats.py) -- also what Qwen-Image uses,
# there is no separate "QwenImage" latent format in ComfyUI. Per-channel, not
# scalar: encode -> (latent - mean) / std (scale_factor is 1.0 so omitted).
LATENTS_MEAN: tuple[float, ...] = (
    -0.7571, -0.7089, -0.9113, 0.1075, -0.1745, 0.9653, -0.1517, 1.5508,
    0.4134, -0.0715, 0.5517, -0.3632, -0.1922, -0.9497, 0.2503, -0.2921,
)
LATENTS_STD: tuple[float, ...] = (
    2.8184, 1.4541, 2.3275, 2.6558, 1.2196, 1.7708, 2.6052, 2.0743,
    3.2687, 2.1526, 2.8652, 1.5579, 1.6382, 1.1253, 2.8251, 1.9160,
)


def _cat_nonempty(tensors: list[torch.Tensor | None], dim: int) -> torch.Tensor:
    """``torch.cat`` skipping ``None``/empty-along-``dim`` tensors (ComfyUI's
    ``torch_cat_if_needed`` -- also sidesteps an fp8-on-cuda ``torch.cat``
    limitation by avoiding a 1-tensor cat)."""
    xs = [t for t in tensors if t is not None and t.shape[dim] > 0]
    if len(xs) == 1:
        return xs[0]
    return torch.cat(xs, dim=dim)


def _causal_conv3d(
    in_channels: int,
    out_channels: int,
    kernel_size: int | tuple[int, int, int],
    *,
    stride: int | tuple[int, int, int] = 1,
    padding: int | tuple[int, int, int] = 0,
    operations: Any,
) -> nn.Module:
    """Build a causal 3D conv: a plain ``operations.Conv3d`` (no wrapper --
    see module docstring) with only *spatial* padding baked into the layer
    itself; the temporal (causal, left-only) padding amount is stashed as a
    plain attribute and applied by :func:`_conv3d_forward`."""
    if isinstance(padding, int):
        padding = (padding, padding, padding)
    conv = operations.Conv3d(in_channels, out_channels, kernel_size, stride=stride, padding=(0, padding[1], padding[2]))
    conv._causal_time_pad = 2 * padding[0]
    return conv


def _conv3d_forward(conv: nn.Module, x: torch.Tensor, cache_x: torch.Tensor | None = None) -> torch.Tensor:
    """Apply a causal conv built by :func:`_causal_conv3d`, left-padding the
    time axis (optionally consuming a cross-chunk ``cache_x`` instead of
    zeros for the causal history)."""
    time_pad = conv._causal_time_pad
    if time_pad > 0:
        pad_needed = time_pad
        if cache_x is not None:
            cache_x = cache_x.to(device=x.device, dtype=x.dtype)
            pad_needed = max(0, pad_needed - cache_x.shape[2])
        zpad = None
        if pad_needed > 0:
            pad_shape = list(x.shape)
            pad_shape[2] = pad_needed
            zpad = torch.zeros(pad_shape, device=x.device, dtype=x.dtype)
        x = _cat_nonempty([zpad, cache_x, x], dim=2)
    return conv(x)


def _is_causal_conv3d(layer: nn.Module) -> bool:
    return hasattr(layer, "_causal_time_pad")


class RMS_norm(nn.Module):
    """Not built through ``operations`` -- a small elementwise scale (no
    matmul), always resident, matching ComfyUI's own choice not to route it
    through the cast-weight ops layer."""

    def __init__(self, dim: int, *, images: bool = True, bias: bool = False) -> None:
        super().__init__()
        shape = (dim, 1, 1) if images else (dim, 1, 1, 1)
        self.scale = dim ** 0.5
        self.gamma = nn.Parameter(torch.ones(shape))
        self.bias = nn.Parameter(torch.zeros(shape)) if bias else None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = F.normalize(x, dim=1) * self.scale * self.gamma.to(x.dtype)
        if self.bias is not None:
            out = out + self.bias.to(x.dtype)
        return out


class Resample(nn.Module):
    """``mode`` in {"downsample2d", "downsample3d", "upsample2d", "upsample3d"}."""

    def __init__(self, dim: int, mode: str, *, operations: Any) -> None:
        super().__init__()
        self.mode = mode

        if mode in ("upsample2d", "upsample3d"):
            self.resample = nn.Sequential(
                nn.Upsample(scale_factor=(2.0, 2.0), mode="nearest-exact"),
                operations.Conv2d(dim, dim // 2, 3, padding=1),
            )
            if mode == "upsample3d":
                self.time_conv = _causal_conv3d(dim, dim * 2, (3, 1, 1), padding=(1, 0, 0), operations=operations)
        elif mode in ("downsample2d", "downsample3d"):
            self.resample = nn.Sequential(
                nn.ZeroPad2d((0, 1, 0, 1)),
                operations.Conv2d(dim, dim, 3, stride=2),
            )
            if mode == "downsample3d":
                self.time_conv = _causal_conv3d(dim, dim, (3, 1, 1), stride=(2, 1, 1), operations=operations)
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


class ResidualBlock(nn.Module):
    def __init__(self, in_dim: int, out_dim: int, *, operations: Any) -> None:
        super().__init__()
        self.residual = nn.Sequential(
            RMS_norm(in_dim, images=False),
            nn.SiLU(),
            _causal_conv3d(in_dim, out_dim, 3, padding=1, operations=operations),
            RMS_norm(out_dim, images=False),
            nn.SiLU(),
            nn.Dropout(0.0),
            _causal_conv3d(out_dim, out_dim, 3, padding=1, operations=operations),
        )
        self.shortcut = (
            _causal_conv3d(in_dim, out_dim, 1, operations=operations) if in_dim != out_dim else nn.Identity()
        )

    def forward(self, x: torch.Tensor, feat_cache: list | None = None, feat_idx: list[int] = [0]) -> torch.Tensor:  # noqa: B006
        old_x = x
        for layer in self.residual:
            if _is_causal_conv3d(layer) and feat_cache is not None:
                idx = feat_idx[0]
                cache_x = x[:, :, -_CACHE_T:, :, :].clone()
                prev = feat_cache[idx]
                if cache_x.shape[2] < 2 and prev is not None:
                    cache_x = torch.cat([prev[:, :, -1, :, :].unsqueeze(2).to(cache_x.device), cache_x], dim=2)
                x = _conv3d_forward(layer, x, cache_x=prev)
                feat_cache[idx] = cache_x
                feat_idx[0] += 1
            elif _is_causal_conv3d(layer):
                x = _conv3d_forward(layer, x)
            else:
                x = layer(x)
        shortcut = old_x if isinstance(self.shortcut, nn.Identity) else _conv3d_forward(self.shortcut, old_x)
        return x + shortcut


class AttentionBlock(nn.Module):
    """Single-head causal self-attention over spatial positions, per-frame
    (reshapes (B,C,T,H,W) -> (B*T,C,H,W) exactly like ``vae/ae_2d.py``'s 2D
    ``AttnBlock2D`` -- same math, just applied frame-by-frame here)."""

    def __init__(self, dim: int, *, operations: Any) -> None:
        super().__init__()
        self.norm = RMS_norm(dim)
        self.to_qkv = operations.Conv2d(dim, dim * 3, 1)
        self.proj = operations.Conv2d(dim, dim, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        b, c, t, h, w = x.shape
        x = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
        x = self.norm(x)
        q, k, v = self.to_qkv(x).chunk(3, dim=1)

        bt = q.shape[0]
        q = q.view(bt, 1, c, h * w).transpose(2, 3).contiguous()
        k = k.view(bt, 1, c, h * w).transpose(2, 3).contiguous()
        v = v.view(bt, 1, c, h * w).transpose(2, 3).contiguous()
        out = F.scaled_dot_product_attention(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False)
        out = out.transpose(2, 3).reshape(bt, c, h, w)

        out = self.proj(out)
        out = out.reshape(b, t, c, h, w).permute(0, 2, 1, 3, 4)
        return identity + out


def _run_container(
    container: nn.Sequential, x: torch.Tensor, feat_cache: list | None, feat_idx: list[int],
) -> torch.Tensor:
    """Iterate a ``head``/``middle`` ``nn.Sequential`` manually (a causal conv
    needs the extra ``cache_x`` argument a plain ``Sequential.forward`` can't
    pass through)."""
    for layer in container:
        if _is_causal_conv3d(layer):
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
        elif isinstance(layer, ResidualBlock):
            x = layer(x, feat_cache, feat_idx) if feat_cache is not None else layer(x)
        elif isinstance(layer, Resample):
            x = layer(x, feat_cache, feat_idx) if feat_cache is not None else layer(x)
        else:
            x = layer(x)
    return x


class Encoder3d(nn.Module):
    def __init__(
        self, *, dim: int, z_dim: int, input_channels: int, dim_mult: tuple[int, ...],
        num_res_blocks: int, attn_scales: tuple[float, ...], temporal_downsample: tuple[bool, ...],
        operations: Any,
    ) -> None:
        super().__init__()
        dims = [dim * m for m in (1,) + dim_mult]
        scale = 1.0

        self.conv1 = _causal_conv3d(input_channels, dims[0], 3, padding=1, operations=operations)

        downsamples: list[nn.Module] = []
        out_dim = dims[0]
        for i, (in_dim, out_dim) in enumerate(zip(dims[:-1], dims[1:])):
            for _ in range(num_res_blocks):
                downsamples.append(ResidualBlock(in_dim, out_dim, operations=operations))
                if scale in attn_scales:
                    downsamples.append(AttentionBlock(out_dim, operations=operations))
                in_dim = out_dim
            if i != len(dim_mult) - 1:
                mode = "downsample3d" if temporal_downsample[i] else "downsample2d"
                downsamples.append(Resample(out_dim, mode, operations=operations))
                scale /= 2.0
        self.downsamples = nn.Sequential(*downsamples)

        self.middle = nn.Sequential(
            ResidualBlock(out_dim, out_dim, operations=operations),
            AttentionBlock(out_dim, operations=operations),
            ResidualBlock(out_dim, out_dim, operations=operations),
        )

        self.head = nn.Sequential(
            RMS_norm(out_dim, images=False),
            nn.SiLU(),
            _causal_conv3d(out_dim, z_dim, 3, padding=1, operations=operations),
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

        x = _run_container(self.downsamples, x, feat_cache, feat_idx)
        x = _run_container(self.middle, x, feat_cache, feat_idx)
        x = _run_container(self.head, x, feat_cache, feat_idx)
        return x


class Decoder3d(nn.Module):
    def __init__(
        self, *, dim: int, z_dim: int, output_channels: int, dim_mult: tuple[int, ...],
        num_res_blocks: int, attn_scales: tuple[float, ...], temporal_upsample: tuple[bool, ...],
        operations: Any,
    ) -> None:
        super().__init__()
        dims = [dim * m for m in (dim_mult[-1],) + dim_mult[::-1]]
        scale = 1.0 / 2 ** (len(dim_mult) - 2)

        self.conv1 = _causal_conv3d(z_dim, dims[0], 3, padding=1, operations=operations)

        self.middle = nn.Sequential(
            ResidualBlock(dims[0], dims[0], operations=operations),
            AttentionBlock(dims[0], operations=operations),
            ResidualBlock(dims[0], dims[0], operations=operations),
        )

        upsamples: list[nn.Module] = []
        out_dim = dims[0]
        for i, (in_dim, out_dim) in enumerate(zip(dims[:-1], dims[1:])):
            if i in (1, 2, 3):
                in_dim = in_dim // 2
            for _ in range(num_res_blocks + 1):
                upsamples.append(ResidualBlock(in_dim, out_dim, operations=operations))
                if scale in attn_scales:
                    upsamples.append(AttentionBlock(out_dim, operations=operations))
                in_dim = out_dim
            if i != len(dim_mult) - 1:
                mode = "upsample3d" if temporal_upsample[i] else "upsample2d"
                upsamples.append(Resample(out_dim, mode, operations=operations))
                scale *= 2.0
        self.upsamples = nn.Sequential(*upsamples)

        self.head = nn.Sequential(
            RMS_norm(out_dim, images=False),
            nn.SiLU(),
            _causal_conv3d(out_dim, output_channels, 3, padding=1, operations=operations),
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

        x = _run_container(self.middle, x, feat_cache, feat_idx)
        x = _run_container(self.upsamples, x, feat_cache, feat_idx)
        x = _run_container(self.head, x, feat_cache, feat_idx)
        return x


def _count_causal_conv3d(module: nn.Module) -> int:
    return sum(1 for m in module.modules() if _is_causal_conv3d(m))


class AutoEncoderCausal3D(NativeArchModule):
    """Wan-shaped causal 3D VAE (Qwen-Image's checkpoint is this architecture
    verbatim -- see module docstring). ``encode_image``/``decode_image`` are
    the single-frame entry points image-generating callers use; ``encode``/
    ``decode`` are the raw video-shaped API (with chunked ``feat_cache``
    decode, ported from ComfyUI) for later multi-frame reuse.
    """

    def __init__(self, *, operations: Any) -> None:
        super().__init__()
        self.encoder = Encoder3d(
            dim=_DIM, z_dim=_Z_DIM * 2, input_channels=_IMAGE_CHANNELS, dim_mult=_DIM_MULT,
            num_res_blocks=_NUM_RES_BLOCKS, attn_scales=_ATTN_SCALES,
            temporal_downsample=_TEMPORAL_DOWNSAMPLE, operations=operations,
        )
        self.conv1 = _causal_conv3d(_Z_DIM * 2, _Z_DIM * 2, 1, operations=operations)
        self.conv2 = _causal_conv3d(_Z_DIM, _Z_DIM, 1, operations=operations)
        self.decoder = Decoder3d(
            dim=_DIM, z_dim=_Z_DIM, output_channels=_IMAGE_CHANNELS, dim_mult=_DIM_MULT,
            num_res_blocks=_NUM_RES_BLOCKS, attn_scales=_ATTN_SCALES,
            temporal_upsample=tuple(reversed(_TEMPORAL_DOWNSAMPLE)), operations=operations,
        )

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "AutoEncoderCausal3D":
        return cls(operations=operations)

    def post_load(self) -> None:
        # No computed buffers: RMS_norm's gamma/bias are real loaded
        # parameters (not derived), and the causal convs' left-padding is
        # computed per-forward from their own padding config, not cached.
        return None

    # -- video-shaped API (chunked, feat_cache) ------------------------------

    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """``x``: (B, 3, T, H, W) pixels in [-1, 1]. Returns (B, 16, T', H/8, W/8)."""
        t = x.shape[2]
        n_chunks = 1 + (t - 1) // 4
        feat_cache: list | None = [None] * _count_causal_conv3d(self.decoder) if n_chunks > 1 else None

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

        Pass the same list to successive :meth:`decode` calls (it's mutated in
        place) to carry causal-conv state across temporal chunks of a longer
        clip instead of decoding the whole ``T`` axis in one call -- see
        ``vae/tiling.py::chunked_decode_causal3d``. Engine-side wiring (when
        to chunk, chunk-size selection) is out of scope here; this is only the
        primitive the orchestration layer would call into.
        """
        return [None] * _count_causal_conv3d(self.decoder)

    def decode(self, z: torch.Tensor, feat_cache: list | None = None) -> torch.Tensor:
        """``z``: (B, 16, T, H, W). Returns (B, 3, T', H*8, W*8) pixels in [-1, 1].

        ``feat_cache``: normally left ``None`` -- a cache is then built (or not,
        for a single latent frame) internally and discarded after this call, as
        before. Pass an external cache from :meth:`new_feat_cache` (mutated in
        place) to decode ``z`` as one temporal chunk of a longer clip whose
        causal state is meant to continue into a following call; every latent
        frame is already decoded one at a time internally (``n_chunks =
        z.shape[2]`` below), so an externally-chunked ``z`` needs no special
        alignment -- unlike :meth:`encode`'s 1-then-4 grouping, which is an
        artifact of the encoder's 4x temporal downsample and has no decode-side
        analogue (a decoder latent frame already IS the causal-chunk unit).
        """
        n_chunks = z.shape[2]
        if feat_cache is None:
            feat_cache = [None] * _count_causal_conv3d(self.decoder) if n_chunks > 1 else None
        # else: caller-supplied cache, used regardless of this call's own
        # n_chunks (it may itself be a single-frame chunk of a larger clip).

        x = _conv3d_forward(self.conv2, z)
        out = None
        for i in range(n_chunks):
            idx = [0]
            chunk = self.decoder(x[:, :, i:i + 1], feat_cache=feat_cache, feat_idx=idx)
            out = chunk if out is None else torch.cat([out, chunk], dim=2)
        return out

    # -- image-shaped convenience API ----------------------------------------

    def encode_image(self, pixels: torch.Tensor) -> torch.Tensor:
        """``pixels``: (B, 3, H, W) in [-1, 1]. Returns (B, 16, H/8, W/8)."""
        latent = self.encode(pixels.unsqueeze(2))
        return latent.squeeze(2)

    def decode_image(self, latent: torch.Tensor) -> torch.Tensor:
        """``latent``: (B, 16, H, W). Returns (B, 3, H*8, W*8) in [-1, 1]."""
        pixels = self.decode(latent.unsqueeze(2))
        return pixels.squeeze(2)
