"""LTX-2.5 ``CausalDiffusionVAE``: the 2.3-shaped causal conv **encoder** paired
with a **diffusion decoder** (``NADiffusionDecoder``), ported from diffusers'
Apache-2.0 ``models/autoencoders/ltx2_diffusion_decoder.py`` +
``pipelines/ltx2/pipeline_ltx2_diffusion_decode.py``.

**Config comes from the checkpoint, like the 2.0/2.3 VAE** -- but the embedded
``config["vae"]`` block is NESTED here (``{"encoder": {...}, "decoder": {...}}``)
where ``CausalVideoAutoencoder``'s is flat (``encoder_blocks``,
``decoder_blocks``, ...). ``detect_ltx_diffusion_vae_config`` returns the raw
nested dict; :meth:`LTXDiffusionVideoVAE.from_config` flattens it.

**The encoder is the 2.3 encoder, unchanged.** Verified against the real file's
header: all 84 ``encoder.*`` keys and their shapes are exactly what
``ltx_causal_video.Encoder`` builds for this config (patch_size 4, base 128,
``latent_log_var: "constant"`` -> ``conv_out`` emits 129 = 128 + 1 channels).
So that class is imported and composed rather than re-implemented. The 2.5
config adds ``latent_log_var_value`` (-7.824...) where the 2.3 encoder hardcodes
the constant it fills; immaterial here because :meth:`encode` keeps only the
means chunk and discards the log-variance one.

**The decoder is not a decoder in the conv sense -- it is a small diffusion
model.** Stages 1-4 (``det_stages`` + ``upsamples``) deterministically upsample
the latent into a context volume with 3D neighborhood-attention blocks; stage 5
(``diff_blocks``) denoises patchified pixels conditioned on that context via
AdaLN-Zero scale/shift. The whole denoising loop lives behind :meth:`decode`, so
the pipes' decode call site (``latents in -> pixel frames out``) is unchanged.

**Decode recipe** (diffusers ``LTX2VideoDiffusionDecoder3d.denoise``, cross-checked
against ltx-core's ``diffusion_video_decoder.py`` as facts): timesteps are
``linspace(1.0, 1/N, N)`` for ``N = default_num_inference_steps``; with ``N == 1``
and ``model_output_type == "x0"`` -- how this checkpoint ships -- stage 5 runs
once on pure noise and its prediction IS the output, no Euler update. For
``N > 1`` an x0 prediction is converted to a velocity (``(x_t - x0) / sigma``)
and integrated with ``x_t <- x_t - dt * v``. The timestep entering the embedder
is scaled by ``timestep_scale_multiplier`` (1000.0 in this checkpoint).

**Neighborhood attention without NATTEN.** The reference kernel (NATTEN's
``na3d``) gives every query a ``kernel_size`` window that is centred where
possible and shifted *inward* at the grid borders, so it always holds exactly
``prod(kernel_size)`` positions. diffusers' portable stand-in builds a
FlexAttention ``BlockMask``, which is not viable at production grids (its own
docstring notes a 69x64x96 stage would need 167 GiB for the mask alone). This
port instead evaluates the same windows exactly, in tiles: queries are grouped
into small blocks, each block's queries share one gathered key/value *region*
big enough to contain every one of their windows, and a small per-tile boolean
mask selects each query's own window out of that region. Sharing the region
across a tile is what makes it tractable -- a per-query gather would
materialize ``prod(kernel_size)`` keys per query. The extra keys a tile carries
are masked out, so the result is the true neighborhood attention, not an
approximation; the cost is a compute factor of
``prod((tile + k - 1) / k)`` over the theoretical minimum (~2x at the shipped
kernels).

**``decoder.type_emb``** (a ``[128]`` parameter in the real file) is registered
for exact key parity and never read. Neither port defines it: it is absent from
diffusers' ``LTX2VideoDiffusionDecoder3d`` and from ltx-core's own
``NADiffusionDecoder``, whose conversion path would reject it outright
(``load_state_dict(strict=True)``). Treated as vestigial rather than silently
folded into a forward that no reference performs.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

import torch
import torch.nn as nn
import torch.nn.functional as F

from ..attention import attention
from ..base import NativeArchModule
from ..errors import NativeEngineUnsupportedError
from .ltx_causal_video import (
    Encoder,
    _clear_thread_cache,
    _patchify,
    _PerChannelStatistics,
    _unpatchify,
)

if TYPE_CHECKING:
    from .ltx_tiling import LtxTilingConfig

logger = logging.getLogger(__name__)

# Tokens per tile in the gated MLP. ``w_gate(x)`` and ``w_up(x)`` are both
# hidden-width and their product makes a third, so a whole-volume evaluation
# holds three hidden-width tensors at once. Fixing a token COUNT keeps that
# bound independent of resolution. Matches diffusers' own default.
_SWIGLU_TILE_SIZE = 16384

# Upper bound on live attention-score elements per neighborhood-attention chunk.
# Scores are the largest transient in that path (tile queries x region keys x
# heads); the chunk count follows from this rather than from a tile count, so
# the bound holds at any grid size.
_ATTENTION_SCORE_BUDGET = 1 << 26


def _randn(shape: tuple[int, ...], generator: torch.Generator | None,
           device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    """``torch.randn`` that tolerates a generator seeded on another device.

    A CPU generator is the ordinary way to make a decode reproducible, and
    ``torch.randn`` rejects one outright when asked for a CUDA tensor, so the
    draw happens on the generator's device and moves afterwards."""
    if generator is not None and generator.device.type != device.type:
        return torch.randn(shape, generator=generator, device=generator.device, dtype=dtype).to(device)
    return torch.randn(shape, generator=generator, device=device, dtype=dtype)


def _timestep_embedding(timesteps: torch.Tensor, dim: int, max_period: int = 10000) -> torch.Tensor:
    """DDPM sinusoidal embedding in diffusers' ``PixArtAlpha`` configuration:
    ``flip_sin_to_cos=True``, ``downscale_freq_shift=0``, ``scale=1``."""
    half = dim // 2
    exponent = -math.log(max_period) * torch.arange(half, dtype=torch.float32, device=timesteps.device)
    freqs = torch.exp(exponent / half)
    angles = timesteps[:, None].float() * freqs[None, :]
    return torch.cat([torch.cos(angles), torch.sin(angles)], dim=-1)


class _TimestepEmbedder(nn.Module):
    """``PixArtAlphaCombinedTimestepSizeEmbeddings`` with ``size_emb_dim=0``,
    under the checkpoint's own key names (``mlp.0``/``mlp.2``, where diffusers
    reads ``timestep_embedder.linear_1``/``linear_2``)."""

    _FREQ_CHANNELS = 256

    def __init__(self, embedding_dim: int, *, operations: Any) -> None:
        super().__init__()
        self.mlp = nn.Sequential(
            operations.Linear(self._FREQ_CHANNELS, embedding_dim, bias=True),
            nn.SiLU(),
            operations.Linear(embedding_dim, embedding_dim, bias=True),
        )

    def forward(self, timesteps: torch.Tensor, hidden_dtype: torch.dtype) -> torch.Tensor:
        proj = _timestep_embedding(timesteps, self._FREQ_CHANNELS)
        return self.mlp(proj.to(dtype=hidden_dtype))


class _RotaryPosEmbed3D(nn.Module):
    """Absolute 3D rotary embedding over the (T, H, W) token grid.

    ``head_dim`` splits into a T chunk of ``(head_dim // 4)`` rounded down to an
    even width and two equal H/W chunks. Positions are the tensor's own 0-based
    indices: the window is local and never causally masked, so a score depends
    only on the relative offset and a shared origin shift is a no-op.
    """

    def __init__(self, head_dim: int, base: float = 10000.0) -> None:
        super().__init__()
        if head_dim % 8 != 0:
            raise NativeEngineUnsupportedError(
                f"LTX diffusion VAE: head_dim must be a multiple of 8, got {head_dim}."
            )
        dim_t = (head_dim // 4) // 2 * 2
        dim_hw = (head_dim - dim_t) // 2
        if dim_hw % 2 != 0:
            dim_t -= 2
            dim_hw = (head_dim - dim_t) // 2
        self.rope_dim_split = (dim_t, dim_hw, dim_hw)
        self.base = base

    def _inv_freqs(self, dim: int, device: torch.device) -> torch.Tensor:
        exponents = torch.arange(0, dim, 2, dtype=torch.float64, device=device) / dim
        return (1.0 / self.base ** exponents).to(torch.float32)

    @staticmethod
    def _rotate_axis(x: torch.Tensor, positions: torch.Tensor, inv_freqs: torch.Tensor, axis: int) -> torch.Tensor:
        out_dtype = x.dtype
        pairs = x.reshape(*x.shape[:-1], x.shape[-1] // 2, 2)
        even = pairs[..., 0].float()
        odd = pairs[..., 1].float()
        shape = [1, 1, 1, 1, 1, inv_freqs.shape[0]]
        shape[axis] = positions.shape[0]
        angles = (positions[:, None] * inv_freqs[None, :]).reshape(shape)
        cos, sin = angles.cos(), angles.sin()
        rotated = torch.stack([even * cos - odd * sin, even * sin + odd * cos], dim=-1)
        return rotated.reshape(x.shape).to(out_dtype)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        """``hidden_states``: ``(B, T, H, W, heads, head_dim)``."""
        dim_t, dim_h, _ = self.rope_dim_split
        num_frames, height, width = hidden_states.shape[1:4]
        device = hidden_states.device
        inv_t, inv_h, inv_w = (self._inv_freqs(dim, device) for dim in self.rope_dim_split)

        positions_t = torch.arange(num_frames, dtype=torch.float32, device=device)
        positions_h = torch.arange(height, dtype=torch.float32, device=device)
        positions_w = torch.arange(width, dtype=torch.float32, device=device)
        rotated_t = self._rotate_axis(hidden_states[..., :dim_t], positions_t, inv_t, axis=1)
        rotated_h = self._rotate_axis(hidden_states[..., dim_t:dim_t + dim_h], positions_h, inv_h, axis=2)
        rotated_w = self._rotate_axis(hidden_states[..., dim_t + dim_h:], positions_w, inv_w, axis=3)
        return torch.cat([rotated_t, rotated_h, rotated_w], dim=-1)


def _axis_windows(length: int, kernel: int, tile: int) -> tuple[int, torch.Tensor, torch.Tensor]:
    """Per-axis tiling plan for the inward-shifted neighborhood window.

    Returns ``(region, region_starts, mask)`` where ``region`` is the shared
    key/value extent every tile on this axis gathers, ``region_starts`` is
    ``(num_tiles,)`` and ``mask`` is ``(num_tiles, tile, region)`` -- true where
    that tile-local query's own ``kernel``-wide window covers that region slot.

    The query grid is padded up to a whole number of tiles by the caller, so the
    trailing tile's out-of-range queries get a well-defined (discarded) window
    rather than a ragged shape.
    """
    region = min(length, tile + kernel - 1)
    num_tiles = -(-length // tile)
    tile_starts = torch.arange(num_tiles) * tile
    region_starts = torch.clamp(tile_starts - kernel // 2, 0, length - region)

    query_pos = tile_starts[:, None] + torch.arange(tile)[None, :]
    window_starts = torch.clamp(query_pos - kernel // 2, 0, length - kernel)
    region_pos = region_starts[:, None] + torch.arange(region)[None, :]

    covered = (region_pos[:, None, :] >= window_starts[..., None]) & (
        region_pos[:, None, :] < window_starts[..., None] + kernel
    )
    return region, region_starts, covered


def _neighborhood_attention(
    query: torch.Tensor,
    key: torch.Tensor,
    value: torch.Tensor,
    kernel_size: tuple[int, int, int],
) -> torch.Tensor:
    """3D neighborhood attention over ``(B, T, H, W, heads, head_dim)`` tensors.

    Each query attends to exactly ``prod(kernel_size)`` positions: a window
    centred on it where possible and shifted inward at the grid borders. See the
    module docstring for why this is evaluated tile-by-tile rather than through a
    FlexAttention ``BlockMask``.
    """
    batch, num_frames, height, width, heads, head_dim = query.shape
    grid = (num_frames, height, width)
    kernels = tuple(min(k, n) for k, n in zip(kernel_size, grid))
    tiles = tuple(max(1, k // 2) for k in kernels)

    plans = [_axis_windows(n, k, t) for n, k, t in zip(grid, kernels, tiles)]
    regions = tuple(plan[0] for plan in plans)
    counts = tuple(-(-n // t) for n, t in zip(grid, tiles))
    device = query.device

    padded = tuple(c * t for c, t in zip(counts, tiles))
    pad = (0, 0, 0, 0, 0, padded[2] - width, 0, padded[1] - height, 0, padded[0] - num_frames)
    query = F.pad(query, pad) if any(p > 0 for p in pad) else query

    tile_tokens = math.prod(tiles)
    region_tokens = math.prod(regions)
    query = query.reshape(
        batch, counts[0], tiles[0], counts[1], tiles[1], counts[2], tiles[2], heads, head_dim,
    ).permute(0, 1, 3, 5, 7, 2, 4, 6, 8)
    query = query.reshape(batch, math.prod(counts), heads, tile_tokens, head_dim)

    flat_key = key.reshape(batch, num_frames * height * width, heads, head_dim)
    flat_value = value.reshape(batch, num_frames * height * width, heads, head_dim)

    tile_index = torch.arange(math.prod(counts), device=device)
    tile_t = tile_index // (counts[1] * counts[2])
    tile_h = (tile_index // counts[2]) % counts[1]
    tile_w = tile_index % counts[2]

    starts = [plan[1].to(device) for plan in plans]
    masks = [plan[2].to(device) for plan in plans]

    offsets = [torch.arange(r, device=device) for r in regions]
    gather = (
        ((starts[0][tile_t][:, None, None, None] + offsets[0][None, :, None, None]) * height
         + starts[1][tile_h][:, None, None, None] + offsets[1][None, None, :, None]) * width
        + starts[2][tile_w][:, None, None, None] + offsets[2][None, None, None, :]
    ).reshape(-1, region_tokens)

    chunk = max(1, _ATTENTION_SCORE_BUDGET // (heads * tile_tokens * region_tokens))
    outputs = []
    for start in range(0, gather.shape[0], chunk):
        index = gather[start:start + chunk]
        group = index.shape[0]
        keys = flat_key.index_select(1, index.reshape(-1))
        keys = keys.reshape(batch * group, region_tokens, heads, head_dim).transpose(1, 2)
        values = flat_value.index_select(1, index.reshape(-1))
        values = values.reshape(batch * group, region_tokens, heads, head_dim).transpose(1, 2)

        mask = (
            masks[0][tile_t[start:start + chunk]][:, :, None, None, :, None, None]
            & masks[1][tile_h[start:start + chunk]][:, None, :, None, None, :, None]
            & masks[2][tile_w[start:start + chunk]][:, None, None, :, None, None, :]
        ).reshape(1, group, 1, tile_tokens, region_tokens)
        mask = mask.expand(batch, -1, -1, -1, -1).reshape(batch * group, 1, tile_tokens, region_tokens)

        queries = query[:, start:start + chunk].reshape(batch * group, heads, tile_tokens, head_dim)
        out = attention(queries, keys, values, mask=mask)
        outputs.append(out.reshape(batch, group, heads, tile_tokens, head_dim))

    hidden_states = torch.cat(outputs, dim=1) if len(outputs) > 1 else outputs[0]
    hidden_states = hidden_states.reshape(
        batch, counts[0], counts[1], counts[2], heads, tiles[0], tiles[1], tiles[2], head_dim,
    ).permute(0, 1, 5, 2, 6, 3, 7, 4, 8)
    hidden_states = hidden_states.reshape(batch, padded[0], padded[1], padded[2], heads, head_dim)
    return hidden_states[:, :num_frames, :height, :width]


class NeighborhoodAttention(nn.Module):
    """Fused-QKV neighborhood attention, channels-last in and out.

    The checkpoint stores one ``Linear(dim, 3 * dim)`` (``qkv``) and names the
    output projection ``proj`` and the per-head RMS norms ``q_norm``/``k_norm``
    -- diffusers renames all four on conversion; this port keeps the shipped
    names. The ``1 / sqrt(head_dim)`` factor is left to the attention backend
    rather than pre-applied to the query: rotation is linear, so scaling before
    or after RoPE is the same value.
    """

    def __init__(self, dim: int, kernel_size: tuple[int, int, int], head_dim: int, *, operations: Any) -> None:
        super().__init__()
        if dim % head_dim != 0:
            raise NativeEngineUnsupportedError(
                f"LTX diffusion VAE: dim {dim} is not divisible by head_dim {head_dim}."
            )
        self.heads = dim // head_dim
        self.head_dim = head_dim
        self.kernel_size = tuple(kernel_size)

        self.qkv = operations.Linear(dim, 3 * dim, bias=True)
        self.proj = operations.Linear(dim, dim, bias=True)
        self.q_norm = operations.RMSNorm(head_dim, eps=1e-6)
        self.k_norm = operations.RMSNorm(head_dim, eps=1e-6)
        self.rope = _RotaryPosEmbed3D(head_dim)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        batch, num_frames, height, width, _ = hidden_states.shape
        kernel_t, kernel_h, kernel_w = self.kernel_size
        if num_frames < kernel_t or height < kernel_h or width < kernel_w:
            raise ValueError(
                f"LTX diffusion VAE: neighborhood attention needs each grid dim to be at least its "
                f"kernel size; got (T, H, W) = ({num_frames}, {height}, {width}) with "
                f"kernel_size {self.kernel_size}."
            )

        shape = (batch, num_frames, height, width, self.heads, self.head_dim)
        query, key, value = self.qkv(hidden_states).chunk(3, dim=-1)
        query = self.rope(self.q_norm(query.reshape(shape)))
        key = self.rope(self.k_norm(key.reshape(shape)))
        value = value.reshape(shape)

        hidden_states = _neighborhood_attention(query, key, value, self.kernel_size)
        hidden_states = hidden_states.reshape(batch, num_frames, height, width, self.heads * self.head_dim)
        return self.proj(hidden_states)


class SwiGLU(nn.Module):
    """``w_down(silu(w_gate(x)) * w_up(x))``, evaluated in tiles of
    :data:`_SWIGLU_TILE_SIZE` tokens. The MLP is pointwise across tokens, so
    tiling changes only how many hidden-width elements are live at once."""

    def __init__(self, dim: int, hidden_dim: int, *, operations: Any) -> None:
        super().__init__()
        self.w_up = operations.Linear(dim, hidden_dim, bias=False)
        self.w_gate = operations.Linear(dim, hidden_dim, bias=False)
        self.w_down = operations.Linear(hidden_dim, dim, bias=False)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        batch, *token_dims, channels = hidden_states.shape
        num_tokens = math.prod(token_dims)
        if num_tokens <= _SWIGLU_TILE_SIZE:
            return self.w_down(F.silu(self.w_gate(hidden_states)) * self.w_up(hidden_states))

        flat = hidden_states.reshape(batch, num_tokens, channels)
        out = torch.empty_like(flat)
        for start in range(0, num_tokens, _SWIGLU_TILE_SIZE):
            tile = flat[:, start:start + _SWIGLU_TILE_SIZE]
            out[:, start:start + _SWIGLU_TILE_SIZE] = self.w_down(
                F.silu(self.w_gate(tile)) * self.w_up(tile)
            )
        return out.reshape(hidden_states.shape)


def _swiglu_hidden_dim(dim: int, mlp_ratio: float) -> int:
    return (int(dim * mlp_ratio) + 15) // 16 * 16


class NABlock(nn.Module):
    """Pre-norm neighborhood-attention block used by the deterministic stages."""

    def __init__(self, dim: int, kernel_size: tuple[int, int, int], head_dim: int,
                 mlp_ratio: float = 4.0, *, operations: Any) -> None:
        super().__init__()
        self.norm1 = operations.RMSNorm(dim, eps=1e-6)
        self.attn = NeighborhoodAttention(dim, kernel_size, head_dim, operations=operations)
        self.norm2 = operations.RMSNorm(dim, eps=1e-6)
        self.mlp = SwiGLU(dim, _swiglu_hidden_dim(dim, mlp_ratio), operations=operations)

    def forward(self, hidden_states: torch.Tensor) -> torch.Tensor:
        hidden_states = hidden_states + self.attn(self.norm1(hidden_states))
        return hidden_states + self.mlp(self.norm2(hidden_states))


class AdaLNZero(nn.Module):
    """Shared AdaLN-Zero modulation: a timestep embedding to seven
    ``(B, 1, 1, 1, C)`` chunks. Seven is the reference's shape (scale/shift/gate
    for attention and MLP plus a context gate); only the four scale/shift chunks
    are consumed -- this decoder's residuals are ungated and this checkpoint
    ships no gate parameters to fold in."""

    def __init__(self, dim: int, t_emb_dim: int, num_chunks: int = 7, *, operations: Any) -> None:
        super().__init__()
        self.num_chunks = num_chunks
        self.proj = operations.Linear(t_emb_dim, num_chunks * dim, bias=True)

    def forward(self, t_emb: torch.Tensor) -> tuple[torch.Tensor, ...]:
        chunks = self.proj(F.silu(t_emb)).chunk(self.num_chunks, dim=-1)
        return tuple(chunk[:, None, None, None, :] for chunk in chunks)


class DiffusionNABlock(nn.Module):
    """Stage-5 block: neighborhood attention + SwiGLU under the shared
    AdaLN-Zero scale/shift, with the latent context injected through
    ``context_proj`` and a per-block ``scale_shift_table`` residual on the
    modulation."""

    def __init__(self, dim: int, kernel_size: tuple[int, int, int], context_channels: int,
                 head_dim: int, mlp_ratio: float = 4.0, num_mod_params: int = 7, *, operations: Any) -> None:
        super().__init__()
        self.num_mod_params = num_mod_params
        self.context_proj = operations.Linear(context_channels, dim, bias=True)
        self.scale_shift_table = nn.Parameter(torch.zeros(num_mod_params, dim))

        self.norm1 = operations.RMSNorm(dim, eps=1e-6)
        self.attn = NeighborhoodAttention(dim, kernel_size, head_dim, operations=operations)
        self.norm2 = operations.RMSNorm(dim, eps=1e-6)
        self.mlp = SwiGLU(dim, _swiglu_hidden_dim(dim, mlp_ratio), operations=operations)

    def forward(self, hidden_states: torch.Tensor, latent_context: torch.Tensor,
                modulation: tuple[torch.Tensor, ...]) -> torch.Tensor:
        scale_msa, shift_msa, _, scale_mlp, shift_mlp, _, _ = [
            modulation[i] + self.scale_shift_table[i].view(1, 1, 1, 1, -1)
            for i in range(self.num_mod_params)
        ]
        hidden_states = hidden_states + self.context_proj(latent_context)
        hidden_states = hidden_states + self.attn(self.norm1(hidden_states) * (1 + scale_msa) + shift_msa)
        return hidden_states + self.mlp(self.norm2(hidden_states) * (1 + scale_mlp) + shift_mlp)


class PixelShuffleUpsampler(nn.Module):
    """Linear channel expansion followed by a channels-last pixel shuffle.

    A temporal stride of 2 produces a duplicate leading frame, dropped to keep
    the causal 1:2 (composed 1:8) frame mapping. ``drop_leading_frame=False``
    keeps it -- what a tiled decode passes for temporal tiles that do not
    contain t=0, whose first input frame is an interior frame with two real
    output frames."""

    def __init__(self, in_channels: int, stride: tuple[int, int, int],
                 out_channels_reduction_factor: int = 1, *, operations: Any) -> None:
        super().__init__()
        self.stride = tuple(stride)
        proj_out_channels = math.prod(self.stride) * in_channels // out_channels_reduction_factor
        self.out_channels = proj_out_channels // math.prod(self.stride)
        self.proj = operations.Linear(in_channels, proj_out_channels, bias=True)

    def forward(self, hidden_states: torch.Tensor, drop_leading_frame: bool = True) -> torch.Tensor:
        batch, num_frames, height, width, _ = hidden_states.shape
        stride_t, stride_h, stride_w = self.stride
        hidden_states = self.proj(hidden_states)
        hidden_states = hidden_states.reshape(
            batch, num_frames, height, width, self.out_channels, stride_t, stride_h, stride_w,
        )
        hidden_states = hidden_states.permute(0, 1, 5, 2, 6, 3, 7, 4)
        hidden_states = hidden_states.reshape(
            batch, num_frames * stride_t, height * stride_h, width * stride_w, self.out_channels,
        )
        if stride_t == 2 and drop_leading_frame:
            hidden_states = hidden_states[:, 1:]
        return hidden_states


class DiffusionDecoder3d(nn.Module):
    """``NADiffusionDecoder``: four deterministic upsampling stages feeding a
    stage-5 denoiser. See the module docstring for the decode recipe."""

    def __init__(
        self, *,
        in_channels: int,
        out_channels: int,
        patch_size: int,
        head_dim: int,
        stage_channels: tuple[int, ...],
        stage_depths: tuple[int, ...],
        stage_kernels: tuple[tuple[int, int, int], ...],
        upsample_strides: tuple[tuple[int, int, int], ...],
        upsample_channel_reductions: tuple[int, ...],
        stage5_kernel: tuple[int, int, int],
        t_emb_dim: int,
        timestep_scale_multiplier: float,
        model_output_type: str,
        default_num_inference_steps: int,
        operations: Any,
    ) -> None:
        super().__init__()
        if model_output_type not in ("x0", "v"):
            raise NativeEngineUnsupportedError(
                f"LTX diffusion VAE: model_output_type must be 'x0' or 'v', got {model_output_type!r}."
            )
        for stage_idx, reduction in enumerate(upsample_channel_reductions):
            expected = stage_channels[stage_idx] // reduction
            if stage_channels[stage_idx + 1] != expected:
                raise NativeEngineUnsupportedError(
                    f"LTX diffusion VAE: stage_channels[{stage_idx + 1}] must be "
                    f"stage_channels[{stage_idx}] // {reduction} = {expected}, "
                    f"got {stage_channels[stage_idx + 1]}."
                )

        self.patch_size = patch_size
        self.out_channels = out_channels
        self.timestep_scale_multiplier = timestep_scale_multiplier
        self.model_output_type = model_output_type
        self.default_num_inference_steps = default_num_inference_steps
        self.context_channels = stage_channels[-1]
        self.temporal_compression_ratio = math.prod(stride[0] for stride in upsample_strides)
        # The window shifts inward at the grid border, so the last latent frame
        # is replicated through stages 1-4 and cropped off the context before
        # stage 5, moving that border past the frames that are kept.
        self.trailing_pad_latent_frames = (stage_kernels[0][0] // 2) * 2

        self.conv_in = operations.Linear(in_channels, stage_channels[0], bias=True)
        self.type_emb = nn.Parameter(torch.zeros(in_channels))

        self.det_stages = nn.ModuleList()
        self.upsamples = nn.ModuleList()
        for stage_idx, stride in enumerate(upsample_strides):
            channels = stage_channels[stage_idx]
            self.det_stages.append(nn.ModuleList([
                NABlock(channels, stage_kernels[stage_idx], head_dim, operations=operations)
                for _ in range(stage_depths[stage_idx])
            ]))
            self.upsamples.append(PixelShuffleUpsampler(
                channels, stride,
                out_channels_reduction_factor=upsample_channel_reductions[stage_idx],
                operations=operations,
            ))

        self.t_embedder = _TimestepEmbedder(t_emb_dim, operations=operations)

        stage5_channels = stage_channels[-1]
        noised_pixel_channels = out_channels * patch_size ** 2
        self.conv_in_x_t = operations.Linear(noised_pixel_channels, stage5_channels, bias=True)
        self.shared_adaln = AdaLNZero(stage5_channels, t_emb_dim, operations=operations)
        self.diff_blocks = nn.ModuleList([
            DiffusionNABlock(
                stage5_channels, stage5_kernel, self.context_channels, head_dim,
                num_mod_params=self.shared_adaln.num_chunks, operations=operations,
            )
            for _ in range(stage_depths[-1])
        ])
        self.norm_out = operations.RMSNorm(stage5_channels, eps=1e-6)
        self.conv_out = operations.Linear(stage5_channels, noised_pixel_channels, bias=True)

    def forward_stages_1_to_3(self, hidden_states: torch.Tensor, pad: bool = True,
                              drop_leading_frame: bool = True) -> torch.Tensor:
        """Latent ``(B, C, T, H, W)`` to a channels-last feature volume. The
        trailing ghost frames stay in the output; :meth:`forward_stage_4` crops
        them.

        ``pad``/``drop_leading_frame`` describe the untiled (whole-clip)
        decode. A tiled decode overrides them per latent tile exactly like
        :meth:`forward_stage_4` already does for its own upsample: padding
        happens once, up front, on the un-tiled latent (a per-tile pad would
        replicate an interior tile's own last frame instead of the true clip
        end), and only the tile touching latent frame 0 drops any stage's
        duplicate leading frame -- every temporal-stride-2 upsample in this
        loop produces one, not just stage 4's."""
        if pad:
            num_pad = self.trailing_pad_latent_frames
            if num_pad > 0:
                trailing = hidden_states[:, :, -1:].expand(-1, -1, num_pad, -1, -1)
                hidden_states = torch.cat([hidden_states, trailing], dim=2)

        hidden_states = hidden_states.permute(0, 2, 3, 4, 1)
        hidden_states = self.conv_in(hidden_states)
        for blocks, upsample in zip(self.det_stages[:-1], self.upsamples[:-1]):
            for block in blocks:
                hidden_states = block(hidden_states)
            hidden_states = upsample(hidden_states, drop_leading_frame=drop_leading_frame)
        return hidden_states

    def forward_stage_4(self, hidden_states: torch.Tensor, drop_leading_frame: bool = True,
                        crop_trailing_ghost: bool = True) -> torch.Tensor:
        """Last deterministic stage: to context ``(B, T5, H5, W5, C5)``.

        The defaults describe the untiled decode. A tiled decode overrides them
        per temporal tile: only the tile containing t=0 drops the upsample's
        duplicate leading frame, and only the tile containing the video end
        carries the trailing ghost frames to crop.
        """
        for block in self.det_stages[-1]:
            hidden_states = block(hidden_states)
        hidden_states = self.upsamples[-1](hidden_states, drop_leading_frame=drop_leading_frame)

        num_pad = self.trailing_pad_latent_frames
        if crop_trailing_ghost and num_pad > 0:
            hidden_states = hidden_states[:, : -num_pad * self.temporal_compression_ratio]
        return hidden_states

    def forward_diffusion_step(self, latent_context: torch.Tensor, x_t: torch.Tensor,
                               timestep: torch.Tensor) -> torch.Tensor:
        """One stage-5 step; the model's prediction in pixel space ``(B, C, F, H, W)``."""
        t_emb = self.t_embedder(self.timestep_scale_multiplier * timestep, hidden_dtype=latent_context.dtype)
        modulation = self.shared_adaln(t_emb)

        hidden_states = _patchify(x_t, self.patch_size, 1).permute(0, 2, 3, 4, 1)
        hidden_states = self.conv_in_x_t(hidden_states)
        for block in self.diff_blocks:
            hidden_states = block(hidden_states, latent_context, modulation)

        hidden_states = self.norm_out(hidden_states)
        hidden_states = self.conv_out(hidden_states)
        hidden_states = hidden_states.permute(0, 4, 1, 2, 3).contiguous()
        return _unpatchify(hidden_states, self.patch_size, 1)

    def denoise(self, latent_context: torch.Tensor, x_t: torch.Tensor, num_inference_steps: int) -> torch.Tensor:
        """Denoise ``x_t`` ``(B, C, F, H, W)`` conditioned on ``latent_context``."""
        batch_size = latent_context.shape[0]
        timesteps = torch.linspace(
            1.0, 1.0 / num_inference_steps, num_inference_steps,
            device=latent_context.device, dtype=torch.float32,
        )

        if num_inference_steps == 1 and self.model_output_type == "x0":
            return self.forward_diffusion_step(latent_context, x_t, timesteps[:1].expand(batch_size))

        for step_idx in range(num_inference_steps):
            t_now = timesteps[step_idx].expand(batch_size)
            t_next = timesteps[step_idx + 1] if step_idx + 1 < num_inference_steps else torch.zeros_like(t_now)
            model_out = self.forward_diffusion_step(latent_context, x_t, t_now).float()
            x_t_fp32 = x_t.float()
            if self.model_output_type == "x0":
                sigma = t_now.view(-1, *([1] * (x_t.ndim - 1)))
                model_out = (x_t_fp32 - model_out) / sigma
            dt = (t_now - t_next).view(-1, *([1] * (x_t.ndim - 1)))
            x_t = (x_t_fp32 - dt * model_out).to(x_t.dtype)
        return x_t

    def pixel_shape(self, batch_size: int, latent_context: torch.Tensor) -> tuple[int, ...]:
        """The stage-5 pixel canvas for a context volume: the context grid is the
        stage-5 token grid, so the canvas is its shape times the patch size --
        temporally the causal ``(T - 1) * ratio + 1`` mapping of LTX-2 latents."""
        return (
            batch_size,
            self.out_channels,
            latent_context.shape[1],
            latent_context.shape[2] * self.patch_size,
            latent_context.shape[3] * self.patch_size,
        )

    def forward(self, hidden_states: torch.Tensor, generator: torch.Generator | None = None,
                num_inference_steps: int | None = None) -> torch.Tensor:
        num_inference_steps = num_inference_steps or self.default_num_inference_steps
        latent_context = self.forward_stage_4(self.forward_stages_1_to_3(hidden_states))
        x_t = _randn(
            self.pixel_shape(hidden_states.shape[0], latent_context),
            generator, hidden_states.device, hidden_states.dtype,
        )
        return self.denoise(latent_context, x_t, num_inference_steps)


def _stage_min_latent_sizes(decoder: "DiffusionDecoder3d") -> tuple[int, int, int]:
    """The minimum latent-tile size on each axis (T, H, W) that keeps every
    stage's neighborhood-attention kernel inside the grid it actually sees,
    mapped back to latent units through that stage's own cumulative upsample
    stride from the latent -- stage 0 sees the latent as-is (cumulative
    stride 1); stage 5 sees it scaled by every upsample in the decoder."""
    cumulative = (1, 1, 1)
    floors = []
    for blocks, upsample in zip(decoder.det_stages, decoder.upsamples):
        kernel = blocks[0].attn.kernel_size
        floors.append(tuple(-(-k // c) for k, c in zip(kernel, cumulative)))
        cumulative = tuple(c * s for c, s in zip(cumulative, upsample.stride))
    kernel5 = decoder.diff_blocks[0].attn.kernel_size
    floors.append(tuple(-(-k // c) for k, c in zip(kernel5, cumulative)))
    return tuple(max(floor[axis] for floor in floors) for axis in range(3))


def _tile_intervals(length: int, tile_size: int, stride: int, min_size: int) -> list[tuple[int, int]]:
    """Overlapping ``[start, end)`` tiles covering ``[0, length)``, starts spaced
    ``stride`` apart. A trailing remnant shorter than ``min_size`` is merged into
    the previous tile: neighborhood attention rejects any grid smaller than its
    kernel, so a remnant cannot always stand alone."""
    if length <= tile_size:
        return [(0, length)]
    starts = list(range(0, length, stride))
    while len(starts) > 1 and length - starts[-1] < min_size:
        starts.pop()
    return [(start, min(start + tile_size, length)) for start in starts[:-1]] + [(starts[-1], length)]


def _blend(previous: torch.Tensor, current: torch.Tensor, extent: int, dim: int) -> torch.Tensor:
    extent = min(previous.shape[dim], current.shape[dim], extent)
    for offset in range(extent):
        weight = offset / extent
        tail = previous.narrow(dim, previous.shape[dim] - extent + offset, 1)
        head = current.narrow(dim, offset, 1)
        head.copy_(tail * (1 - weight) + head * weight)
    return current


class LTXDiffusionVideoVAE(NativeArchModule):
    """LTX-2.5 ``CausalDiffusionVAE``.

    Same public surface as :class:`~.ltx_causal_video.LTXCausalVideoVAE` --
    ``encode``/``decode`` on ``(B, 3, T, H, W)`` pixels in [-1, 1] and
    ``(B, latent_channels, T', H', W')`` latents, ``T`` = ``1 + 8*k``, plus
    ``tiled_encode``/``reset_cache`` -- so the LTX pipes decode through it
    unchanged. The multi-step denoise is internal to :meth:`decode`.
    """

    # Diffusers' own defaults for the tiled decode, in pixels/frames of the
    # decoded video. The difference between a size and its stride is the
    # blended overlap.
    _TILE_DEFAULTS = {
        "tile_sample_min_height": 768,
        "tile_sample_min_width": 768,
        "tile_sample_min_num_frames": 80,
        "tile_sample_stride_height": 704,
        "tile_sample_stride_width": 704,
        "tile_sample_stride_num_frames": 56,
    }

    def __init__(self, *, config: dict[str, Any], operations: Any) -> None:
        super().__init__()
        encoder_config = config["encoder"]
        decoder_config = config["decoder"]
        if decoder_config.get("resampler_kind", "linear") != "linear":
            raise NativeEngineUnsupportedError(
                f"LTX diffusion VAE: only resampler_kind='linear' is implemented, got "
                f"{decoder_config['resampler_kind']!r}."
            )

        latent_channels = decoder_config["in_channels"]
        self.latent_channels = latent_channels
        patch_size = encoder_config.get("patch_size", 4)
        spatial_padding_mode = config.get("spatial_padding_mode", "zeros")

        self.encoder = Encoder(
            in_channels=encoder_config.get("in_channels", 3),
            latent_channels=encoder_config["out_channels"],
            blocks=encoder_config["blocks"],
            base_channels=encoder_config.get("base_channels", 128),
            patch_size=patch_size,
            norm_layer=encoder_config.get("norm_layer", "pixel_norm"),
            latent_log_var=encoder_config.get("latent_log_var", "per_channel"),
            spatial_padding_mode=encoder_config.get("spatial_padding_mode", spatial_padding_mode),
            operations=operations,
        )

        stage_kernels = [tuple(k) for k in decoder_config["stage_kernels"]]
        strides = [tuple(stride) for stride, _ in decoder_config["upsamples"]]
        reductions = [int(reduction) for _, reduction in decoder_config["upsamples"]]
        self.decoder = DiffusionDecoder3d(
            in_channels=latent_channels,
            out_channels=decoder_config.get("out_channels", 3),
            patch_size=decoder_config.get("patch_size", patch_size),
            head_dim=decoder_config.get("head_dim", 64),
            stage_channels=tuple(decoder_config["stage_channels"]),
            stage_depths=tuple(decoder_config["stage_depths"]),
            # ``stage_kernels`` carries the stage-5 kernel as a fifth entry
            # alongside its own ``stage5_kernel`` field; the deterministic
            # stages take the first four.
            stage_kernels=tuple(stage_kernels[:len(strides)]),
            upsample_strides=tuple(strides),
            upsample_channel_reductions=tuple(reductions),
            stage5_kernel=tuple(decoder_config.get("stage5_kernel", stage_kernels[-1])),
            t_emb_dim=decoder_config.get("t_emb_dim", 384),
            timestep_scale_multiplier=decoder_config.get("timestep_scale_multiplier", 1000.0),
            model_output_type=config.get("model_output_type", "x0"),
            default_num_inference_steps=decoder_config.get("default_num_inference_steps", 1),
            operations=operations,
        )
        self.per_channel_statistics = _PerChannelStatistics(latent_channels)

        self.spatial_compression_ratio = self._spatial_compression(encoder_config)
        self.temporal_compression_ratio = self.decoder.temporal_compression_ratio
        self.use_tiling = False
        for name, value in self._TILE_DEFAULTS.items():
            setattr(self, name, value)

    @staticmethod
    def _spatial_compression(encoder_config: dict[str, Any]) -> int:
        ratio = encoder_config.get("patch_size", 1)
        for name, params in encoder_config["blocks"]:
            params = params if isinstance(params, dict) else {}
            if name in ("compress_space_res", "compress_all_res", "compress_space", "compress_all"):
                ratio *= 2
        return ratio

    @classmethod
    def from_config(cls, config: dict[str, Any], operations: Any) -> "LTXDiffusionVideoVAE":
        return cls(config=config, operations=operations)

    def post_load(self) -> None:
        # No computed buffers: per_channel_statistics are loaded weights and the
        # rotary frequencies are derived per forward from the token grid, never
        # persisted, so empty-weight construction leaves nothing stale behind.
        return None

    def reset_cache(self) -> None:
        """Clear the encoder's thread-local streaming cache. The diffusion
        decoder holds no such state -- it has no causal convolutions."""
        _clear_thread_cache(self)

    def enable_tiling(self, **tile_sizes: int | None) -> None:
        """Run the whole decode -- stages 1-3, stage 4, and the stage-5 blocks
        -- on overlapping latent tiles whose seams are blended linearly, so no
        full-clip feature volume is ever materialized."""
        self.use_tiling = True
        for name in self._TILE_DEFAULTS:
            value = tile_sizes.get(name)
            if value:
                setattr(self, name, value)

    def disable_tiling(self) -> None:
        self.use_tiling = False

    def encode(self, pixels: torch.Tensor) -> torch.Tensor:
        """``pixels``: ``(B, 3, T, H, W)`` in [-1, 1], ``T = 1 + 8*k``. Returns
        the normalized latent ``(B, latent_channels, T', H', W')``."""
        t = pixels.shape[2]
        if (t - 1) % self.temporal_compression_ratio != 0:
            raise ValueError(
                f"LTX diffusion VAE encode: T={t} invalid -- must be "
                f"1 + {self.temporal_compression_ratio}*k."
            )
        try:
            means, _logvar = torch.chunk(self.encoder(pixels), 2, dim=1)
            return self.per_channel_statistics.normalize(means)
        finally:
            self.reset_cache()

    def decode(self, latent: torch.Tensor, generator: torch.Generator | None = None,
               num_inference_steps: int | None = None) -> torch.Tensor:
        """``latent``: ``(B, latent_channels, T, H, W)``. Returns pixels
        ``(B, 3, T', H', W')`` in [-1, 1].

        The decoder samples the noise it denoises, so pass ``generator`` for a
        reproducible decode."""
        z = self.per_channel_statistics.un_normalize(latent)
        tile_latent_frames = self.tile_sample_min_num_frames // self.temporal_compression_ratio
        tile_latent_height = self.tile_sample_min_height // self.spatial_compression_ratio
        tile_latent_width = self.tile_sample_min_width // self.spatial_compression_ratio
        if self.use_tiling and (
            z.shape[2] > tile_latent_frames
            or z.shape[3] > tile_latent_height
            or z.shape[4] > tile_latent_width
        ):
            return self.tiled_decode(z, generator=generator, num_inference_steps=num_inference_steps)
        return self.decoder(z, generator=generator, num_inference_steps=num_inference_steps)

    def tiled_decode(self, z: torch.Tensor, generator: torch.Generator | None = None,
                     num_inference_steps: int | None = None) -> torch.Tensor:
        """Decode with the FULL stack -- stages 1-3, stage 4, and the stage-5
        diffusion blocks -- running per LATENT tile, so no full-clip feature
        volume is ever materialized. ``z`` is already un-normalized.

        Tiles live on the latent grid; ``tile_sample_*`` sizes (in output
        pixels) map back through the module's own ``temporal_compression_ratio``
        / ``spatial_compression_ratio``, so a size that isn't a whole number of
        latent cells rounds down. Temporal tiles follow the causal frame
        mapping: the tile containing latent frame 0 is the only one that drops
        any stage's duplicate leading frame (every temporal-stride-2 upsample
        in stages 1-4 is now gated per tile, not just stage 4's), and only the
        tile touching the clip's end carries ``trailing_pad_latent_frames`` --
        padded once, up front, on the whole latent, since padding per tile
        would replicate an interior tile's own last frame instead of the true
        clip end.
        """
        decoder = self.decoder
        num_inference_steps = num_inference_steps or decoder.default_num_inference_steps
        batch_size = z.shape[0]
        ratio_t, ratio_hw = self.temporal_compression_ratio, self.spatial_compression_ratio

        tile_latent_t = self.tile_sample_min_num_frames // ratio_t
        stride_latent_t = self.tile_sample_stride_num_frames // ratio_t
        tile_latent_h = self.tile_sample_min_height // ratio_hw
        stride_latent_h = self.tile_sample_stride_height // ratio_hw
        tile_latent_w = self.tile_sample_min_width // ratio_hw
        stride_latent_w = self.tile_sample_stride_width // ratio_hw
        min_sizes = _stage_min_latent_sizes(decoder)

        num_pad = decoder.trailing_pad_latent_frames
        if num_pad > 0:
            trailing = z[:, :, -1:].expand(-1, -1, num_pad, -1, -1)
            z_padded = torch.cat([z, trailing], dim=2)
        else:
            z_padded = z
        num_latent_frames = z.shape[2]
        height, width = z.shape[3], z.shape[4]

        temporal_tiles = _tile_intervals(num_latent_frames, tile_latent_t, stride_latent_t, min_sizes[0])
        height_tiles = _tile_intervals(height, tile_latent_h, stride_latent_h, min_sizes[1])
        width_tiles = _tile_intervals(width, tile_latent_w, stride_latent_w, min_sizes[2])
        blend_frames = (tile_latent_t - stride_latent_t) * ratio_t
        blend_height = (tile_latent_h - stride_latent_h) * ratio_hw
        blend_width = (tile_latent_w - stride_latent_w) * ratio_hw
        # Every temporal-stride-2 upsample in the decoder drops its duplicate
        # leading frame only for the tile touching latent frame 0, so that one
        # tile alone carries the WHOLE causal deficit ``(T - 1) * ratio_t + 1``
        # is built from -- every other tile needs the full correction to align
        # with it, not the single upsample's worth a features-space tiling
        # would have needed.
        origin_deficit = ratio_t - 1

        # A single-step x0 decode predicts pixels from pure noise, so each tile
        # draws its own; a multi-step decode integrates its noise across steps,
        # so overlapping tiles must start from the same canvas.
        single_step_x0 = num_inference_steps == 1 and decoder.model_output_type == "x0"
        x_t_full = None
        if not single_step_x0:
            pixel_frames = (num_latent_frames - 1) * ratio_t + 1
            x_t_full = _randn(
                (batch_size, decoder.out_channels, pixel_frames, height * ratio_hw, width * ratio_hw),
                generator, z.device, z.dtype,
            )

        frame_groups = []
        for t0, t1 in temporal_tiles:
            is_origin = t0 == 0
            is_trailing = t1 == num_latent_frames
            z_t1 = z_padded.shape[2] if is_trailing else t1
            rows = []
            for h0, h1 in height_tiles:
                row = []
                for w0, w1 in width_tiles:
                    features = decoder.forward_stages_1_to_3(
                        z_padded[:, :, t0:z_t1, h0:h1, w0:w1],
                        pad=False, drop_leading_frame=is_origin,
                    )
                    context = decoder.forward_stage_4(
                        features, drop_leading_frame=is_origin, crop_trailing_ghost=is_trailing,
                    )
                    tile_pixel_shape = decoder.pixel_shape(batch_size, context)
                    if single_step_x0:
                        x_t = _randn(tile_pixel_shape, generator, z.device, z.dtype)
                    else:
                        # A non-origin tile keeps every duplicate leading frame,
                        # so its content sits ``origin_deficit`` pixel frames
                        # later than a naive ``t0 * ratio_t`` would place it.
                        pixel_t0 = t0 * ratio_t - (0 if is_origin else origin_deficit)
                        x_t = x_t_full[
                            :, :,
                            pixel_t0:pixel_t0 + tile_pixel_shape[2],
                            h0 * ratio_hw:h0 * ratio_hw + tile_pixel_shape[3],
                            w0 * ratio_hw:w0 * ratio_hw + tile_pixel_shape[4],
                        ]
                    row.append(decoder.denoise(context, x_t, num_inference_steps))
                rows.append(row)

            result_rows = []
            for i, row in enumerate(rows):
                result_row = []
                for j, tile in enumerate(row):
                    if i > 0:
                        tile = _blend(rows[i - 1][j], tile, blend_height, dim=3)
                    if j > 0:
                        tile = _blend(row[j - 1], tile, blend_width, dim=4)
                    # The last tile can extend past the stride grid (a short
                    # remnant is merged into it), so it keeps its full extent.
                    keep_height = stride_latent_h * ratio_hw if i < len(rows) - 1 else tile.shape[3]
                    keep_width = stride_latent_w * ratio_hw if j < len(row) - 1 else tile.shape[4]
                    result_row.append(tile[:, :, :, :keep_height, :keep_width])
                result_rows.append(torch.cat(result_row, dim=4))
            frame_groups.append(torch.cat(result_rows, dim=3))

        result = []
        for k, group in enumerate(frame_groups):
            if k > 0:
                group = _blend(frame_groups[k - 1], group, blend_frames, dim=2)
            if k < len(frame_groups) - 1:
                # The origin group alone carries the causal deficit, so only it
                # keeps fewer than a full stride's worth of frames.
                keep_frames = stride_latent_t * ratio_t - (origin_deficit if k == 0 else 0)
                group = group[:, :, :keep_frames]
            result.append(group)
        return torch.cat(result, dim=2)

    def tiled_encode(self, pixels: torch.Tensor, tiling_config: "LtxTilingConfig | None" = None) -> torch.Tensor:
        """Tiled twin of :meth:`encode`, sharing the 2.0/2.3 encoder's tiling
        algorithm (``vae/ltx_tiling.py``) -- the encoder is the same module."""
        from .ltx_tiling import tiled_encode as _tiled_encode
        return _tiled_encode(self, pixels, tiling_config)
