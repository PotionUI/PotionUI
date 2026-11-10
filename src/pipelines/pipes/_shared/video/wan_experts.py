from __future__ import annotations

from typing import Any
import numpy as np
import torch
from src.platform.runtime.native.errors import DecodeNumericsError, retry_after_cuda_oom
from src.platform.runtime.native.memory.residency import (
    effective_free_vram_gb,
    free_vram_gb,
    get_residency_registry,
    minimum_inference_memory_gb,
)
from src.platform.runtime.native.optimizations.compile import maybe_compile_dit
from src.platform.runtime.native.memory.tiering import sampling_headroom_gb
from src.platform.runtime.native.resolution import snap_frame_count, snap_resolution
from src.platform.runtime.native.sampling.denoise_loop import schedule_from_settings
from src.platform.runtime.native.vae.causal_3d import LATENTS_MEAN, LATENTS_STD
from src.platform.runtime.native.vae.causal_3d_v2 import LATENTS_MEAN as WAN22_LATENTS_MEAN, LATENTS_STD as WAN22_LATENTS_STD
from src.platform.runtime.native.vae.tiling import (
    causal3d_chunk_frames,
    causal3d_decode_mb_per_latent_px,
    causal3d_decode_overlap,
    causal3d_decode_tile,
    causal3d_frame_need_gb,
    chunked_decode_causal3d,
    smaller_decode_tile,
    tiled_decode_causal3d,
)
from src.platform.runtime.system_memory import get_system_memory
from src.platform.settings.runtime_flags import runtime_flag
from src.pipelines.contracts import logger
from src.pipelines.pipes._shared.generation.dit_placement import _dit_lora_delta_gb, _move_resident, _release_idle_pool
from src.pipelines.pipes._shared.media.pixel_convert import pixels_3thw_to_uint8_frames


WAN_TEMPORAL_DOWNSCALE = 4

_WAN_LATENT_STATS = {
    len(LATENTS_MEAN): (LATENTS_MEAN, LATENTS_STD),
    len(WAN22_LATENTS_MEAN): (WAN22_LATENTS_MEAN, WAN22_LATENTS_STD),
}


def wan_latent_stats(latent_channels: int) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    return _WAN_LATENT_STATS.get(int(latent_channels))


_warned_snapped_resolution: set[tuple[int, int]] = set()
_warned_snapped_frames: set[int] = set()


def snap_wan_geometry(bundle, lf: dict, width: int, height: int, frames: int,
                      *, patch_size=None) -> tuple[int, int, int]:
    spatial_downscale = int(lf.get("spatial_downscale", 8))
    patch = patch_size if patch_size is not None else bundle.high_dit.module.patch_size
    spatial_patch = int(patch[-1] if isinstance(patch, (tuple, list)) else patch)
    snapped_w, snapped_h = snap_resolution(width, height, spatial_downscale, spatial_patch)
    snapped_frames = snap_frame_count(frames, WAN_TEMPORAL_DOWNSCALE)
    if (snapped_w, snapped_h) != (width, height) and (width, height) not in _warned_snapped_resolution:
        _warned_snapped_resolution.add((width, height))
        logger.warning("[GENERATOR WAN] snapped resolution %dx%d -> %dx%d (granularity %dpx)",
                       width, height, snapped_w, snapped_h, spatial_downscale * spatial_patch)
    if snapped_frames != frames and frames not in _warned_snapped_frames:
        _warned_snapped_frames.add(frames)
        logger.warning("[GENERATOR WAN] snapped frames %d -> %d (1 + %d*k)",
                       frames, snapped_frames, WAN_TEMPORAL_DOWNSCALE)
    return snapped_w, snapped_h, snapped_frames


TIMESTEP_SCALE = 1000.0
_PARK_PINNED_SHARE = 0.6


class ExpertRouter:
    def __init__(self, high, low, boundary: float, device: str, riflex: dict | None = None,
                 latents_shape: tuple | None = None) -> None:
        self.high = high
        self.low = low
        self.boundary = boundary
        self.device = device
        self.active = None
        self.latents_shape = latents_shape
        self.riflex = riflex
        self.preview_reserve_gb = 0.0

    def _select(self, sigma_val: float):
        if self.low is None or sigma_val > self.boundary:
            return self.high
        return self.low

    def cache_identity(self, sigma_val: float) -> tuple:
        dit = self._select(sigma_val)
        return (id(dit.module), getattr(dit, "effective_revision", None))

    def _own_experts(self) -> list:
        return [e for e in (self.high, self.low) if e is not None]

    def _weights_budget_gb(self, dit) -> float:
        need = float(getattr(dit, "estimated_vram_gb", None) or 0.0) + _dit_lora_delta_gb(dit)
        _release_idle_pool(self.device)
        free = free_vram_gb(self.device)
        if free is None:
            return need
        shape = self.latents_shape
        frames = shape[2] if len(shape) == 5 else 1
        headroom = sampling_headroom_gb((shape[-2], shape[-1]), latent_frames=frames)
        headroom += max(0.0, float(self.preview_reserve_gb))
        reserve = max(minimum_inference_memory_gb(), headroom)
        target = need + reserve if need > 0.0 else 0.0
        evicted = get_residency_registry().ensure_free(self.device, target, free, exclude=self._own_experts())
        if evicted:
            logger.debug("[GENERATOR WAN] evicted %s for %.2fGB of expert weights + %.2fGB reserve (%.2fGB free)",
                         ", ".join(type(m).__name__ for m in evicted), need, reserve, free)
            _release_idle_pool(self.device)
        free = effective_free_vram_gb(self.device) or 0.0
        return max(0.0, free - headroom)

    def _expert_label(self, dit) -> str:
        return "low" if dit is self.low and dit is not self.high else "high"

    def _park_or_offload_active(self, incoming) -> None:
        outgoing = self.active
        if outgoing is None:
            return
        placed = str(getattr(outgoing, "device", "cpu")).startswith("cuda")
        if not placed or not callable(getattr(outgoing, "park", None)):
            outgoing.offload()
            return
        parked, reason = self._park(outgoing, incoming)
        logger.debug("[GENERATOR WAN] %s expert %s: %s", self._expert_label(outgoing),
                     "parked pinned" if parked else "torn down", reason)
        if not parked:
            outgoing.offload()

    def _park(self, outgoing, incoming) -> tuple[bool, str]:
        if not runtime_flag("native_park_experts"):
            return False, "parking is off (native_park_experts)"
        if not getattr(outgoing, "is_streaming", False):
            return False, "it was not streamed from pinned RAM"
        pinned_gb = sum(float(getattr(dit, "estimated_vram_gb", None) or 0.0) + _dit_lora_delta_gb(dit)
                        for dit in (outgoing, incoming))
        limit_gb = _PARK_PINNED_SHARE * get_system_memory().total_gb
        if pinned_gb > limit_gb:
            return False, (f"up to {pinned_gb:.2f}GB pinned with the next expert, over {limit_gb:.2f}GB "
                           f"({_PARK_PINNED_SHARE:.0%} of total RAM)")
        if not outgoing.park():
            return False, "its placement is not pinned or free host RAM would fall under the streaming reserve"
        return True, f"up to {pinned_gb:.2f}GB pinned with the next expert, limit {limit_gb:.2f}GB"

    def _place_expert(self, dit) -> None:
        self._park_or_offload_active(dit)
        self.active = dit
        if self.latents_shape is None or not str(self.device).startswith("cuda"):
            dit.move_to(self.device)
            maybe_compile_dit(dit, resident=True, is_cuda=str(self.device).startswith("cuda"))
            return
        budget = self._weights_budget_gb(dit)
        need = float(getattr(dit, "estimated_vram_gb", None) or 0.0) + _dit_lora_delta_gb(dit)
        if need and need <= budget:
            mode = _move_resident(dit, self.device, self._own_experts())
            if mode == "resident":
                maybe_compile_dit(dit, resident=True, is_cuda=True)
            return
        try:
            dit.stream_to(self.device, budget)
        except RuntimeError as exc:
            if not retry_after_cuda_oom(exc):
                raise
            logger.warning("[GENERATOR WAN] partial-residency expert placement OOM'd "
                           "(budget %.1fGB); evicting foreign residents and streaming fully", budget)
            dit.offload()
            get_residency_registry().offload_all(self.device, exclude=self._own_experts())
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            dit.stream_to(self.device, 0.0)

    def __call__(self, x: torch.Tensor, sigma: torch.Tensor, conditioning: dict) -> torch.Tensor:
        dit = self._select(float(sigma.max()))
        if dit is not self.active:
            self._place_expert(dit)
        extra = {}
        nag_context = conditioning.get("nag_context")
        if nag_context is not None:
            extra["nag_context"] = nag_context
            extra["nag"] = conditioning.get("nag")
        skip_layers = conditioning.get("skip_layers")
        if skip_layers:
            extra["skip_layers"] = skip_layers
        step_cache = conditioning.get("step_cache")
        if step_cache is not None:
            extra["step_cache"] = step_cache
        if self.riflex:
            extra["riflex"] = self.riflex
        return dit.module(x, sigma * TIMESTEP_SCALE, conditioning["context"], **extra)


def resolve_expert_boundary(spec, config: dict, sampling_settings: dict, steps: int,
                            *, default: float = 0.900, log_tag: str = "GENERATOR WAN") -> float:
    boundary = spec.sampling_settings.get("expert_boundary", default)

    switch_step = config.get("expert_switch_step")
    if switch_step not in (None, "", "None"):
        try:
            step_idx = int(switch_step)
        except (TypeError, ValueError):
            logger.warning("[%s] ignoring non-integer expert_switch_step %r", log_tag, switch_step)
            step_idx = None
        if step_idx is not None and step_idx > 0:
            sigmas = schedule_from_settings(int(steps), sampling_settings)
            return float(sigmas[min(step_idx, len(sigmas) - 1)])

    override = config.get("expert_boundary")
    if override not in (None, "", "None"):
        try:
            return float(override)
        except (TypeError, ValueError):
            logger.warning("[%s] ignoring non-numeric expert_boundary %r", log_tag, override)
    return boundary


def _decode_mb_per_latent_px(c: Any) -> float:
    return causal3d_decode_mb_per_latent_px(c.spatial_downscale, c.spec.latent_format)


def _make_room_for_decode(c: Any, latent: torch.Tensor) -> None:
    if not str(c.device).startswith("cuda") or not torch.cuda.is_available():
        return
    free = effective_free_vram_gb(c.device)
    if free is None:
        return
    need = causal3d_frame_need_gb((latent.shape[-2], latent.shape[-1]), vae_resident_gb=float(c.vae.estimated_vram_gb or 0.0),
                                  decode_mb_per_latent_px=_decode_mb_per_latent_px(c))
    evicted = get_residency_registry().ensure_free(c.device, need, free, exclude=[c.vae])
    if evicted:
        logger.info("[WAN DECODE] evicted %d resident component(s) (%s) before the VAE decode: %.2fGB needed, "
                    "%.2fGB free", len(evicted), ", ".join(type(m).__name__ for m in evicted), need, free)
        _release_idle_pool(c.device)


def _clear_card_for_decode(c: Any) -> None:
    if not str(c.device).startswith("cuda") or not torch.cuda.is_available():
        return
    get_residency_registry().offload_all(c.device, exclude=[c.vae])
    _release_idle_pool(c.device)


def _decode_latent(vae_module, z: torch.Tensor, chunk_frames: int | None) -> torch.Tensor:
    if chunk_frames is None:
        return vae_module.decode(z)
    return chunked_decode_causal3d(vae_module, z, chunk_latent_frames=chunk_frames, accumulate_device="cpu")


def _decode_tiled(c: Any, z: torch.Tensor, free: float) -> torch.Tensor:
    mb_per_latent_px = _decode_mb_per_latent_px(c)
    tile = causal3d_decode_tile((z.shape[-2], z.shape[-1]), free, decode_mb_per_latent_px=mb_per_latent_px)
    scale = c.spatial_downscale
    logger.info("[WAN DECODE] one latent frame does not fit %.2fGB free (%.1f MB per latent px at %dx): decoding in "
                "spatial tiles of %d latent px, one latent frame at a time", free, mb_per_latent_px, scale, tile)
    while True:
        try:
            return tiled_decode_causal3d(
                c.vae.module, z, tile_size=tile, overlap=causal3d_decode_overlap(tile), scale=scale,
                accumulate_device="cpu", chunk_latent_frames=1,
            )
        except RuntimeError as exc:
            smaller = smaller_decode_tile(tile)
            if not retry_after_cuda_oom(exc) or smaller >= tile:
                raise
            logger.warning("[WAN DECODE] tiled VAE decode failed for lack of VRAM at %d latent px (%s); "
                           "clearing the card and retrying at %d", tile, str(exc).splitlines()[0][:200], smaller)
        _clear_card_for_decode(c)
        tile = smaller


def _decode_pixels(c: Any, z: torch.Tensor) -> torch.Tensor:
    free = effective_free_vram_gb(c.device)
    mb_per_latent_px = _decode_mb_per_latent_px(c)
    if free is not None and free < causal3d_frame_need_gb((z.shape[-2], z.shape[-1]), decode_mb_per_latent_px=mb_per_latent_px):
        return _decode_tiled(c, z, free)
    chunk_frames = causal3d_chunk_frames(c.vae.module, z, free_vram_gb_value=free, decode_mb_per_latent_px=mb_per_latent_px)
    try:
        return _decode_latent(c.vae.module, z, chunk_frames)
    except RuntimeError as exc:
        if not retry_after_cuda_oom(exc):
            raise
        logger.warning("[WAN DECODE] VAE decode failed for lack of VRAM at %s latent frame(s) per chunk (%s); "
                       "clearing the card and decoding one latent frame at a time",
                       chunk_frames or int(z.shape[2]), str(exc).splitlines()[0][:200])
    _clear_card_for_decode(c)
    return _decode_latent(c.vae.module, z, 1)


def decode_wan_video(c: Any, latent: torch.Tensor) -> np.ndarray:
    _make_room_for_decode(c, latent)
    c.vae.move_to(c.device)
    z = latent.to(device=c.device, dtype=c.vae.compute_dtype)
    stats = wan_latent_stats(c.latent_channels)
    if stats is not None:
        mean = torch.tensor(stats[0], device=z.device, dtype=z.dtype).view(1, -1, 1, 1, 1)
        std = torch.tensor(stats[1], device=z.device, dtype=z.dtype).view(1, -1, 1, 1, 1)
        z = z * std + mean

    with torch.no_grad():
        pixels = _decode_pixels(c, z)
    c.vae.offload()

    if not torch.isfinite(pixels).all():
        raise DecodeNumericsError()

    return pixels_3thw_to_uint8_frames(pixels[0])
