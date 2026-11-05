from __future__ import annotations

from typing import Any, Dict, TypedDict
import numpy as np
import torch
from src.platform.runtime.native.errors import DecodeNumericsError
from src.platform.runtime.native.memory.residency import free_vram_gb
from src.platform.runtime.native.resolution import snap_frame_count, snap_resolution
from src.platform.runtime.native.vae.tiling import causal3d_chunk_frames, chunked_decode_causal3d
from src.pipelines.contracts import logger
from src.pipelines.pipes._shared.vae.ltx_tiled_decode import (
    DECODE_NOISE_SEED_OFFSET,
    decode_with_oom_retry,
)
from src.pipelines.pipes._shared.generation.guidance_options import (
    check_guider_mode_conflict,
)
from src.pipelines.pipes._shared.media.pixel_convert import pixels_3thw_to_uint8_frames
from src.pipelines.pipes._shared.video.ltx_constants import (
    LTX_ADALN_TABLE_ROWS,
    LTX_AV_CROSS_ADALN_ROWS,
    LTX_AV_NORM_ROWS,
    LTX_FFN_MULT,
    LTX_MODULATION_ROWS,
    LTX_PROMPT_ADALN_MODULATION_ROWS,
    LTX_PROMPT_ADALN_TABLE_ROWS,
    LTX_SPATIAL_DOWNSCALE,
    LTX_TEMPORAL_DOWNSCALE,
)


_warned_snapped_resolution: set[tuple[int, int]] = set()
_warned_snapped_frames: set[int] = set()


class LtxReserveTerms(TypedDict):
    gelu_ffn_dim: int
    modulation_rows: int


def ltx_reserve_terms(
    dit: Any, *, per_token_timestep: bool, per_token_frames_broken: bool = False, generates_audio: bool = False,
) -> LtxReserveTerms:
    config = dit.module.config
    rows = 0
    if per_token_timestep:
        rows = LTX_PROMPT_ADALN_MODULATION_ROWS if config.has_prompt_adaln else LTX_MODULATION_ROWS
        if per_token_frames_broken or generates_audio:
            av_cross_rows = 0 if config.use_cross_timestep else LTX_AV_CROSS_ADALN_ROWS
            if per_token_frames_broken:
                table_rows = LTX_PROMPT_ADALN_TABLE_ROWS if config.has_prompt_adaln else LTX_ADALN_TABLE_ROWS
                rows += table_rows + av_cross_rows
            if generates_audio:
                rows += LTX_AV_NORM_ROWS + av_cross_rows
    return {"gelu_ffn_dim": LTX_FFN_MULT * config.inner_dim, "modulation_rows": rows}


def snap_ltx_geometry(width: int, height: int, frames: int) -> tuple[int, int, int]:
    snapped_w, snapped_h = snap_resolution(width, height, LTX_SPATIAL_DOWNSCALE, 1)
    snapped_frames = snap_frame_count(frames, LTX_TEMPORAL_DOWNSCALE)
    if (snapped_w, snapped_h) != (width, height) and (width, height) not in _warned_snapped_resolution:
        _warned_snapped_resolution.add((width, height))
        logger.warning("[GENERATOR LTX] snapped resolution %dx%d -> %dx%d (granularity %dpx)",
                       width, height, snapped_w, snapped_h, LTX_SPATIAL_DOWNSCALE)
    if snapped_frames != frames and frames not in _warned_snapped_frames:
        _warned_snapped_frames.add(frames)
        logger.warning("[GENERATOR LTX] snapped frames %d -> %d (1 + %d*k)",
                       frames, snapped_frames, LTX_TEMPORAL_DOWNSCALE)
    return snapped_w, snapped_h, snapped_frames


def decode_ltx_video(vae: Any, device: str, latent: torch.Tensor, seed: int,
                     keep_resident: tuple = ()) -> np.ndarray:
    vae.move_to(device)
    z = latent.to(device=device, dtype=vae.compute_dtype)
    with torch.no_grad():
        chunk_frames = causal3d_chunk_frames(vae.module, z, free_vram_gb_value=free_vram_gb(device))
        if chunk_frames is not None:
            pixels = chunked_decode_causal3d(
                vae.module, z, chunk_latent_frames=chunk_frames, accumulate_device="cpu",
            )
        else:
            pixels = decode_with_oom_retry(
                vae, z, device,
                generator=torch.Generator(device=device).manual_seed(
                    int(seed) + DECODE_NOISE_SEED_OFFSET
                ),
                profiler_mark="ltx.decode", log_prefix="[GENERATOR LTX]",
                keep_resident=keep_resident,
            )
    vae.offload()

    if not torch.isfinite(pixels).all():
        raise DecodeNumericsError()

    return pixels_3thw_to_uint8_frames(pixels[0])


def parse_explicit_sigmas(raw: str) -> torch.Tensor:
    values = [float(v.strip()) for v in str(raw).split(",") if v.strip() != ""]
    if len(values) < 2:
        raise ValueError(f"refine sigma schedule needs at least 2 values, got {len(values)}: {raw!r}")
    for a, b in zip(values, values[1:]):
        if b > a:
            raise ValueError(f"refine sigma schedule must be non-increasing, got {values}")
    return torch.tensor(values, dtype=torch.float32)


def validate_ltx_schedule_config(config: Dict[str, Any], pipe_id: str) -> None:
    check_guider_mode_conflict(config, pipe_id=pipe_id)

    sampler = config.get("sampler")
    if not sampler or not str(sampler).strip():
        raise ValueError(f"{pipe_id}: 'sampler' is required and cannot be empty")

    schedule = str(config.get("schedule") or "").strip()
    manual_sigmas_raw = config.get("manual_sigmas") or ""
    schedule_options = config.get("schedule_options") or {}
    has_manual_sigmas = bool(str(manual_sigmas_raw).strip()) or bool(schedule_options.get("sigmas"))

    if schedule == "manual" and not has_manual_sigmas:
        raise ValueError(
            f"{pipe_id}: schedule='manual' requires 'manual_sigmas' (or "
            f"schedule_options.sigmas) to be set -- got neither"
        )

    if manual_sigmas_raw:
        try:
            values = [float(v.strip()) for v in str(manual_sigmas_raw).split(",") if v.strip() != ""]
        except ValueError:
            raise ValueError(f"{pipe_id}: 'manual_sigmas' contains a non-numeric value: {manual_sigmas_raw!r}")
        if len(values) < 2:
            raise ValueError(f"{pipe_id}: 'manual_sigmas' needs at least 2 sigma values, got {len(values)}")
        for a, b in zip(values, values[1:]):
            if b > a:
                raise ValueError(f"{pipe_id}: 'manual_sigmas' must be non-increasing, got {values}")

    refine_sigmas_raw = str(config.get("refine_sigmas") or "").strip()
    if refine_sigmas_raw:
        try:
            parse_explicit_sigmas(refine_sigmas_raw)
        except ValueError as e:
            raise ValueError(f"{pipe_id}: 'refine_sigmas' is invalid: {e}") from e

    steps = config.get("steps")
    if isinstance(steps, (int, float)) and int(steps) < 2 and not has_manual_sigmas and not refine_sigmas_raw:
        raise ValueError(
            f"{pipe_id}: 'steps' must be >= 2 for the default multi-sigma schedule (got {steps}); "
            f"set 'manual_sigmas'/'refine_sigmas' for an explicit short/single-step recipe if intended"
        )
