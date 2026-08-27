"""VRAM-aware LTX-2/2.3 whole-clip/tiled VAE encode ladder, shared by every
pipe that hands raw pixels to ``LTXCausalVideoVAE.encode()``.

``LTXCausalVideoVAE.encode()`` has NO internal chunking -- unlike its own
``Decoder``, which self-chunks (see ``vae/ltx_tiling.py``'s module docstring).
A whole-clip encode of any real video is architecturally impossible past a
certain size: an instrumented run OOM'd a 32GB card on a clean-GPU
480x832x121-frame clip (~26GiB of encoder activations). This module is the
fix, first landed in ``latent_upscaler/ltx`` (the standalone-upscale path)
and extracted here so ``detailer/video_ltx``'s per-tube encode can reuse the
identical estimate-then-choose-then-retry ladder rather than re-deriving it --
a tube spanning a long track at working resolution can be a
multi-GB-to-tens-of-GB encode just like a full clip.

The ladder (:func:`encode_with_oom_retry`):

1. Estimate whole-clip activation memory from ``T*H*W`` (see
   :data:`ENCODE_BYTES_PER_PIXEL_FRAME`'s derivation). If it plausibly fits
   the currently free VRAM, attempt the whole-clip encode.
2. On CUDA OOM, evict every foreign GPU-resident component and retry the
   whole-clip encode once.
3. If the estimate said whole-clip wouldn't fit in the first place, or it
   still OOM'd after the retry, fall back to ``vae.module.tiled_encode``
   (LTX-2/2.3's own tiled encoder, ``vae/ltx_tiling.py`` -- a faithful port
   of Lightricks' first-party ``ltx-core``'s ``VideoEncoder.tiled_encode``)
   instead of raising.
4. Only if the TILED encode itself still OOMs does this finally raise.

Callers pass their own ``profiler_mark`` name (``ltx_upscale.encode`` for the
standalone upscale path, ``detailer.tube_encode`` for the per-tube refine)
and ``log_prefix`` (used in log lines and the final OOM error message) so
each call site's profiler/log output stays attributable to the pipe that made
the call, even though the ladder itself is shared.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import torch

from src.platform.observability.profiling import get_profiler
from src.platform.runtime.native.memory.residency import free_vram_gb, get_residency_registry
from src.platform.runtime.device import clear_gpu_memory
from src.platform.runtime.native.vae.ltx_tiling import LtxTilingConfig

logger = logging.getLogger(__name__)

# Whole-clip encoder activation bytes per (T*H*W) "pixel-frame" unit, derived
# from an instrumented whole-clip-OOM datapoint: 26GiB allocated encoding a
# clean-GPU 480x832x121-frame clip right before it OOM'd (~578 bytes/unit).
# Computed here rather than hardcoded so the derivation is auditable.
ENCODE_BYTES_PER_PIXEL_FRAME = (26 * (1024 ** 3)) / (121 * 480 * 832)

# Leave 25% VRAM headroom on top of the raw activation estimate before
# trusting a whole-clip attempt -- the same conservative fraction-of-free-VRAM
# idiom `vae/tiling.py` already uses (`causal3d_chunk_frames`'s 0.75,
# `auto_tile_size`'s 0.7).
ENCODE_VRAM_BUDGET_FRACTION = 0.75


def estimate_whole_clip_encode_gb(pixels: torch.Tensor) -> float:
    """Estimate whole-clip encoder activation memory from ``T*H*W`` (see
    :data:`ENCODE_BYTES_PER_PIXEL_FRAME`'s derivation). ``pixels`` is
    ``(B, C, T, H, W)`` -- only its shape is read."""
    _b, _c, t, h, w = pixels.shape
    return ENCODE_BYTES_PER_PIXEL_FRAME * t * h * w / (1024 ** 3)


def encode_with_oom_retry(
    vae: Any,
    pixels: torch.Tensor,
    device: str,
    *,
    tiling_config: Optional[LtxTilingConfig] = None,
    profiler_mark: str,
    log_prefix: str = "ltx_vae_encode",
) -> torch.Tensor:
    """``vae.module.encode(pixels)``, with the VRAM-aware ladder described in
    the module docstring. ``vae`` is a bundle's ``NativeModel``-shaped VAE
    component (``.module``, ``.compute_dtype``) -- callers pass ``bundle.vae``
    directly, not the whole bundle, since that's all this ladder touches.
    """
    if tiling_config is None:
        tiling_config = LtxTilingConfig.default()

    profiler = get_profiler()
    free_before = free_vram_gb(device)
    estimated_gb = estimate_whole_clip_encode_gb(pixels)
    budget_gb = free_before * ENCODE_VRAM_BUDGET_FRACTION if free_before is not None else None
    plausibly_fits = budget_gb is None or estimated_gb <= budget_gb

    def _do_encode() -> torch.Tensor:
        with torch.no_grad():
            return vae.module.encode(pixels.to(dtype=vae.compute_dtype))

    def _do_tiled_encode() -> torch.Tensor:
        with torch.no_grad():
            return vae.module.tiled_encode(pixels.to(dtype=vae.compute_dtype), tiling_config)

    def _tile_mark_fields() -> Dict[str, Any]:
        return {
            "tile_size_px": tiling_config.spatial.tile_size_in_pixels if tiling_config.spatial else None,
            "tile_overlap_px": tiling_config.spatial.tile_overlap_in_pixels if tiling_config.spatial else None,
            "tile_size_frames": tiling_config.temporal.tile_size_in_frames if tiling_config.temporal else None,
            "tile_overlap_frames": tiling_config.temporal.tile_overlap_in_frames if tiling_config.temporal else None,
        }

    def _mark(**fields: Any) -> None:
        profiler.mark(
            profiler_mark, device=str(device), free_vram_before_gb=free_before,
            estimated_gb=estimated_gb, budget_gb=budget_gb, **fields,
        )

    if plausibly_fits:
        try:
            latent = _do_encode()
            _mark(mode="whole", evicted=False, retried=False, free_vram_after_gb=free_vram_gb(device))
            return latent
        except torch.cuda.OutOfMemoryError:
            logger.warning(
                "%s: whole-clip VAE encode OOM'd (free_vram_before_gb=%s, estimated_gb=%.2f); "
                "evicting every foreign GPU-resident component and retrying once",
                log_prefix, free_before, estimated_gb,
            )
            get_residency_registry().offload_all(device, exclude=(vae,))
            clear_gpu_memory()
            try:
                latent = _do_encode()
                _mark(mode="whole", evicted=True, retried=True, free_vram_after_gb=free_vram_gb(device))
                return latent
            except torch.cuda.OutOfMemoryError:
                logger.warning(
                    "%s: whole-clip VAE encode still OOM'd after eviction; falling back to tiled encode",
                    log_prefix,
                )
    else:
        logger.debug(
            "%s: estimated whole-clip encode %.2fGB exceeds the %.2fGB budget (free_vram=%.2fGB) -- "
            "skipping the whole-clip attempt and going straight to tiled encode",
            log_prefix, estimated_gb, budget_gb, free_before,
        )
        get_residency_registry().offload_all(device, exclude=(vae,))
        clear_gpu_memory()

    try:
        latent = _do_tiled_encode()
    except torch.cuda.OutOfMemoryError as exc:
        _mark(mode="tiled", evicted=True, retried=True, oom=True, **_tile_mark_fields())
        raise torch.cuda.OutOfMemoryError(
            f"{log_prefix}: VAE encode OOM'd even with tiled encoding -- this clip/resolution "
            "does not fit in the remaining VRAM; try a lower resolution or a shorter source clip"
        ) from exc

    _mark(mode="tiled", evicted=True, retried=True, free_vram_after_gb=free_vram_gb(device), **_tile_mark_fields())
    return latent
