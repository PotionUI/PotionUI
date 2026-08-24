"""Cheap latent -> RGB previews for the workbench (no VAE decode).

Mirrors ComfyUI's ``Latent2RGBPreviewer`` (``latent_preview.py``): the running
``x0`` estimate is projected to RGB by a per-family ``C x 3`` linear map (plus a
bias), then scaled to a small uint8 image. It costs a single matmul at latent
resolution -- orders of magnitude cheaper than a real VAE decode -- so it can run
every few sampling steps to drive the live workbench preview the SDXL path already
emits via ``latents_to_rgb``.

The factor tables are vendored verbatim from ComfyUI's ``comfy/latent_formats.py``
for the families the native engine hosts:

  * ``FLUX``  -- Flux1 / Z-Image (16ch flux latent).
  * ``FLUX2`` -- Flux2 / Klein (128ch sampling latent, pixel-unshuffled to 32ch
    by :func:`_flux2_reshape` before the projection, exactly like ComfyUI's
    ``latent_rgb_factors_reshape``).
  * ``WAN21`` -- Krea-2 / Qwen-Image / Anima / Wan-2.1 (16ch wan21 latent).
  * ``WAN22`` -- Wan-2.2 TI2V-5B (48ch).
  * ``LTXV``  -- LTX-2 / LTXV video (128ch).

:func:`resolve_preview_factors` keys a :class:`~..detect.registry.ModelSpec` to
its table the same way the engine keys latent formats (``latent_format`` dict);
unknown families return ``None`` so previews are simply skipped. Wire it into a
sampler run with :func:`make_preview_hook`, which returns the generic
:class:`~.hooks.PreviewHook` bound to a spec-derived decode function.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np
import torch
from PIL import Image

from .hooks import PreviewHook

logger = logging.getLogger(__name__)

# Cadence + size defaults. ``EVERY_N`` matches SDXL's every-5-steps preview
# (sdxl_model.py); ``MAX_SIZE`` caps the (cheap, latent-resolution) preview's
# long edge so a tiny 64-135px latent is upscaled to something visible and a
# large one is not shipped full-size over the websocket.
PREVIEW_EVERY_N = 5
PREVIEW_MAX_SIZE = 512


def _flux2_reshape(t: torch.Tensor) -> torch.Tensor:
    """Un-shuffle Flux2's 128ch sampling latent to the 32ch RGB-factor space.

    Vendored from ComfyUI's ``Flux2.latent_rgb_factors_reshape``: the flux2 VAE
    folds a 2x2 spatial block into channels (32 -> 128), so the preview reverses
    it before the 32-channel projection.
    """
    return (
        t.reshape(t.shape[0], 32, 2, 2, t.shape[-2], t.shape[-1])
        .permute(0, 1, 4, 2, 5, 3)
        .reshape(t.shape[0], 32, t.shape[-2] * 2, t.shape[-1] * 2)
    )


@dataclass(frozen=True)
class PreviewFactors:
    """A vendored ComfyUI latent->RGB projection for one latent family."""

    name: str
    factors: list[list[float]]           # C x 3
    bias: Optional[list[float]] = None   # 3
    reshape: Optional[Callable[[torch.Tensor], torch.Tensor]] = None


# --- Vendored factor tables (comfy/latent_formats.py) ----------------------

FLUX = PreviewFactors(
    name="flux",
    factors=[
        [-0.0346, 0.0244, 0.0681], [0.0034, 0.0210, 0.0687], [0.0275, -0.0668, -0.0433],
        [-0.0174, 0.0160, 0.0617], [0.0859, 0.0721, 0.0329], [0.0004, 0.0383, 0.0115],
        [0.0405, 0.0861, 0.0915], [-0.0236, -0.0185, -0.0259], [-0.0245, 0.0250, 0.1180],
        [0.1008, 0.0755, -0.0421], [-0.0515, 0.0201, 0.0011], [0.0428, -0.0012, -0.0036],
        [0.0817, 0.0765, 0.0749], [-0.1264, -0.0522, -0.1103], [-0.0280, -0.0881, -0.0499],
        [-0.1262, -0.0982, -0.0778],
    ],
    bias=[-0.0329, -0.0718, -0.0851],
)

FLUX2 = PreviewFactors(
    name="flux2",
    factors=[
        [0.0058, 0.0113, 0.0073], [0.0495, 0.0443, 0.0836], [-0.0099, 0.0096, 0.0644],
        [0.2144, 0.3009, 0.3652], [0.0166, -0.0039, -0.0054], [0.0157, 0.0103, -0.0160],
        [-0.0398, 0.0902, -0.0235], [-0.0052, 0.0095, 0.0109], [-0.3527, -0.2712, -0.1666],
        [-0.0301, -0.0356, -0.0180], [-0.0107, 0.0078, 0.0013], [0.0746, 0.0090, -0.0941],
        [0.0156, 0.0169, 0.0070], [-0.0034, -0.0040, -0.0114], [0.0032, 0.0181, 0.0080],
        [-0.0939, -0.0008, 0.0186], [0.0018, 0.0043, 0.0104], [0.0284, 0.0056, -0.0127],
        [-0.0024, -0.0022, -0.0030], [0.1207, -0.0026, 0.0065], [0.0128, 0.0101, 0.0142],
        [0.0137, -0.0072, -0.0007], [0.0095, 0.0092, -0.0059], [0.0000, -0.0077, -0.0049],
        [-0.0465, -0.0204, -0.0312], [0.0095, 0.0012, -0.0066], [0.0290, -0.0034, 0.0025],
        [0.0220, 0.0169, -0.0048], [-0.0332, -0.0457, -0.0468], [-0.0085, 0.0389, 0.0609],
        [-0.0076, 0.0003, -0.0043], [-0.0111, -0.0460, -0.0614],
    ],
    bias=[-0.0329, -0.0718, -0.0851],
    reshape=_flux2_reshape,
)

WAN21 = PreviewFactors(
    name="wan21",
    factors=[
        [-0.1299, -0.1692, 0.2932], [0.0671, 0.0406, 0.0442], [0.3568, 0.2548, 0.1747],
        [0.0372, 0.2344, 0.1420], [0.0313, 0.0189, -0.0328], [0.0296, -0.0956, -0.0665],
        [-0.3477, -0.4059, -0.2925], [0.0166, 0.1902, 0.1975], [-0.0412, 0.0267, -0.1364],
        [-0.1293, 0.0740, 0.1636], [0.0680, 0.3019, 0.1128], [0.0032, 0.0581, 0.0639],
        [-0.1251, 0.0927, 0.1699], [0.0060, -0.0633, 0.0005], [0.3477, 0.2275, 0.2950],
        [0.1984, 0.0913, 0.1861],
    ],
    bias=[-0.1835, -0.0868, -0.3360],
)

WAN22 = PreviewFactors(
    name="wan22",
    factors=[
        [0.0119, 0.0103, 0.0046], [-0.1062, -0.0504, 0.0165], [0.0140, 0.0409, 0.0491],
        [-0.0813, -0.0677, 0.0607], [0.0656, 0.0851, 0.0808], [0.0264, 0.0463, 0.0912],
        [0.0295, 0.0326, 0.0590], [-0.0244, -0.0270, 0.0025], [0.0443, -0.0102, 0.0288],
        [-0.0465, -0.0090, -0.0205], [0.0359, 0.0236, 0.0082], [-0.0776, 0.0854, 0.1048],
        [0.0564, 0.0264, 0.0561], [0.0006, 0.0594, 0.0418], [-0.0319, -0.0542, -0.0637],
        [-0.0268, 0.0024, 0.0260], [0.0539, 0.0265, 0.0358], [-0.0359, -0.0312, -0.0287],
        [-0.0285, -0.1032, -0.1237], [0.1041, 0.0537, 0.0622], [-0.0086, -0.0374, -0.0051],
        [0.0390, 0.0670, 0.2863], [0.0069, 0.0144, 0.0082], [0.0006, -0.0167, 0.0079],
        [0.0313, -0.0574, -0.0232], [-0.1454, -0.0902, -0.0481], [0.0714, 0.0827, 0.0447],
        [-0.0304, -0.0574, -0.0196], [0.0401, 0.0384, 0.0204], [-0.0758, -0.0297, -0.0014],
        [0.0568, 0.1307, 0.1372], [-0.0055, -0.0310, -0.0380], [0.0239, -0.0305, 0.0325],
        [-0.0663, -0.0673, -0.0140], [-0.0416, -0.0047, -0.0023], [0.0166, 0.0112, -0.0093],
        [-0.0211, 0.0011, 0.0331], [0.1833, 0.1466, 0.2250], [-0.0368, 0.0370, 0.0295],
        [-0.3441, -0.3543, -0.2008], [-0.0479, -0.0489, -0.0420], [-0.0660, -0.0153, 0.0800],
        [-0.0101, 0.0068, 0.0156], [-0.0690, -0.0452, -0.0927], [-0.0145, 0.0041, 0.0015],
        [0.0421, 0.0451, 0.0373], [0.0504, -0.0483, -0.0356], [-0.0837, 0.0168, 0.0055],
    ],
    bias=[0.0317, -0.0878, -0.1388],
)

LTXV = PreviewFactors(
    name="ltxv",
    factors=[
        [0.0112, -0.0006, -0.0100], [0.0860, 0.0658, 0.0010], [-0.0126, -0.0076, -0.0041],
        [0.0094, -0.0022, 0.0026], [0.0038, 0.0128, 0.0092], [0.0210, -0.0053, 0.0034],
        [-0.0089, -0.0197, -0.0188], [-0.0132, -0.0105, 0.0020], [-0.0015, -0.0070, -0.0076],
        [-0.0017, 0.0005, -0.0034], [0.0136, 0.0047, -0.0020], [0.0103, 0.0077, 0.0139],
        [-0.0161, -0.0062, 0.0012], [0.0073, 0.0156, 0.0004], [0.0010, -0.0030, -0.0148],
        [0.0191, 0.0109, 0.0123], [0.0045, 0.0000, -0.0069], [-0.0005, 0.0033, 0.0078],
        [0.0339, 0.0334, 0.0375], [-0.0230, -0.0025, -0.0031], [0.0503, 0.0388, 0.0335],
        [-0.0041, -0.0011, 0.0016], [-0.1269, -0.1311, -0.2100], [0.0263, 0.0142, -0.0036],
        [-0.0049, 0.0088, 0.0078], [-0.0017, -0.0049, -0.0052], [-0.0021, 0.0024, 0.0094],
        [-0.0225, -0.0213, -0.0151], [-0.0158, -0.0106, -0.0065], [-0.0047, 0.0050, -0.0067],
        [0.0120, 0.0207, 0.0162], [-0.0064, -0.0085, -0.0095], [0.0073, -0.0099, -0.0230],
        [-0.0009, 0.0063, 0.0096], [-0.0372, -0.0371, -0.0567], [-0.1337, -0.1072, -0.0538],
        [-0.0054, 0.0081, 0.0088], [-0.1525, -0.2144, -0.2184], [0.0314, 0.0070, -0.0098],
        [0.0022, -0.0090, -0.0210], [0.0038, -0.0059, -0.0150], [-0.0043, -0.0129, -0.0160],
        [-0.0055, -0.0108, -0.0030], [-0.0065, 0.0031, -0.0102], [-0.0050, -0.0072, -0.0009],
        [-0.0086, -0.0024, 0.0011], [-0.0090, -0.0096, 0.0016], [0.0051, 0.0121, 0.0200],
        [0.0138, 0.0117, 0.0082], [-0.0105, -0.0116, -0.0041], [-0.0284, -0.0313, -0.0221],
        [0.0029, 0.0365, 0.0187], [-0.0167, -0.0167, -0.0045], [0.0488, 0.0401, 0.0087],
        [-0.0151, -0.0006, 0.0030], [-0.0176, -0.0081, 0.0131], [-0.0093, 0.0108, -0.0063],
        [0.0031, 0.0005, 0.0123], [-0.0228, -0.0230, -0.0260], [-0.0248, -0.0154, -0.0221],
        [-0.0236, 0.0011, 0.0124], [-0.0079, -0.0012, -0.0061], [-0.0115, -0.0013, 0.0063],
        [-0.0542, 0.0266, 0.0063], [0.0044, -0.0073, -0.0105], [-0.0045, 0.0016, 0.0144],
        [0.0137, 0.0089, 0.0041], [-0.0101, 0.0090, 0.0157], [-0.0056, 0.0012, 0.0081],
        [-0.0037, -0.0054, 0.0013], [0.0295, 0.0214, 0.0304], [-0.0349, -0.0243, -0.0253],
        [-0.0341, -0.0224, -0.0106], [-0.0173, -0.0132, -0.0107], [-0.0021, -0.0086, -0.0030],
        [0.0012, -0.0042, -0.0069], [0.0009, -0.0067, -0.0001], [0.0160, -0.0101, -0.0289],
        [0.0012, 0.0102, 0.0189], [0.0173, 0.0003, 0.0138], [-0.0135, -0.0036, 0.0007],
        [0.0047, -0.0052, 0.0024], [-0.0059, -0.0062, -0.0018], [0.0155, 0.0146, 0.0020],
        [0.0075, 0.0016, -0.0082], [0.0191, 0.0016, -0.0040], [-0.0057, -0.0027, -0.0041],
        [0.0017, 0.0146, 0.0258], [-0.0008, 0.0023, 0.0045], [0.0116, 0.0089, -0.0073],
        [0.0076, 0.0027, 0.0114], [0.0052, 0.0037, 0.0140], [-0.0184, -0.0225, -0.0245],
        [0.0006, -0.0058, -0.0148], [-0.0161, -0.0086, -0.0145], [0.0205, 0.0207, 0.0064],
        [0.0034, -0.0112, -0.0164], [-0.0015, -0.0105, 0.0017], [0.0281, 0.0235, 0.0328],
        [-0.0185, -0.0128, -0.0088], [-0.0081, -0.0108, -0.0175], [-0.0039, 0.0162, 0.0334],
        [-0.0075, -0.0142, -0.0062], [0.0035, -0.0114, -0.0106], [0.0115, 0.0039, 0.0028],
        [0.0072, -0.0015, -0.0038], [0.0022, -0.0088, -0.0096], [0.0241, 0.0217, 0.0281],
        [-0.0054, -0.0243, -0.0178], [0.0074, 0.0105, 0.0127], [0.0063, 0.0063, 0.0192],
        [0.0164, 0.0095, 0.0067], [0.0172, 0.0236, 0.0233], [-0.0146, -0.0098, -0.0116],
        [0.0144, 0.0144, 0.0066], [-0.0068, 0.0189, 0.0146], [0.0061, 0.0035, -0.0027],
        [-0.0027, -0.0059, -0.0092], [0.0102, 0.0074, -0.0076], [-0.0133, 0.0193, -0.0009],
        [0.0024, -0.0048, -0.0158], [0.0262, 0.0260, 0.0202], [0.0157, 0.0185, 0.0027],
        [-0.0022, 0.0047, -0.0224], [-0.0075, 0.0074, 0.0144], [-0.0084, -0.0080, 0.0098],
        [0.0383, 0.0097, -0.0193], [-0.0146, -0.0067, 0.0040],
    ],
    bias=[-0.0571, -0.1657, -0.2512],
)


# MiniMax-H3's video VAE (24ch) has no published trained latent->RGB factor
# table (unlike the tables above, all vendored from ComfyUI's own
# ``latent_formats.py``): the family is new enough that neither ComfyUI nor
# any other tool ships one yet, and the real per-channel ``latents_mean``/
# ``latents_std`` needed to fit one aren't available either (see
# ``ai/minimax_h3/h3_architecture_dossier.md`` -- the VAE config that would
# carry them was never pasted into the dossier). Rather than skip previews for
# this family entirely, this table is a STRUCTURAL placeholder: a smooth
# 3-phase cosine spread across the 24 channels (each channel's contribution to
# R/G/B is 120 degrees out of phase with its neighbours), scaled to roughly the
# same magnitude as the trained tables above (max |0.05| vs. their typical
# 0.02-0.35 range) and a zero bias (no assumed scene-tone offset, since there
# is no data to justify one). It gives a colour-varying, deterministic preview
# that reacts to the running latent instead of a flat wash of one colour, at
# the acknowledged cost of not having trained colour fidelity.
MINIMAX_H3 = PreviewFactors(
    name="minimax_h3",
    factors=[
        [0.05, -0.025, -0.025], [0.0483, -0.0129, -0.0354], [0.0433, 0.0, -0.0433],
        [0.0354, 0.0129, -0.0483], [0.025, 0.025, -0.05], [0.0129, 0.0354, -0.0483],
        [0.0, 0.0433, -0.0433], [-0.0129, 0.0483, -0.0354], [-0.025, 0.05, -0.025],
        [-0.0354, 0.0483, -0.0129], [-0.0433, 0.0433, 0.0], [-0.0483, 0.0354, 0.0129],
        [-0.05, 0.025, 0.025], [-0.0483, 0.0129, 0.0354], [-0.0433, 0.0, 0.0433],
        [-0.0354, -0.0129, 0.0483], [-0.025, -0.025, 0.05], [-0.0129, -0.0354, 0.0483],
        [0.0, -0.0433, 0.0433], [0.0129, -0.0483, 0.0354], [0.025, -0.05, 0.025],
        [0.0354, -0.0483, 0.0129], [0.0433, -0.0433, 0.0], [0.0483, -0.0354, -0.0129],
    ],
    bias=[0.0, 0.0, 0.0],
)


def resolve_preview_factors(spec: Any) -> Optional[PreviewFactors]:
    """Map a ``ModelSpec`` to its vendored preview factors, or ``None``.

    Keys off the spec's ``latent_format`` dict the way the engine's decode paths
    do -- the ``"format"`` string when present, else the latent channel count
    (Krea-2's wan21 latent carries no ``"format"`` key, so its per-channel
    ``latents_mean`` is the tell). An unrecognised family returns ``None`` and
    previews are silently skipped for it.
    """
    lf = getattr(spec, "latent_format", None) or {}
    fmt = lf.get("format")
    ch = lf.get("latent_channels")
    if fmt == "minimax_h3" or ch == 24:
        return MINIMAX_H3
    if fmt == "wan22" or ch == 48:
        return WAN22
    if fmt in ("ltxav", "ltxv") or ch == 128:
        return LTXV
    if fmt == "wan21" or (ch == 16 and "latents_mean" in lf):
        return WAN21
    if ch == 32:
        return FLUX2
    if ch == 16:
        return FLUX
    return None


def latent_to_rgb(x0: torch.Tensor, factors: PreviewFactors) -> np.ndarray:
    """Project one latent to a ``(H, W, 3)`` uint8 RGB array (ComfyUI's math).

    Collapses batch (and, for 5D video/causal latents, the temporal axis) to the
    first frame, applies the ``C x 3`` linear map + bias, then the ``(x+1)/2``
    scale ComfyUI's ``preview_to_image`` uses. Runs on CPU float32 -- a single
    matmul at latent resolution.
    """
    t = x0.detach().to(dtype=torch.float32, device="cpu")
    if factors.reshape is not None:
        t = factors.reshape(t)
    if t.ndim == 5:            # (B, C, T, H, W) -> first frame of first item
        t = t[0, :, 0]
    elif t.ndim == 4:          # (B, C, H, W)
        t = t[0]
    elif t.ndim != 3:          # (C, H, W) is the terminal shape
        raise ValueError(f"latent_to_rgb expects a 3/4/5D latent, got {t.ndim}D")

    weight = torch.tensor(factors.factors, dtype=t.dtype).transpose(0, 1)  # (3, C)
    bias = torch.tensor(factors.bias, dtype=t.dtype) if factors.bias is not None else None
    rgb = torch.nn.functional.linear(t.movedim(0, -1), weight, bias)       # (H, W, 3)
    rgb = ((rgb + 1.0) / 2.0).clamp(0.0, 1.0).mul(255.0).round().to(torch.uint8)
    return rgb.numpy()


def latent_to_preview_image(
    x0: torch.Tensor, factors: PreviewFactors, *, max_size: int = PREVIEW_MAX_SIZE
) -> Image.Image:
    """:func:`latent_to_rgb` scaled so its long edge is ``max_size`` (bilinear)."""
    arr = latent_to_rgb(x0, factors)
    img = Image.fromarray(arr, mode="RGB")
    long_edge = max(img.width, img.height)
    if long_edge > 0 and long_edge != max_size:
        scale = max_size / long_edge
        new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(new_size, Image.BILINEAR)
    return img


def make_preview_hook(
    spec: Any,
    emit: Callable[[Image.Image], None],
    *,
    every_n: int = PREVIEW_EVERY_N,
    max_size: int = PREVIEW_MAX_SIZE,
    priority: int = 50,
    latent_transform: Optional[Callable[[torch.Tensor], torch.Tensor]] = None,
) -> Optional[PreviewHook]:
    """Build a :class:`~.hooks.PreviewHook` for ``spec`` that emits PIL previews.

    Returns ``None`` when the family has no vendored factors (``spec`` unknown or
    latent-only-video without a table) so callers can unconditionally
    ``if hook: hooks.append(hook)``. Decode and emit are both guarded: a preview
    that raises is logged at debug and dropped, never surfacing to the sampler
    (which already isolates hook failures, but the extra guard keeps a preview
    error inert even off the sampler's ``run_hooks`` path).

    ``latent_transform`` (optional) maps the sampler's x0 estimate to the
    family's native 5D/4D latent grid before RGB projection — for samplers
    whose working state is packed tokens (e.g. the conditioned LTX path, which
    carries ``[B, S, C]`` tokens plus appended conditioning/audio slices).
    """
    factors = resolve_preview_factors(spec)
    if factors is None:
        logger.debug(
            "no preview factors for spec %s; step previews disabled",
            getattr(spec, "variant", spec),
        )
        return None

    def decode_fn(x0: torch.Tensor) -> Optional[Image.Image]:
        try:
            if latent_transform is not None:
                x0 = latent_transform(x0)
            return latent_to_preview_image(x0, factors, max_size=max_size)
        except Exception:  # noqa: BLE001 - a preview must never break generation
            logger.debug("latent preview decode failed; skipping frame", exc_info=True)
            return None

    def callback(image: Optional[Image.Image], step_index: int) -> None:
        if image is None:
            return
        try:
            emit(image)
        except Exception:  # noqa: BLE001 - as above
            logger.debug("latent preview emit failed; skipping frame", exc_info=True)

    return PreviewHook(decode_fn, every_n, callback, priority=priority)
