"""Wan image-to-video conditioning: the 36-channel concat construction.

Ported EXACTLY from ComfyUI's ``WanImageToVideo`` node + ``WAN21.concat_cond``
(comfy_extras/nodes_wan.py + comfy/model_base.py). The concat-i2v Wan DiTs
(``wan22_i2v_14b``, ``in_dim`` 36 = 16 noise + 20 concat) take, per forward:

    DiT_input = cat([ noise(16ch),  mask(4ch),  ref_latent(16ch) ], dim=1)   # 36

where the 20-channel ``c_concat = cat(mask, ref_latent)`` is built here (constant
across sampling steps) and prepended to the noisy latent inside the generator's
i2v ``model_forward``. Layout details, all verified against ComfyUI and against
diffusers' ``pipeline_wan_i2v.py`` (first+last-frame / FLF variant):

  * ``ref_latent`` — VAE-encode a (length, H, W, 3) buffer that is grey (0.5)
    everywhere except the provided start frame(s) (front) and, if ``end_frames``
    is given, the provided end frame(s) (tail), then Wan21-normalize
    ``(latent - mean) / std`` (``process_latent_in``).
  * ``mask`` — built at PIXEL-frame resolution: 1 where a frame is provided
    (start and/or end), 0 where generated (mid, grey). The first pixel frame is
    repeated 4x and concatenated with the remaining ``length - 1`` pixel-frame
    mask values, giving ``length + 3 == 4 * t_lat`` entries; reshaping that into
    ``(t_lat, 4)`` groups packs, per latent frame, which of its 4 underlying
    pixel-frame slots were actually provided. For start-only input this
    collapses to the same result as zeroing the covering latent slots and
    inverting (the old construction); for FLF, the last latent frame's group
    comes out ``[0, 0, 0, 1]`` (only its final pixel slot is the real end frame).
  * final order: ``cat(mask, ref_latent)`` — mask FIRST, then the reference.

``tail_latent`` (chain-video seam hand-off): a real previously-SAMPLED
latent tail, already in this same process_latent_in-normalized space (the
sampler's ``x`` and this function's post-normalize ``ref`` are the same
"model space" for the Wan21 16ch format -- ``_decode_video`` only ever
un-normalizes on the way OUT to pixels; nothing normalizes going IN to the
sampler), spliced directly over ``ref``'s leading temporal slots, replacing
whatever ``vae_encode`` produced there. Lets a chain continuation lock onto
the previous segment's own latent instead of that segment's decoded ->
uint8-quantized -> re-encoded pixels, removing two of the three seam
color-shift sources (the third, causal leakage from the grey/dummy buffer
into the still-soft slots past the splice, is unaffected on purpose -- that
region is mask=0 / free, not something a caller needs bit-exact).
"""

from __future__ import annotations

from typing import Callable, Sequence

import torch

Tensor = torch.Tensor
_TEMPORAL_DOWNSCALE = 4
_SPATIAL_DOWNSCALE = 8


def build_i2v_concat(
    start_frames: Tensor,
    vae_encode: Callable[[Tensor], Tensor],
    *,
    length: int,
    height: int,
    width: int,
    latents_mean: Sequence[float],
    latents_std: Sequence[float],
    end_frames: Tensor | None = None,
    anchor_frames: Tensor | None = None,
    anchor_strength: float = 1.0,
    tail_latent: Tensor | None = None,
    device: str | torch.device = "cpu",
    dtype: torch.dtype = torch.float32,
) -> Tensor:
    """Build the 20-channel ``c_concat`` (mask 4ch + ref latent 16ch) for i2v.

    ``start_frames``: ``(n, H, W, 3)`` in ``[0, 1]`` (already resized to H×W);
    typically one frame. ``end_frames``: optional ``(m, H, W, 3)`` in ``[0, 1]``,
    placed at the tail of the conditioning buffer for first+last-frame (FLF)
    conditioning; ``None`` (default) reproduces the start-only construction
    exactly. ``vae_encode``: the causal-3D VAE ``encode`` — takes
    ``(1, 3, T, H, W)`` in ``[-1, 1]`` and returns ``(1, 16, T_lat, H/8, W/8)``.

    ``anchor_strength`` scales the mask weight over the anchor frames: ``1.0``
    (default) hard-locks them exactly as before; lower values soften the lock so
    the sampler is free to move away from the anchor while the end (FLF) frames
    stay hard-locked.

    ``anchor_frames`` (SVI Pro continuity, ``None`` in the plain i2v/flf path)
    places a persistent anchor at slot 0 AHEAD of ``start_frames``. When given,
    ``start_frames`` becomes a SOFT motion tail: its pixels enter the reference
    latent as context but its mask stays 0, so only the anchor (and any end
    frames) are locked -- the layout the SVI Pro recipe grounds every
    continuation segment on. ``None`` reproduces today's construction exactly.

    ``tail_latent`` (``None`` by default, byte-identical omission): a real
    sampled latent tail, ``(1, 16, n, H/8, W/8)`` already process_latent_in
    normalized, spliced directly over ``ref``'s first ``n`` temporal slots
    AFTER the normal encode+normalize below -- bypassing the decode ->
    uint8 -> re-encode round trip for exactly the region the mask actually
    locks. The caller picks ``start_frames``/``anchor_frames`` counts that
    still make the mask lock ``n`` latent slots (unchanged from today); only
    the *content* of those slots' `ref` changes.

    Returns ``(1, 20, T_lat, H/8, W/8)``.
    """
    n_start = int(start_frames.shape[0])
    n_end = int(end_frames.shape[0]) if end_frames is not None else 0
    n_anchor = int(anchor_frames.shape[0]) if anchor_frames is not None else 0
    t_lat = (length - 1) // _TEMPORAL_DOWNSCALE + 1
    h_lat, w_lat = height // _SPATIAL_DOWNSCALE, width // _SPATIAL_DOWNSCALE

    # Grey-filled (0.5) buffer. Plain path: start frame(s) at the front. SVI Pro
    # path: the anchor at the front, the (soft) motion tail immediately after it.
    # FLF end frame(s) always sit at the tail; mid frames stay grey.
    image = torch.ones((length, height, width, 3), device=device, dtype=dtype) * 0.5
    if anchor_frames is not None:
        image[:n_anchor] = anchor_frames[:length].to(device=device, dtype=dtype)
        motion = start_frames[:max(0, length - n_anchor)].to(device=device, dtype=dtype)
        image[n_anchor:n_anchor + motion.shape[0]] = motion
    else:
        image[:n_start] = start_frames[:length].to(device=device, dtype=dtype)
    if end_frames is not None:
        image[length - n_end:] = end_frames[-n_end:].to(device=device, dtype=dtype)

    # (length, H, W, 3) [0,1] -> (1, 3, length, H, W) [-1,1] for the VAE.
    pixels = (image * 2.0 - 1.0).permute(3, 0, 1, 2).unsqueeze(0)
    ref = vae_encode(pixels)  # (1, 16, T_lat, h_lat, w_lat)

    mean = torch.tensor(latents_mean, device=ref.device, dtype=ref.dtype).view(1, -1, 1, 1, 1)
    std = torch.tensor(latents_std, device=ref.device, dtype=ref.dtype).view(1, -1, 1, 1, 1)
    ref = (ref - mean) / std  # Wan21 process_latent_in

    if tail_latent is not None:
        n_tail = int(tail_latent.shape[2])
        ref = ref.clone()
        ref[:, :, :n_tail] = tail_latent.to(device=ref.device, dtype=ref.dtype)

    # Pixel-resolution temporal mask (ComfyUI/diffusers WanImageToVideo packing):
    # 1 where a frame is provided (start and/or end), 0 where generated. The
    # first pixel frame is repeated 4x before the 4-channel latent-frame
    # grouping so the resulting (t_lat, 4) blocks correctly encode sub-frame
    # position within each latent frame -- needed for correct FLF blending. In
    # the SVI Pro path only the anchor is locked; the motion tail stays 0 (soft).
    mask_px = torch.zeros(length, device=ref.device, dtype=ref.dtype)
    if anchor_frames is not None:
        mask_px[:n_anchor] = float(anchor_strength)
    else:
        mask_px[:n_start] = float(anchor_strength)
    if end_frames is not None:
        mask_px[length - n_end:] = 1.0
    expanded = torch.cat([mask_px[0:1].repeat(4), mask_px[1:]])  # length+3 == 4*t_lat
    mask = expanded.view(t_lat, 4).permute(1, 0).reshape(1, 4, t_lat, 1, 1)
    mask = mask.expand(1, 4, t_lat, h_lat, w_lat)

    return torch.cat((mask, ref), dim=1)  # (1, 20, T_lat, h_lat, w_lat)
