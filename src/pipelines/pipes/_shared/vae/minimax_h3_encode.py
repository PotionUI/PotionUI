from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

import torch

from src.pipelines.pipes._shared.vae.minimax_h3_decode import apply_tile_px
from src.platform.observability.profiling import get_profiler
from src.platform.runtime.native.errors import retry_after_cuda_oom
from src.platform.runtime.native.memory.residency import free_vram_gb

logger = logging.getLogger(__name__)

ENCODE_LIVE_FULL_RES_TENSORS = 6
ENCODE_VRAM_BUDGET_FRACTION = 0.75
ENCODE_TILE_PX_LADDER: tuple[int, ...] = (256, 192, 128)
_BYTES_PER_GB = 1 << 30
_CAUSAL_TEMPORAL_PAD = 2
_REFLECT_PAD = 2


def encode_compute_dtype(vae_module: Any) -> torch.dtype:
    if getattr(vae_module.encoder.conv_in, "comfy_cast_weights", False):
        return torch.float32
    for parameter in vae_module.encoder.parameters():
        if parameter.is_floating_point():
            return parameter.dtype
    return torch.float32


def _element_bytes(dtype: torch.dtype) -> int:
    return torch.empty((), dtype=dtype).element_size()


def encode_bytes_per_voxel(vae_module: Any, dtype: torch.dtype) -> int:
    conv_in = vae_module.encoder.conv_in
    elements = ENCODE_LIVE_FULL_RES_TENSORS * conv_in.out_channels + conv_in.in_channels
    return _element_bytes(dtype) * elements


def encode_tile_voxels(vae_module: Any, tile_px: int, height: int, width: int) -> int:
    tile_h = height if tile_px <= 0 else min(height, tile_px)
    tile_w = width if tile_px <= 0 else min(width, tile_px)
    frames = vae_module.clip_length + _CAUSAL_TEMPORAL_PAD
    return frames * (tile_h + _REFLECT_PAD) * (tile_w + _REFLECT_PAD)


def encode_tile_gb(vae_module: Any, tile_px: int, height: int, width: int, dtype: torch.dtype) -> float:
    voxels = encode_tile_voxels(vae_module, tile_px, height, width)
    return voxels * encode_bytes_per_voxel(vae_module, dtype) / _BYTES_PER_GB


def encode_clip_pixels_gb(vae_module: Any, height: int, width: int, dtype: torch.dtype) -> float:
    return vae_module.clip_length * 3 * height * width * _element_bytes(dtype) / _BYTES_PER_GB


def module_tile_px(vae_module: Any) -> int:
    return vae_module.tile_sample_min_height if vae_module.use_tiling else 0


def encode_activation_gb(vae_module: Any, height: int, width: int, dtype: torch.dtype) -> float:
    tile_gb = encode_tile_gb(vae_module, module_tile_px(vae_module), height, width, dtype)
    return tile_gb + encode_clip_pixels_gb(vae_module, height, width, dtype)


def plan_encode(
    vae_module: Any, height: int, width: int, dtype: torch.dtype, device: str,
    ladder: tuple[int, ...] = ENCODE_TILE_PX_LADDER,
) -> dict[str, Any]:
    free_before = free_vram_gb(device)
    if free_before is None:
        return {
            "tile_px": ladder[0], "batch_size": 1, "per_tile_gb": None, "estimated_gb": None,
            "budget_gb": None, "free_vram_before_gb": None,
        }
    budget_gb = free_before * ENCODE_VRAM_BUDGET_FRACTION
    room_gb = budget_gb - encode_clip_pixels_gb(vae_module, height, width, dtype)
    tile_px = ladder[-1]
    for candidate in ladder:
        if encode_tile_gb(vae_module, candidate, height, width, dtype) <= room_gb:
            tile_px = candidate
            break
    per_tile_gb = encode_tile_gb(vae_module, tile_px, height, width, dtype)
    batch = max(1, int(room_gb // per_tile_gb)) if per_tile_gb > 0 else 1
    return {
        "tile_px": tile_px, "batch_size": batch, "per_tile_gb": per_tile_gb, "estimated_gb": per_tile_gb * batch,
        "budget_gb": budget_gb, "free_vram_before_gb": free_before,
    }


def _encode_with_plan(vae_module: Any, pixels: torch.Tensor, tile_px: int, batch_size: int) -> torch.Tensor:
    apply_tile_px(vae_module, tile_px)
    rows, cols = vae_module.encode_tile_grid(int(pixels.shape[-2]), int(pixels.shape[-1]))
    vae_module.encode_tile_batch_size = max(1, min(batch_size, rows * cols))
    with torch.no_grad():
        return vae_module.encode(pixels)


def encode_video(
    vae_module: Any, pixels: torch.Tensor, device: str, *,
    free_more: Optional[Callable[[], bool]] = None, log_prefix: str = "[H3_UPSCALE]",
) -> torch.Tensor:
    is_cuda = str(device).startswith("cuda")
    profiler = get_profiler()
    height, width = int(pixels.shape[-2]), int(pixels.shape[-1])
    dtype = pixels.dtype
    plan = plan_encode(vae_module, height, width, dtype, device)
    ladder = [px for px in ENCODE_TILE_PX_LADDER if px <= plan["tile_px"]] or [plan["tile_px"]]
    batch_size = plan["batch_size"]

    was_tiling = vae_module.use_tiling
    was_height = vae_module.tile_sample_min_height
    was_width = vae_module.tile_sample_min_width
    was_batch = vae_module.encode_tile_batch_size
    retries: list[str] = []
    try:
        if is_cuda:
            torch.cuda.reset_peak_memory_stats(device)
        started = time.perf_counter()
        step = 0
        freed = False
        while True:
            tile_px = ladder[step]
            try:
                latent = _encode_with_plan(vae_module, pixels, tile_px, batch_size)
                break
            except RuntimeError as exc:
                if not retry_after_cuda_oom(exc):
                    raise
                if is_cuda:
                    torch.cuda.empty_cache()
                if not freed and free_more is not None and free_more():
                    freed = True
                    retries.append(f"freed@{tile_px}")
                    continue
                if step + 1 >= len(ladder):
                    raise torch.cuda.OutOfMemoryError(
                        f"{log_prefix} the MiniMax-H3 video VAE encode ran out of VRAM even at {tile_px}px tiles, "
                        "one tile at a time -- free VRAM held by other processes or use a shorter source clip"
                    ) from exc
                step += 1
                batch_size = 1
                retries.append(f"oom@{tile_px}")
                logger.warning("%s VAE encode OOM at %dpx tiles; retrying at %dpx", log_prefix, tile_px, ladder[step])
        if is_cuda:
            torch.cuda.synchronize()
        seconds = time.perf_counter() - started
        peak_vram_gb = torch.cuda.max_memory_allocated(device) / _BYTES_PER_GB if is_cuda else None
        rows, cols = vae_module.encode_tile_grid(height, width)
        fields = dict(
            device=str(device), frames=int(pixels.shape[2]), height=height, width=width,
            latent_frames=int(latent.shape[2]), tile_px=module_tile_px(vae_module), tiles_per_clip=rows * cols,
            batch_size=vae_module.encode_tile_batch_size, dtype=str(dtype).replace("torch.", ""),
            seconds=round(seconds, 3), peak_vram_gb=peak_vram_gb, retries=retries,
            **{k: plan[k] for k in ("per_tile_gb", "estimated_gb", "budget_gb", "free_vram_before_gb")},
        )
        profiler.mark("minimax_h3.encode", **fields)
        logger.info(
            "%s encoded %d frames at %dx%d in %.2fs (latent_frames=%d tile_px=%d tiles_per_clip=%d batch=%d "
            "dtype=%s per_tile_gb=%s budget_gb=%s peak_vram_gb=%s retries=%s)",
            log_prefix, fields["frames"], width, height, seconds, fields["latent_frames"], fields["tile_px"],
            fields["tiles_per_clip"], fields["batch_size"], fields["dtype"], _optional(plan["per_tile_gb"]),
            _optional(plan["budget_gb"]), _optional(peak_vram_gb), ",".join(retries) or "none",
        )
        return latent
    finally:
        vae_module.use_tiling = was_tiling
        vae_module.tile_sample_min_height = was_height
        vae_module.tile_sample_min_width = was_width
        vae_module.encode_tile_batch_size = was_batch


def _optional(value: Any) -> str:
    return format(value, ".2f") if value is not None else "n/a"
