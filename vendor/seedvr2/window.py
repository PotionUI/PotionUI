# Vendored from ByteDance's SeedVR2 — https://github.com/ByteDance-Seed/SeedVR
# Upstream path: models/dit_v2 (window partitioning) @ unknown; vendored ~2025
# (moved into vendor/seedvr2/ from src/platform/runtime/native/arch/seedvr2/
# as part of the license-relocation workstream, BE-97).
# License: Apache-2.0 (see LICENSE).

"""Swin-style window partitioning for NaDiT attention (verbatim from SeedVR2).

Each layer attends within 3D windows whose count is fixed at ``(4, 3, 3)`` but
whose pixel size is derived per-frame by rescaling to a 720p reference area
(``45 * 80`` tokens) — so the effective receptive field is resolution-adaptive.
Even layers use aligned windows (``720pwin``); odd layers use half-shifted
windows (``720pswin``) so information crosses window boundaries, exactly like
Swin-Transformer's shifted-window scheme. Returns a list of ``(slice_t, slice_h,
slice_w)`` covering the volume.
"""

from __future__ import annotations

import math
from math import ceil
from typing import Callable, Tuple


def get_window_op(name: str) -> Callable:
    if name == "720pwin_by_size_bysize":
        return make_720p_windows_bysize
    if name == "720pswin_by_size_bysize":
        return make_shifted_720p_windows_bysize
    raise ValueError(f"Unknown windowing method: {name}")


def make_720p_windows_bysize(size: Tuple[int, int, int], num_windows: Tuple[int, int, int]):
    t, h, w = size
    resized_nt, resized_nh, resized_nw = num_windows
    scale = math.sqrt((45 * 80) / (h * w))
    resized_h, resized_w = round(h * scale), round(w * scale)
    wh, ww = ceil(resized_h / resized_nh), ceil(resized_w / resized_nw)
    wt = ceil(min(t, 30) / resized_nt)
    nt, nh, nw = ceil(t / wt), ceil(h / wh), ceil(w / ww)
    return [
        (
            slice(it * wt, min((it + 1) * wt, t)),
            slice(ih * wh, min((ih + 1) * wh, h)),
            slice(iw * ww, min((iw + 1) * ww, w)),
        )
        for iw in range(nw)
        if min((iw + 1) * ww, w) > iw * ww
        for ih in range(nh)
        if min((ih + 1) * wh, h) > ih * wh
        for it in range(nt)
        if min((it + 1) * wt, t) > it * wt
    ]


def make_shifted_720p_windows_bysize(size: Tuple[int, int, int], num_windows: Tuple[int, int, int]):
    t, h, w = size
    resized_nt, resized_nh, resized_nw = num_windows
    scale = math.sqrt((45 * 80) / (h * w))
    resized_h, resized_w = round(h * scale), round(w * scale)
    wh, ww = ceil(resized_h / resized_nh), ceil(resized_w / resized_nw)
    wt = ceil(min(t, 30) / resized_nt)

    st, sh, sw = (
        0.5 if wt < t else 0,
        0.5 if wh < h else 0,
        0.5 if ww < w else 0,
    )
    nt, nh, nw = ceil((t - st) / wt), ceil((h - sh) / wh), ceil((w - sw) / ww)
    nt, nh, nw = (
        nt + 1 if st > 0 else 1,
        nh + 1 if sh > 0 else 1,
        nw + 1 if sw > 0 else 1,
    )
    return [
        (
            slice(max(int((it - st) * wt), 0), min(int((it - st + 1) * wt), t)),
            slice(max(int((ih - sh) * wh), 0), min(int((ih - sh + 1) * wh), h)),
            slice(max(int((iw - sw) * ww), 0), min(int((iw - sw + 1) * ww), w)),
        )
        for iw in range(nw)
        if min(int((iw - sw + 1) * ww), w) > max(int((iw - sw) * ww), 0)
        for ih in range(nh)
        if min(int((ih - sh + 1) * wh), h) > max(int((ih - sh) * wh), 0)
        for it in range(nt)
        if min(int((it - st + 1) * wt), t) > max(int((it - st) * wt), 0)
    ]
