"""Tests for VRAM tiering / placement planning (pure logic, no GPU)."""

from __future__ import annotations

from src.platform.runtime.native.memory.device_plan import DevicePlan
from src.platform.runtime.native.memory.tiering import (
    activation_headroom_gb,
    plan_placement,
    ref_latents_headroom_gb,
    sampling_headroom_gb,
)
from vendor.gpl.comfyui.ops import QUANT_FP8_SCALED


def _single(device: str = "cuda:0") -> DevicePlan:
    return DevicePlan(device, device, device)


def _multi() -> DevicePlan:
    return DevicePlan("cuda:0", "cuda:1", "cuda:0")


# --- fallback tier table (sizes unknown) --------------------------------------

def _fallback(vram: float):
    return plan_placement(vram, {}, None, _single())


def test_fallback_streaming_under_8gb():
    p = _fallback(6.0)
    assert p.tier == "streaming"
    assert p.dit.resident is False
    assert p.dit.ops_mode == "manual_cast"
    assert p.text_encoder.resident is False
    assert p.vae.resident is False
    assert p.vae_tiling is True


def test_fallback_component_offload_8_to_12():
    p = _fallback(10.0)
    assert p.tier == "component_offload"
    assert p.dit.resident is True
    assert p.dit.ops_mode == "standard"
    assert p.text_encoder.resident is False
    assert p.vae.resident is False


def test_fallback_vae_offload_12_to_16():
    p = _fallback(14.0)
    assert p.tier == "vae_offload"
    assert p.dit.resident is True
    assert p.text_encoder.resident is True
    assert p.vae.resident is False
    assert p.vae_tiling is True


def test_fallback_resident_over_16():
    p = _fallback(24.0)
    assert p.tier == "resident"
    assert p.dit.resident and p.text_encoder.resident and p.vae.resident
    assert p.vae_tiling is False


def test_tier_boundaries_are_half_open():
    # exactly 8 -> not streaming; exactly 16 -> resident.
    assert _fallback(8.0).tier == "component_offload"
    assert _fallback(12.0).tier == "vae_offload"
    assert _fallback(16.0).tier == "resident"


# --- fit-based residency (sizes known) ----------------------------------------

_KLEIN = {"dit": 9.0, "text_encoder": 5.0, "vae": 1.0}


def test_fit_all_resident_on_ample_vram():
    p = plan_placement(24.0, _KLEIN, None, _single())
    assert p.tier == "resident"
    assert p.dit.resident and p.text_encoder.resident and p.vae.resident


def test_fit_drops_vae_first():
    # 16GB, headroom 2 -> budget 14; dit 9 leaves 5; te+vae=6 > 5 -> drop VAE.
    p = plan_placement(16.0, _KLEIN, None, _single())
    assert p.tier == "vae_offload"
    assert p.dit.resident and p.text_encoder.resident
    assert p.vae.resident is False
    assert p.vae_tiling is True


def test_fit_drops_te_when_tighter():
    # 12GB -> budget 10; dit 9 leaves 1; te 5 > 1 -> TE also offloaded.
    p = plan_placement(12.0, _KLEIN, None, _single())
    assert p.tier == "component_offload"
    assert p.dit.resident is True
    assert p.text_encoder.resident is False
    assert p.vae.resident is False


def test_fit_override_streams_big_model_that_tier_table_would_offload():
    # 10GB tier table says "component_offload" (dit resident), but a 9GB DiT
    # does not fit with headroom (budget 8) -> fit overrides to streaming.
    p = plan_placement(10.0, _KLEIN, None, _single())
    assert p.tier == "streaming"
    assert p.dit.resident is False
    assert p.dit.ops_mode == "manual_cast"


def test_fit_override_keeps_small_model_resident_below_16gb():
    # 14GB tier table says "vae_offload", but a tiny model fits fully ->
    # fit overrides to fully resident.
    small = {"dit": 3.0, "text_encoder": 1.0, "vae": 0.3}
    p = plan_placement(14.0, small, None, _single())
    assert p.tier == "resident"
    assert p.vae.resident is True
    assert p.vae_tiling is False


# --- fp8 ops mode -------------------------------------------------------------

def test_fp8_quant_sets_dit_ops_fp8_even_when_resident():
    p = plan_placement(24.0, _KLEIN, QUANT_FP8_SCALED, _single())
    assert p.dit.ops_mode == "fp8"
    assert p.dit.resident is True


def test_fp8_quant_wins_over_streaming_manual_cast():
    p = plan_placement(10.0, _KLEIN, QUANT_FP8_SCALED, _single())
    assert p.dit.resident is False
    assert p.dit.ops_mode == "fp8"   # fp8 takes priority over manual_cast


# --- multi-GPU TE placement ---------------------------------------------------

def test_te_on_second_gpu_is_resident_and_frees_dit_budget():
    # DiT 20GB alone on a 24GB card: with TE spilled to cuda:1 it fits resident.
    big = {"dit": 20.0, "text_encoder": 5.0, "vae": 1.0}
    p = plan_placement(24.0, big, None, _multi())
    assert p.dit.device == "cuda:0" and p.dit.resident is True
    assert p.text_encoder.device == "cuda:1" and p.text_encoder.resident is True
    assert p.vae.device == "cuda:0" and p.vae.resident is True
    assert p.tier == "resident"


def test_activation_headroom_scales_with_resolution():
    # 512x512 -> latent 32x32 (flux2 //16); 1024x1024 -> 64x64. Bigger => more.
    small = activation_headroom_gb((32, 32))
    big = activation_headroom_gb((64, 64))
    assert big > small > 2.0
    # 64x64 latent * 0.6 MB/px = 2.4 GB on top of the 2 GB base.
    assert abs(big - (2.0 + 64 * 64 * 0.6 / 1024)) < 1e-6
    # causal-3D callers pass a larger per-px cost (heavier fp32 3D decode).
    heavy = activation_headroom_gb((64, 64), decode_mb_per_latent_px=1.2)
    assert abs(heavy - (2.0 + 64 * 64 * 1.2 / 1024)) < 1e-6
    assert heavy > big


def test_bf16_klein_1024_headroom_flips_te_offload():
    # Real data point: 31.3GB card, bf16 Klein DiT 18 + 8B TE 9 + fp32 VAE 1.
    # Budget ~29.3GB. With a FLAT 2GB headroom the fit keeps the TE resident
    # (the bug that OOM'd decode at 29.6GB). With the resolution-scaled headroom
    # for 1024² (latent 64x64 -> 4.4GB) the TE is dropped.
    sizes = {"dit": 18.0, "text_encoder": 9.0, "vae": 1.0}
    dp = DevicePlan("cuda:0", "cuda:0", "cuda:0")

    flat = plan_placement(29.3, sizes, None, dp, working_headroom_gb=2.0)
    assert flat.dit.resident is True
    assert flat.text_encoder.resident is True    # the bug: TE stays resident

    scaled = plan_placement(29.3, sizes, None, dp,
                            working_headroom_gb=activation_headroom_gb((64, 64)))
    assert scaled.dit.resident is True            # DiT still fits for sampling
    assert scaled.text_encoder.resident is False  # TE now correctly dropped
    assert scaled.vae.resident is False


# --- latent_frames (Fix 5: video headroom must not be frame-blind) -----------


def test_activation_headroom_default_frames_matches_old_4d_formula():
    """Regression guard: the T=1 default must reproduce the EXACT pre-Fix-5
    numbers for image (4D) latents -- this must never change."""
    assert activation_headroom_gb((64, 64)) == 2.0 + 64 * 64 * 0.6 / 1024
    assert activation_headroom_gb((64, 64), decode_mb_per_latent_px=1.2) == 2.0 + 64 * 64 * 1.2 / 1024
    # Explicit latent_frames=1 must be identical to the omitted default.
    assert activation_headroom_gb((64, 64), latent_frames=1) == activation_headroom_gb((64, 64))


def test_activation_headroom_scales_linearly_with_frames():
    one_frame = activation_headroom_gb((64, 64), latent_frames=1)
    sixteen_frames = activation_headroom_gb((64, 64), latent_frames=16)
    base = 2.0
    # The base (fixed) term doesn't scale with T -- only the per-pixel decode term does.
    assert abs((sixteen_frames - base) - 16 * (one_frame - base)) < 1e-9
    assert sixteen_frames > one_frame


def test_sampling_headroom_default_frames_matches_old_4d_formula():
    """Regression guard: the T=1 default must reproduce the EXACT pre-Fix-5
    numbers for image (4D) latents."""
    assert sampling_headroom_gb((128, 128)) == max(0.75, 128 * 128 * 0.1 / 1024)
    assert sampling_headroom_gb((128, 128), latent_frames=1) == sampling_headroom_gb((128, 128))


def test_sampling_headroom_scales_linearly_with_frames_above_floor():
    # Large enough resolution that the per-pixel term dominates the 0.75GB floor.
    one_frame = sampling_headroom_gb((256, 256), latent_frames=1)
    sixteen_frames = sampling_headroom_gb((256, 256), latent_frames=16)
    assert abs(sixteen_frames - 16 * one_frame) < 1e-9
    assert sixteen_frames > one_frame


def test_sampling_headroom_floor_still_applies_at_tiny_resolution_with_frames():
    # Below the floor even at T=16 -> the base floor still wins, unchanged.
    assert sampling_headroom_gb((4, 4), latent_frames=16) == 0.75


def test_te_offgpu_not_counted_against_dit_budget():
    # Same DiT+VAE on cuda:0 would be tight if the 5GB TE were co-located;
    # with the TE elsewhere the VAE stays resident.
    sizes = {"dit": 11.0, "text_encoder": 5.0, "vae": 1.0}
    colocated = plan_placement(16.0, sizes, None, _single())
    spilled = plan_placement(16.0, sizes, None, _multi())
    assert colocated.vae.resident is False      # 11+5+1 > budget 14
    assert spilled.vae.resident is True         # 11+1 <= budget 14


# --- ref_latents_headroom_gb (Qwen-Image-Edit ref-token OOM) --------


def test_ref_latents_headroom_empty_list_is_zero():
    assert ref_latents_headroom_gb([]) == 0.0


def test_ref_latents_headroom_matches_sampling_headroom_raw_term():
    # One same-resolution ref should cost exactly the SAME per-pixel term as
    # the main image's own (unfloored) sampling headroom - "edit mode doubles
    # the image token count" means the total (main + ref) headroom should be
    # ~2x the main-alone term whenever both are above the floor.
    hw = (256, 256)
    main = sampling_headroom_gb(hw)
    ref_only = ref_latents_headroom_gb([(hw, 1)])
    assert abs((main + ref_only) - 2 * main) < 1e-9


def test_ref_latents_headroom_is_not_floored():
    # A tiny ref alone must NOT hit the 0.75GB floor sampling_headroom_gb
    # applies to the main term - that floor is already reserved once.
    tiny_ref_headroom = ref_latents_headroom_gb([((4, 4), 1)])
    assert tiny_ref_headroom < 0.75
    assert tiny_ref_headroom > 0.0


def test_ref_latents_headroom_sums_multiple_references():
    one = ref_latents_headroom_gb([((256, 256), 1)])
    two = ref_latents_headroom_gb([((256, 256), 1), ((256, 256), 1)])
    assert abs(two - 2 * one) < 1e-9


def test_ref_latents_headroom_handles_differently_shaped_refs():
    # Edit mode allows a reference at a different resolution than the target
    # (see arch/qwen_image test_differently_shaped_ref_is_allowed_unlike_krea2)
    # - each ref must be priced at its OWN h/w, not the main image's.
    small = ref_latents_headroom_gb([((64, 64), 1)])
    big = ref_latents_headroom_gb([((512, 512), 1)])
    assert big > small


def test_ref_latents_headroom_scales_with_frames():
    one_frame = ref_latents_headroom_gb([((256, 256), 1)])
    four_frames = ref_latents_headroom_gb([((256, 256), 4)])
    assert abs(four_frames - 4 * one_frame) < 1e-9
