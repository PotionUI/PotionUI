"""Snap requested pixel dimensions / frame counts to a model's latent granularity.

A DiT patchifies the VAE latent (which is ``spatial_downscale`` smaller than the
image) into ``patch_size`` x ``patch_size`` tokens, so both width and height must
be a multiple of ``spatial_downscale * patch_size`` — otherwise the patchify
rearrange fails outright (Krea-2 crashes on a 1080px axis: 1080/8 = 135, not
divisible by patch 2). Video adds a temporal constraint: the causal VAE chunks
frames as ``1 + k*temporal_downscale``, so the requested frame count must land on
that lattice.

These helpers round to the NEAREST valid value (ties round down, so the very
common 1080 -> 1072 rather than 1088) with a floor of one patch / one chunk, and
the caller logs a warning when the request was changed.
"""

from __future__ import annotations

import math


def snap_to_multiple(value: int, multiple: int) -> int:
    """Round ``value`` to the nearest multiple of ``multiple`` (minimum one).

    Ties round down: ``snap_to_multiple(1080, 16) == 1072`` (not 1088).
    """
    if multiple <= 1:
        return max(1, int(value))
    k = max(1, math.ceil(value / multiple - 0.5))
    return k * multiple


def snap_resolution(width: int, height: int, spatial_downscale: int, patch_size: int) -> tuple[int, int]:
    """Snap ``(width, height)`` to the model's ``spatial_downscale * patch_size`` grid."""
    granularity = max(1, int(spatial_downscale) * int(patch_size))
    return snap_to_multiple(width, granularity), snap_to_multiple(height, granularity)


def snap_frame_count(frames: int, temporal_downscale: int) -> int:
    """Snap ``frames`` to the nearest ``1 + k*temporal_downscale`` (k >= 0).

    The causal VAE chunks time as one anchor frame plus ``temporal_downscale``
    per latent step, so a valid count is ``1 + k*temporal_downscale`` (81 for the
    Wan default: 1 + 4*20). ``k == 0`` (a single frame) is allowed.
    """
    td = max(1, int(temporal_downscale))
    if td <= 1:
        return max(1, int(frames))
    k = max(0, math.ceil((frames - 1) / td - 0.5))
    return 1 + k * td
