"""Pixel-space tile planning + feathered blend for the tiled refiner.

Pure array math, no model/torch dependency, so the tile geometry and seam
weights are unit-testable in isolation (the refine callable is injected). The
blend mirrors ``src/platform/runtime/native/vae/tiling.py`` and the SDXL upscaler: run a
callable per overlapping tile, weight each tile with a linear edge ramp over the
overlap region, and normalize by the summed weight where tiles overlap — no
cross-tile attention, no visible seams.

Alignment contract: the caller snaps the full image and picks ``tile``/``overlap``
as multiples of the DiT pixel granularity, so every tile crop lands on an exact
latent boundary and needs no per-tile snapping. This module only does geometry
and blending; it does not know about granularity.
"""

from __future__ import annotations

from typing import Callable, List

import numpy as np


def plan_tile_positions(dim: int, tile: int, overlap: int) -> List[int]:
    """Tile origins covering ``[0, dim)`` with ``tile``-wide windows.

    Steps by ``tile - overlap``; the final window is clamped to ``dim - tile`` so
    it never runs past the edge (overlapping its neighbour a little more instead).
    Returns ``[0]`` when the image is no larger than a tile on this axis.
    """
    if dim <= tile:
        return [0]
    step = max(1, tile - overlap)
    origins = list(range(0, dim - tile + 1, step))
    if not origins or origins[-1] != dim - tile:
        origins.append(dim - tile)
    return origins


def feather_mask(
    height: int,
    width: int,
    overlap: int,
    *,
    top: bool,
    bottom: bool,
    left: bool,
    right: bool,
) -> np.ndarray:
    """A ``(H, W, 1)`` float32 blend mask ramping 0->1 over ``overlap`` pixels on
    each *interior* edge (a border edge stays at full weight).

    Two neighbouring tiles' opposing ramps sum across the overlap; the caller
    divides by the accumulated weight, so the ramps need only be complementary,
    not an exact partition of unity.
    """
    mask = np.ones((height, width), dtype=np.float32)
    f = min(overlap, height, width)
    for t in range(f):
        a = (t + 1) / f
        if top:
            mask[t, :] *= a
        if bottom:
            mask[height - 1 - t, :] *= a
        if left:
            mask[:, t] *= a
        if right:
            mask[:, width - 1 - t] *= a
    return mask[:, :, None]


def _sobel_edge_density(gray: np.ndarray) -> float:
    """Mean 3x3-Sobel gradient magnitude of an ``(H,W)`` [0,255] luminance array,
    normalized to ~[0,1]. Pure numpy (no cv2/scipy) so it stays dependency-light
    and testable; the kernel matches OpenCV's Sobel on the valid interior."""
    g = gray.astype(np.float64)
    if g.shape[0] < 3 or g.shape[1] < 3:
        return 0.0
    tl, tc, tr = g[:-2, :-2], g[:-2, 1:-1], g[:-2, 2:]
    ml, mr = g[1:-1, :-2], g[1:-1, 2:]
    bl, bc, br = g[2:, :-2], g[2:, 1:-1], g[2:, 2:]
    gx = (tr + 2 * mr + br) - (tl + 2 * ml + bl)
    gy = (bl + 2 * bc + br) - (tl + 2 * tc + tr)
    mag = np.sqrt(gx * gx + gy * gy)
    return float(mag.mean() / 255.0)


def tile_complexity(crop: np.ndarray) -> float:
    """Texture/detail score in ``[0, 1]`` for a uint8 HWC tile (0 = flat, 1 = busy).

    Combines edge density (Sobel) and luminance variance, matching the SDXL
    tiled_detailer metric. Flat regions (sky, mist, smooth rock) score near 0.
    """
    rgb = crop[:, :, :3].astype(np.float64)
    gray = rgb @ np.array([0.299, 0.587, 0.114])   # ITU-R 601 luma
    edge_density = _sobel_edge_density(gray)
    variance = float(gray.var() / (255.0 ** 2))
    return min(1.0, edge_density * 0.7 + variance * 0.3)


def tile_denoise(crop: np.ndarray, base_denoise: float, min_denoise: float) -> float:
    """Content-aware per-tile denoise strength.

    Interpolates between ``min_denoise`` (flat/low-texture tiles -> stay close to the
    upscaled original, so ambiguous regions can't grow phantom subjects) and
    ``base_denoise`` (busy tiles -> full refine for detail recovery) by
    :func:`tile_complexity`. This is the anti-hallucination guard for the whole-prompt
    tiled refine: without it, every tile is refined with the full prompt and low-texture
    regions sprout tiny duplicated subjects.
    """
    lo = min(min_denoise, base_denoise)
    return lo + tile_complexity(crop) * (base_denoise - lo)


def tiled_refine(
    image: np.ndarray,
    refine: Callable[[np.ndarray, int], np.ndarray],
    *,
    tile: int,
    overlap: int,
    on_tile: Callable[[int, int], None] | None = None,
) -> np.ndarray:
    """Refine ``image`` (uint8 HWC) tile-by-tile and blend the results.

    ``refine(crop_uint8_hwc, tile_index) -> refined_uint8_hwc`` processes ONE tile
    (same H/W in and out) — it is the only piece that touches the model, so exactly
    one tile is on the GPU at a time and peak VRAM is bounded by ``tile``, not by the
    output size. ``on_tile(done, total)`` is called after each tile for progress.

    A single tile that covers the whole image is the degenerate one-iteration case
    (no feather, weight is all ones) — no special-casing needed.
    """
    h, w = image.shape[:2]
    ys = plan_tile_positions(h, tile, overlap)
    xs = plan_tile_positions(w, tile, overlap)
    total = len(ys) * len(xs)

    canvas = np.zeros((h, w, 3), dtype=np.float32)
    weight = np.zeros((h, w, 1), dtype=np.float32)

    done = 0
    for y in ys:
        for x in xs:
            crop = image[y:y + tile, x:x + tile]
            refined = np.asarray(refine(crop, done)).astype(np.float32)
            th, tw = refined.shape[:2]
            mask = feather_mask(
                th, tw, overlap,
                top=y > 0, bottom=y + tile < h, left=x > 0, right=x + tile < w,
            )
            canvas[y:y + th, x:x + tw] += refined * mask
            weight[y:y + th, x:x + tw] += mask
            done += 1
            if on_tile is not None:
                on_tile(done, total)

    blended = canvas / np.clip(weight, 1e-6, None)
    return blended.round().clip(0, 255).astype(np.uint8)
