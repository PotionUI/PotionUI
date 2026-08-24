"""Pure paste-back compositing tests for the LTX video detailer.

Mask/ramp shapes and the in-place feathered blend -- the parts that keep a tube
from showing a hard edge in space or a pop in time.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.pipelines.pipes.detailer.video_ltx.compositing import (
    composite_tube,
    feather_mask,
    resize_patches_to_window,
    temporal_ramp,
    tukey_window_1d,
)
from src.pipelines.pipes.detailer.video_ltx.windowing import TubeWindow


# -- 1D window ------------------------------------------------------------


def test_tukey_window_zero_border_is_all_ones():
    assert np.array_equal(tukey_window_1d(5, 0), np.ones(5, np.float32))


def test_tukey_window_tapers_both_ends():
    w = tukey_window_1d(10, 3)
    assert w[0] < w[1] < w[2]          # rising taper
    assert w[-1] < w[-2] < w[-3]       # falling taper
    assert w[4] == pytest.approx(1.0)  # flat interior
    assert np.allclose(w, w[::-1])     # symmetric


def test_tukey_window_short_never_overflows():
    w = tukey_window_1d(3, 10)  # border longer than half the window
    assert w.shape == (3,)
    assert (w >= 0).all() and (w <= 1).all()


# -- spatial mask ---------------------------------------------------------


def test_feather_mask_shape_center_and_edges():
    m = feather_mask(64, 48, border_frac=0.1)
    assert m.shape == (48, 64)          # (height, width)
    assert m[24, 32] == pytest.approx(1.0)
    assert m[0, 0] < 0.05               # corner nearly transparent
    assert m.max() <= 1.0 and m.min() >= 0.0


# -- temporal ramp --------------------------------------------------------


def test_temporal_ramp_fades_in_and_out():
    r = temporal_ramp(11, ramp_frames=3)
    assert r[0] < r[1] < r[2]
    assert r[5] == pytest.approx(1.0)
    assert r[-1] < r[-2] < r[-3]


# -- composite ------------------------------------------------------------


def _fixed_window(n, box, kind="face"):
    w, h = box[2] - box[0], box[3] - box[1]
    return TubeWindow(kind, 0, n - 1, w, h, [box] * n, moving=False)


def test_composite_blends_toward_refined_in_place():
    frames = np.zeros((5, 40, 40, 3), np.uint8)          # black clip
    box = (10, 10, 30, 30)
    win = _fixed_window(5, box)
    refined = [np.full((20, 20, 3), 255, np.uint8) for _ in range(5)]  # white patches
    same = composite_tube(frames, refined, win, border_frac=0.08, ramp_frames=1)
    assert same is frames                                # mutated in place, same buffer
    # center of the middle frame moved strongly toward white
    assert frames[2, 20, 20, 0] > 200
    # a pixel OUTSIDE the window is untouched
    assert frames[2, 0, 0, 0] == 0


def test_composite_temporal_ramp_leaves_endpoints_mostly_original():
    frames = np.zeros((6, 40, 40, 3), np.uint8)
    box = (10, 10, 30, 30)
    win = _fixed_window(6, box)
    refined = [np.full((20, 20, 3), 255, np.uint8) for _ in range(6)]
    composite_tube(frames, refined, win, border_frac=0.0, ramp_frames=3)
    # first frame's fade-in weight is small -> center still near black
    assert frames[0, 20, 20, 0] < frames[2, 20, 20, 0]


def test_composite_spatial_feather_edges_less_changed_than_center():
    frames = np.zeros((3, 40, 40, 3), np.uint8)
    box = (10, 10, 30, 30)
    win = _fixed_window(3, box)
    refined = [np.full((20, 20, 3), 255, np.uint8) for _ in range(3)]
    composite_tube(frames, refined, win, border_frac=0.25, ramp_frames=0)
    center = frames[1, 20, 20, 0]
    edge = frames[1, 10, 10, 0]  # top-left corner of the patch
    assert edge < center


def test_resize_patches_noop_when_already_target_size():
    win = _fixed_window(2, (0, 0, 16, 16))
    patches = [np.zeros((16, 16, 3), np.uint8), np.zeros((16, 16, 3), np.uint8)]
    out = resize_patches_to_window(patches, win)
    assert out[0] is patches[0]  # untouched


def test_resize_patches_resizes_to_window():
    win = _fixed_window(1, (0, 0, 16, 16))
    patches = [np.zeros((32, 40, 3), np.uint8)]
    out = resize_patches_to_window(patches, win)
    assert out[0].shape == (16, 16, 3)


# -- paste-back resample filter (softness root-cause) -------------
#
# The paste-back is (almost always) a DOWNSCALE of the refine's just-added
# detail back to the tube's native crop size -- see the docstring. Bilinear
# throws away more of that detail than LANCZOS; these tests pin the filter
# choice and the sharpness-retention property it buys, using a synthetic
# high-frequency checkerboard and a cv2-free Laplacian-variance sharpness
# metric (variance of a 3x3 discrete Laplacian -- higher = sharper).


def _laplacian_variance(img: np.ndarray) -> float:
    gray = img.astype(np.float64).mean(axis=-1)
    h, w = gray.shape
    padded = np.pad(gray, 1, mode="edge")
    lap = (
        padded[0:h, 1:w + 1] + padded[2:h + 2, 1:w + 1]
        + padded[1:h + 1, 0:w] + padded[1:h + 1, 2:w + 2]
        - 4 * padded[1:h + 1, 1:w + 1]
    )
    return float(lap.var())


def _checkerboard(size: int, cell: int = 4) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size]
    board = (((xx // cell) + (yy // cell)) % 2) * 255
    return np.stack([board] * 3, axis=-1).astype(np.uint8)


def test_resize_patches_uses_lanczos_not_bilinear(monkeypatch):
    from PIL import Image

    seen = {}
    orig_resize = Image.Image.resize

    def spy_resize(self, size, resample=None, *a, **kw):
        seen["resample"] = resample
        return orig_resize(self, size, resample, *a, **kw)

    monkeypatch.setattr(Image.Image, "resize", spy_resize)
    win = _fixed_window(1, (0, 0, 16, 16))
    resize_patches_to_window([np.zeros((32, 32, 3), np.uint8)], win)
    assert seen["resample"] == Image.LANCZOS
    assert seen["resample"] != Image.BILINEAR


def test_resize_patches_lanczos_retains_more_detail_than_bilinear():
    from PIL import Image

    # A high-frequency 512x512 pattern standing in for a refine's just-added
    # detail, downscaled back to a 128x128 native crop size (a 4x reduction,
    # typical of a small-face working-resolution upscale inverted back).
    src = _checkerboard(512, cell=4)
    win = _fixed_window(1, (0, 0, 128, 128))

    lanczos_out = resize_patches_to_window([src], win)[0]
    bilinear_out = np.asarray(Image.fromarray(src).resize((128, 128), Image.BILINEAR))

    assert _laplacian_variance(lanczos_out) > _laplacian_variance(bilinear_out)
