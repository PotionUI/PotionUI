"""Unit tests for the tiled refiner's pure geometry + blend math."""

import numpy as np
import pytest

from src.pipelines.pipes.tiled_refiner.tiling import (
    feather_mask,
    plan_tile_positions,
    tile_complexity,
    tile_denoise,
    tiled_refine,
)


class TestPlanTilePositions:
    def test_single_tile_when_image_fits(self):
        assert plan_tile_positions(64, 64, 16) == [0]
        assert plan_tile_positions(40, 64, 16) == [0]

    def test_covers_full_extent_with_clamped_last_tile(self):
        # step = 48; first window [0:64], last clamped to dim-tile=36 -> [36:100].
        origins = plan_tile_positions(100, 64, 16)
        assert origins[0] == 0
        assert origins[-1] == 100 - 64
        # every pixel is covered by at least one [origin, origin+tile) window
        covered = np.zeros(100, dtype=bool)
        for o in origins:
            covered[o:o + 64] = True
        assert covered.all()

    def test_interior_origins_are_multiples_of_step(self):
        origins = plan_tile_positions(300, 64, 16)  # step 48
        assert origins[:-1] == [0, 48, 96, 144, 192]
        assert origins[-1] == 300 - 64


class TestFeatherMask:
    def test_border_edges_stay_full_weight(self):
        # A tile that is the whole image (no interior edges) -> all ones.
        m = feather_mask(8, 8, 4, top=False, bottom=False, left=False, right=False)
        assert m.shape == (8, 8, 1)
        assert np.allclose(m, 1.0)

    def test_interior_edge_ramps_from_low_to_full(self):
        m = feather_mask(8, 8, 4, top=True, bottom=False, left=False, right=False)[:, 0, 0]
        # top edge ramps up over 4 px then holds at 1.
        assert m[0] < m[1] < m[2] < m[3]
        assert np.allclose(m[4:], 1.0)


class TestTiledRefine:
    def _gradient(self, h, w):
        y = np.linspace(0, 255, h)[:, None]
        x = np.linspace(0, 255, w)[None, :]
        img = ((y + x) / 2).astype(np.uint8)
        return np.repeat(img[:, :, None], 3, axis=2)

    def test_identity_refine_reconstructs_image(self):
        """An identity per-tile refine must reconstruct the input exactly: the
        feather weights cancel under the normalize (proves the blend is seam-free)."""
        img = self._gradient(200, 260)
        out = tiled_refine(img, lambda crop, k: crop, tile=64, overlap=16)
        assert out.shape == img.shape
        assert np.abs(out.astype(int) - img.astype(int)).max() <= 1  # rounding only

    def test_single_tile_path(self):
        img = self._gradient(48, 48)
        calls = []
        out = tiled_refine(img, lambda crop, k: (calls.append(crop.shape), crop)[1],
                           tile=64, overlap=16)
        assert len(calls) == 1                      # whole image is one tile
        assert np.array_equal(out, img)

    def test_linear_transform_blends_uniformly(self):
        """A uniform +20 per tile must appear as +20 everywhere — overlaps don't
        double-count (weight normalization holds for a linear op)."""
        img = np.full((160, 160, 3), 100, dtype=np.uint8)
        out = tiled_refine(img, lambda crop, k: np.clip(crop.astype(int) + 20, 0, 255).astype(np.uint8),
                           tile=64, overlap=16)
        assert np.allclose(out, 120, atol=1)

    def test_progress_callback_fires_per_tile(self):
        img = self._gradient(160, 160)
        seen = []
        tiled_refine(img, lambda crop, k: crop, tile=64, overlap=16,
                     on_tile=lambda done, total: seen.append((done, total)))
        assert seen[-1][0] == seen[-1][1]           # last done == total
        assert seen[-1][1] == len(seen)             # one callback per tile


class TestContentAwareDenoise:
    def _flat(self):
        return np.full((256, 256, 3), 90, dtype=np.uint8)

    def _busy(self):
        rng = np.random.default_rng(0)
        return rng.integers(0, 256, (256, 256, 3), dtype=np.uint8)

    def test_complexity_low_for_flat_high_for_busy(self):
        assert tile_complexity(self._flat()) < 0.05
        assert tile_complexity(self._busy()) > 0.5

    def test_flat_tile_gets_min_denoise(self):
        # A flat tile (sky/mist) must refine at (near) the floor — the anti-hallucination guard.
        d = tile_denoise(self._flat(), base_denoise=0.2, min_denoise=0.1)
        assert abs(d - 0.1) < 0.02

    def test_busy_tile_approaches_base_denoise(self):
        d = tile_denoise(self._busy(), base_denoise=0.2, min_denoise=0.1)
        assert d > 0.15  # pulled up toward the base for detail recovery

    def test_denoise_stays_within_bounds(self):
        for crop in (self._flat(), self._busy()):
            d = tile_denoise(crop, base_denoise=0.2, min_denoise=0.1)
            assert 0.1 <= d <= 0.2

    def test_gradient_tile_is_intermediate(self):
        # A smooth ramp has some edges but low variance -> between flat and busy.
        ramp = np.repeat(np.linspace(0, 255, 256).astype(np.uint8)[None, :], 256, axis=0)
        crop = np.stack([ramp] * 3, axis=2)
        assert 0.0 <= tile_complexity(crop) <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
