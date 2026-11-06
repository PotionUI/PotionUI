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
from src.platform.runtime.native.vae.tiling import causal3d_chunk_frames, chunked_decode_causal3d
from src.pipelines.contracts import logger
from src.pipelines.pipes._shared.generation.dit_placement import _dit_lora_delta_gb, _move_resident, _release_idle_pool
from src.pipelines.pipes._shared.media.pixel_convert import pixels_3thw_to_uint8_frames


WAN_TEMPORAL_DOWNSCALE = 4


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
        reserve = max(minimum_inference_memory_gb(), headroom)
        target = need + reserve if need > 0.0 else 0.0
        evicted = get_residency_registry().ensure_free(self.device, target, free, exclude=self._own_experts())
        if evicted:
            logger.debug("[GENERATOR WAN] evicted %s for %.2fGB of expert weights + %.2fGB reserve (%.2fGB free)",
                         ", ".join(type(m).__name__ for m in evicted), need, reserve, free)
            _release_idle_pool(self.device)
        free = effective_free_vram_gb(self.device) or 0.0
        return max(0.0, free - headroom)

    def _place_expert(self, dit) -> None:
        if self.active is not None:
            self.active.offload()
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
        dit = self._select(float(sigma.reshape(-1)[0]))
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


def decode_wan_video(c: Any, latent: torch.Tensor) -> np.ndarray:
    c.vae.move_to(c.device)
    z = latent.to(device=c.device, dtype=c.vae.compute_dtype)
    if c.latent_channels == len(LATENTS_MEAN):
        mean = torch.tensor(LATENTS_MEAN, device=z.device, dtype=z.dtype).view(1, -1, 1, 1, 1)
        std = torch.tensor(LATENTS_STD, device=z.device, dtype=z.dtype).view(1, -1, 1, 1, 1)
        z = z * std + mean

    with torch.no_grad():
        chunk_frames = causal3d_chunk_frames(c.vae.module, z, free_vram_gb_value=free_vram_gb(c.device))
        if chunk_frames is not None:
            pixels = chunked_decode_causal3d(
                c.vae.module, z, chunk_latent_frames=chunk_frames, accumulate_device="cpu",
            )
        else:
            pixels = c.vae.module.decode(z)
    c.vae.offload()

    if not torch.isfinite(pixels).all():
        raise DecodeNumericsError()

    return pixels_3thw_to_uint8_frames(pixels[0])
