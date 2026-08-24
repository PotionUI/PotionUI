# Vendored from ComfyUI — https://github.com/comfyanonymous/ComfyUI
# Upstream path: comfy/ldm/lightricks/model.py (rotary-embedding + freq grid
# helpers) + av_model.py (CompressedTimestep) @ unknown; vendored ~2025
# (moved into vendor/gpl/comfyui/ltx/ from
# src/platform/runtime/native/arch/ltx/ as part of the license-relocation
# workstream, BE-97).
# License: GPL-3.0 (see ../LICENSE). Copyright (c) comfyanonymous and contributors.

"""RoPE + compressed-timestep infrastructure for the LTX-2 AV forward.

Vendored from ComfyUI ``comfy/ldm/lightricks/model.py`` (rotary-embedding + freq
grid helpers) and ``av_model.py`` (``CompressedTimestep``). Pure functions /
stateless helper — no registered buffers, so the DiT's ``post_load`` stays a
no-op (RoPE frequencies are recomputed per forward from the patch positions).

LTX uses TWO rotary conventions selected per call by a ``split`` flag carried as
the 3rd element of the ``freqs_cis`` tuple ``(cos, sin, split)``:
  * interleaved (``split=False``): the classic ``x·cos + rotate_half(x)·sin``.
  * split (``split=True``): halves rotated against each other, cos/sin already
    reshaped to ``(B, heads, T, dim/2)``. The connector + LTX-2.3 use this.
"""

from __future__ import annotations

import functools
import math

import numpy as np
import torch
from einops import rearrange


def _log_base(x: float, base: float) -> float:
    return math.log(x) / math.log(base)


def apply_interleaved_rotary_emb(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    t_dup = rearrange(x, "... (d r) -> ... d r", r=2)
    t1, t2 = t_dup.unbind(dim=-1)
    t_dup = torch.stack((-t2, t1), dim=-1)
    x_rot = rearrange(t_dup, "... d r -> ... (d r)")
    return x * cos + x_rot * sin


def apply_split_rotary_emb(x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor) -> torch.Tensor:
    needs_reshape = False
    B = H = T = None
    if x.ndim != 4 and cos.ndim == 4:
        # Head-split a flat (B, T, H*D) input. The batch size MUST come from x,
        # not cos: the connector builds its freqs with a batch dim of 1 (they
        # broadcast), so using cos's batch would fold a multi-sample batch into
        # the head dim (first hit by quality mode's batched pos+neg encode:
        # per-head 128 -> 256 vs cos halves of 64).
        _, H, T, _ = cos.shape
        B = x.shape[0]
        x = x.reshape(B, T, H, -1).swapaxes(1, 2)
        needs_reshape = True
    split_input = rearrange(x, "... (d r) -> ... d r", d=2)
    first_half = split_input[..., :1, :]
    second_half = split_input[..., 1:, :]
    output = split_input * cos.unsqueeze(-2)
    first_out = output[..., :1, :]
    second_out = output[..., 1:, :]
    first_out.addcmul_(-sin.unsqueeze(-2), second_half)
    second_out.addcmul_(sin.unsqueeze(-2), first_half)
    output = rearrange(output, "... d r -> ... (d r)")
    return output.swapaxes(1, 2).reshape(B, T, -1) if needs_reshape else output


def apply_rotary_emb(x: torch.Tensor, freqs_cis) -> torch.Tensor:
    cos, sin = freqs_cis[0], freqs_cis[1]
    split = freqs_cis[2] if len(freqs_cis) > 2 else False
    return apply_split_rotary_emb(x, cos, sin) if split else apply_interleaved_rotary_emb(x, cos, sin)


def get_fractional_positions(indices_grid: torch.Tensor, max_pos) -> torch.Tensor:
    n_pos_dims = indices_grid.shape[1]
    return torch.stack([indices_grid[:, i] / max_pos[i] for i in range(n_pos_dims)], dim=-1)


@functools.lru_cache(maxsize=16)
def generate_freq_grid_np(theta: float, max_pos_count: int, inner_dim: int) -> torch.Tensor:
    """Double-precision log-spaced frequency indices (ComfyUI ``generate_freq_grid_np``)."""
    n_elem = 2 * max_pos_count
    pow_indices = np.power(
        theta,
        np.linspace(_log_base(1, theta), _log_base(theta, theta), inner_dim // n_elem, dtype=np.float64),
    )
    return torch.tensor(pow_indices * math.pi / 2, dtype=torch.float32)


def _resolve_indices_grid(indices_grid: torch.Tensor, use_middle_indices_grid: bool) -> torch.Tensor:
    """Collapse a ``(start, end)``-paired or 4-D indices grid down to the plain
    ``(B, n_pos_dims, T)`` grid ``generate_freqs`` operates on. Split out of
    ``generate_freqs`` so the chunked builder can resolve ONCE (this part is
    cheap — it never scales with the model's ``inner_dim``) and then slice the
    resolved grid per token-chunk for the expensive part."""
    if use_middle_indices_grid:
        start, end = indices_grid[..., 0], indices_grid[..., 1]
        return (start + end) / 2.0
    if len(indices_grid.shape) == 4:
        return indices_grid[..., 0]
    return indices_grid


def generate_freqs(indices, indices_grid, max_pos, use_middle_indices_grid: bool = False) -> torch.Tensor:
    indices_grid = _resolve_indices_grid(indices_grid, use_middle_indices_grid)
    fractional = get_fractional_positions(indices_grid, max_pos)
    indices = indices.to(device=fractional.device)
    return (indices * (fractional.unsqueeze(-1) * 2 - 1)).transpose(-1, -2).flatten(2)


def freq_feature_dim(indices: torch.Tensor, n_pos_dims: int) -> int:
    """The size of ``generate_freqs``'s last (feature) dim, by pure shape
    arithmetic — lets a caller size RoPE padding without materializing the
    (potentially huge, per-token) freqs tensor first."""
    return indices.shape[-1] * n_pos_dims


# Token-chunk size for the bit-exact chunked builder below. At S≈110,880 tokens
# (720x1280, LTX long-video) the whole-tensor fp32 construction (freqs + cos +
# sin, inner_dim=2048) is a ~2.5GB transient rebuilt every denoise step; capping
# construction at this many tokens per chunk bounds the transient to a few
# hundred MB regardless of S, while casting each chunk to the target dtype
# before the next chunk is built keeps only one chunk's fp32 data live at a time.
DEFAULT_ROPE_CHUNK_TOKENS = 8192


def build_freqs_cis_chunked(
    indices: torch.Tensor,
    indices_grid: torch.Tensor,
    max_pos,
    pad_size: int,
    out_dtype: torch.dtype,
    *,
    use_middle_indices_grid: bool = False,
    split_mode: bool = False,
    num_attention_heads: int | None = None,
    chunk_tokens: int = DEFAULT_ROPE_CHUNK_TOKENS,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Bit-identical replacement for ``generate_freqs`` + ``{interleaved,split}_freqs_cis``
    + the caller's final ``.to(out_dtype)`` cast — built T tokens at a time.

    Every token's cos/sin is a pure function of that token's own fractional
    position (no cross-token reduction anywhere in the math), so slicing the
    token axis into chunks, running the identical fp32 math on each slice, and
    concatenating the per-chunk ``out_dtype`` casts reproduces the whole-tensor
    path exactly — this is a memory-shape change only, never a numeric one.
    Each chunk's fp32 intermediates go out of scope (eligible for GC) before
    the next chunk is built, so the fp32 spike is capped at ``chunk_tokens``
    instead of scaling with the full sequence length ``S``.
    """
    resolved = _resolve_indices_grid(indices_grid, use_middle_indices_grid)
    total = resolved.shape[-1]
    t_dim = 2 if split_mode else 1  # split: (B, heads, T, d); interleaved: (B, T, d)
    cos_chunks: list[torch.Tensor] = []
    sin_chunks: list[torch.Tensor] = []
    # An empty stream (e.g. video-only forward, no audio latent: T=0) must still
    # run once over an empty slice to produce a correctly-shaped empty result —
    # `range(0, 0, chunk_tokens)` has zero iterations, which would otherwise leave
    # the chunk lists empty and torch.cat below would raise.
    for start in range(0, total, chunk_tokens) if total > 0 else (0,):
        grid_chunk = resolved[..., start : start + chunk_tokens]
        fractional = get_fractional_positions(grid_chunk, max_pos)
        idx = indices.to(device=fractional.device)
        freqs_chunk = (idx * (fractional.unsqueeze(-1) * 2 - 1)).transpose(-1, -2).flatten(2)
        if split_mode:
            cos_c, sin_c = split_freqs_cis(freqs_chunk, pad_size, num_attention_heads)
        else:
            cos_c, sin_c = interleaved_freqs_cis(freqs_chunk, pad_size)
        cos_chunks.append(cos_c.to(out_dtype))
        sin_chunks.append(sin_c.to(out_dtype))
    if len(cos_chunks) == 1:
        return cos_chunks[0], sin_chunks[0]
    return torch.cat(cos_chunks, dim=t_dim), torch.cat(sin_chunks, dim=t_dim)


def interleaved_freqs_cis(freqs: torch.Tensor, pad_size: int):
    cos = freqs.cos().repeat_interleave(2, dim=-1)
    sin = freqs.sin().repeat_interleave(2, dim=-1)
    if pad_size != 0:
        cos = torch.cat([torch.ones_like(cos[:, :, :pad_size]), cos], dim=-1)
        sin = torch.cat([torch.zeros_like(sin[:, :, :pad_size]), sin], dim=-1)
    return cos, sin


def split_freqs_cis(freqs: torch.Tensor, pad_size: int, num_attention_heads: int):
    cos = freqs.cos()
    sin = freqs.sin()
    if pad_size != 0:
        cos = torch.cat([torch.ones_like(cos[:, :, :pad_size]), cos], dim=-1)
        sin = torch.cat([torch.zeros_like(sin[:, :, :pad_size]), sin], dim=-1)
    b, t, half_hd = cos.shape
    cos = cos.reshape(b, t, num_attention_heads, half_hd // num_attention_heads).swapaxes(1, 2)
    sin = sin.reshape(b, t, num_attention_heads, half_hd // num_attention_heads).swapaxes(1, 2)
    return cos, sin


class CompressedTimestep:
    """Store per-frame video timestep adaLN embeddings compactly (ComfyUI ``CompressedTimestep``).

    All spatial patches of a frame share the same timestep embedding, so only one
    value per frame is kept; ``expand_for_computation`` computes the adaLN
    scale/shift on the compact per-frame data then broadcasts back over patches.
    """

    __slots__ = ("data", "batch_size", "num_frames", "patches_per_frame", "feature_dim")

    def __init__(self, tensor: torch.Tensor, patches_per_frame: int | None):
        self.batch_size, num_tokens, self.feature_dim = tensor.shape
        if patches_per_frame is not None and num_tokens % patches_per_frame == 0 and num_tokens >= patches_per_frame:
            self.patches_per_frame = patches_per_frame
            self.num_frames = num_tokens // patches_per_frame
            reshaped = tensor.view(self.batch_size, self.num_frames, patches_per_frame, self.feature_dim)
            self.data = reshaped[:, :, 0, :].contiguous()
        else:
            self.patches_per_frame = 1
            self.num_frames = num_tokens
            self.data = tensor

    def expand(self) -> torch.Tensor:
        if self.patches_per_frame == 1:
            return self.data
        expanded = self.data.unsqueeze(2).expand(self.batch_size, self.num_frames, self.patches_per_frame, self.feature_dim)
        return expanded.reshape(self.batch_size, -1, self.feature_dim)

    def expand_for_computation(self, scale_shift_table: torch.Tensor, batch_size: int, indices: slice = slice(None, None)):
        num_ada_params = scale_shift_table.shape[0]
        if self.patches_per_frame == 1:
            num_tokens = self.data.shape[1]
            dim_per_param = self.feature_dim // num_ada_params
            reshaped = self.data.reshape(batch_size, num_tokens, num_ada_params, dim_per_param)[:, :, indices, :]
            table_values = scale_shift_table[indices].unsqueeze(0).unsqueeze(0).to(device=self.data.device, dtype=self.data.dtype)
            return (table_values + reshaped).unbind(dim=2)

        frame_reshaped = self.data.reshape(batch_size, self.num_frames, num_ada_params, -1)[:, :, indices, :]
        table_values = scale_shift_table[indices].unsqueeze(0).unsqueeze(0).to(device=self.data.device, dtype=self.data.dtype)
        frame_ada = (table_values + frame_reshaped).unbind(dim=2)
        return tuple(
            frame_val.unsqueeze(2).expand(batch_size, self.num_frames, self.patches_per_frame, -1).reshape(batch_size, -1, frame_val.shape[-1])
            for frame_val in frame_ada
        )
