"""VRAM-aware whole-clip/tiled LTX-2.5 diffusion-decode ladder, the decode-side
twin of :mod:`ltx_tiled_encode`.

Only the LTX-2.5 ``CausalDiffusionVAE`` needs the whole-clip/tiled ladder
below. The 2.0/2.3 conv decoder self-chunks internally (``vae/ltx_causal_video.py``'s
``Decoder.forward`` recurses by a fixed 128MB budget), so a whole-clip
``decode()`` on it is already bounded for its OWN activations -- it still gets
a single evict-and-retry rung here, for callers (e.g. the detailer tube refine)
that keep other large models resident on the same device across the decode.
The diffusion decoder has no such internal bound: its stage 5 runs
neighborhood-attention blocks over the FULL pixel-token grid, and every
transient there scales with that grid.

**Observed failure this exists to fix:** a 5090 (31.37 GiB) OOM'd
inside ``NADiffusionDecoder`` on a clip past ~5 seconds, failing a 1.00 GiB
allocation in the stage-5 rotary embedding with 29.89 GiB already in use.

The ladder (:func:`decode_with_oom_retry`) mirrors the encode side's shape:

1. Project whole-clip decode activation memory from the latent's own shape and
   the module's own widths (:func:`estimate_whole_clip_decode_gb`). If it
   plausibly fits the free-VRAM budget, attempt the whole-clip decode --
   tiling blends seams, so it must never run when it isn't needed.
2. On CUDA OOM, evict every foreign GPU-resident component and retry the
   whole-clip decode once.
3. If the projection said it wouldn't fit, or it still OOM'd, fall back to a
   tiled decode whose tile size is sized to the SAME budget rather than left at
   the reference defaults -- a fixed 768px tile is itself a ~11GB decode at the
   shipped widths, so a fixed default would simply OOM again on a busy card.
4. Only if the TILED decode still OOMs does this raise, naming the grid.

``use_tiling`` is restored in a ``finally``: the VAE module is cached across
generations by ``ModelLifecycleManager``, so a leaked ``True`` would silently
blend seams into every later decode that did not need tiling.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, Optional

import torch

from src.platform.observability.profiling import get_profiler
from src.platform.runtime.device import clear_gpu_memory
from src.platform.runtime.native.memory.residency import free_vram_gb, get_residency_manager

logger = logging.getLogger(__name__)

# Same conservative fraction-of-free-VRAM idiom the encode ladder and
# ``vae/tiling.py`` already use.
DECODE_VRAM_BUDGET_FRACTION = 0.75

# The diffusion decoder samples the pixels it denoises, so a decode is only
# reproducible if it draws from a seeded stream. That stream must be its own:
# drawing from the request's main per-seed generator would shift what the
# sampler draws for the same seed, which is the exact reasoning behind
# ``ANCESTRAL_NOISE_SEED_OFFSET`` (10000, ``sampling/algorithms/euler_ancestral.py``).
# 20000 keeps the same 10000 spacing, so the three streams one request can drive
# -- ``seed`` (init noise / FreeInit), ``seed + 10000`` (ancestral), and
# ``seed + 20000`` (decode noise) -- are always distinct from each other.
DECODE_NOISE_SEED_OFFSET = 20000

# Tile ladder, in pixels / frames of the decoded video. The first entry is the
# reference implementation's own default; the rest are the fallbacks tried when
# the budget cannot hold it. Floors stay well clear of the tile minimum the
# neighborhood kernels impose (stage 5's 11-wide kernel over a stride-2 upsample
# needs 6 grid cells = 48px / 12 frames).
_SPATIAL_TILE_LADDER = (768, 512, 384, 256)
_TEMPORAL_TILE_LADDER = (80, 48, 32, 16)

# The reference's stride-to-tile ratios; the blended overlap is the difference.
_SPATIAL_STRIDE_RATIO = 704 / 768
_TEMPORAL_STRIDE_RATIO = 56 / 80


def supports_diffusion_tiled_decode(vae_module: Any) -> bool:
    """Whether this VAE is the diffusion decoder (the only one with a
    ``tiled_decode``/``enable_tiling`` pair). The conv VAE has neither, so a
    caller can route both through here and have this be a clean no-op."""
    return hasattr(vae_module, "tiled_decode") and hasattr(vae_module, "enable_tiling")


def _decode_geometry(vae_module: Any) -> tuple[int, int, int]:
    """``(temporal_ratio, spatial_cells_per_latent, patch_size)`` -- how one
    latent voxel maps onto the stage-5 token grid."""
    patch_size = vae_module.decoder.patch_size
    spatial_cells = vae_module.spatial_compression_ratio // patch_size
    return vae_module.temporal_compression_ratio, spatial_cells, patch_size


def context_token_count(vae_module: Any, latent: torch.Tensor) -> int:
    """Stage-5 token count for this latent: the pixel frame count times the
    context volume's spatial grid. Every decode transient scales with it."""
    temporal_ratio, spatial_cells, _ = _decode_geometry(vae_module)
    _b, _c, t, h, w = latent.shape
    pixel_frames = (t - 1) * temporal_ratio + 1
    return pixel_frames * (h * spatial_cells) * (w * spatial_cells)


def decode_bytes_per_context_token(vae_module: Any) -> int:
    """Peak stage-5 bytes per context token, summed from the module's own
    widths rather than a fitted constant so it tracks any config change.

    The terms, all live simultaneously inside one ``diff_blocks`` forward:

    * the latent context, held for the whole denoise loop;
    * the stage-5 hidden states, plus one residual/normed copy;
    * the ``x_t`` pixel canvas (``out_channels * patch**2`` per token);
    * the fused QKV projection and the three reshaped copies it feeds -- the
      single largest term, at four times the stage width;
    * the rotary embedding's fp32 promotion for its widest axis chunk, which is
      where the reported OOM actually landed.
    """
    decoder = vae_module.decoder
    attn = decoder.diff_blocks[0].attn
    width = attn.heads * attn.head_dim
    element_size = torch.finfo(next(vae_module.parameters()).dtype).bits // 8
    pixel_channels = decoder.out_channels * decoder.patch_size ** 2

    context_and_hidden = 3 * width * element_size
    canvas = pixel_channels * element_size
    qkv = 4 * width * element_size
    widest_rope_axis = max(attn.rope.rope_dim_split)
    rope = 3 * attn.heads * widest_rope_axis * 4
    return context_and_hidden + canvas + qkv + rope


def decode_bytes_per_latent_cell(vae_module: Any) -> int:
    """Peak ``forward_stages_1_to_3`` bytes per INPUT latent cell, summed from
    the module's own widths the same way :func:`decode_bytes_per_context_token`
    sums stage 5's -- a tiled decode now runs stages 1-3 per tile too, and the
    whole-clip route runs them once over the entire latent, so both need this
    term to route/size correctly.

    Each of the three deterministic stages ``forward_stages_1_to_3`` runs
    processes ``cumulative_tokens`` tokens per input latent cell --
    ``cumulative_tokens`` being the product of the upsample strides applied by
    every earlier stage. At each stage the live transients are an NABlock's
    fused QKV (four times the stage width) plus its SwiGLU's three
    hidden-width buffers, plus that stage's own ``PixelShuffleUpsampler.proj``
    channel-expansion output. Summing these, scaled by each stage's token
    multiplicity, is a deliberate overestimate -- it treats the QKV/SwiGLU/proj
    terms as simultaneously live rather than tracking which actually overlap,
    which is the safety margin the module docstring asks for rather than a
    fitted constant.
    """
    decoder = vae_module.decoder
    element_size = torch.finfo(next(vae_module.parameters()).dtype).bits // 8
    cumulative_tokens = 1
    total = 0
    for blocks, upsample in zip(decoder.det_stages[:-1], decoder.upsamples[:-1]):
        block = blocks[0]
        stage_width = block.attn.heads * block.attn.head_dim
        mlp_hidden = block.mlp.w_gate.weight.shape[0]
        proj_out = upsample.proj.weight.shape[0]
        per_token = 4 * stage_width * element_size + 3 * mlp_hidden * element_size + proj_out * element_size
        total += cumulative_tokens * per_token
        cumulative_tokens *= math.prod(upsample.stride)
    return total


def estimate_stages_1_to_3_gb(vae_module: Any, latent: torch.Tensor) -> float:
    """Project whole-clip ``forward_stages_1_to_3`` activation memory, in GB,
    from the latent's own cell count. This is the term the estimator was
    missing before tiling moved to cover stages 1-3 as well as stage 5 --
    without it, the whole-clip projection under-priced exactly the phase that
    actually OOM'd."""
    _b, _c, t, h, w = latent.shape
    return t * h * w * decode_bytes_per_latent_cell(vae_module) / (1024 ** 3)


def estimate_whole_clip_decode_gb(vae_module: Any, latent: torch.Tensor) -> float:
    """Project whole-clip diffusion-decode activation memory, in GB: stage 5's
    per-token cost plus stages 1-3's per-latent-cell cost, the two phases a
    whole-clip decode runs through in sequence."""
    tokens = context_token_count(vae_module, latent)
    stage5_gb = tokens * decode_bytes_per_context_token(vae_module) / (1024 ** 3)
    return stage5_gb + estimate_stages_1_to_3_gb(vae_module, latent)


def _snap(value: int, cell: int, minimum: int) -> int:
    return max(minimum, (value // cell) * cell)


def auto_decode_tile_sizes(
    vae_module: Any, latent: torch.Tensor, budget_gb: Optional[float],
) -> Dict[str, int]:
    """Largest tile off the ladder whose projected per-tile decode -- stages
    1-3's latent-cell cost plus stage 5's token cost -- fits ``budget_gb``,
    snapped to the module's own latent-cell size.

    ``None`` budget (VRAM unqueryable, or CPU) keeps the reference defaults.
    ``tiled_decode`` tiles the LATENT grid, so a size that isn't a whole
    number of latent cells (``temporal_compression_ratio`` /
    ``spatial_compression_ratio``) silently rounds down there; snapping here
    keeps the projection honest about what will actually run.
    """
    _, _, patch_size = _decode_geometry(vae_module)
    ratio_t = vae_module.temporal_compression_ratio
    ratio_hw = vae_module.spatial_compression_ratio

    override_px = _env_int("NATIVE_LTX_DIFFUSION_TILE_PX")
    override_frames = _env_int("NATIVE_LTX_DIFFUSION_TILE_FRAMES")

    per_token = decode_bytes_per_context_token(vae_module)
    per_latent_cell = decode_bytes_per_latent_cell(vae_module)
    spatial_options = (override_px,) if override_px else _SPATIAL_TILE_LADDER
    temporal_options = (override_frames,) if override_frames else _TEMPORAL_TILE_LADDER

    chosen_px, chosen_frames = spatial_options[-1], temporal_options[-1]
    for tile_px, tile_frames in zip(spatial_options, temporal_options):
        tokens = tile_frames * (tile_px // patch_size) ** 2
        latent_cells = (tile_frames // ratio_t) * (tile_px // ratio_hw) ** 2
        tile_gb = (tokens * per_token + latent_cells * per_latent_cell) / (1024 ** 3)
        if budget_gb is None or tile_gb <= budget_gb:
            chosen_px, chosen_frames = tile_px, tile_frames
            break

    tile_height = _snap(chosen_px, ratio_hw, ratio_hw)
    tile_width = _snap(chosen_px, ratio_hw, ratio_hw)
    tile_frames = _snap(chosen_frames, ratio_t, ratio_t)
    return {
        "tile_sample_min_height": tile_height,
        "tile_sample_min_width": tile_width,
        "tile_sample_min_num_frames": tile_frames,
        "tile_sample_stride_height": _snap(int(tile_height * _SPATIAL_STRIDE_RATIO), ratio_hw, ratio_hw),
        "tile_sample_stride_width": _snap(int(tile_width * _SPATIAL_STRIDE_RATIO), ratio_hw, ratio_hw),
        "tile_sample_stride_num_frames": _snap(
            int(tile_frames * _TEMPORAL_STRIDE_RATIO), ratio_t, ratio_t
        ),
    }


def _env_int(name: str) -> Optional[int]:
    """A positive ``NATIVE_*`` integer override, or ``None``. Same read-at-use
    idiom as ``NATIVE_FP8_QUANTIZE`` / ``NATIVE_SOL_ATTN_BACKEND``."""
    raw = os.environ.get(name)
    if not raw:
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("%s=%r is not an integer -- ignoring", name, raw)
        return None
    if value <= 0:
        logger.warning("%s=%d must be positive -- ignoring", name, value)
        return None
    return value


def decode_with_oom_retry(
    vae: Any,
    latent: torch.Tensor,
    device: str,
    *,
    generator: Optional[torch.Generator] = None,
    profiler_mark: str,
    log_prefix: str = "ltx_vae_decode",
) -> torch.Tensor:
    """``vae.module.decode(latent)`` with the ladder described in the module
    docstring. ``vae`` is a bundle's ``NativeModel``-shaped VAE component.

    ``generator`` seeds the diffusion decoder's own noise draw (see
    :data:`DECODE_NOISE_SEED_OFFSET`); without one the decode falls back to
    global RNG and is not reproducible per seed. Its state is rewound before
    every rung, so which rung ends up succeeding cannot change the noise a given
    seed decodes from -- an OOM on the whole-clip attempt must not silently
    re-roll the tiled fallback's canvas.

    A VAE without a tiled decode (the 2.0/2.3 conv one) is deterministic and
    ignores ``generator``; it gets a single evict-and-retry rung rather than the
    full whole-clip/tiled ladder, so callers can route every LTX decode through
    here.
    """
    module = vae.module
    if not supports_diffusion_tiled_decode(module):
        # The conv decoder self-chunks its OWN activations, but a caller that
        # keeps other large models resident on the same device (e.g. the
        # detailer's DiT, pinned for the denoise that just fed this decode)
        # can still OOM here -- retry once after evicting every OTHER
        # GPU-resident component, the same belt the diffusion-decoder rungs
        # below use.
        try:
            with torch.no_grad():
                return module.decode(latent)
        except torch.cuda.OutOfMemoryError:
            logger.warning(
                "%s: conv VAE decode OOM'd; evicting every foreign GPU-resident "
                "component and retrying once", log_prefix,
            )
            get_residency_manager().offload_all(device, exclude=(vae,))
            clear_gpu_memory()
            try:
                with torch.no_grad():
                    return module.decode(latent)
            except torch.cuda.OutOfMemoryError as exc:
                raise torch.cuda.OutOfMemoryError(
                    f"{log_prefix}: LTX conv VAE decode OOM'd even after evicting "
                    "other GPU-resident components -- this clip does not fit in "
                    "the remaining VRAM"
                ) from exc

    profiler = get_profiler()
    free_before = free_vram_gb(device)
    estimated_gb = estimate_whole_clip_decode_gb(module, latent)
    budget_gb = free_before * DECODE_VRAM_BUDGET_FRACTION if free_before is not None else None
    plausibly_fits = budget_gb is None or estimated_gb <= budget_gb
    grid = tuple(latent.shape[2:])

    initial_rng_state = generator.get_state() if generator is not None else None

    def _do_decode() -> torch.Tensor:
        if generator is not None:
            generator.set_state(initial_rng_state)
        with torch.no_grad():
            return module.decode(latent, generator=generator)

    def _mark(**fields: Any) -> None:
        profiler.mark(
            profiler_mark, device=str(device), free_vram_before_gb=free_before,
            estimated_gb=estimated_gb, budget_gb=budget_gb,
            context_tokens=context_token_count(module, latent), **fields,
        )

    if plausibly_fits:
        try:
            pixels = _do_decode()
            _mark(mode="whole", evicted=False, retried=False, free_vram_after_gb=free_vram_gb(device))
            return pixels
        except torch.cuda.OutOfMemoryError:
            logger.warning(
                "%s: whole-clip diffusion decode OOM'd at latent grid %s "
                "(free_vram_before_gb=%s, estimated_gb=%.2f); evicting every foreign "
                "GPU-resident component and retrying once",
                log_prefix, grid, free_before, estimated_gb,
            )
            get_residency_manager().offload_all(device, exclude=(vae,))
            clear_gpu_memory()
            try:
                pixels = _do_decode()
                _mark(mode="whole", evicted=True, retried=True, free_vram_after_gb=free_vram_gb(device))
                return pixels
            except torch.cuda.OutOfMemoryError:
                logger.warning(
                    "%s: whole-clip diffusion decode still OOM'd after eviction; "
                    "falling back to TILED decode (seams are blended, so output "
                    "differs slightly from a whole-clip decode)",
                    log_prefix,
                )
    else:
        logger.info(
            "%s: projected whole-clip diffusion decode %.2fGB exceeds the %.2fGB budget "
            "(free_vram=%.2fGB) at latent grid %s -- going straight to TILED decode",
            log_prefix, estimated_gb, budget_gb, free_before, grid,
        )
        get_residency_manager().offload_all(device, exclude=(vae,))
        clear_gpu_memory()

    tile_sizes = auto_decode_tile_sizes(module, latent, budget_gb)
    logger.warning(
        "%s: tiled diffusion decode at latent grid %s with tiles "
        "%dx%d px / %d frames (strides %d/%d/%d)",
        log_prefix, grid,
        tile_sizes["tile_sample_min_height"], tile_sizes["tile_sample_min_width"],
        tile_sizes["tile_sample_min_num_frames"], tile_sizes["tile_sample_stride_height"],
        tile_sizes["tile_sample_stride_width"], tile_sizes["tile_sample_stride_num_frames"],
    )
    was_tiling = module.use_tiling
    try:
        module.enable_tiling(**tile_sizes)
        pixels = _do_decode()
    except torch.cuda.OutOfMemoryError as exc:
        _mark(mode="tiled", evicted=True, retried=True, oom=True, **tile_sizes)
        raise torch.cuda.OutOfMemoryError(
            f"{log_prefix}: LTX diffusion decode OOM'd even with tiled decoding at latent "
            f"grid {grid} (tiles {tile_sizes['tile_sample_min_height']}x"
            f"{tile_sizes['tile_sample_min_width']} px / "
            f"{tile_sizes['tile_sample_min_num_frames']} frames) -- this clip does not fit "
            "in the remaining VRAM; try a lower resolution or fewer frames"
        ) from exc
    finally:
        # The module is cached across generations; a leaked ``use_tiling`` would
        # blend seams into later decodes that never needed tiling.
        module.use_tiling = was_tiling

    _mark(mode="tiled", evicted=True, retried=True, free_vram_after_gb=free_vram_gb(device), **tile_sizes)
    return pixels
