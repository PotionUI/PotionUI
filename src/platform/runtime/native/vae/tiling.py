"""Spatial tiled encode/decode, ported from ComfyUI's ``comfy/utils.py``
``tiled_scale_multidim`` (trimmed to linear feather blending; the N-dim
index-formula generality there is for video VAEs this module doesn't need).

Covers two VAE shapes:
  * ``AutoEncoder2D`` (4D ``(B,C,H,W)`` tensors) -- ``tiled_decode``/``tiled_encode``.
  * ``AutoEncoderCausal3D`` (5D ``(B,C,T,H,W)`` tensors) -- ``tiled_encode_causal3d``
    and ``tiled_decode_causal3d``, which tile the H/W axes only and pass the full
    temporal axis through each tile whole (the causal ``feat_cache`` chunking must
    not be split; SPATIAL tiling only). Encode is the Wan i2v 480p+ start-frame
    path; decode is the Krea-2/Qwen-Image high-res path (the fp32 3D-conv decode
    spike scales with pixels and exceeds a 31GB card past ~1024², and caps 12GB
    cards at 512² untiled).

Algorithm: run ``vae.decode``/``vae.encode`` tile-by-tile, weight each tile's
edges with a linear ramp over the overlap region, and normalize by the summed
weight where tiles overlap. This avoids visible seams without needing any
cross-tile attention.
"""

from __future__ import annotations

import logging

import torch

from .ae_2d import AutoEncoder2D
from .causal_3d import AutoEncoderCausal3D
from .causal_3d_v2 import AutoEncoderCausal3D_2_2
from ..memory.tiering import activation_headroom_gb

logger = logging.getLogger(__name__)

# Spatial downscale ratio of the Flux 2D AE (8x, from resolution=256 with 3
# downsample stages -- verified against both ae.sft and flux2-vae header
# dumps: 3 downsample convs before the bottleneck).
VAE_SPATIAL_DOWNSCALE = 8


def _feather_mask(tile: torch.Tensor, feather: int, spatial_dims: tuple[int, int] = (2, 3)) -> torch.Tensor:
    mask = torch.ones_like(tile)
    for dim in spatial_dims:
        f = min(feather, mask.shape[dim] // 2)
        if f <= 0:
            continue
        for t in range(f):
            a = (t + 1) / f
            mask.narrow(dim, t, 1).mul_(a)
            mask.narrow(dim, mask.shape[dim] - 1 - t, 1).mul_(a)
    return mask


def _tiled_apply(
    x: torch.Tensor,
    fn,
    *,
    tile_size: int,
    overlap: int,
    scale: float,
    out_channels: int,
) -> torch.Tensor:
    """Shared tile/blend loop. ``scale`` maps input spatial size -> output
    spatial size (``VAE_SPATIAL_DOWNSCALE`` for decode, ``1/VAE_SPATIAL_DOWNSCALE``
    for encode)."""
    b, _c, h, w = x.shape
    if h <= tile_size and w <= tile_size:
        return fn(x)

    out_h, out_w = round(h * scale), round(w * scale)
    out = torch.zeros((b, out_channels, out_h, out_w), device=x.device, dtype=torch.float32)
    out_weight = torch.zeros_like(out)

    step = tile_size - overlap
    ys = list(range(0, max(h - overlap, 1), step)) if h > tile_size else [0]
    xs = list(range(0, max(w - overlap, 1), step)) if w > tile_size else [0]

    for y in ys:
        y = max(0, min(h - overlap, y)) if h > tile_size else 0
        ty = min(tile_size, h - y)
        for xx in xs:
            xx = max(0, min(w - overlap, xx)) if w > tile_size else 0
            tx = min(tile_size, w - xx)

            tile_in = x[:, :, y:y + ty, xx:xx + tx]
            tile_out = fn(tile_in).to(dtype=torch.float32)

            feather = round(overlap * scale)
            mask = _feather_mask(tile_out, feather)

            oy, ox = round(y * scale), round(xx * scale)
            oh, ow = tile_out.shape[2], tile_out.shape[3]
            out[:, :, oy:oy + oh, ox:ox + ow] += tile_out * mask
            out_weight[:, :, oy:oy + oh, ox:ox + ow] += mask

    return (out / out_weight.clamp_min(1e-8)).to(dtype=x.dtype)


def tiled_decode(
    vae: AutoEncoder2D,
    latent: torch.Tensor,
    tile_size: int = 64,
    overlap: int = 8,
) -> torch.Tensor:
    """Tiled ``vae.decode`` with linear-blended seams. ``tile_size``/``overlap``
    are in *latent* pixels (matching ComfyUI convention); the output is full
    resolution (``tile_size * VAE_SPATIAL_DOWNSCALE`` per tile before blending)."""
    return _tiled_apply(
        latent, vae.decode,
        tile_size=tile_size, overlap=overlap,
        scale=VAE_SPATIAL_DOWNSCALE, out_channels=3,
    )


def tiled_encode(
    vae: AutoEncoder2D,
    pixels: torch.Tensor,
    tile_size: int = 512,
    overlap: int = 64,
) -> torch.Tensor:
    """Tiled ``vae.encode`` with linear-blended seams. ``tile_size``/``overlap``
    are in *pixel* space."""
    return _tiled_apply(
        pixels, vae.encode,
        tile_size=tile_size, overlap=overlap,
        scale=1.0 / VAE_SPATIAL_DOWNSCALE, out_channels=vae.z_channels,
    )


@torch.no_grad()
def tiled_encode_causal3d(
    vae: AutoEncoderCausal3D,
    pixels: torch.Tensor,
    tile_size: int = 512,
    overlap: int = 64,
) -> torch.Tensor:
    """Spatial tiled ``vae.encode`` for the causal-3D (Wan-shaped) VAE, with
    linear-blended seams.

    Tiles only the H/W axes of a ``(B, 3, T, H, W)`` pixel tensor; the full
    temporal axis is passed through each tile whole, so the VAE's causal
    ``feat_cache`` temporal chunking is byte-for-byte unchanged (this is a
    SPATIAL tiling only -- the ``feat_cache`` 1-then-4 frame grouping is
    architecturally required and must not be split, see ``causal_3d.py``).

    ``tile_size``/``overlap`` are in *pixel* space and should be multiples of
    ``VAE_SPATIAL_DOWNSCALE`` so tile seams land on exact latent-pixel
    boundaries. Returns ``(B, 16, T_lat, H/8, W/8)`` -- the distribution mean
    (``mu``), identical to what the untiled ``encode`` returns.
    """
    b, _c, _t, h, w = pixels.shape
    if h <= tile_size and w <= tile_size:
        return vae.encode(pixels)

    scale = 1.0 / VAE_SPATIAL_DOWNSCALE
    feather = round(overlap * scale)
    step = tile_size - overlap
    ys = list(range(0, max(h - overlap, 1), step)) if h > tile_size else [0]
    xs = list(range(0, max(w - overlap, 1), step)) if w > tile_size else [0]

    out: torch.Tensor | None = None
    out_weight: torch.Tensor | None = None
    for y in ys:
        y = max(0, min(h - overlap, y)) if h > tile_size else 0
        ty = min(tile_size, h - y)
        for xx in xs:
            xx = max(0, min(w - overlap, xx)) if w > tile_size else 0
            tx = min(tile_size, w - xx)

            tile_in = pixels[:, :, :, y:y + ty, xx:xx + tx]
            tile_out = vae.encode(tile_in).to(dtype=torch.float32)

            if out is None:
                # T_lat/out_channels are only known after the first encode
                # (T_lat depends on the temporal-downsample chunking).
                out = torch.zeros(
                    (b, tile_out.shape[1], tile_out.shape[2], round(h * scale), round(w * scale)),
                    device=pixels.device, dtype=torch.float32,
                )
                out_weight = torch.zeros_like(out)

            mask = _feather_mask(tile_out, feather, spatial_dims=(3, 4))
            oy, ox = round(y * scale), round(xx * scale)
            oh, ow = tile_out.shape[3], tile_out.shape[4]
            out[:, :, :, oy:oy + oh, ox:ox + ow] += tile_out * mask
            out_weight[:, :, :, oy:oy + oh, ox:ox + ow] += mask

    return (out / out_weight.clamp_min(1e-8)).to(dtype=pixels.dtype)


@torch.no_grad()
def tiled_decode_causal3d(
    vae: AutoEncoderCausal3D,
    latent: torch.Tensor,
    tile_size: int = 256,
    overlap: int = 32,
) -> torch.Tensor:
    """Spatial tiled ``vae.decode`` for the causal-3D (Wan-shaped) VAE, with
    linear-blended seams -- the decode twin of :func:`tiled_encode_causal3d`.

    Tiles only the H/W axes of a ``(B, 16, T, H, W)`` *latent* tensor; the full
    temporal axis is passed through each tile whole, so the VAE's causal
    ``feat_cache`` temporal chunking is byte-for-byte unchanged (SPATIAL tiling
    only -- the ``feat_cache`` 1-then-4 frame grouping is architecturally
    required and must not be split, see ``causal_3d.py``). Each tile is decoded
    independently and the ``8x``-upscaled pixel tiles are feathered together in
    OUTPUT (pixel) space with a linear ramp over the overlap region, normalized
    by the summed weight where tiles overlap.

    ``tile_size``/``overlap`` are in *latent* pixels (matching the 2D
    :func:`tiled_decode` convention); the output is full resolution
    (``tile_size * VAE_SPATIAL_DOWNSCALE`` per tile before blending). Returns
    ``(B, 3, T', H*8, W*8)`` pixels in ``[-1, 1]`` -- identical shape to the
    untiled ``decode``. The interior (away from the outer edge) matches the
    whole decode tightly; only the middle-block spatial attention differs
    per-tile, which the seam blend keeps close (mirrors the encode twin's
    ~1e-3 seam tolerance).
    """
    b, _c, _t, h, w = latent.shape
    if h <= tile_size and w <= tile_size:
        return vae.decode(latent)

    scale = VAE_SPATIAL_DOWNSCALE
    feather = round(overlap * scale)
    step = tile_size - overlap
    ys = list(range(0, max(h - overlap, 1), step)) if h > tile_size else [0]
    xs = list(range(0, max(w - overlap, 1), step)) if w > tile_size else [0]

    out: torch.Tensor | None = None
    out_weight: torch.Tensor | None = None
    for y in ys:
        y = max(0, min(h - overlap, y)) if h > tile_size else 0
        ty = min(tile_size, h - y)
        for xx in xs:
            xx = max(0, min(w - overlap, xx)) if w > tile_size else 0
            tx = min(tile_size, w - xx)

            tile_in = latent[:, :, :, y:y + ty, xx:xx + tx]
            tile_out = vae.decode(tile_in).to(dtype=torch.float32)

            if out is None:
                # T'/out_channels are only known after the first decode (T'
                # depends on the temporal-upsample chunking, 3 channels for RGB).
                out = torch.zeros(
                    (b, tile_out.shape[1], tile_out.shape[2], round(h * scale), round(w * scale)),
                    device=latent.device, dtype=torch.float32,
                )
                out_weight = torch.zeros_like(out)

            mask = _feather_mask(tile_out, feather, spatial_dims=(3, 4))
            oy, ox = round(y * scale), round(xx * scale)
            oh, ow = tile_out.shape[3], tile_out.shape[4]
            out[:, :, :, oy:oy + oh, ox:ox + ow] += tile_out * mask
            out_weight[:, :, :, oy:oy + oh, ox:ox + ow] += mask

    return (out / out_weight.clamp_min(1e-8)).to(dtype=latent.dtype)


@torch.no_grad()
def chunked_decode_causal3d(
    vae: AutoEncoderCausal3D | AutoEncoderCausal3D_2_2,
    z: torch.Tensor,
    chunk_latent_frames: int = 8,
    *,
    accumulate_device: "str | torch.device | None" = None,
) -> torch.Tensor:
    """Temporal-chunked ``vae.decode`` for a causal-3D video VAE -- bounds peak
    decode VRAM (and the growing ``out`` accumulator) by ``chunk_latent_frames``
    instead of the full clip length, the current ceiling on video duration.

    ``vae.decode`` already decodes ONE latent frame at a time internally (its
    own ``for i in range(z.shape[2])`` loop, see ``causal_3d.py`` /
    ``causal_3d_v2.py``), carrying causal-conv state through a ``feat_cache``
    it normally builds fresh and discards per call. That per-frame loop is
    where the causal state actually advances -- there is no "1-then-4"
    grouping to respect on the DECODE side (that's an ENCODE-only artifact of
    the encoder's 4x temporal downsample: a decoder latent frame already IS
    the causal-chunk unit). So this function's whole job is to build ONE
    persistent cache up front via ``vae.new_feat_cache()`` and feed it
    successive temporal SLICES of ``z`` across repeated ``decode()`` calls
    instead of a single call over the whole tensor -- mathematically identical
    to one full decode, because the cache carries the exact same state across
    the call boundary that it would carry across the internal per-frame-loop
    boundary within a single call. ``chunk_latent_frames`` therefore has no
    alignment requirement (any value >= 1 is valid, degenerating to a single
    ``decode()`` call when it's >= the clip length).

    The Wan 2.2 VAE (:class:`AutoEncoderCausal3D_2_2`) additionally needs
    ``first_chunk=True`` on (and only on) the call carrying the clip's true
    first latent frame -- threaded through here automatically.

    NOT composed with spatial tiling (:func:`tiled_decode_causal3d`): that
    would need a separate persistent ``feat_cache`` per spatial-tile position
    (the cached tensors are shaped to whatever spatial slice produced them),
    which is more machinery than the current spatial tiler's stateless
    tile-then-blend loop supports. Ship temporal-only for now; a caller that
    needs both should pick one axis to bound VRAM on the current call site
    rather than force the composition.

    Engine wiring note: nothing here decides WHEN to chunk (VRAM-based sizing)
    or calls this from the decode orchestration path -- that's
    ``engine.py``'s ``_decode_causal3d_tiled`` (or its caller) to wire up, out
    of scope for this change; this function only provides the primitive.
    """
    def _place(x: torch.Tensor) -> torch.Tensor:
        # Move each decoded chunk off the decode device as it is produced so the
        # accumulator + the final concat don't hold the whole clip (twice, counting
        # cat's fresh allocation) on the GPU — that duplicate can OOM a long clip at
        # assembly even when a single chunk's activations fit, defeating the point
        # of chunking. ``accumulate_device=None`` keeps the legacy on-device return.
        return x if accumulate_device is None else x.to(accumulate_device)

    t = z.shape[2]
    if t <= chunk_latent_frames:
        return _place(vae.decode(z))

    is_v2 = isinstance(vae, AutoEncoderCausal3D_2_2)
    feat_cache = vae.new_feat_cache()
    outs: list[torch.Tensor] = []
    for start in range(0, t, chunk_latent_frames):
        z_chunk = z[:, :, start:start + chunk_latent_frames]
        if is_v2:
            outs.append(_place(vae.decode(z_chunk, feat_cache=feat_cache, first_chunk=(start == 0))))
        else:
            outs.append(_place(vae.decode(z_chunk, feat_cache=feat_cache)))
    return torch.cat(outs, dim=2)


def causal3d_chunk_frames(
    vae_module,
    latents: torch.Tensor,
    *,
    free_vram_gb_value: float | None,
    vae_resident_gb: float = 0.0,
    is_self_normalizing: bool = False,
    decode_mb_per_latent_px: float = 1.2,
    vram_fraction: float = 0.75,
) -> int | None:
    """Max latent frames per TEMPORAL chunk (:func:`chunked_decode_causal3d`)
    whose full-spatial decode fits the given free-VRAM budget, or ``None`` when
    temporal chunking doesn't apply.

    Shared sizing primitive behind both ``NativeGenerator._causal3d_chunk_frames``
    (``engine.py``, which supplies its own live-queried VRAM/residency state)
    and the Wan/LTX generator pipes' own ``_decode_video`` (which have no
    ``NativeGenerator`` instance to ask, so they query
    :func:`~..memory.residency.free_vram_gb` and estimate the VAE's
    resident-ness themselves before calling in here with plain values).
    ``decode_mb_per_latent_px``/``vram_fraction`` default to the SAME values
    ``engine.py`` uses internally (its own constants are passed explicitly, so
    there's no drift risk there; a pipe caller relying on these defaults gets
    identical sizing behavior to the engine path -- see
    ``tests/core/native/vae/test_tiling.py`` for a test pinning the two
    constant sets equal).

    Applies only to the Wan v1/v2 feat_cache causal-3D VAEs (detected via
    ``hasattr(vae_module, "new_feat_cache")`` -- LTX's VAE has no such method,
    so this is a no-op there by construction) and only to multi-frame
    latents; ``None`` on self-normalizing VAEs (SeedVR2), when free VRAM can't
    be queried, or when even a SINGLE latent frame's spatial decode wouldn't
    fit (spatial-tiling territory instead -- the two axes don't compose, see
    :func:`chunked_decode_causal3d`).
    """
    if is_self_normalizing or not hasattr(vae_module, "new_feat_cache"):
        return None
    frames = int(latents.shape[2]) if latents.ndim == 5 else 1
    if frames <= 1:
        return None
    if free_vram_gb_value is None:
        return None

    h, w = latents.shape[-2], latents.shape[-1]
    one = activation_headroom_gb((h, w), decode_mb_per_latent_px=decode_mb_per_latent_px, latent_frames=1)
    two = activation_headroom_gb((h, w), decode_mb_per_latent_px=decode_mb_per_latent_px, latent_frames=2)
    per_frame = two - one          # marginal decode spike for one extra latent frame
    base = one - per_frame         # fixed working overhead (paid once)
    budget = free_vram_gb_value * vram_fraction - vae_resident_gb - base
    if per_frame <= 0 or budget < per_frame:   # not even one frame fits -> spatial tiling territory
        return None
    max_frames = int(budget / per_frame)
    if max_frames >= frames:                   # whole clip already fits temporally -> no chunking
        return None
    return max(1, max_frames)


def auto_tile_size(vram_free_gb: float | None, latent_hw: tuple[int, int]) -> int | None:
    """Heuristic latent-space tile size, or ``None`` when no tiling is needed.

    Decode is the memory-heavy direction (attention + convs materialize at up
    to ``8x`` the latent resolution). Budget: empirically the Flux 2D AE
    decoder needs roughly ``2.5 GB`` per ``512x512`` *pixel* output tile
    (``64x64`` latent tile) at bf16 -- i.e. ``~0.6 MB`` per latent pixel. This
    is a simple, deliberately conservative heuristic (not a measured VRAM
    profile); it only decides *whether and how much* to tile, real allocation
    still fails safe (OOM surfaces normally, tiling just makes it far less
    likely for typical resolutions).
    """
    h, w = latent_hw
    if vram_free_gb is None:
        return None

    bytes_per_latent_px = 0.6 * 1024 * 1024  # ~0.6 MB/px, see docstring
    budget_bytes = vram_free_gb * (1024 ** 3) * 0.7  # keep 30% headroom
    full_bytes = h * w * bytes_per_latent_px
    if full_bytes <= budget_bytes:
        return None

    # Largest square tile (in latent px) whose area fits the budget, snapped
    # down to a multiple of 8 (comfortably divisible, avoids degenerate tiles).
    max_tile_area = budget_bytes / bytes_per_latent_px
    tile = int(max_tile_area ** 0.5)
    tile = max(8, (tile // 8) * 8)
    return min(tile, max(h, w))
