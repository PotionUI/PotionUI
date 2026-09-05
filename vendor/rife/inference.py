# Arbitrary-timestep inference wrapper for the RIFE 4.x IFNet (see ifnet.py for
# the provenance/licence note). MIT, Copyright (c) 2021 hzwer.
#
# Padding + scale_list follow Practical-RIFE's inference_video.py:
#   tmp = max(128, int(128 / scale)); pad each side up to a multiple of tmp;
#   inference(I0, I1, t, scale) with scale_list = [8/s, 4/s, 2/s, 1/s].
#
# Local modification (PotionUI): the per-frame work upstream repeats on every
# call -- the zero-padding and, on the encoder-equipped variants, the feature
# encode -- is split out of `interpolate` into `prepare_frame`/`PreparedFrame`,
# and `interpolate_prepared` consumes the result. A caller synthesising several
# timesteps between one pair, or streaming a clip where each frame is the right
# half of one pair and the left half of the next, prepares each source frame
# once. `interpolate` keeps upstream's signature and semantics by preparing both
# frames itself; there is no second copy of the padding/scale math.

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import torch
import torch.nn.functional as F

from .ifnet import IFNet


def pad_dims(h: int, w: int, flow_scale: float) -> Tuple[int, int]:
    tmp = max(128, int(128 / flow_scale))
    ph = ((h - 1) // tmp + 1) * tmp
    pw = ((w - 1) // tmp + 1) * tmp
    return ph, pw


def _scale_list(flow_scale: float) -> List[float]:
    return [8.0 / flow_scale, 4.0 / flow_scale, 2.0 / flow_scale, 1.0 / flow_scale]


@dataclass(frozen=True)
class PreparedFrame:
    """One source frame padded to the multiple RIFE needs, together with the
    feature-encoder output for the variants that have an encoder (``features`` is
    ``None`` on rife46, which has none). ``size`` is the unpadded ``(h, w)`` the
    result is cropped back to; ``flow_scale`` and ``model_id`` record the context
    the padding and features were computed under, so a caller that keeps a
    prepared frame across pairs can tell when it has gone stale."""

    padded: torch.Tensor
    features: Optional[torch.Tensor]
    size: Tuple[int, int]
    flow_scale: float
    model_id: int

    @property
    def device(self) -> torch.device:
        return self.padded.device

    @property
    def dtype(self) -> torch.dtype:
        return self.padded.dtype

    def matches(self, model: IFNet, img: torch.Tensor, flow_scale: float) -> bool:
        """A COMPATIBILITY check, not an identity one: whether this was prepared
        under ``model``, ``flow_scale`` and ``img``'s geometry, device and dtype,
        so the padding lattice and the encoder weights behind ``features`` are the
        ones ``img`` would get. It cannot establish that ``features`` describe
        ``img``'s pixels -- two different frames of one clip match each other.

        Reuse is therefore exact only when the caller separately guarantees the
        same-frame invariant: that the prepared frame was built from this very
        ``img``. A streaming caller gets that from its own structure (the pair's
        right frame is literally the next pair's left frame); a caller keyed on
        anything looser must not use this to decide reuse. ``model_id`` likewise
        pins the object, not its weights, so a caller mutating a model's
        parameters in place must invalidate its own prepared frames."""
        return (
            self.model_id == id(model)
            and self.flow_scale == float(flow_scale)
            and self.size == (int(img.shape[-2]), int(img.shape[-1]))
            and self.device == img.device
            and self.dtype == img.dtype
        )


def prepare_frame(model: IFNet, img: torch.Tensor, flow_scale: float = 1.0) -> PreparedFrame:
    """Pad ``img`` (``(B, 3, H, W)`` float in ``[0, 1]`` on the model's device) to
    RIFE's lattice and run the per-image feature encoder on it, if the variant has
    one. The result is valid for any timestep between this frame and any other
    frame prepared under the same model, geometry and ``flow_scale``."""
    h, w = int(img.shape[-2]), int(img.shape[-1])
    ph, pw = pad_dims(h, w, flow_scale)
    padded = F.pad(img, (0, pw - w, 0, ph - h))
    with torch.no_grad():
        features = model.encode(padded[:, :3]) if model.encode is not None else None
    return PreparedFrame(padded, features, (h, w), float(flow_scale), id(model))


def interpolate_prepared(
    model: IFNet,
    f0: PreparedFrame,
    f1: PreparedFrame,
    timestep: float,
    flow_scale: float = 1.0,
) -> torch.Tensor:
    """Synthesise the frame at ``timestep`` in ``(0, 1)`` between two frames
    already run through :func:`prepare_frame`, cropped back to their unpadded
    size. Flow, mask and refinement are per timestep and are recomputed here."""
    if f0.size != f1.size:
        raise ValueError(
            f"prepared frames disagree on geometry: {f0.size} vs {f1.size}"
        )
    if f0.flow_scale != float(flow_scale) or f1.flow_scale != float(flow_scale):
        raise ValueError(
            f"prepared frames were padded at flow_scale "
            f"{f0.flow_scale}/{f1.flow_scale}, asked to run at {float(flow_scale)}"
        )
    with torch.no_grad():
        _, _, merged = model(
            torch.cat((f0.padded, f1.padded), 1),
            timestep,
            _scale_list(flow_scale),
            f0=f0.features,
            f1=f1.features,
        )
    h, w = f0.size
    return merged[..., :h, :w]


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
    return interpolate_prepared(
        model,
        prepare_frame(model, img0, flow_scale),
        prepare_frame(model, img1, flow_scale),
        timestep,
        flow_scale,
    )
