# Arbitrary-timestep inference wrapper for the RIFE 4.x IFNet (see ifnet.py for
# the provenance/licence note). MIT, Copyright (c) 2021 hzwer.
#
# Padding + scale_list follow Practical-RIFE's inference_video.py:
#   tmp = max(128, int(128 / scale)); pad each side up to a multiple of tmp;
#   inference(I0, I1, t, scale) with scale_list = [8/s, 4/s, 2/s, 1/s].

from __future__ import annotations

from typing import Tuple

import torch
import torch.nn.functional as F

from .ifnet import IFNet


def pad_dims(h: int, w: int, flow_scale: float) -> Tuple[int, int]:
    tmp = max(128, int(128 / flow_scale))
    ph = ((h - 1) // tmp + 1) * tmp
    pw = ((w - 1) // tmp + 1) * tmp
    return ph, pw


def interpolate(
    model: IFNet,
    img0: torch.Tensor,
    img1: torch.Tensor,
    timestep: float,
    flow_scale: float = 1.0,
) -> torch.Tensor:
    """Synthesise the frame at ``timestep`` in ``(0, 1)`` between ``img0`` and
    ``img1`` (both ``(B, 3, H, W)`` float in ``[0, 1]`` on the model's device).
    Inputs are reflect-free zero-padded to the multiple RIFE needs, run through
    the flow network, and the result is cropped back to ``(H, W)``. Lower
    ``flow_scale`` (e.g. 0.5) computes flow at a coarser scale for high-res
    (>2K) inputs."""
    h, w = int(img0.shape[-2]), int(img0.shape[-1])
    ph, pw = pad_dims(h, w, flow_scale)
    pad = (0, pw - w, 0, ph - h)
    i0 = F.pad(img0, pad)
    i1 = F.pad(img1, pad)
    scale_list = [8.0 / flow_scale, 4.0 / flow_scale, 2.0 / flow_scale, 1.0 / flow_scale]
    with torch.no_grad():
        _, _, merged = model(torch.cat((i0, i1), 1), timestep, scale_list)
    return merged[..., :h, :w]
