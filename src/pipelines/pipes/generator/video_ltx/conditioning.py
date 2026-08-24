"""LTX-2/2.3 media-conditioning builder for the native video-director generator.

Re-derived from the diffusers v0.39 reference (Apache-2.0):
``pipelines/ltx2/pipeline_ltx2_condition.py`` (first-frame token overwrite +
appended keyframe tokens with their own RoPE coords) and
``pipeline_ltx2_ic_lora.py`` (appended reference-video tokens with base-grid
coords). Three conditioning shapes, one packed contract:

* ``latent_index == 0`` (``role="keyframe"``) — the VAE-encoded condition
  OVERWRITES the first tokens of the base packed latent (i2v).
* ``latent_index > 0`` (``role="keyframe"``) — the condition is APPENDED as
  extra tokens with keyframe coords: latent grid × VAE scale, NO causal fix,
  temporal offset ``(latent_index - 1) * 8 + 1`` pixel frames, single-pixel-
  frame conditions clamped to a ``[idx, idx + 1)`` temporal extent.
* ``role="reference"`` (IC-LoRA) — appended with coords built exactly like the
  base grid at the reference's latent dims (WITH the causal fix, temporal
  origin 0), overlapping the base coordinate space.

Two orthogonal addressing/entry options on top of those three shapes, both used
by ``generator/dfr_video_ltx``'s carried anchors: ``pixel_frame_index`` puts the
appended tokens at an exact pixel frame instead of deriving one from
``latent_index``, and ``latent`` supplies an already-encoded single-frame latent
in place of pixel ``frames``. See :class:`LTXMediaCondition`.

All coords are PIXEL-space with the temporal axis in pixel FRAMES — the DiT
divides the whole grid by ``frame_rate`` once (`LTXAVModel
._prepare_positional_embeddings`), matching diffusers' per-coord ``/ fps``.

Token layout of every output tensor: ``[base (t·h·w) | appended conditions in
input order]``. The mask drives both the per-token timestep
(``t * (1 - mask)``) and the per-step x0-space blend in the generator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

import torch

from vendor.gpl.comfyui.ltx.patchifier import SymmetricPatchifier, latent_to_pixel_coords

Tensor = torch.Tensor

LTX_TEMPORAL = 8
LTX_SPATIAL = 32
LTX_CHANNELS = 128
_SCALE_FACTORS = (LTX_TEMPORAL, LTX_SPATIAL, LTX_SPATIAL)


@dataclass
class LTXMediaCondition:
    """One media conditioning entry.

    ``frames``: ``(n, H0, W0, 3)`` float in [0, 1] (``n == 1`` for an image).
    ``latent_index``: target latent frame; 0 = first-frame overwrite, > 0 =
    appended keyframe, negative = from the end (resolved modulo the base latent
    frame count, e.g. -1 = last latent frame). Ignored for ``role="reference"``.
    ``role``: ``"keyframe"`` (timeline anchor) or ``"reference"`` (IC-LoRA).
    ``strength`` is OUR polarity: 1 = fully clean/pinned, 0 = free.

    Two alternative entry shapes, both for callers that address the timeline in
    raw pixel frames rather than latent indices (DFR's carried anchors):

    ``pixel_frame_index``: when set, the appended tokens' temporal extent is
    exactly ``[idx, idx + 1)`` at that pixel index, bypassing the
    ``(latent_index - 1) * 8 + 1`` derivation ``latent_index`` implies. DFR's
    canvas puts keyframes on the x8 border (24, 48, 72, …), which is a
    different convention: latent index 3 maps to pixel 17, not 24.

    ``latent``: an ALREADY-ENCODED ``(1, C, f, h, w)`` latent used in place of
    ``frames``, skipping the resize + ``vae_encode`` round trip. ``f == 1``
    means a standalone one-pixel-frame clip, which is the only thing a carried
    anchor ever is -- slicing a mid-stream latent frame is NOT a substitute
    (under causal encoding such a frame encodes 8 pixel frames relative to its
    predecessors, not a standalone one).
    """

    frames: Optional[Tensor] = None
    latent_index: int = 0
    strength: float = 1.0
    role: str = "keyframe"
    pixel_frame_index: Optional[int] = None
    latent: Optional[Tensor] = None

    def __post_init__(self) -> None:
        if (self.frames is None) == (self.latent is None):
            raise ValueError(
                "LTXMediaCondition needs exactly one of 'frames' (pixel domain) or "
                "'latent' (pre-encoded)")
        if self.pixel_frame_index is not None:
            if self.role != "keyframe":
                raise ValueError("'pixel_frame_index' only applies to role='keyframe' conditions")
            if self.pixel_frame_index < 1:
                raise ValueError(
                    f"'pixel_frame_index' must be a strictly interior pixel frame, got "
                    f"{self.pixel_frame_index} (frame 0 is the base latent's own first frame -- "
                    f"use latent_index=0 for a first-frame overwrite)")


@dataclass
class PreparedConditioning:
    """Packed conditioning state for one generation (constant across seeds).

    ``tokens``: ``[1, S_base + n_extra, C]`` clean condition values at
    conditioned positions, zeros elsewhere (the pre-noise init).
    ``mask``: ``[1, S_base + n_extra]`` float strengths in [0, 1].
    ``clean``: same shape/content as ``tokens`` (kept separate for the per-step
    x0 blend; aliasing them would break if a caller mutates the init).
    ``extra_coords``: ``[1, 3, n_extra, 2]`` pixel-space coords for appended
    tokens, or None.
    """

    tokens: Tensor
    mask: Tensor
    clean: Tensor
    extra_coords: Optional[Tensor]
    n_extra: int
    base_tokens: int


def _resize_cover_center_crop(frames: Tensor, height: int, width: int) -> Tensor:
    """``(n, H0, W0, 3)`` [0,1] -> ``(1, 3, n, H, W)`` in [-1, 1] (cover + center crop)."""
    chw = frames.permute(0, 3, 1, 2)  # (n, 3, H0, W0)
    _, _, h0, w0 = chw.shape
    scale = max(width / w0, height / h0)
    chw = torch.nn.functional.interpolate(
        chw, size=(round(h0 * scale), round(w0 * scale)), mode="bilinear", align_corners=False)
    _, _, h1, w1 = chw.shape
    top, left = (h1 - height) // 2, (w1 - width) // 2
    chw = chw[:, :, top:top + height, left:left + width]
    return (chw * 2.0 - 1.0).permute(1, 0, 2, 3).unsqueeze(0)  # (1, 3, n, H, W)


def _trim_condition_frames(start_px: int, n_frames: int, target_frames: int) -> int:
    """diffusers ``trim_conditioning_sequence``: clip to the target range, then
    down to ``1 + 8*k`` frames."""
    n = min(n_frames, target_frames - start_px)
    return (n - 1) // LTX_TEMPORAL * LTX_TEMPORAL + 1


def _pack(latent: Tensor) -> Tensor:
    """``(1, C, f, h, w)`` -> ``(1, f*h*w, C)`` (patch size 1, SymmetricPatchifier order)."""
    b, c, f, h, w = latent.shape
    return latent.permute(0, 2, 3, 4, 1).reshape(b, f * h * w, c)


def _grid_coords(f: int, h: int, w: int, device) -> Tensor:
    """Start/end latent coords ``[1, 3, f*h*w, 2]`` (SymmetricPatchifier layout)."""
    return SymmetricPatchifier(1, start_end=True).get_latent_coords(f, h, w, 1, device).to(torch.float32)


def prepare_ltx_conditions(
    conditions: List[LTXMediaCondition],
    vae_encode: Callable[[Tensor], Tensor],
    *,
    frames: int,
    height: int,
    width: int,
    device,
    dtype,
    latent_channels: int = LTX_CHANNELS,
    causal_fix: bool = True,
) -> PreparedConditioning:
    """Build the packed conditioning state for one generation.

    ``vae_encode``: ``(1, 3, T, H, W)`` in [-1, 1] -> normalized latent
    ``(1, C, T', H', W')`` (i.e. ``LTXCausalVideoVAE.encode``). ``causal_fix``
    mirrors ``config.causal_temporal_positioning`` and only affects
    ``role="reference"`` coords (keyframe coords never take it, per reference).
    """
    t_lat = (frames - 1) // LTX_TEMPORAL + 1
    h_lat = height // LTX_SPATIAL
    w_lat = width // LTX_SPATIAL
    s_base = t_lat * h_lat * w_lat

    tokens = torch.zeros((1, s_base, latent_channels), device=device, dtype=dtype)
    mask = torch.zeros((1, s_base), device=device, dtype=dtype)

    extra_tokens: List[Tensor] = []
    extra_masks: List[Tensor] = []
    extra_coords: List[Tensor] = []

    for cond in conditions:
        if cond.role not in ("keyframe", "reference"):
            raise ValueError(f"unknown LTX media condition role {cond.role!r}")

        if cond.latent is not None:
            # Pre-encoded entry: no resize, no vae_encode. `f == 1` is a
            # standalone one-pixel-frame clip and takes the [idx, idx + 1)
            # clamp below, exactly like a single-frame image condition does.
            lat = cond.latent.to(device=device, dtype=dtype)
            _, _, f, h, w = lat.shape
            n = 1 if f == 1 else (f - 1) * LTX_TEMPORAL + 1
            packed_pre = _pack(lat)
            pixel_frame_idx = cond.pixel_frame_index
            if pixel_frame_idx is None:
                # No pixel index: the only remaining addressing a pre-encoded
                # condition supports is the first-frame overwrite (the target's
                # own first latent frame already covers exactly one pixel frame,
                # so a standalone one-frame latent drops straight into it).
                if cond.latent_index != 0:
                    raise ValueError(
                        "a pre-encoded LTX media condition must carry either 'pixel_frame_index' "
                        "or latent_index=0 -- there is no pixel domain left to derive an "
                        "interior position from")
                n_tok = packed_pre.shape[1]
                tokens[:, :n_tok] = packed_pre
                mask[:, :n_tok] = float(cond.strength)
                continue
            if pixel_frame_idx >= frames:
                raise ValueError(
                    f"LTX media condition at pixel frame {pixel_frame_idx} is outside the "
                    f"{frames}-frame target")
            coords = latent_to_pixel_coords(_grid_coords(f, h, w, device), _SCALE_FACTORS, False)
            coords[:, 0, :, :] = coords[:, 0, :, :] + pixel_frame_idx
            if n == 1:
                coords[:, 0, :, 1:] = coords[:, 0, :, :1] + 1
            extra_tokens.append(packed_pre)
            extra_masks.append(torch.full((1, packed_pre.shape[1]), float(cond.strength),
                                          device=device, dtype=dtype))
            extra_coords.append(coords)
            continue

        n_in = int(cond.frames.shape[0])

        if cond.role == "reference":
            # IC-LoRA reference: trim to the generation length, coords = base grid
            # at the reference's latent dims (causal fix, temporal origin 0).
            n = min(n_in, frames)
            n = (n - 1) // LTX_TEMPORAL * LTX_TEMPORAL + 1
            pixels = _resize_cover_center_crop(cond.frames[:n], height, width).to(device=device, dtype=dtype)
            lat = vae_encode(pixels)
            _, _, f, h, w = lat.shape
            packed = _pack(lat).to(device=device, dtype=dtype)
            coords = latent_to_pixel_coords(_grid_coords(f, h, w, device), _SCALE_FACTORS, causal_fix)
            extra_tokens.append(packed)
            extra_masks.append(torch.full((1, packed.shape[1]), float(cond.strength), device=device, dtype=dtype))
            extra_coords.append(coords)
            continue

        explicit_px = cond.pixel_frame_index
        if explicit_px is not None:
            # Explicit pixel addressing: no latent-index derivation, and the
            # condition is always APPENDED (frame 0 is rejected in __post_init__).
            if explicit_px >= frames:
                raise ValueError(
                    f"LTX media condition at pixel frame {explicit_px} is outside the "
                    f"{frames}-frame target")
            latent_idx, start_px = -1, explicit_px
        else:
            latent_idx = cond.latent_index
            if latent_idx < 0:
                latent_idx = latent_idx % t_lat
            if latent_idx >= t_lat:
                raise ValueError(
                    f"LTX media condition latent_index {latent_idx} out of range for {t_lat} latent frames")
            start_px = max((latent_idx - 1) * LTX_TEMPORAL + 1, 0)

        n = _trim_condition_frames(start_px, n_in, frames)
        if n < 1:
            raise ValueError(
                f"LTX media condition at pixel frame {start_px} has no frames left after trimming")
        pixels = _resize_cover_center_crop(cond.frames[:n], height, width).to(device=device, dtype=dtype)
        lat = vae_encode(pixels)
        _, _, f, h, w = lat.shape
        packed = _pack(lat).to(device=device, dtype=dtype)

        if latent_idx == 0:
            n_tok = packed.shape[1]
            tokens[:, :n_tok] = packed
            mask[:, :n_tok] = float(cond.strength)
        else:
            # Appended keyframe: latent grid × VAE scale, NO causal fix, temporal
            # offset in pixel frames; a single-pixel-frame condition occupies
            # [idx, idx + 1) instead of the VAE-scaled span.
            coords = latent_to_pixel_coords(_grid_coords(f, h, w, device), _SCALE_FACTORS, False)
            coords[:, 0, :, :] = coords[:, 0, :, :] + start_px
            if n == 1:
                coords[:, 0, :, 1:] = coords[:, 0, :, :1] + 1
            extra_tokens.append(packed)
            extra_masks.append(torch.full((1, packed.shape[1]), float(cond.strength), device=device, dtype=dtype))
            extra_coords.append(coords)

    if extra_tokens:
        tokens = torch.cat([tokens] + extra_tokens, dim=1)
        mask = torch.cat([mask] + extra_masks, dim=1)
        coords_out: Optional[Tensor] = torch.cat(extra_coords, dim=2)
        n_extra = tokens.shape[1] - s_base
    else:
        coords_out = None
        n_extra = 0

    return PreparedConditioning(
        tokens=tokens, mask=mask, clean=tokens.clone(),
        extra_coords=coords_out, n_extra=n_extra, base_tokens=s_base,
    )


def mix_initial_noise(prepared: PreparedConditioning, noise: Tensor, sigma0: float = 1.0) -> Tensor:
    """diffusers init mix: ``scaled = (1 - mask) * sigma0``;
    ``x = noise * scaled + clean * (1 - scaled)``. mask=1 tokens start exactly
    clean; mask=0 tokens are pure noise at ``sigma0 = sigmas[0]``."""
    scaled = (1.0 - prepared.mask).unsqueeze(-1) * sigma0
    return noise * scaled + prepared.tokens * (1.0 - scaled)


def merge_initial_latent_tokens(prepared: PreparedConditioning, packed: Tensor) -> Tensor:
    """Merge a stage-2 refine's packed initial-latent tokens into ``prepared``'s
    base token slice: masked (keyframe/reference-anchored) positions keep
    ``prepared``'s own encoded conditioning, unmasked positions take the
    upsampled prior latent (``packed``). Any appended (``n_extra``) tokens
    ride through unchanged.

    Pure -- reads ``prepared`` but never mutates it or the object it came
    from. ``mask`` is a STRENGTH (fractional, not just 0/1 -- see
    ``ConditionedAVForward``'s module docstring), so the caller MUST NOT feed
    this call's own return value back into ``prepared`` for a LATER call: at
    fractional strength the blend is not idempotent, and a caller sharing one
    ``PreparedConditioning`` object across multiple seeds while re-assigning
    its ``tokens`` field after each seed would leak that seed's own packed
    latent into the next seed's "unmasked" contribution.
    """
    s_base = prepared.base_tokens
    m = prepared.mask[:, :s_base].unsqueeze(-1)
    merged_base = packed * (1.0 - m) + prepared.tokens[:, :s_base] * m
    if prepared.n_extra:
        return torch.cat([merged_base, prepared.tokens[:, s_base:]], dim=1)
    return merged_base
