"""Pure paste-back compositing for the LTX video detailer.

Once a tube has been refined, its frames must be laid back into the full clip
without a visible seam or a flicker at the tube's edges in space OR time. Two
feathers do that:

  * a SPATIAL cosine mask (a separable Tukey window) that fades the refined
    patch out over a border band (~8% of the tube size) so its rectangle edge
    dissolves into the untouched surroundings;
  * a TEMPORAL ramp that fades the refinement in over the first few frames of
    the tube segment and out over the last few, so a tube that starts/ends mid
    clip does not pop.

The composite is ``frame = frame*(1 - w) + refined*w`` where
``w = spatial_mask * temporal_weight`` -- a partial, edge-soft, time-soft
replacement. Written to operate IN PLACE on one uint8 frame buffer (converting
only the small per-frame crop region to float), so a long clip never needs a
second full-resolution copy.

numpy-only and pure -- mask/ramp shapes and the blend are fully unit-testable.
"""

from __future__ import annotations

from typing import List, Sequence

import numpy as np

from src.pipelines.pipes.detailer.video_ltx.windowing import TubeWindow


def tukey_window_1d(n: int, border: int) -> np.ndarray:
    """1-D Tukey (cosine-tapered) window of length ``n``: rises 0->1 over the
    first ``border`` samples, holds 1, falls 1->0 over the last ``border``. A
    non-positive ``border`` (or a window too short to hold two tapers) degrades
    gracefully to all-ones / a single centered taper."""
    if n <= 0:
        return np.ones(0, dtype=np.float32)
    w = np.ones(n, dtype=np.float32)
    b = int(border)
    if b <= 0:
        return w
    b = min(b, (n + 1) // 2)  # never overlap the two tapers past the midpoint
    # cosine ramp 0..1 across b samples (endpoints hit exactly 0 and ~1)
    ramp = 0.5 * (1.0 - np.cos(np.pi * (np.arange(1, b + 1) / (b + 1))))
    w[:b] = ramp
    w[n - b:] = ramp[::-1]
    return w


def feather_mask(width: int, height: int, border_frac: float = 0.08) -> np.ndarray:
    """Separable cosine feather mask, shape ``(height, width)`` in ``[0, 1]``:
    ~1 in the interior, tapering to 0 at the border over ``border_frac`` of the
    SHORTER side. The outer product of two Tukey windows -- corners taper on
    both axes, which is exactly the soft rounded falloff wanted at a patch's
    edge."""
    border = max(1, int(round(border_frac * min(width, height))))
    wx = tukey_window_1d(width, border)
    wy = tukey_window_1d(height, border)
    return np.outer(wy, wx).astype(np.float32)


def temporal_ramp(n_frames: int, ramp_frames: int = 4) -> np.ndarray:
    """Per-frame temporal weight, shape ``(n_frames,)`` in ``[0, 1]``: fades in
    over the first ``ramp_frames`` and out over the last ``ramp_frames`` (the
    same Tukey taper as the spatial mask, one dimension)."""
    return tukey_window_1d(n_frames, ramp_frames)


def composite_tube(
    frames: np.ndarray,
    refined: Sequence[np.ndarray],
    window: TubeWindow,
    *,
    border_frac: float = 0.08,
    ramp_frames: int = 4,
) -> np.ndarray:
    """Blend a refined tube back into ``frames`` (``(T, H, W, 3)`` uint8) IN
    PLACE, returning the same buffer.

    ``refined`` is one uint8 ``(window.height, window.width, 3)`` patch per tube
    frame (already colour-matched + resized to the window size by the caller),
    ordered from ``window.start_frame``. Each patch is composited at that
    frame's ``window.box_at(frame)`` under ``spatial_mask * temporal_weight[i]``
    -- so a tube edge (space) and a tube start/end (time) both dissolve softly
    into the untouched clip."""
    spatial = feather_mask(window.width, window.height, border_frac)  # (h, w)
    temporal = temporal_ramp(len(refined), ramp_frames)               # (n,)

    for i, patch in enumerate(refined):
        frame_idx = window.start_frame + i
        x0, y0, x1, y1 = window.box_at(frame_idx)
        w = (spatial * float(temporal[i]))[..., None]  # (h, w, 1)
        region = frames[frame_idx, y0:y1, x0:x1].astype(np.float32)
        blended = region * (1.0 - w) + patch.astype(np.float32) * w
        frames[frame_idx, y0:y1, x0:x1] = np.clip(blended, 0.0, 255.0).astype(np.uint8)
    return frames


def resize_patches_to_window(patches: Sequence[np.ndarray], window: TubeWindow) -> List[np.ndarray]:
    """Resize each refined ``(h, w, 3)`` uint8 patch back to the window's exact
    ``(height, width)`` -- the inverse of the working-resolution upscale done
    before the refine. Uses PIL (already a hard dependency of the
    detection/media stack) so no new import; a no-op when a patch is already
    the target size.

    LANCZOS, not bilinear: this is almost always a DOWNSCALE (the refine ran at
    a working resolution >= the crop's native size -- see
    ``snap_working_resolution``), throwing away exactly the fine detail the
    refine just spent a denoise pass adding back. This matches the sibling SDXL
    detailer's own paste-back convention (``_shared/detection/detailer_helper.py``'s
    ``downscale_region``/``paste_region``, both LANCZOS by default) -- video
    detailer bilinear was the odd one out. A CPU repro (identity-model resize
    round-trip, Laplacian-variance sharpness metric) measured LANCZOS
    retaining ~63% more post-refine high-frequency energy than bilinear at the
    same downscale factor."""
    from PIL import Image

    out: List[np.ndarray] = []
    for patch in patches:
        if patch.shape[0] == window.height and patch.shape[1] == window.width:
            out.append(patch)
            continue
        img = Image.fromarray(patch).resize((window.width, window.height), Image.LANCZOS)
        out.append(np.asarray(img))
    return out
