"""Shrink-on-OOM spatially-tiled causal-3D VAE encode for the Wan i2v concat
build, shared by every pipe that VAE-encodes a start/end frame into
``build_i2v_concat``'s conditioning.

The untiled encode OOMs at 480p+ on a busy card; on OOM this retries with
``tiled_encode_causal3d`` (spatial tiling only -- temporal chunking is
unchanged), halving the tile size until it fits or reaches
``ENCODE_TILE_FLOOR`` (mirrors ComfyUI's shrink-on-OOM VAE encode). First
landed in ``generator/img2vid_wan22``; extracted here so
``generator/chain_video_wan22``'s per-segment concat encode gets the same
OOM safety net instead of a bare ``vae.module.encode`` call.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import torch

from src.platform.runtime.native.vae.tiling import tiled_encode_causal3d

logger = logging.getLogger(__name__)

# Smallest spatial tile (pixels) the OOM-fallback encode will shrink to before
# giving up -- below this the tiling overhead dominates and a genuine OOM should
# surface rather than be masked.
ENCODE_TILE_FLOOR = 128


def make_wan_vae_encode(vae_module: Any, width: int, height: int, *, log_prefix: str = "GENERATOR WAN") -> Callable[[torch.Tensor], torch.Tensor]:
    """Wrap a Wan causal-3D VAE ``encode`` with a spatial-tiling OOM fallback.

    Returns a ``(1,3,T,H,W) -> (1,16,T_lat,H/8,W/8)`` callable that first
    tries the untiled encode and, only if it OOMs, retries with
    ``tiled_encode_causal3d``, halving the tile size until it fits or reaches
    ``ENCODE_TILE_FLOOR``. The untiled path is unchanged for resolutions that
    already fit (e.g. 256p), so this only adds cost when the whole-frame
    encode would otherwise OOM (480p+).
    """
    def _snap8(v: int) -> int:
        return max(ENCODE_TILE_FLOOR, (v // 8) * 8)

    def encode(pixels: torch.Tensor) -> torch.Tensor:
        try:
            return vae_module.encode(pixels)
        except torch.cuda.OutOfMemoryError:
            logger.warning(
                "[%s] VAE encode OOM at %dx%d; retrying spatially tiled",
                log_prefix, width, height,
            )

        # First tiled attempt splits the long axis into ~2; shrink from there.
        tile = _snap8(max(width, height) // 2)
        while True:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            try:
                return tiled_encode_causal3d(vae_module, pixels, tile_size=tile)
            except torch.cuda.OutOfMemoryError:
                if tile <= ENCODE_TILE_FLOOR:
                    raise
                tile = _snap8(tile // 2)
                logger.warning("[%s] tiled VAE encode still OOM; shrinking tile to %dpx", log_prefix, tile)

    return encode
