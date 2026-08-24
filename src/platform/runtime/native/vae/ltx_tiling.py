"""Tiled VIDEO ENCODE for the LTX-2/2.3 causal video VAE -- a faithful port of
Lightricks' first-party ``ltx-core`` package (local checkout, Apache-2.0, NOT
vendored into this repo -- see below), not a reinvention.

**The problem this solves**: ``LTXCausalVideoVAE.encode()`` (``ltx_causal_video.py``)
runs the ENTIRE clip through the encoder in one shot -- unlike its own
``Decoder``, which self-chunks (``Decoder.forward``'s ``MAX_CHUNK_BYTES``
recursion). This OOMs a 32GB card on a
480x832x121-frame clip even on a CLEAN GPU (0.05GB allocated pre-encode): early
full-resolution encoder activations are ~12GB each, with causal-pad
``torch.cat`` copies stacked on top. Whole-clip encode of any real video is
architecturally impossible past a certain size -- this module is the fix.

**Reference source** (read, not imported -- this repo has no dependency on
``ltx_core``):
  - ``ltx_core/model/video_vae/video_vae.py``:
    ``VideoEncoder.tiled_encode`` / ``prepare_tiles_for_encoding``
    (+ the module-level ``map_spatial_interval_to_latent`` /
    ``map_temporal_interval_to_latent`` / ``to_mapping_operation`` helpers).
  - ``ltx_core/tiling.py``: ``split_by_size``, ``split_temporal`` (the
    ENCODE-side temporal splitter -- NOT ``split_temporal_causal``, which is
    the DECODE-side one and is out of scope here since decode already
    self-chunks), ``compute_rectangular_mask_1d``.
  - ``ltx_core/model/video_vae/tiling.py``: ``SpatialTilingConfig`` /
    ``TemporalTilingConfig`` / ``TilingConfig`` (+ their validation and
    ``TilingConfig.default()``).

**The algorithm, summarized** (see each function's docstring for the exact
transcription):

1. The clip is split into overlapping tiles along up to three axes -- T
   (temporal), H, W (spatial) -- directly in PIXEL/FRAME space. Batch and
   channel axes always pass through whole.
2. Each tile is encoded INDEPENDENTLY via a full (non-chunked) encoder
   forward -- no shared causal-conv cache across tiles, exactly like calling
   :meth:`LTXCausalVideoVAE.encode` fresh on each tile's pixels.
3. Reassembly is NOT a blended overlap (unlike this codebase's own
   ``vae/tiling.py``, tuned for the Wan-shaped causal VAEs' bidirectional
   attention-heavy decoder): each output-latent axis interval gets a HARD 0/1
   ("rectangular") mask that DISCARDS the padding-corrupted latents at a
   tile's edges rather than blending them in -- the encoder's zero-padding at
   an artificial tile boundary is WRONG data, not merely lower-quality data,
   so blending it in would mix garbage into the seam instead of avoiding it.
   Discarded regions are covered by the (real, non-padded) data the
   NEIGHBORING tile produces for that same output region, i.e. "discard your
   own corrupted edge, and rely on the overlap so the neighbor's clean data
   fills it in" -- concatenation via disjoint hard masks, not a blend.
4. Spatial (H/W) discard math: the encoder's convs use SYMMETRIC zero padding
   (``padding=1`` at every 3x3 conv), so BOTH edges of a spatial tile are
   corrupted by fabricated (padding) context, not just one. Given ``scale``
   px/latent and a tile's input ``left_ramp``/``right_ramp`` (the overlap
   width shared with the previous/next tile, 0 at the clip's own outer edge):
   discard ``max(0, left_ramp // scale - 1)`` latents from the left and ``0``
   or ``1`` latent from the right (present iff there IS a right neighbor).
   The asymmetry (left uses the full ``// scale - 1``, right only ever
   discards a single latent) is transcribed as-is from ``ltx_core`` -- not
   independently re-derived here, since re-deriving would risk "improving"
   away a carefully-tuned discard width. Minimum enforced overlap: 64px.
5. Temporal (T) discard math: the encoder's temporal convs are CAUSAL
   (``CausalConv3d`` -- only look at PAST frames, so only the LEFT/past edge
   of a temporal tile is corrupted by fabricated padding; the right/future
   edge is always real data from the next tile, hence temporal tiles are
   built with ``right_ramp`` forced to 0 everywhere and only ever discard
   from the left: ``0 if left_ramp == 0 else 1 + (left_ramp - 1) // scale``.
   The ENCODE-side temporal split (``split_temporal``, not
   ``split_temporal_causal``) additionally EXTENDS every non-last tile's
   input end by exactly 1 frame -- this keeps each tile's own frame count on
   the encoder's required ``1 + 8*k`` lattice (a plain multiple-of-8 tile
   size, e.g. 64, is otherwise an INVALID encode() input on its own).
   Minimum enforced overlap: 16 frames.
6. Reassembly: accumulate ``latent_tile * mask`` and ``mask`` into two
   same-shaped buffers per tile, then divide (``weights.clamp(min=1e-8)``
   only to guard a true zero-coverage gap -- see point 7, weight is NOT
   always exactly 1 at every position).
7. **Verified-by-hand property, not a bug**: temporal tiling's "+1 frame"
   extension (point 5) means the retained (non-discarded) latent ranges of
   two temporally-adjacent tiles OVERLAP BY EXACTLY ONE LATENT at every
   internal junction -- e.g. for a 3-or-more-tile temporal split, the
   junction latent between tile *i* and tile *i+1* gets weight 2 (both
   tiles keep it, neither discards it), while every other retained position
   gets weight 1. The reassembly divide-by-weight silently turns that one
   junction latent into a 2-way AVERAGE of the two tiles' values instead of
   a hard cut -- this is not a partition bug to "fix" by widening a discard
   window; it is what ``ltx_core``'s own fixed (frame-count-independent)
   discard widths produce, and the divide step is what absorbs it
   correctly. Spatial-only tiling does NOT exhibit this (its splitter has
   no "+1"-style extension) -- verified: weight sums to exactly 1 at every
   output position for a pure spatial split.

**Deviation from whole-clip encode, BY DESIGN** (not a bug to "fix"): the
discard widths above are a fixed, receptive-field-INDEPENDENT heuristic (1-2
latents), not a computed exact receptive-field bound. For a deep encoder
(multiple stacked resnet blocks before each downsample), a *retained* latent
near a tile boundary can still have part of its true receptive field pulled
from the padded region, so tiled and whole-clip encodes are NOT guaranteed
bit-identical near seams -- only in the tile INTERIOR (several latents away
from any boundary) is agreement expected to be tight. ``ltx_core`` accepts
this deviation as the cost of tiling at all; this port mirrors that choice
rather than widening the discard to chase exact parity (which ``ltx_core``
itself does not do either). ``tiled_encode(vae, video, tiling_config=None)``
is the one case that IS bit-exact with :meth:`LTXCausalVideoVAE.encode`: a
``None`` config produces a single default (whole-axis, zero-ramp) tile per
axis, i.e. no splitting at all -- mask is all-ones everywhere, weights are
all-ones, so the divide is a no-op and the "tiled" path degenerates to
exactly one call to :meth:`encode`.

**Scope: ENCODE only.** Decode doesn't need tiling here -- ``Decoder.forward``
already self-chunks by a fixed memory budget (see ``ltx_causal_video.py``'s
``_MAX_CHUNK_BYTES`` machinery), which is a materially different problem
(bounding one big call's peak activations) from what this module solves
(the encoder has no such internal chunking at all).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

import torch

if TYPE_CHECKING:
    from .ltx_causal_video import LTXCausalVideoVAE

logger = logging.getLogger(__name__)

# LTX video<->latent scale factors for the STANDARD deployed architecture
# (patch_size=4 spatial patchify + 3 further 2x-per-axis downsample stages =
# 32x spatial; 1 temporal-only + 2 combined downsample stages = 8x temporal --
# verified against both real checkpoints' latent shapes in
# test_ltx_causal_video.py's TestRealLtx2VideoVae/TestRealLtx23VideoVae).
# ``ltx_core`` hardcodes these identically as ``VIDEO_SCALE_FACTORS`` rather
# than deriving them from a config's block list -- mirrored here for the same
# reason: every real checkpoint uses this exact compression ratio regardless
# of which specific block types realize it, only ARTIFICIALLY tiny test
# configs (e.g. this repo's ``_TINY_CONFIG``) use a smaller ratio, and those
# are not what tiling exists for.
_SCALE_T = 8
_SCALE_H = 32
_SCALE_W = 32

# ltx-core enforces these floors inside `prepare_tiles_for_encoding` regardless
# of what a caller's tiling config requests -- discarding fewer latents than
# this risks keeping padding-corrupted values (see module docstring point 4/5).
_MIN_SPATIAL_OVERLAP_PX = 64
_MIN_TEMPORAL_OVERLAP_FRAMES = 16


@dataclass(frozen=True)
class SpatialTilingConfig:
    """Mirrors ``ltx_core.model.video_vae.tiling.SpatialTilingConfig`` exactly
    (same fields, same validation)."""

    tile_size_in_pixels: int
    tile_overlap_in_pixels: int = 0

    def __post_init__(self) -> None:
        if self.tile_size_in_pixels < 64:
            raise ValueError(f"tile_size_in_pixels must be at least 64, got {self.tile_size_in_pixels}")
        if self.tile_size_in_pixels % 32 != 0:
            raise ValueError(f"tile_size_in_pixels must be divisible by 32, got {self.tile_size_in_pixels}")
        if self.tile_overlap_in_pixels % 32 != 0:
            raise ValueError(f"tile_overlap_in_pixels must be divisible by 32, got {self.tile_overlap_in_pixels}")
        if self.tile_overlap_in_pixels >= self.tile_size_in_pixels:
            raise ValueError(
                f"Overlap must be less than tile size, got {self.tile_overlap_in_pixels} and {self.tile_size_in_pixels}"
            )


@dataclass(frozen=True)
class TemporalTilingConfig:
    """Mirrors ``ltx_core.model.video_vae.tiling.TemporalTilingConfig`` exactly."""

    tile_size_in_frames: int
    tile_overlap_in_frames: int = 0

    def __post_init__(self) -> None:
        if self.tile_size_in_frames < 16:
            raise ValueError(f"tile_size_in_frames must be at least 16, got {self.tile_size_in_frames}")
        if self.tile_size_in_frames % 8 != 0:
            raise ValueError(f"tile_size_in_frames must be divisible by 8, got {self.tile_size_in_frames}")
        if self.tile_overlap_in_frames % 8 != 0:
            raise ValueError(f"tile_overlap_in_frames must be divisible by 8, got {self.tile_overlap_in_frames}")
        if self.tile_overlap_in_frames >= self.tile_size_in_frames:
            raise ValueError(
                f"Overlap must be less than tile size, got {self.tile_overlap_in_frames} and {self.tile_size_in_frames}"
            )


@dataclass(frozen=True)
class LtxTilingConfig:
    """Mirrors ``ltx_core.model.video_vae.tiling.TilingConfig`` -- spatial and
    temporal tiling are independently optional; ``None`` for either means
    "don't tile that axis". A fully-``None`` config (both fields ``None``)
    makes :func:`tiled_encode` behave as exactly one whole-clip tile."""

    spatial: SpatialTilingConfig | None = None
    temporal: TemporalTilingConfig | None = None

    @classmethod
    def default(cls) -> "LtxTilingConfig":
        """Same defaults as ``ltx_core``'s ``TilingConfig.default()``."""
        return cls(
            spatial=SpatialTilingConfig(tile_size_in_pixels=512, tile_overlap_in_pixels=64),
            temporal=TemporalTilingConfig(tile_size_in_frames=64, tile_overlap_in_frames=24),
        )


class _Interval(NamedTuple):
    """One tile's extent along a single axis, in INPUT (pixel/frame) space.
    ``left_ramp``/``right_ramp`` are the overlap widths shared with the
    previous/next tile (0 at the clip's own outer edge) -- mirrors
    ``ltx_core.tiling.DimensionInterval``."""

    start: int
    end: int
    left_ramp: int
    right_ramp: int


def _split_by_size(dimension_size: int, size: int, overlap: int) -> list[_Interval]:
    """Port of ``ltx_core.tiling.split_by_size(size, overlap)(dimension_size)``:
    a generic overlapping split of one axis into tiles of ``size`` elements
    sharing ``overlap`` elements with each neighbor (the last tile may be
    shorter)."""
    if dimension_size <= size:
        return [_Interval(0, dimension_size, 0, 0)]
    amount = (dimension_size + size - 2 * overlap - 1) // (size - overlap)
    intervals = [_Interval(0, size, 0, overlap)]
    for i in range(1, amount - 1):
        start = i * (size - overlap)
        intervals.append(_Interval(start, start + size, overlap, overlap))
    start_last = (amount - 1) * (size - overlap)
    intervals.append(_Interval(start_last, dimension_size, overlap, 0))
    return intervals


def _split_temporal_for_encode(dimension_size: int, tile_size_frames: int, overlap_frames: int) -> list[_Interval]:
    """Port of ``ltx_core.tiling.split_temporal`` -- the ENCODE-side temporal
    splitter (distinct from ``split_temporal_causal``, which is DECODE-side
    and out of scope here). Grows every non-last interval's ``end`` by 1
    frame and forces every interval's ``right_ramp`` to 0 -- see module
    docstring point 5 for why (causal convs only corrupt the past/left edge;
    the "+1" keeps each tile's own frame count on the ``1 + 8*k`` lattice
    ``encode()`` requires)."""
    if dimension_size <= tile_size_frames:
        return [_Interval(0, dimension_size, 0, 0)]
    base = _split_by_size(dimension_size, tile_size_frames, overlap_frames)
    modified = [_Interval(iv.start, iv.end + 1, iv.left_ramp, 0) for iv in base[:-1]]
    modified.append(_Interval(base[-1].start, base[-1].end, base[-1].left_ramp, 0))
    return modified


def _compute_rectangular_mask_1d(length: int, left_ramp: int, right_ramp: int) -> torch.Tensor:
    """Port of ``ltx_core.tiling.compute_rectangular_mask_1d`` -- a HARD 0/1
    mask (not a blend): the first ``left_ramp`` and last ``right_ramp``
    elements are zeroed (the padding-corrupted latents), everything else is
    1."""
    if length <= 0:
        raise ValueError("Mask length must be positive.")
    mask = torch.ones(length)
    if left_ramp > 0:
        mask[:left_ramp] = 0
    if right_ramp > 0:
        mask[-right_ramp:] = 0
    return mask


def _map_spatial_interval_to_latent(iv: _Interval, scale: int) -> tuple[slice, torch.Tensor]:
    """Port of ``ltx_core.model.video_vae.video_vae.map_spatial_interval_to_latent``
    (see module docstring point 4)."""
    start = iv.start // scale
    stop = iv.end // scale
    left_ramp = max(0, iv.left_ramp // scale - 1)
    right_ramp = 0 if iv.right_ramp == 0 else 1
    mask = _compute_rectangular_mask_1d(stop - start, left_ramp, right_ramp)
    return slice(start, stop), mask


def _map_temporal_interval_to_latent(iv: _Interval, scale: int) -> tuple[slice, torch.Tensor]:
    """Port of ``ltx_core.model.video_vae.video_vae.map_temporal_interval_to_latent``
    (see module docstring point 5)."""
    start = iv.start // scale
    stop = (iv.end - 1) // scale + 1
    left_ramp = 0 if iv.left_ramp == 0 else 1 + (iv.left_ramp - 1) // scale
    right_ramp = iv.right_ramp // scale
    if right_ramp != 0:
        raise ValueError("For tiled encoding, temporal tiles are expected to have a right ramp equal to 0")
    mask = _compute_rectangular_mask_1d(stop - start, left_ramp, right_ramp)
    return slice(start, stop), mask


class _Tile(NamedTuple):
    in_coords: tuple[slice, slice, slice, slice, slice]
    out_coords: tuple[slice, slice, slice, slice, slice]
    blend_mask: torch.Tensor  # (1, 1, t, h, w), already the combined N-D mask


def _prepare_tiles_for_encoding(
    video_shape: torch.Size,
    tiling_config: LtxTilingConfig | None,
) -> list[_Tile]:
    """Port of ``ltx_core.model.video_vae.video_vae.prepare_tiles_for_encoding``:
    builds the (T, H, W) tile grid over a ``(B, 3, T, H, W)`` video tensor's
    shape. Batch/channel axes always pass through whole (no split, matching
    ``ltx_core``'s ``DEFAULT_SPLIT_OPERATION``/``DEFAULT_MAPPING_OPERATION``
    for those two axes)."""
    _b, _c, t, h, w = video_shape

    t_intervals = [_Interval(0, t, 0, 0)]
    h_intervals = [_Interval(0, h, 0, 0)]
    w_intervals = [_Interval(0, w, 0, 0)]

    if tiling_config is not None and tiling_config.spatial is not None:
        cfg = tiling_config.spatial
        overlap_px = cfg.tile_overlap_in_pixels
        if overlap_px < _MIN_SPATIAL_OVERLAP_PX:
            logger.warning(
                "Overlap pixels %d in spatial tiling is less than %d, setting to minimum required %d",
                overlap_px, _MIN_SPATIAL_OVERLAP_PX, _MIN_SPATIAL_OVERLAP_PX,
            )
            overlap_px = _MIN_SPATIAL_OVERLAP_PX
        h_intervals = _split_by_size(h, cfg.tile_size_in_pixels, overlap_px)
        w_intervals = _split_by_size(w, cfg.tile_size_in_pixels, overlap_px)

    if tiling_config is not None and tiling_config.temporal is not None:
        cfg = tiling_config.temporal
        overlap_frames = cfg.tile_overlap_in_frames
        if overlap_frames < _MIN_TEMPORAL_OVERLAP_FRAMES:
            logger.warning(
                "Overlap frames %d is less than %d, setting to minimum required %d",
                overlap_frames, _MIN_TEMPORAL_OVERLAP_FRAMES, _MIN_TEMPORAL_OVERLAP_FRAMES,
            )
            overlap_frames = _MIN_TEMPORAL_OVERLAP_FRAMES
        t_intervals = _split_temporal_for_encode(t, cfg.tile_size_in_frames, overlap_frames)

    tiles: list[_Tile] = []
    for t_iv in t_intervals:
        t_out, t_mask = _map_temporal_interval_to_latent(t_iv, _SCALE_T)
        for h_iv in h_intervals:
            h_out, h_mask = _map_spatial_interval_to_latent(h_iv, _SCALE_H)
            for w_iv in w_intervals:
                w_out, w_mask = _map_spatial_interval_to_latent(w_iv, _SCALE_W)
                blend = (
                    t_mask.view(-1, 1, 1) * h_mask.view(1, -1, 1) * w_mask.view(1, 1, -1)
                ).view(1, 1, t_mask.shape[0], h_mask.shape[0], w_mask.shape[0])
                tiles.append(_Tile(
                    in_coords=(
                        slice(None), slice(None),
                        slice(t_iv.start, t_iv.end), slice(h_iv.start, h_iv.end), slice(w_iv.start, w_iv.end),
                    ),
                    out_coords=(slice(None), slice(None), t_out, h_out, w_out),
                    blend_mask=blend,
                ))
    return tiles


@torch.no_grad()
def tiled_encode(
    vae: "LTXCausalVideoVAE",
    video: torch.Tensor,
    tiling_config: LtxTilingConfig | None = None,
) -> torch.Tensor:
    """Tiled twin of :meth:`LTXCausalVideoVAE.encode` -- see module docstring
    for the exact discard/concat algorithm and its documented deviation from
    whole-clip encode near tile seams.

    Args:
        vae: The (already-loaded) ``LTXCausalVideoVAE`` module.
        video: ``(B, 3, T, H, W)`` pixels in ``[-1, 1]``. ``T`` must be
            ``1 + 8*k`` (cropped with a warning otherwise, exactly like
            :meth:`encode`).
        tiling_config: ``None`` degenerates to a single whole-clip tile
            (bit-exact with :meth:`encode`, see module docstring).

    Returns:
        The normalized latent, on the VAE module's own device/dtype (each
        tile is moved there before its independent encode, mirroring
        ``ltx_core``'s own device-handling contract).
    """
    t = video.shape[2]
    if (t - 1) % _SCALE_T != 0:
        frames_to_crop = (t - 1) % _SCALE_T
        logger.warning(
            "Number of frames %d of input video is not (%d * k + 1), last %d frames will be cropped",
            t, _SCALE_T, frames_to_crop,
        )
        video = video[:, :, : t - frames_to_crop, ...]

    tiles = _prepare_tiles_for_encoding(video.shape, tiling_config)

    model_device = next(vae.parameters()).device
    model_dtype = next(vae.parameters()).dtype

    b = video.shape[0]
    out_t = (video.shape[2] - 1) // _SCALE_T + 1
    out_h = video.shape[3] // _SCALE_H
    out_w = video.shape[4] // _SCALE_W
    latent_buffer = torch.zeros((b, vae.latent_channels, out_t, out_h, out_w), device=model_device, dtype=model_dtype)
    weights_buffer = torch.zeros_like(latent_buffer)

    for tile in tiles:
        video_tile = video[tile.in_coords]
        if video_tile.device != model_device or video_tile.dtype != model_dtype:
            video_tile = video_tile.to(device=model_device, dtype=model_dtype)

        latent_tile = vae.encode(video_tile)
        mask = tile.blend_mask.to(device=model_device, dtype=model_dtype)

        latent_buffer[tile.out_coords] += latent_tile * mask
        weights_buffer[tile.out_coords] += mask

        del latent_tile, mask, video_tile

    weights_buffer = weights_buffer.clamp(min=1e-8)
    return latent_buffer / weights_buffer
