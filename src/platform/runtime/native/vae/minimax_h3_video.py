# Derived from: diffusers `autoencoder_kl_minimax_h3.py` (Apache-2.0,
# "Copyright 2026 The MiniMax and HuggingFace Teams") — the causal-3D-conv
# encoder, ViT decoder, temporal chunking/token-drop math, and spatial tiling
# are ported from that file. Module/attribute names below target the
# Comfy-Org single-file repack (`minimax_h3_video_vae_fp16.safetensors`)
# instead, which differs from the diffusers class in several places (see
# "Repack vs. diffusers" below) — verified against the repack's own
# safetensors header (`ai/minimax_h3/video_vae_header.json`, 562 keys,
# every one accounted for by this module's parameter/buffer count).
"""MiniMax-H3 video VAE: causal-3D-conv encoder, ViT decoder.

**Repack vs. diffusers (the discrepancies a port must reconcile):**

1. **Encoder naming.** The repack uses old-style (`AutoencoderKLLegacy`,
   per the checkpoint's own embedded `source_config`) key paths:
   `encoder.down.{i}.block.{j}.*` instead of diffusers'
   `encoder.down_blocks.{i}.resnets.{j}.*`, `nin_shortcut` instead of
   `conv_shortcut`, and a bare `encoder.down.{i}.downsample.conv.*` instead of
   a `downsamplers` list. Structurally identical module, different attribute
   names — this file uses the repack's names throughout.
2. **Decoder naming.** `proj_in` -> `x_embedder`; fused `attn.to_qkv`
   (`[3*inner_dim, dim]`) instead of split `to_q`/`to_k`/`to_v`; flat
   `attn.to_out` (a bare `Linear`) instead of `to_out` being a
   `ModuleList([Linear, Dropout])`; `ff.w1`/`ff.w2` instead of the SwiGLU
   `FeedForward`'s `ff.net.0.proj`/`ff.net.2` (same math — `ff.w1` IS that
   fused `[2*inner, dim]` SwiGLU projection, chunked into `(value, gate)`).
3. **`mask_token` is a REAL loaded parameter**, not diffusers' `cls_token`
   (a `torch.zeros_like(...)` computed fresh every forward). The repack
   checkpoint carries `decoder.mask_token` as an actual `[1, 1, dim]` weight
   — this module registers it as an `nn.Parameter` and uses ITS value
   (loaded from the checkpoint) at the position diffusers hardcodes to zero.
   Skipping this (i.e. porting diffusers' zero-literal verbatim) would leave
   `decoder.mask_token` an unused/unexpected key and silently drop whatever
   the checkpoint actually trained there.
4. **`latents_mean`/`latents_std` are REAL top-level tensor keys** in the
   repack (`[24]` each), not diffusers-config-only floats with no state-dict
   presence. Registered here as persistent buffers so they load like any
   other weight; `encode`/`decode` still do NOT apply them (pipe-level
   concern per the port plan), but a pipe can now read the real per-checkpoint
   values off `module.latents_mean`/`.latents_std` instead of hardcoding them.

**Precision.** The repack ships fp16 (unlike the original HF checkpoint,
which is fp32) — `NativeEngineLoader._ops_for` already selects `manual_cast`
whenever storage dtype != compute dtype, which is the "fp16/bf16 autocast
over the checkpoint's own weights" recipe the dossier calls for; no
module-level dtype pinning is needed here (contrast the audio VAE, which
IS fp32-stored and DOES need an explicit fp32 pin — see
`minimax_h3_audio.py`).

**Posterior.** Like the other native VAEs in this package (`causal_3d.py`,
`ae_2d.py`), there is no separate `DiagonalGaussianDistribution` object —
`encode` defaults to returning the mode (mean half of the `2*latent_channels`
moments) directly, matching the house convention and this VAE's general
usage in the reference pipeline. The ONE exception is `encode_vae_condition`
(fl2va keyframe-anchor conditioning), which samples the posterior under a
fixed seed — `encode`'s `sample_posterior`/`generator` kwargs implement that
one path's math (mirroring `DiagonalGaussianDistribution.sample()` exactly)
without turning the default call into anything other than the mode.

**Chunking (dossier §D.1 / port plan S3), all fixed by `clip_length=17` and
`temporal_compression_ratio=4`:

    frame_pre_padding = (-clip_length) % temporal_ratio        = 3
    tokens_chunk_size = ceil(clip_length / temporal_ratio)     = 5
    token_overlap     = (-token_drop) % tokens_chunk_size      = 2
    frame_overlap     = max(token_overlap*temporal_ratio - frame_pre_padding, 0) = 5

`encode` pads the pixel input up to a multiple of 17 by repeating the last
frame, encodes 17-frame chunks independently, concatenates, and drops the
trailing `token_drop=3` latent frames ONCE (not per-chunk — see
`video_latent_frame_count`'s docstring for why that distinction is
behavior-changing). `decode` re-decodes each chunk with `token_overlap=2`
extra latent frames of look-ahead and linearly cross-fades the resulting
`frame_overlap=5`-frame pixel overlap against the previous chunk. `num_frames
== 1` bypasses all of this — a single frame goes straight through the
spatial encoder with no chunk padding (padding it to 17 identical frames
would run the temporal path over duplicate content and return the wrong
latent-frame count; see `_encode`'s docstring).

Spatial tiling (256px tile, 64px overlap) is ON by default, matching the
reference (the released frames ARE the tiled/blended ones).
"""

from __future__ import annotations

import math
from typing import Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..base import NativeArchModule

# -- fixed H3 video-VAE geometry (single released variant) ------------------

LATENT_CHANNELS = 24
IN_CHANNELS = 3
OUT_CHANNELS = 3
BLOCK_OUT_CHANNELS: tuple[int, ...] = (128, 256, 256, 512, 512, 1024)
LAYERS_PER_BLOCK = 2
SPATIAL_DOWNSAMPLE_FACTORS: tuple[int, ...] = (2, 2, 2, 2, 1, 1)
TEMPORAL_DOWNSAMPLE_FACTORS: tuple[int, ...] = (1, 2, 2, 1, 1, 1)
NORM_NUM_GROUPS = 32
NORM_EPS = 1e-6

DECODER_NUM_LAYERS = 36
DECODER_NUM_ATTENTION_HEADS = 32
DECODER_ATTENTION_HEAD_DIM = 64
DECODER_DIM = DECODER_NUM_ATTENTION_HEADS * DECODER_ATTENTION_HEAD_DIM  # 2048
DECODER_NUM_REGISTER_TOKENS = 4
DECODER_FFN_MULT = 4  # inner_dim = DECODER_DIM * DECODER_FFN_MULT = 8192
DECODER_ROPE_THETA = 100.0
DECODER_ROPE_DIM_RATIO = 0.75
DECODER_NORM_EPS = 1e-5

SPATIAL_COMPRESSION_RATIO = math.prod(SPATIAL_DOWNSAMPLE_FACTORS)   # 16
TEMPORAL_COMPRESSION_RATIO = math.prod(TEMPORAL_DOWNSAMPLE_FACTORS)  # 4
CLIP_LENGTH = 17
TOKEN_DROP = 3

TILE_SAMPLE_MIN_HEIGHT = 256
TILE_SAMPLE_MIN_WIDTH = 256
TILE_SAMPLE_MIN_OVERLAP_HEIGHT = 64
TILE_SAMPLE_MIN_OVERLAP_WIDTH = 64


def _randn_like_reference(
    shape: torch.Size, *, generator: torch.Generator | None, device: torch.device, dtype: torch.dtype,
) -> torch.Tensor:
    """Port of diffusers' ``randn_tensor`` (single-generator case only --
    MiniMax-H3's own conditioning code never passes a per-batch-item
    generator list, so that branch is not ported).

    A CPU ``generator`` forces the draw onto CPU even when ``device`` is
    CUDA (then moves the result), so a fixed-seed CPU generator produces the
    SAME noise regardless of what device the tensor ends up on -- this is
    what lets a caller pass a CPU ``torch.Generator().manual_seed(42)`` and
    get reproducible conditioning noise on any GPU. A CUDA generator whose
    device doesn't match the target device raises, matching the reference.
    """
    rand_device = device
    if generator is not None:
        gen_device = generator.device
        if gen_device.type != device.type and gen_device.type == "cpu":
            rand_device = torch.device("cpu")
        elif gen_device.type != device.type and gen_device.type == "cuda":
            raise ValueError(f"Cannot generate a {device} tensor from a generator of type {gen_device.type}.")
    noise = torch.randn(shape, generator=generator, device=rand_device, dtype=dtype)
    return noise.to(device)


def video_latent_frame_count(num_pixel_frames: int, *, clip_length: int = CLIP_LENGTH, token_drop: int = TOKEN_DROP) -> int:
    """Pixel-frame count -> latent-frame count, mirroring `_encode`'s chunk math.

    `num_pixel_frames == 1` bypasses temporal chunking entirely (single-frame
    fast path) and returns 1 latent frame. Otherwise the general closed form
    is ``L = 5 * ceil(P / 17) - 3`` (`5 = tokens_chunk_size`, `3 = token_drop`
    — the `17*n + 5 -> 5*n + 2` example in the reference docstring is this
    formula evaluated at `P = 17*n + 5`, since `ceil((17*n+5)/17) = n+1`).
    """
    if num_pixel_frames <= 1:
        return 1
    tokens_chunk_size = math.ceil(clip_length / TEMPORAL_COMPRESSION_RATIO)
    num_chunks = math.ceil(num_pixel_frames / clip_length)
    return max(num_chunks * tokens_chunk_size - token_drop, 0)


# -- causal 3D conv (reflect spatial pad, causal left-only temporal pad) ----

def _causal_conv3d(
    in_channels: int, out_channels: int, kernel_size: int, *,
    stride: int | tuple[int, int, int] = 1, spatial_padding: int = 0, temporal_padding: int = 0,
    operations: Any,
) -> nn.Module:
    conv = operations.Conv3d(in_channels, out_channels, kernel_size, stride=stride, padding=0)
    conv._h3_spatial_padding = spatial_padding
    conv._h3_temporal_padding = temporal_padding
    return conv


def _causal_conv3d_forward(conv: nn.Module, x: torch.Tensor) -> torch.Tensor:
    pad = conv._h3_spatial_padding
    if pad > 0:
        x = F.pad(x, (pad, pad, pad, pad, 0, 0), mode="reflect")
    tpad = conv._h3_temporal_padding
    if tpad > 0:
        x = F.pad(x, (0, 0, 0, 0, tpad, 0), mode="constant")
    return conv(x)


def _group_norm_isolated(norm: nn.Module, x: torch.Tensor) -> torch.Tensor:
    """Time-isolated GroupNorm: fold T into the batch axis so statistics
    never mix across latent frames (`use_t_isolated_gn` in the checkpoint's
    embedded config)."""
    b, c, t, h, w = x.shape
    x = x.permute(0, 2, 1, 3, 4).reshape(b * t, c, h, w)
    x = norm(x)
    return x.view(b, t, c, h, w).permute(0, 2, 1, 3, 4)


class _Downsample3d(nn.Module):
    """Strided 3x3x3 downsample. Spatial stride 2 is preceded by an
    asymmetric bottom/right reflect pad of 1 (the conv itself carries no
    spatial padding), so output size is exactly `ceil(size / 2)`."""

    def __init__(self, in_channels: int, out_channels: int, *, temporal_stride: int, spatial_stride: int, operations: Any) -> None:
        super().__init__()
        self.spatial_stride = spatial_stride
        self.conv = _causal_conv3d(
            in_channels, out_channels, 3,
            stride=(temporal_stride, spatial_stride, spatial_stride),
            spatial_padding=0, temporal_padding=2, operations=operations,
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if self.spatial_stride == 2:
            x = F.pad(x, (0, 1, 0, 1, 0, 0), mode="reflect")
        return _causal_conv3d_forward(self.conv, x)


class _ResnetBlock3d(nn.Module):
    def __init__(self, in_channels: int, out_channels: int, *, norm_num_groups: int, norm_eps: float, operations: Any) -> None:
        super().__init__()
        self.norm1 = operations.GroupNorm(norm_num_groups, in_channels, eps=norm_eps, affine=True)
        self.conv1 = _causal_conv3d(in_channels, out_channels, 3, spatial_padding=1, temporal_padding=2, operations=operations)
        self.norm2 = operations.GroupNorm(norm_num_groups, out_channels, eps=norm_eps, affine=True)
        self.conv2 = _causal_conv3d(out_channels, out_channels, 3, spatial_padding=1, temporal_padding=2, operations=operations)
        self.nin_shortcut = None
        if in_channels != out_channels:
            self.nin_shortcut = operations.Conv3d(in_channels, out_channels, kernel_size=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x
        h = F.silu(_group_norm_isolated(self.norm1, x))
        h = _causal_conv3d_forward(self.conv1, h)
        h = F.silu(_group_norm_isolated(self.norm2, h))
        h = _causal_conv3d_forward(self.conv2, h)
        if self.nin_shortcut is not None:
            residual = self.nin_shortcut(residual)
        return residual + h


class _DownLevel3d(nn.Module):
    def __init__(
        self, in_channels: int, out_channels: int, num_layers: int, *,
        temporal_downsample_factor: int, spatial_downsample_factor: int,
        norm_num_groups: int, norm_eps: float, operations: Any,
    ) -> None:
        super().__init__()
        self.block = nn.ModuleList([
            _ResnetBlock3d(
                in_channels if i == 0 else out_channels, out_channels,
                norm_num_groups=norm_num_groups, norm_eps=norm_eps, operations=operations,
            )
            for i in range(num_layers)
        ])
        self.downsample = None
        if temporal_downsample_factor * spatial_downsample_factor > 1:
            self.downsample = _Downsample3d(
                out_channels, out_channels,
                temporal_stride=temporal_downsample_factor, spatial_stride=spatial_downsample_factor,
                operations=operations,
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        for block in self.block:
            x = block(x)
        if self.downsample is not None:
            x = self.downsample(x)
        return x


class _VideoEncoder3d(nn.Module):
    def __init__(
        self, *, block_out_channels: tuple[int, ...], layers_per_block: int,
        spatial_downsample_factors: tuple[int, ...], temporal_downsample_factors: tuple[int, ...],
        latent_channels: int, norm_num_groups: int, norm_eps: float, operations: Any,
    ) -> None:
        super().__init__()
        self.conv_in = _causal_conv3d(IN_CHANNELS, block_out_channels[0], 3, spatial_padding=1, temporal_padding=2, operations=operations)

        block_in_channels = (block_out_channels[0],) + tuple(block_out_channels[:-1])
        self.down = nn.ModuleList([
            _DownLevel3d(
                block_in_channels[i], block_out_channels[i], layers_per_block,
                temporal_downsample_factor=temporal_downsample_factors[i],
                spatial_downsample_factor=spatial_downsample_factors[i],
                norm_num_groups=norm_num_groups, norm_eps=norm_eps, operations=operations,
            )
            for i in range(len(block_out_channels))
        ])

        self.norm_out = operations.GroupNorm(norm_num_groups, block_out_channels[-1], eps=norm_eps, affine=True)
        self.conv_out = _causal_conv3d(block_out_channels[-1], 2 * latent_channels, 3, spatial_padding=1, temporal_padding=2, operations=operations)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = _causal_conv3d_forward(self.conv_in, x)
        for level in self.down:
            x = level(x)
        x = F.silu(_group_norm_isolated(self.norm_out, x))
        return _causal_conv3d_forward(self.conv_out, x)


# -- ViT decoder --------------------------------------------------------

def _rms_norm_fp32(x: torch.Tensor, eps: float) -> torch.Tensor:
    """Parameterless RMSNorm, computed in fp32 regardless of the running
    compute dtype (per-head q/k norm — no `elementwise_affine`, no state-dict
    key, matches the header's absence of `norm_q`/`norm_k` weights)."""
    dtype = x.dtype
    xf = x.float()
    xf = xf * torch.rsqrt(xf.pow(2).mean(dim=-1, keepdim=True) + eps)
    return xf.to(dtype)


def _rms_norm_affine_fp32(weight: torch.Tensor, x: torch.Tensor, eps: float) -> torch.Tensor:
    """Affine RMSNorm, computed in fp32 regardless of the running compute
    dtype ("The reference normalizes in float32 regardless of the compute
    dtype" — reference comment on both `TransformerBlock.forward` and
    `AttnProcessor.__call__`). Reads `weight` directly rather than calling the
    `operations.RMSNorm` module's own forward, so this is correct under every
    ops mode (`manual_cast` casts a module's weight to the ACTIVATION's
    dtype, which is the opposite of what "always fp32" needs)."""
    dtype = x.dtype
    xf = x.float()
    xf = xf * torch.rsqrt(xf.pow(2).mean(dim=-1, keepdim=True) + eps)
    return (xf * weight.float()).to(dtype)


class _VideoRotaryPosEmbed(nn.Module):
    """3-axis RoPE for the ViT decoder. `dim` is `rotary_dim` (already
    `int(head_dim * rope_dim_ratio)`), NOT the full head dim — the caller
    only rotates the first `rotary_dim` channels of each head."""

    def __init__(self, dim: int, *, theta: float, num_axes: int = 3) -> None:
        super().__init__()
        self.dim = dim
        self.theta = theta
        self.num_axes = num_axes
        self.register_buffer("inv_freq", self._compute_inv_freq(), persistent=False)

    def _compute_inv_freq(self) -> torch.Tensor:
        return 1.0 / self.theta ** torch.arange(0, 1, 2 * self.num_axes / self.dim, dtype=torch.float32)

    def post_load(self) -> None:
        self.inv_freq = self._compute_inv_freq().to(self.inv_freq.device)

    def forward(self, position_ids: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        angles = 2.0 * math.pi * position_ids[:, :, :, None] * self.inv_freq[None, None, None, :]
        angles = angles.flatten(2, 3).tile(2).unsqueeze(2)
        return angles.cos(), angles.sin()


class _VideoDecoderAttention(nn.Module):
    """Full bidirectional attention, fused `to_qkv` (repack layout — see
    module docstring point 2). `qkv.reshape(b, n, heads, 3, head_dim)` splits
    the fused projection PER-HEAD-INTERLEAVED — `[head0: q|k|v (head_dim
    each) | head1: q|k|v | ...]` — verified against ComfyUI's real
    `comfy/ldm/minimax/vae.py` `Attention.forward`
    (``qkv.view(b, s, -1, 3*dim_head)`` then ``chunk(3, dim=-1)``, which
    resolves the `-1` to `heads` and only THEN splits each head's own
    `3*dim_head` span into q/k/v). A previous version of this port grouped
    q/k/v as three big contiguous blocks across ALL heads instead
    (`reshape(b, n, 3, heads, head_dim)`) — a DIFFERENT physical row mapping
    of the same fused weight, silently assigning the wrong output neurons to
    the wrong (head, q/k/v) slot for every head but the first. Same bug
    class, same root cause (an unfused-reference convention copied without
    cross-checking the actual repack consumer) as the DiT's SwiGLU gate/value
    swap (`arch/minimax_h3/model.py`'s `MiniMaxH3MLP`)."""

    def __init__(self, dim: int, heads: int, dim_head: int, *, eps: float, operations: Any) -> None:
        super().__init__()
        self.heads = heads
        self.dim_head = dim_head
        self.eps = eps
        inner_dim = heads * dim_head
        self.to_qkv = operations.Linear(dim, inner_dim * 3, bias=True)
        self.to_out = operations.Linear(inner_dim, dim, bias=True)

    def forward(self, x: torch.Tensor, rotary_emb: tuple[torch.Tensor, torch.Tensor] | None) -> torch.Tensor:
        b, n, _ = x.shape
        qkv = self.to_qkv(x).reshape(b, n, self.heads, 3, self.dim_head)
        query, key, value = qkv.unbind(dim=3)  # each (b, n, heads, dim_head)

        query = _rms_norm_fp32(query, self.eps)
        key = _rms_norm_fp32(key, self.eps)

        if rotary_emb is not None:
            cos, sin = rotary_emb
            cos = cos.to(query.dtype)
            sin = sin.to(query.dtype)
            rd = cos.shape[-1]
            q_rot, q_pass = query[..., :rd], query[..., rd:]
            k_rot, k_pass = key[..., :rd], key[..., rd:]
            q1, q2 = q_rot.chunk(2, dim=-1)
            k1, k2 = k_rot.chunk(2, dim=-1)
            q_rotated = torch.cat([-q2, q1], dim=-1)
            k_rotated = torch.cat([-k2, k1], dim=-1)
            query = torch.cat([q_rot * cos + q_rotated * sin, q_pass], dim=-1)
            key = torch.cat([k_rot * cos + k_rotated * sin, k_pass], dim=-1)

        query = query.transpose(1, 2)
        key = key.transpose(1, 2)
        value = value.transpose(1, 2)
        out = F.scaled_dot_product_attention(query, key, value, attn_mask=None)
        out = out.transpose(1, 2).flatten(2, 3)
        return self.to_out(out)


class _VideoDecoderFeedForward(nn.Module):
    """SwiGLU, repack layout: `w1` is the fused `[2*inner_dim, dim]`
    projection, chunked into `(gate, value)` -- GATE FIRST, value second --
    `silu(gate) * value`, `w2` projects back down. Verified against
    ComfyUI's real `comfy/ldm/minimax/vae.py` `FeedForward.forward`:
    ``gate, x = self.w1(x).chunk(2, dim=-1); return self.w2(F.silu(gate).mul_(x))``.
    A previous version of this port instead copied diffusers' own (unfused,
    value-first/gate-second) `SwiGLU` convention -- backwards for this
    checkpoint, and the same class of bug (same root cause, same fix
    pattern) as the DiT's `MiniMaxH3MLP` gate/value swap
    (`arch/minimax_h3/model.py`). The FFN is a per-patch-token op, run once
    per ViT decoder patch (16px spatially) -- this is what produced the
    16px-grid "hundreds of squares" structured-noise symptom, not a crash or
    a shape mismatch (confirmed against a saved real generation's frame:
    measured square period 16px, matching this component's patch size
    exactly)."""

    def __init__(self, dim: int, inner_dim: int, *, operations: Any) -> None:
        super().__init__()
        self.w1 = operations.Linear(dim, inner_dim * 2, bias=True)
        self.w2 = operations.Linear(inner_dim, dim, bias=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        gate, value = self.w1(x).chunk(2, dim=-1)
        return self.w2(F.silu(gate) * value)


class _VideoDecoderBlock(nn.Module):
    def __init__(self, dim: int, heads: int, dim_head: int, ffn_inner_dim: int, eps: float, *, operations: Any) -> None:
        super().__init__()
        self.norm1 = operations.RMSNorm(dim, eps=eps)
        self.attn = _VideoDecoderAttention(dim, heads, dim_head, eps=eps, operations=operations)
        self.scale1 = nn.Parameter(torch.zeros(dim))
        self.norm2 = operations.RMSNorm(dim, eps=eps)
        self.ff = _VideoDecoderFeedForward(dim, ffn_inner_dim, operations=operations)
        self.scale2 = nn.Parameter(torch.zeros(dim))
        self.eps = eps

    def forward(self, x: torch.Tensor, rotary_emb: tuple[torch.Tensor, torch.Tensor] | None) -> torch.Tensor:
        n1 = _rms_norm_affine_fp32(self.norm1.weight, x, self.eps)
        x = x + self.attn(n1, rotary_emb) * self.scale1
        n2 = _rms_norm_affine_fp32(self.norm2.weight, x, self.eps)
        x = x + self.ff(n2) * self.scale2
        return x


class _VideoViTDecoder3d(nn.Module):
    def __init__(
        self, *, in_channels: int, out_channels: int, patch_size: int, patch_size_t: int,
        num_layers: int, num_heads: int, head_dim: int, num_register_tokens: int,
        ffn_mult: int, rope_theta: float, rope_dim_ratio: float, norm_eps: float, operations: Any,
    ) -> None:
        super().__init__()
        dim = num_heads * head_dim
        self.patch_size = patch_size
        self.patch_size_t = patch_size_t
        self.out_channels = out_channels
        self.num_register_tokens = num_register_tokens

        self.rope = _VideoRotaryPosEmbed(int(head_dim * rope_dim_ratio), theta=rope_theta)
        self.x_embedder = operations.Linear(in_channels, dim, bias=True)
        self.register_tokens = nn.Parameter(torch.zeros(1, num_register_tokens, dim))
        # Real loaded parameter -- NOT diffusers' computed torch.zeros_like
        # cls token. See module docstring point 3.
        self.mask_token = nn.Parameter(torch.zeros(1, 1, dim))
        self.transformer_blocks = nn.ModuleList([
            _VideoDecoderBlock(dim, num_heads, head_dim, dim * ffn_mult, norm_eps, operations=operations)
            for _ in range(num_layers)
        ])
        self.norm_out = operations.LayerNorm(dim, eps=norm_eps, elementwise_affine=True)
        self.proj_out = operations.Linear(dim, out_channels * patch_size_t * patch_size * patch_size, bias=True)

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        b, c, f, h, w = z.shape
        x = z.permute(0, 2, 3, 4, 1).reshape(b, f * h * w, c)
        x = self.x_embedder(x)
        num_patches = x.shape[1]

        register_tokens = self.register_tokens.expand(b, -1, -1).to(x.dtype)
        mask_token = self.mask_token.expand(b, -1, -1).to(x.dtype)
        x = torch.cat([x, register_tokens, mask_token], dim=1)

        grids = [
            2.0 * (torch.arange(0.5, size, dtype=torch.float32, device=x.device) / size) - 1.0
            for size in (f, h, w)
        ]
        position_ids = torch.stack(torch.meshgrid(*grids, indexing="ij"), dim=-1).flatten(0, 2)
        position_ids = position_ids.unsqueeze(0).expand(b, -1, -1)
        suffix = position_ids.new_zeros((b, self.num_register_tokens + 1, 3))
        position_ids = torch.cat([position_ids, suffix], dim=1)
        rotary_emb = self.rope(position_ids)

        for block in self.transformer_blocks:
            x = block(x, rotary_emb)

        x = self.norm_out(x)
        x = self.proj_out(x)
        x = x[:, :num_patches, :]

        ps, pst = self.patch_size, self.patch_size_t
        x = x.view(b, f, h, w, self.out_channels, pst, ps, ps)
        x = x.permute(0, 4, 1, 5, 2, 6, 3, 7).contiguous()
        return x.reshape(b, self.out_channels, f * pst, h * ps, w * ps)


class MiniMaxH3VideoVAE(NativeArchModule):
    """Causal-3D-conv encoder + ViT decoder. Spatial tiling ON by default
    (`use_tiling`); temporal chunking is unconditional (see module docstring)
    and NOT gated by a flag -- `_encode`/`_decode` always chunk when
    `num_frames > 1`."""

    def __init__(
        self, *,
        latent_channels: int = LATENT_CHANNELS,
        block_out_channels: tuple[int, ...] = BLOCK_OUT_CHANNELS,
        layers_per_block: int = LAYERS_PER_BLOCK,
        spatial_downsample_factors: tuple[int, ...] = SPATIAL_DOWNSAMPLE_FACTORS,
        temporal_downsample_factors: tuple[int, ...] = TEMPORAL_DOWNSAMPLE_FACTORS,
        norm_num_groups: int = NORM_NUM_GROUPS,
        norm_eps: float = NORM_EPS,
        decoder_num_layers: int = DECODER_NUM_LAYERS,
        decoder_num_attention_heads: int = DECODER_NUM_ATTENTION_HEADS,
        decoder_attention_head_dim: int = DECODER_ATTENTION_HEAD_DIM,
        decoder_num_register_tokens: int = DECODER_NUM_REGISTER_TOKENS,
        decoder_ffn_mult: int = DECODER_FFN_MULT,
        decoder_rope_theta: float = DECODER_ROPE_THETA,
        decoder_rope_dim_ratio: float = DECODER_ROPE_DIM_RATIO,
        decoder_norm_eps: float = DECODER_NORM_EPS,
        clip_length: int = CLIP_LENGTH,
        token_drop: int = TOKEN_DROP,
        tile_sample_min_height: int = TILE_SAMPLE_MIN_HEIGHT,
        tile_sample_min_width: int = TILE_SAMPLE_MIN_WIDTH,
        tile_sample_min_overlap_height: int = TILE_SAMPLE_MIN_OVERLAP_HEIGHT,
        tile_sample_min_overlap_width: int = TILE_SAMPLE_MIN_OVERLAP_WIDTH,
        operations: Any,
    ) -> None:
        super().__init__()
        self.spatial_compression_ratio = math.prod(spatial_downsample_factors)
        self.temporal_compression_ratio = math.prod(temporal_downsample_factors)

        self.encoder = _VideoEncoder3d(
            block_out_channels=block_out_channels, layers_per_block=layers_per_block,
            spatial_downsample_factors=spatial_downsample_factors,
            temporal_downsample_factors=temporal_downsample_factors,
            latent_channels=latent_channels, norm_num_groups=norm_num_groups, norm_eps=norm_eps,
            operations=operations,
        )
        self.quant_conv = operations.Conv3d(2 * latent_channels, 2 * latent_channels, kernel_size=1)
        self.post_quant_conv = operations.Conv3d(latent_channels, latent_channels, kernel_size=1)
        self.decoder = _VideoViTDecoder3d(
            in_channels=latent_channels, out_channels=OUT_CHANNELS,
            patch_size=self.spatial_compression_ratio, patch_size_t=self.temporal_compression_ratio,
            num_layers=decoder_num_layers, num_heads=decoder_num_attention_heads,
            head_dim=decoder_attention_head_dim, num_register_tokens=decoder_num_register_tokens,
            ffn_mult=decoder_ffn_mult, rope_theta=decoder_rope_theta, rope_dim_ratio=decoder_rope_dim_ratio,
            norm_eps=decoder_norm_eps, operations=operations,
        )

        # Real checkpoint tensors (see module docstring point 4) -- not
        # applied inside encode/decode, but loaded for a pipe to read.
        self.register_buffer("latents_mean", torch.zeros(latent_channels), persistent=True)
        self.register_buffer("latents_std", torch.ones(latent_channels), persistent=True)

        self.clip_length = clip_length
        self.token_drop = token_drop
        self.frame_pre_padding = (-clip_length) % self.temporal_compression_ratio
        self.tokens_chunk_size = math.ceil(clip_length / self.temporal_compression_ratio)
        self.token_overlap = (-token_drop) % self.tokens_chunk_size
        self.frame_overlap = max(self.token_overlap * self.temporal_compression_ratio - self.frame_pre_padding, 0)

        self.use_tiling = True
        self.tile_sample_min_height = tile_sample_min_height
        self.tile_sample_min_width = tile_sample_min_width
        self.tile_sample_min_overlap_height = tile_sample_min_overlap_height
        self.tile_sample_min_overlap_width = tile_sample_min_overlap_width

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "MiniMaxH3VideoVAE":
        return cls(
            latent_channels=config.get("latent_channels", LATENT_CHANNELS),
            block_out_channels=tuple(config.get("block_out_channels", BLOCK_OUT_CHANNELS)),
            layers_per_block=config.get("layers_per_block", LAYERS_PER_BLOCK),
            spatial_downsample_factors=tuple(config.get("spatial_downsample_factors", SPATIAL_DOWNSAMPLE_FACTORS)),
            temporal_downsample_factors=tuple(config.get("temporal_downsample_factors", TEMPORAL_DOWNSAMPLE_FACTORS)),
            norm_num_groups=config.get("norm_num_groups", NORM_NUM_GROUPS),
            norm_eps=config.get("norm_eps", NORM_EPS),
            decoder_num_layers=config.get("decoder_num_layers", DECODER_NUM_LAYERS),
            decoder_num_attention_heads=config.get("decoder_num_attention_heads", DECODER_NUM_ATTENTION_HEADS),
            decoder_attention_head_dim=config.get("decoder_attention_head_dim", DECODER_ATTENTION_HEAD_DIM),
            decoder_num_register_tokens=config.get("decoder_num_register_tokens", DECODER_NUM_REGISTER_TOKENS),
            decoder_ffn_mult=config.get("decoder_ffn_mult", DECODER_FFN_MULT),
            decoder_rope_theta=config.get("decoder_rope_theta", DECODER_ROPE_THETA),
            decoder_rope_dim_ratio=config.get("decoder_rope_dim_ratio", DECODER_ROPE_DIM_RATIO),
            decoder_norm_eps=config.get("decoder_norm_eps", DECODER_NORM_EPS),
            clip_length=config.get("clip_length", CLIP_LENGTH),
            token_drop=config.get("token_drop", TOKEN_DROP),
            tile_sample_min_height=config.get("tile_sample_min_height", TILE_SAMPLE_MIN_HEIGHT),
            tile_sample_min_width=config.get("tile_sample_min_width", TILE_SAMPLE_MIN_WIDTH),
            tile_sample_min_overlap_height=config.get("tile_sample_min_overlap_height", TILE_SAMPLE_MIN_OVERLAP_HEIGHT),
            tile_sample_min_overlap_width=config.get("tile_sample_min_overlap_width", TILE_SAMPLE_MIN_OVERLAP_WIDTH),
            operations=operations,
        )

    def post_load(self) -> None:
        self.decoder.rope.post_load()

    # -- tiling --------------------------------------------------------

    def _split_tiles(self, length: int, tile_size: int, min_overlap: int) -> tuple[list[int], list[int], list[int]]:
        if tile_size >= length:
            return [0], [length], []
        num_tiles = math.ceil(length / tile_size)
        while tile_size * num_tiles - min_overlap * (num_tiles - 1) - length < 0:
            num_tiles += 1
        overlaps = [min_overlap] * (num_tiles - 1)
        remaining = tile_size * num_tiles - sum(overlaps) - length
        for i in range(remaining // self.spatial_compression_ratio):
            overlaps[i % (num_tiles - 1)] += self.spatial_compression_ratio
        tile_start_indices = [0]
        for i in range(num_tiles - 1):
            tile_start_indices.append(tile_start_indices[-1] + tile_size - overlaps[i])
        return tile_start_indices, [tile_size] * num_tiles, overlaps

    @staticmethod
    def _blend(a: torch.Tensor, b: torch.Tensor, blend_extent: int, dim: int) -> torch.Tensor:
        blend_extent = min(a.shape[dim], b.shape[dim], blend_extent)
        positions = torch.arange(blend_extent, device=b.device, dtype=b.dtype)
        shape = [1] * a.ndim
        shape[dim] = blend_extent
        weight_a = (1 - positions / blend_extent).view(shape)
        weight_b = (positions / blend_extent).view(shape)

        slice_a = [slice(None)] * a.ndim
        slice_a[dim] = slice(-blend_extent, None)
        slice_b = [slice(None)] * b.ndim
        slice_b[dim] = slice(0, blend_extent)
        blended = a[tuple(slice_a)] * weight_a + b[tuple(slice_b)] * weight_b

        if blend_extent == b.shape[dim]:
            return blended
        slice_rest = [slice(None)] * b.ndim
        slice_rest[dim] = slice(blend_extent, None)
        return torch.cat([blended, b[tuple(slice_rest)]], dim=dim)

    def _stitch_tiles(self, tiles: list[list[torch.Tensor]], height_overlaps: list[int], width_overlaps: list[int]) -> torch.Tensor:
        result_rows = []
        for i, row in enumerate(tiles):
            result_row = []
            for j, tile in enumerate(row):
                if i > 0:
                    tile = self._blend(tiles[i - 1][j], tile, height_overlaps[i - 1], dim=-2)
                if j > 0:
                    tile = self._blend(row[j - 1], tile, width_overlaps[j - 1], dim=-1)
                if i < len(tiles) - 1:
                    tile = tile[..., : -height_overlaps[i], :]
                if j < len(row) - 1:
                    tile = tile[..., :, : -width_overlaps[j]]
                result_row.append(tile)
            result_rows.append(torch.cat(result_row, dim=-1))
        return torch.cat(result_rows, dim=-2)

    def _encode_clip(self, x: torch.Tensor) -> torch.Tensor:
        if not self.use_tiling:
            return self.quant_conv(self.encoder(x))

        height, width = x.shape[-2], x.shape[-1]
        y_indices, y_lengths, y_overlaps = self._split_tiles(height, self.tile_sample_min_height, self.tile_sample_min_overlap_height)
        x_indices, x_lengths, x_overlaps = self._split_tiles(width, self.tile_sample_min_width, self.tile_sample_min_overlap_width)

        rows = []
        for i_pos, i_len in zip(y_indices, y_lengths):
            row = []
            for j_pos, j_len in zip(x_indices, x_lengths):
                tile = x[..., i_pos : i_pos + i_len, j_pos : j_pos + j_len]
                row.append(self.quant_conv(self.encoder(tile)))
            rows.append(row)

        latent_y_overlaps = [o // self.spatial_compression_ratio for o in y_overlaps]
        latent_x_overlaps = [o // self.spatial_compression_ratio for o in x_overlaps]
        return self._stitch_tiles(rows, latent_y_overlaps, latent_x_overlaps)

    def _decode_clip(self, z: torch.Tensor) -> torch.Tensor:
        if not self.use_tiling:
            return self.decoder(self.post_quant_conv(z))

        height = z.shape[-2] * self.spatial_compression_ratio
        width = z.shape[-1] * self.spatial_compression_ratio
        y_indices, y_lengths, y_overlaps = self._split_tiles(height, self.tile_sample_min_height, self.tile_sample_min_overlap_height)
        x_indices, x_lengths, x_overlaps = self._split_tiles(width, self.tile_sample_min_width, self.tile_sample_min_overlap_width)

        ratio = self.spatial_compression_ratio
        rows = []
        for i_pos, i_len in zip(y_indices, y_lengths):
            row = []
            for j_pos, j_len in zip(x_indices, x_lengths):
                tile = z[..., i_pos // ratio : i_pos // ratio + i_len // ratio, j_pos // ratio : j_pos // ratio + j_len // ratio]
                row.append(self.decoder(self.post_quant_conv(tile)))
            rows.append(row)

        return self._stitch_tiles(rows, y_overlaps, x_overlaps)

    # -- temporal chunking -----------------------------------------------

    def _encode(self, x: torch.Tensor) -> torch.Tensor:
        """Encode in `clip_length`-frame chunks, drop `token_drop` trailing
        latent frames ONCE from the very end (not per-chunk). `num_frames ==
        1` bypasses this: padding a single frame up to `clip_length` copies
        and running it through the temporal path would return `tokens_chunk_
        size - token_drop` latent frames instead of the single frame the
        conditioning path actually needs (keyframe-style single-frame
        encodes elsewhere in this family expect a 1:1 frame:latent-frame
        contract, matching diffusers' documented behavior)."""
        clip_length = self.clip_length
        num_frames = x.shape[2]
        if num_frames == 1:
            return self._encode_clip(x)
        if num_frames % clip_length != 0:
            pad_frames = x[:, :, -1:].repeat(1, 1, (-num_frames) % clip_length, 1, 1)
            x = torch.cat([x, pad_frames], dim=2)

        moments = torch.cat(
            [self._encode_clip(x[:, :, i * clip_length : (i + 1) * clip_length]) for i in range(x.shape[2] // clip_length)],
            dim=2,
        )
        if self.token_drop > 0:
            moments = moments[:, :, : -self.token_drop]
        return moments

    def _decode(self, z: torch.Tensor) -> torch.Tensor:
        tokens_chunk_size = self.tokens_chunk_size
        token_drop = self.token_drop
        temporal_ratio = self.temporal_compression_ratio
        chunk_num_frames = tokens_chunk_size * temporal_ratio

        num_tokens = z.shape[2] + token_drop
        pad_tokens = (-num_tokens) % tokens_chunk_size
        num_chunks = (num_tokens + pad_tokens) // tokens_chunk_size - int(token_drop > 0)
        if pad_tokens > 0:
            z = torch.cat([z, z[:, :, -1:].repeat(1, 1, pad_tokens, 1, 1)], dim=2)

        decoded_chunks: list[torch.Tensor] = []
        overlap = None
        for i in range(num_chunks):
            start = i * tokens_chunk_size
            clip = self._decode_clip(z[:, :, start : start + tokens_chunk_size + self.token_overlap])
            for j in range(int(token_drop > 0) + 1):
                frame_start = j * chunk_num_frames
                chunk = clip[:, :, frame_start : frame_start + chunk_num_frames]
                chunk = chunk[:, :, self.frame_pre_padding :]
                if j == 0:
                    if overlap is not None:
                        chunk = self._blend(overlap, chunk, self.frame_overlap, dim=-3)
                    decoded_chunks.append(chunk)
                else:
                    overlap = chunk
        if overlap is not None:
            decoded_chunks.append(overlap)

        dec = torch.cat(decoded_chunks, dim=2)

        if pad_tokens > 0:
            intra_tail = self.clip_length % temporal_ratio
            num_tokens_before_pad = z.shape[2] - pad_tokens
            pad_frames = sum(
                intra_tail if intra_tail and (num_tokens_before_pad + k) % tokens_chunk_size == 0 else temporal_ratio
                for k in range(pad_tokens)
            )
            dec = dec[:, :, :-pad_frames]
        return dec

    # -- public API --------------------------------------------------------

    def encode(
        self, x: torch.Tensor, *, sample_posterior: bool = False, generator: torch.Generator | None = None,
    ) -> torch.Tensor:
        """Pixel video `(B, 3, F, H, W)` -> latent `(B, latent_channels, F',
        H/16, W/16)`.

        Returns the posterior MODE by default (no separate distribution
        object -- matches the house convention and this VAE's general
        `sample_posterior=False` usage in the reference pipeline). Passing
        `sample_posterior=True` instead draws a sample the same way
        diffusers' `DiagonalGaussianDistribution.sample()` does: `logvar`
        clamped to `[-30, 20]`, `std = exp(0.5 * logvar)`, `sample = mean +
        std * noise` with `noise` drawn via `_randn_like_reference` (a CPU
        `generator` produces reproducible noise regardless of `x`'s device --
        see that function's docstring). This is what the reference's
        `encode_vae_condition` uses for fl2va keyframe-anchor conditioning,
        under a fixed `keyframe_encode_seed = 42` generator independent of
        the request's own generator -- the caller (a sibling pipe module)
        owns constructing and seeding that generator; this method only
        implements the sampling math.

        Default behavior (`sample_posterior=False`) is BYTE-IDENTICAL to
        before this parameter existed -- the mode branch never touches
        `logvar` at all.
        """
        moments = self._encode(x)
        mean, logvar = moments.chunk(2, dim=1)
        if not sample_posterior:
            return mean
        logvar = torch.clamp(logvar, -30.0, 20.0)
        std = torch.exp(0.5 * logvar)
        noise = _randn_like_reference(mean.shape, generator=generator, device=mean.device, dtype=mean.dtype)
        return mean + std * noise

    def decode(self, z: torch.Tensor) -> torch.Tensor:
        """Latent video `(B, latent_channels, F', H/16, W/16)` -> pixel video
        `(B, 3, F, H, W)`."""
        return self._decode(z)
