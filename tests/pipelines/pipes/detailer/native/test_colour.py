import numpy as np

from src.pipelines.pipes.detailer.native.colour import (
    lab_to_rgb,
    match_colour,
    rgb_to_lab,
    weighted_stats,
)


def _rng():
    return np.random.default_rng(7)


def test_lab_round_trip_is_lossless_in_float():
    rgb = _rng().uniform(0, 255, size=(8, 8, 3))
    assert np.allclose(lab_to_rgb(rgb_to_lab(rgb)), rgb, atol=1e-9)


def test_masked_mean_and_std_match_the_original_after_transfer():
    rng = _rng()
    original = rng.normal(120, 30, size=(32, 32, 3)).clip(0, 255)
    refined = rng.normal(60, 8, size=(32, 32, 3)).clip(0, 255)
    weights = np.zeros((32, 32))
    weights[8:24, 8:24] = 1.0

    matched = match_colour(refined, original, weights)

    matched_mean, matched_std = weighted_stats(rgb_to_lab(matched), weights)
    original_mean, original_std = weighted_stats(rgb_to_lab(original), weights)
    assert np.allclose(matched_mean, original_mean, atol=1e-3)
    assert np.allclose(matched_std, original_std, atol=1e-3)


def test_unmasked_pixels_are_untouched():
    rng = _rng()
    original = rng.uniform(0, 255, size=(16, 16, 3))
    refined = rng.uniform(0, 255, size=(16, 16, 3))
    weights = np.zeros((16, 16))
    weights[4:12, 4:12] = 1.0

    matched = match_colour(refined, original, weights)

    outside = weights == 0
    assert np.array_equal(matched[outside], refined[outside])
    assert not np.allclose(matched[4:12, 4:12], refined[4:12, 4:12])


def test_feathered_weights_scale_the_correction():
    rng = _rng()
    original = np.full((8, 8, 3), 200.0)
    refined = rng.uniform(0, 60, size=(8, 8, 3))
    full = np.ones((8, 8))
    half = np.full((8, 8), 0.5)

    fully_matched = match_colour(refined, original, full)
    half_matched = match_colour(refined, original, half)

    midpoint = refined + 0.5 * (fully_matched - refined)
    assert np.allclose(half_matched, midpoint, atol=1e-9)


def test_an_empty_mask_returns_the_refined_crop_unchanged():
    refined = _rng().uniform(0, 255, size=(8, 8, 3))
    assert np.array_equal(match_colour(refined, refined * 0.5, np.zeros((8, 8))), refined)
