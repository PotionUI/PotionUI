"""VRAM-budgeted, profiled MiniMax-H3 video decode -- the H3 counterpart of
:mod:`ltx_tiled_decode`.

The H3 video VAE decodes a clip as a stack of independent pieces: temporal
chunks of 5 latent frames (+2 of look-ahead), each split into 256px spatial
tiles, each tile a full 36-block ViT forward. At the shipped 1344x768 that is
7x4 = 28 tiles per chunk, and every one of them was its own launch of a
36-block transformer over ~1.8k tokens -- small enough that the decode spent
most of its wall clock dispatching rather than computing.

The tiles of a chunk never see each other (the blend that joins them runs
after all of them are decoded), so this module sizes a BATCH of tiles to the
free-VRAM budget and hands it to ``MiniMaxH3VideoVAE.decode_tile_batch_size``,
which then runs them as one forward. The blend math is untouched; the only
numerical difference is the reassociation a batched matmul/attention kernel is
free to make, which is why the sequential path (batch 1) stays byte-identical
and only batch > 1 is an fp16-tolerance match.

:func:`decode_bytes_per_tile_token` is the budget's input: peak decoder
activation bytes for ONE token, summed from the module's own widths rather
than a fitted constant, the same way ``ltx_tiled_decode`` prices its
whole-clip decode. It is an UPPER bound -- it adds up every transient that can
be live inside one block forward and does not model the allocator reusing
freed blocks between them -- so the batch it picks is conservative. Both the
estimate and the measured peak are on the ``minimax_h3.decode`` profiler mark,
so a real generation says how far apart they are.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import torch

from src.platform.observability.profiling import get_profiler
from src.platform.runtime.native.memory.residency import free_vram_gb

logger = logging.getLogger(__name__)

# Same conservative fraction-of-free-VRAM idiom as ltx_tiled_decode and
# vae/tiling.py: the decode does not own the whole card.
DECODE_VRAM_BUDGET_FRACTION = 0.75

_BYTES_PER_GB = 1 << 30
_FP32_BYTES = 4

# Tile sizes the pipe's `decode_tile_px` knob accepts. 0 means untiled (one
# forward over the whole frame). 256 is the reference default and the only one
# whose output is reference-parity; the others blend at different seams.
TILE_PX_CHOICES: tuple[int, ...] = (0, 256, 512)


def decode_bytes_per_tile_token(vae_module: Any) -> int:
    """Peak ViT-decoder activation bytes per token, from the module's widths.

    The terms, all able to be live inside one ``transformer_blocks`` forward:

    * the residual stream and the normed copy fed to a sublayer;
    * the fp32 promotion ``_rms_norm_affine_fp32`` makes of that input;
    * attention: the fused QKV projection, the contiguous q/k/v the per-head
      split feeds, the rotated halves RoPE concatenates, the attention output
      and its ``to_out`` projection -- plus the fp32 q/k RMS norms;
    * the SwiGLU feed-forward, whose fused ``w1`` alone is twice the FFN inner
      width and whose ``silu(gate) * value`` holds two more copies of it --
      the single largest term;
    * the RoPE cos/sin tables, held in fp32 for the whole block stack;
    * ``proj_out``'s per-token pixel patch and the contiguous permuted copy
      the un-patchify makes of it.
    """
    decoder = vae_module.decoder
    block = decoder.transformer_blocks[0]
    dim = block.attn.to_out.out_features
    inner = block.attn.to_out.in_features
    ffn_inner = block.ff.w2.in_features
    patch = decoder.proj_out.out_features
    rope_width = decoder.rope.inv_freq.numel() * 2 * decoder.rope.num_axes
    element = torch.finfo(next(vae_module.parameters()).dtype).bits // 8

    compute_elements = 4 * dim + 9 * inner + 4 * ffn_inner + 2 * patch
    fp32_elements = dim + 2 * inner + 2 * rope_width
    return element * compute_elements + _FP32_BYTES * fp32_elements


def decode_tile_tokens(vae_module: Any, latent_height: int, latent_width: int) -> int:
    """Token count of ONE decoded tile: the tile's own latent voxels plus the
    register tokens and the mask token the decoder appends to every forward."""
    ratio = vae_module.spatial_compression_ratio
    if vae_module.use_tiling:
        tile_h = min(latent_height, vae_module.tile_sample_min_height // ratio)
        tile_w = min(latent_width, vae_module.tile_sample_min_width // ratio)
    else:
        tile_h, tile_w = latent_height, latent_width
    frames = vae_module.tokens_chunk_size + vae_module.token_overlap
    return frames * tile_h * tile_w + decoder_extra_tokens(vae_module)


def decoder_extra_tokens(vae_module: Any) -> int:
    return vae_module.decoder.num_register_tokens + 1


def plan_tile_batch(vae_module: Any, latent: torch.Tensor, device: str) -> dict[str, Any]:
    """How many tiles of a chunk fit one batched forward on ``device``.

    Falls back to a batch of 1 (the sequential path) when free VRAM cannot be
    read -- a CPU decode, or a card whose ``mem_get_info`` failed. Guessing a
    batch there would be trading a decode that is merely slow for one that
    OOMs, and the activation estimate is not computed at all: there is no
    budget for it to be compared against.
    """
    rows, cols = vae_module.decode_tile_grid(latent.shape[-2], latent.shape[-1])
    tiles = rows * cols
    free_before = free_vram_gb(device)
    if free_before is None:
        return {
            "tiles_per_chunk": tiles, "tile_tokens": None, "batch_size": 1,
            "estimated_gb": None, "budget_gb": None, "free_vram_before_gb": None,
        }

    tokens = decode_tile_tokens(vae_module, latent.shape[-2], latent.shape[-1])
    per_tile_gb = decode_bytes_per_tile_token(vae_module) * tokens / _BYTES_PER_GB
    budget_gb = free_before * DECODE_VRAM_BUDGET_FRACTION
    batch = max(1, min(tiles, int(budget_gb // per_tile_gb))) if per_tile_gb > 0 else 1

    return {
        "tiles_per_chunk": tiles,
        "tile_tokens": tokens,
        "batch_size": batch,
        "estimated_gb": per_tile_gb * batch,
        "budget_gb": budget_gb,
        "free_vram_before_gb": free_before,
    }


def _optional(value: Any, spec: str = ".2f") -> str:
    """Format a field the plan leaves unset off CUDA (no VRAM budget, so no
    activation estimate) without turning the log line into a traceback."""
    return format(value, spec) if value is not None else "n/a"


def _tile_px(vae_module: Any) -> int:
    return vae_module.tile_sample_min_height if vae_module.use_tiling else 0


def apply_tile_px(vae_module: Any, tile_px: int) -> None:
    """Point the module's spatial tiling at ``tile_px`` (0 = untiled).

    The caller must restore what it read off the module first: ``ModelLifecycle``
    caches the VAE across generations, so a leaked tile size would silently
    re-blend every later decode at a seam spacing that request never asked for.
    """
    if tile_px <= 0:
        vae_module.use_tiling = False
        return
    vae_module.use_tiling = True
    vae_module.tile_sample_min_height = tile_px
    vae_module.tile_sample_min_width = tile_px


def decode_video(
    vae_module: Any,
    latent: torch.Tensor,
    device: str,
    *,
    tile_px: Optional[int] = None,
    log_prefix: str = "[GENERATOR MINIMAX-H3]",
) -> torch.Tensor:
    """``vae_module.decode(latent)`` with a budgeted tile batch, one
    ``minimax_h3.decode`` mark per decode and one ``minimax_h3.decode.chunk``
    mark per temporal chunk.

    ``tile_px`` overrides the module's spatial tile size for this decode only;
    ``None`` leaves it at whatever the module carries (256px, reference parity).
    """
    is_cuda = str(device).startswith("cuda")
    profiler = get_profiler()

    was_tiling = vae_module.use_tiling
    was_height = vae_module.tile_sample_min_height
    was_width = vae_module.tile_sample_min_width
    was_batch = vae_module.decode_tile_batch_size
    was_observer = vae_module.decode_chunk_observer
    try:
        if tile_px is not None:
            apply_tile_px(vae_module, tile_px)
        plan = plan_tile_batch(vae_module, latent, device)
        vae_module.decode_tile_batch_size = plan["batch_size"]

        chunk_clock = 0.0

        def _observe(*, index: int, tiles: int) -> None:
            nonlocal chunk_clock
            if is_cuda:
                torch.cuda.synchronize()
            now = time.perf_counter()
            profiler.mark(
                "minimax_h3.decode.chunk", index=index, tiles=tiles,
                batch_size=plan["batch_size"], seconds=now - chunk_clock,
            )
            chunk_clock = now

        vae_module.decode_chunk_observer = _observe

        if is_cuda:
            torch.cuda.reset_peak_memory_stats(device)
            torch.cuda.synchronize()
        started = time.perf_counter()
        chunk_clock = started
        pixels = vae_module.decode(latent)
        if is_cuda:
            torch.cuda.synchronize()
        seconds = time.perf_counter() - started
        peak_vram_gb = torch.cuda.max_memory_allocated(device) / _BYTES_PER_GB if is_cuda else None

        fields = dict(
            device=str(device), frames=pixels.shape[2], latent_frames=latent.shape[2],
            height=pixels.shape[-2], width=pixels.shape[-1],
            chunks=vae_module.decode_chunk_count(latent.shape[2]),
            tile_px=_tile_px(vae_module), seconds=seconds, peak_vram_gb=peak_vram_gb,
            **{k: plan[k] for k in ("tiles_per_chunk", "tile_tokens", "batch_size", "estimated_gb", "budget_gb", "free_vram_before_gb")},
        )
        profiler.mark("minimax_h3.decode", **fields)
        logger.info(
            "%s: decoded %d frames at %dx%d in %.2fs "
            "(latent_frames=%d chunks=%d tile_px=%d tiles_per_chunk=%d batch=%d "
            "tile_tokens=%s estimated_gb=%s budget_gb=%s peak_vram_gb=%s)",
            log_prefix, fields["frames"], fields["width"], fields["height"], seconds,
            fields["latent_frames"], fields["chunks"], fields["tile_px"],
            fields["tiles_per_chunk"], fields["batch_size"],
            _optional(fields["tile_tokens"], "d"), _optional(fields["estimated_gb"]),
            _optional(fields["budget_gb"]), _optional(peak_vram_gb),
        )
        return pixels
    finally:
        vae_module.use_tiling = was_tiling
        vae_module.tile_sample_min_height = was_height
        vae_module.tile_sample_min_width = was_width
        vae_module.decode_tile_batch_size = was_batch
        vae_module.decode_chunk_observer = was_observer
