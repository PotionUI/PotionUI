# Derived from: https://github.com/lbouaraba/comfyui-krea2edit (Apache-2.0, _fit_encode_image)
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

FIT_CROP_TOL = 0.08
REF_STYLE_SNAP = 16


def _to_uint8(t: torch.Tensor) -> np.ndarray:
    t = t.clamp(0.0, 1.0)
    return (t[0].permute(1, 2, 0).numpy() * 255.0).round().astype(np.uint8)


def crop_region(ih: int, iw: int, height: int, width: int) -> tuple[int, int, int, int]:
    s = max(height / ih, width / iw)
    ch, cw = min(ih, round(height / s)), min(iw, round(width / s))
    return (ih - ch) // 2, (iw - cw) // 2, ch, cw


def crop_fill(t: torch.Tensor, height: int, width: int, mode: str = "bicubic", antialias: bool = True) -> torch.Tensor:
    ih, iw = t.shape[-2:]
    y0, x0, ch, cw = crop_region(ih, iw, height, width)
    t = t[..., y0:y0 + ch, x0:x0 + cw]
    kwargs = {"antialias": antialias} if mode in ("bicubic", "bilinear") else {}
    return F.interpolate(t, size=(height, width), mode=mode, **kwargs)


def fit_geometry(ih: int, iw: int, height: int, width: int, mode: str) -> tuple[int, int, bool]:
    if mode != "fit":
        return height, width, True
    sc = min(height / ih, width / iw)
    if ih * sc >= height * (1 - FIT_CROP_TOL) and iw * sc >= width * (1 - FIT_CROP_TOL):
        return height, width, True
    nh = min(max(16, int(ih * sc) // 16 * 16), max(16, height // 16 * 16))
    nw = min(max(16, int(iw * sc) // 16 * 16), max(16, width // 16 * 16))
    return nh, nw, False


def fit_source_image(image: Image.Image, width: int, height: int, mode: str = "fit") -> np.ndarray:
    arr = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    t = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
    ih, iw = t.shape[-2:]
    th, tw, filled = fit_geometry(ih, iw, height, width, mode)
    if filled:
        return _to_uint8(crop_fill(t, th, tw))
    t = F.interpolate(t, size=(th, tw), mode="bicubic", antialias=True)
    return _to_uint8(t)


def fit_ref_mask(
    mask: Image.Image,
    width: int,
    height: int,
    mode: str = "fit",
    source_size: tuple[int, int] | None = None,
) -> torch.Tensor:
    if source_size is not None and mask.size != tuple(source_size):
        mask = mask.convert("L").resize(tuple(source_size), Image.BILINEAR)
    arr = np.asarray(mask.convert("L"), dtype=np.float32) / 255.0
    t = torch.from_numpy(arr)[None, None]
    ih, iw = t.shape[-2:]
    th, tw, filled = fit_geometry(ih, iw, height, width, mode)
    if filled:
        return crop_fill(t, th, tw, mode="bilinear", antialias=False)
    return F.interpolate(t, size=(th, tw), mode="bilinear")


def area_fit_size(width: int, height: int, max_pixels: int, snap: int = REF_STYLE_SNAP) -> tuple[int, int]:
    scale = min(1.0, (max_pixels / (width * height)) ** 0.5) if max_pixels > 0 else 1.0
    return max(round(width * scale / snap) * snap, snap), max(round(height * scale / snap) * snap, snap)


def fit_source_area(image: Image.Image, max_pixels: int, snap: int = REF_STYLE_SNAP) -> np.ndarray:
    arr = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    t = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
    width, height = area_fit_size(t.shape[-1], t.shape[-2], max_pixels, snap)
    if (height, width) != tuple(t.shape[-2:]):
        t = F.interpolate(t, size=(height, width), mode="area")
    return _to_uint8(t)
