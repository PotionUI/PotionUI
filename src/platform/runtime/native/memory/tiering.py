"""Map the existing MemoryPolicy tiers to a native placement plan.

This does NOT introduce a new tier table — it reuses the VRAM tiers documented
in ``src/platform/runtime/model_lifecycle/memory_policy.py`` (< 8 / 8-12 / 12-16 / >= 16 GB)
and turns them into a concrete plan for the native engine's three heavy
components (DiT / text encoder / VAE): which device, which ops namespace, and
whether the component stays resident on the GPU or is streamed/offloaded.

Precedence (documented contract):
  1. **Fit first.** When component sizes are known (any non-zero entry in
     ``model_memory_gb``), residency is decided by whether the components
     actually fit on the DiT's device — a 9B Klein and a 14B Wan get different
     plans at the same VRAM. We keep the DiT resident preferentially, then the
     TE, dropping the VAE (to tiled/offloaded) first.
  2. **Tier table as fallback.** When sizes are unknown (all zero / missing),
     fall back to the MemoryPolicy VRAM tier table's shape.

Only components co-located with the DiT compete for ``vram_gb``. A TE placed on
a second GPU (see ``device_plan``) is assumed resident on its own device.

**What "not resident" now means.** A ``resident=False`` DiT is no longer streamed
all-or-nothing. The generator places it with **partial residency** (``memory/
partial.py``): as many leaves stay on the GPU as fit the live weights budget, the
rest stream from pinned CPU RAM. Separately, the loader may **quantise a bf16 DiT
to fp8 at load** (``ops/fp8_quant.py``) so it fits resident — preferred over
streaming. This module still only decides *resident vs not* and the ops mode; the
partial split and the fp8 decision live in the generator / loader respectively.
See ``docs/native-engine.md`` → "Memory tiers & low-VRAM".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from vendor.gpl.comfyui.ops import QUANT_FP8_SCALED
from .device_plan import DevicePlan

logger = logging.getLogger(__name__)

OpsMode = Literal["standard", "manual_cast", "fp8"]
Tier = Literal["resident", "vae_offload", "component_offload", "streaming"]

# Working memory (activations, attention buffers) reserved on top of weights.
_WORKING_HEADROOM_GB = 2.0

# VAE decode is the memory spike (fp32 pixels + conv/attn intermediates up to 8x
# the latent resolution). Empirically ~0.6 MB per *latent* pixel for the Flux 2D
# AE decoder at bf16 (same constant as vae/tiling.auto_tile_size). A flat 2GB
# headroom is wrong at high resolution: bf16 Klein @1024² was observed to peak at
# 29.6GB vs 27.3GB of weights — a ~2.3GB activation/decode spike the flat term
# missed, which is what OOM'd decode.
_DECODE_MB_PER_LATENT_PX = 0.6


def activation_headroom_gb(
    latent_hw: tuple[int, int],
    base_gb: float = _WORKING_HEADROOM_GB,
    *,
    decode_mb_per_latent_px: float = _DECODE_MB_PER_LATENT_PX,
    latent_frames: int = 1,
) -> float:
    """Resolution-scaled working headroom = base + estimated decode spike.

    ``latent_hw`` is the VAE-latent H,W (not image pixels). Used as
    ``plan_placement(..., working_headroom_gb=activation_headroom_gb(hw))`` so the
    fit test reserves room for the decode spike instead of a flat 2GB.

    ``decode_mb_per_latent_px`` overrides the per-pixel cost: the causal-3D VAE
    (Qwen/Wan) decodes in fp32 through 3D convs and spikes noticeably higher than
    the 2D AE, so its callers pass a larger value to make the placement offload
    the DiT *before* decode rather than relying on the OOM-retry net.

    ``latent_frames`` is the temporal extent (T) of a 5D ``(B,C,T,H,W)`` video
    latent — the per-pixel constant was calibrated per-frame, so a still image
    (T=1, the default) is unaffected but a multi-frame video's decode spike
    scales linearly with T instead of being silently priced as a single frame.
    """
    h, w = latent_hw
    decode_gb = (h * w) * max(1, latent_frames) * decode_mb_per_latent_px / 1024.0
    return base_gb + decode_gb


# DiT forward activations (attention buffers, hidden states) per latent pixel.
# Far smaller than the decode spike: bf16 Krea-2 @1024² sampled at ~25.96GB peak
# with 24.5GB of weights resident — a ~1.5GB activation spike over weights.
_SAMPLING_MB_PER_LATENT_PX = 0.1


# Minimum sampling headroom when the resolution term is tiny (allocator slack,
# cuBLAS workspaces). NOT added on top of the resolution term: the 0.1 MB/px
# constant was calibrated against the TOTAL peak-over-weights (bf16 Krea-2
# @1024²: measured 1.5GB spike vs 1.6GB from the term alone), so stacking a
# flat 2GB base on top double-counted the same reservation and pushed
# fits-fine models (26.3GB Krea-2 on a 32GB card @1080p) into streaming.
_SAMPLING_MIN_HEADROOM_GB = 0.75


def sampling_headroom_gb(
    latent_hw: tuple[int, int],
    base_gb: float = _SAMPLING_MIN_HEADROOM_GB,
    *,
    latent_frames: int = 1,
) -> float:
    """Working headroom for the SAMPLING phase only (no decode reservation).

    Decode is phase-separated: the engine frees the DiT/TE before decoding and
    tiles the decode when the spike wouldn't fit, so this must NOT reserve the
    decode spike during sampling — doing so only starves the DiT weights
    budget, since at 1080p the decode term alone exceeds a 31GB card and would
    force a fully streamed DiT with ~0 resident weights.

    ``base_gb`` is a FLOOR, not an addend — the per-pixel term is calibrated as
    the total activation spike over weights (see _SAMPLING_MIN_HEADROOM_GB).

    ``latent_frames`` is the temporal extent (T) of a 5D ``(B,C,T,H,W)`` video
    latent — the DiT's attention/activation buffers scale with the full token
    count (T*H*W), not just one frame's H*W; a still image (T=1, the default)
    keeps the existing 2D numbers exactly.
    """
    h, w = latent_hw
    return max(base_gb, (h * w) * max(1, latent_frames) * _SAMPLING_MB_PER_LATENT_PX / 1024.0)

def ref_latents_headroom_gb(ref_hw_frames: list[tuple[tuple[int, int], int]]) -> float:
    """Extra (UNFLOORED) sampling-phase headroom for reference-latent tokens
    concatenated onto the main image's DiT forward — Qwen-Image-Edit's
    in-context edit path (``arch/qwen_image/model.py``'s ``ref_latents`` loop
    packs every ref through the SAME attention forward as the main image, at
    ``index=1,2,...`` on the temporal RoPE axis) and Krea-2's equivalent
    in-context mechanism both ride this. Each ref's activation cost is priced
    with the identical per-latent-pixel constant ``sampling_headroom_gb`` uses
    for the main term — the DiT doesn't distinguish main vs. ref tokens in its
    attention cost.

    Takes ``((h, w), latent_frames)`` PER reference, not one shared
    ``latent_hw`` — edit mode allows differently-shaped/differently-timed
    references (a source photo need not match the output resolution), so a
    single value can't represent an arbitrary list of them.

    Deliberately NOT floored (unlike ``sampling_headroom_gb``): the floor
    represents allocator/cuBLAS slack already reserved once by the main
    image's own ``sampling_headroom_gb`` call — a second floor per reference
    would double-count it (see the ``vram_reserve_double_count``
    history this project has already been burned by once). Callers add this
    directly onto the main term's already-floored result, never call it alone.
    """
    total = 0.0
    for (h, w), frames in ref_hw_frames:
        total += (h * w) * max(1, frames) * _SAMPLING_MB_PER_LATENT_PX / 1024.0
    return total


# VRAM tier boundaries — mirror MemoryPolicy exactly.
_TIER_STREAMING_MAX = 8.0
_TIER_COMPONENT_OFFLOAD_MAX = 12.0
_TIER_VAE_OFFLOAD_MAX = 16.0


@dataclass(frozen=True)
class ComponentPlacement:
    """Placement decision for one component."""

    device: str
    ops_mode: OpsMode
    resident: bool  # False = kept on CPU, moved to GPU only while its phase runs


@dataclass(frozen=True)
class PlacementPlan:
    """Full plan for a native generation."""

    dit: ComponentPlacement
    text_encoder: ComponentPlacement
    vae: ComponentPlacement
    vae_tiling: bool
    tier: Tier


def _dit_ops_mode(quant_format: str | None, resident: bool) -> OpsMode:
    if quant_format == QUANT_FP8_SCALED:
        return "fp8"
    # A DiT that does not fit resident is streamed per-layer via manual_cast.
    return "standard" if resident else "manual_cast"


def _tier_from_vram(vram_gb: float) -> Tier:
    if vram_gb < _TIER_STREAMING_MAX:
        return "streaming"
    if vram_gb < _TIER_COMPONENT_OFFLOAD_MAX:
        return "component_offload"
    if vram_gb < _TIER_VAE_OFFLOAD_MAX:
        return "vae_offload"
    return "resident"


def _residency_from_tier(tier: Tier) -> tuple[bool, bool, bool]:
    """(dit_resident, te_resident, vae_resident) for a tier-table fallback."""
    return {
        "streaming": (False, False, False),
        "component_offload": (True, False, False),
        "vae_offload": (True, True, False),
        "resident": (True, True, True),
    }[tier]


def _tier_from_residency(dit_r: bool, te_r: bool, vae_r: bool) -> Tier:
    if not dit_r:
        return "streaming"
    if not te_r:
        return "component_offload"
    if not vae_r:
        return "vae_offload"
    return "resident"


def plan_placement(
    vram_gb: float,
    model_memory_gb: dict[str, float],
    quant_format: str | None,
    device_plan: DevicePlan,
    *,
    working_headroom_gb: float = _WORKING_HEADROOM_GB,
) -> PlacementPlan:
    """Build a PlacementPlan for the DiT device's VRAM budget.

    ``model_memory_gb`` maps ``"dit"`` / ``"text_encoder"`` / ``"vae"`` to
    estimated resident sizes in GB. ``quant_format`` is the DiT quantisation
    (``"fp8_scaled"`` -> fp8 ops). ``device_plan`` decides co-location.
    """
    dit_gb = float(model_memory_gb.get("dit", 0.0))
    te_gb = float(model_memory_gb.get("text_encoder", 0.0))
    vae_gb = float(model_memory_gb.get("vae", 0.0))
    sizes_known = (dit_gb + te_gb + vae_gb) > 0.0

    te_colocated = device_plan.te_device == device_plan.dit_device
    vae_colocated = device_plan.vae_device == device_plan.dit_device

    if sizes_known:
        dit_r, te_r, vae_r = _fit_residency(
            vram_gb, dit_gb, te_gb, vae_gb,
            te_colocated, vae_colocated, working_headroom_gb,
        )
        tier = _tier_from_residency(dit_r, te_r, vae_r)
    else:
        tier = _tier_from_vram(vram_gb)
        dit_r, te_r, vae_r = _residency_from_tier(tier)

    # A TE on its own GPU is always resident there regardless of the DiT budget.
    if not te_colocated:
        te_r = True
    if not vae_colocated:
        vae_r = True

    plan = PlacementPlan(
        dit=ComponentPlacement(
            device=device_plan.dit_device,
            ops_mode=_dit_ops_mode(quant_format, dit_r),
            resident=dit_r,
        ),
        text_encoder=ComponentPlacement(
            device=device_plan.te_device,
            ops_mode="standard",
            resident=te_r,
        ),
        vae=ComponentPlacement(
            device=device_plan.vae_device,
            ops_mode="standard",
            resident=vae_r,
        ),
        vae_tiling=not vae_r,
        tier=tier,
    )
    logger.debug(
        "placement tier=%s dit(res=%s,%s) te(res=%s) vae(res=%s,tiling=%s) vram=%.1f sizes=%s",
        tier, dit_r, plan.dit.ops_mode, te_r, vae_r, plan.vae_tiling, vram_gb,
        model_memory_gb if sizes_known else "unknown",
    )
    return plan


def _fit_residency(
    vram_gb: float,
    dit_gb: float,
    te_gb: float,
    vae_gb: float,
    te_colocated: bool,
    vae_colocated: bool,
    headroom_gb: float,
) -> tuple[bool, bool, bool]:
    """Greedy fit on the DiT device.

    Residency priority is DiT > TE > VAE, so the VAE is dropped (offloaded/
    tiled) before the TE. Only components co-located with the DiT count against
    the budget; a TE on another GPU contributes zero demand here.
    """
    budget = vram_gb - headroom_gb

    # DiT that cannot fit even alone -> stream it; nothing else stays resident.
    if dit_gb > budget:
        return (False, False, False)

    remaining = budget - dit_gb
    te_demand = te_gb if te_colocated else 0.0
    vae_demand = vae_gb if vae_colocated else 0.0

    if te_demand + vae_demand <= remaining:
        return (True, True, True)          # everything fits
    if te_demand <= remaining:
        return (True, True, False)         # drop the VAE first
    return (True, False, False)            # drop the TE too
