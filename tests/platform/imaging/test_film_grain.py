import time

import numpy as np
import pytest
from PIL import Image

from src.platform.imaging import film_grain
from src.platform.imaging.film_grain import apply_film_grain, grain_mask

SETTINGS = dict(
    intensity=0.5, grain_size=1.5, shadows_bias=0.6, highlights_protect=0.2, monochromatic=False, blur_amount=0.0,
)


def flat(value=128, size=(192, 256)):
    return np.full((*size, 3), value, dtype=np.uint8)


def delta(output, source):
    return np.asarray(output).astype(np.float64) - source.astype(np.float64)


@pytest.mark.parametrize("grain_size", [1.0, 1.5, 3.0])
@pytest.mark.parametrize("monochromatic", [False, True])
def test_the_fast_path_matches_the_float64_path_in_mean_and_spread(grain_size, monochromatic):
    source = flat()
    settings = dict(SETTINGS, grain_size=grain_size, monochromatic=monochromatic)

    exact = delta(apply_film_grain(Image.fromarray(source), **settings, rng=np.random.default_rng(1)), source)
    fast = delta(apply_film_grain(source, **settings, rng=np.random.default_rng(1), fast=True), source)

    assert fast.mean() == pytest.approx(exact.mean(), abs=1.0)
    assert fast.std() == pytest.approx(exact.std(), rel=0.12)
    assert fast.std() > 3.0


def test_the_fast_path_follows_the_shadow_and_highlight_masks():
    rng = np.random.default_rng(2)
    spreads = {
        value: delta(apply_film_grain(flat(value), **SETTINGS, rng=rng, fast=True), flat(value)).std()
        for value in (8, 128, 250)
    }

    assert spreads[128] > spreads[250] and spreads[128] > spreads[8]
    expected = {value: grain_mask(np.float64(value / 255), 0.6, 0.2) for value in spreads}
    ratio = spreads[8] / spreads[128]
    assert ratio == pytest.approx(expected[8] / expected[128], rel=0.25)


def test_the_fast_path_keeps_the_input_untouched_and_the_shape():
    source = flat()
    before = source.copy()

    result = apply_film_grain(source, **SETTINGS, rng=np.random.default_rng(3), fast=True)

    assert result.dtype == np.uint8 and result.shape == source.shape
    assert np.array_equal(source, before)


def test_the_fast_path_is_reproducible_for_a_seed():
    first = apply_film_grain(flat(), **SETTINGS, rng=np.random.default_rng(5), fast=True)
    again = apply_film_grain(flat(), **SETTINGS, rng=np.random.default_rng(5), fast=True)
    other = apply_film_grain(flat(), **SETTINGS, rng=np.random.default_rng(6), fast=True)

    assert np.array_equal(first, again) and not np.array_equal(first, other)


def test_black_and_white_grain_moves_every_channel_by_the_same_amount():
    result = apply_film_grain(flat(), **dict(SETTINGS, monochromatic=True), rng=np.random.default_rng(7), fast=True)

    assert np.array_equal(result[..., 0], result[..., 1]) and np.array_equal(result[..., 1], result[..., 2])
    assert not np.array_equal(result[..., 0], flat()[..., 0])


def test_a_pil_image_in_gives_a_pil_image_out_and_blur_softens_it():
    sharp = apply_film_grain(Image.fromarray(flat()), **SETTINGS, rng=np.random.default_rng(8), fast=True)
    soft = apply_film_grain(
        Image.fromarray(flat()), **dict(SETTINGS, blur_amount=1.5), rng=np.random.default_rng(8), fast=True,
    )

    assert isinstance(sharp, Image.Image) and isinstance(soft, Image.Image)
    assert np.asarray(soft).std() < np.asarray(sharp).std()


def test_a_legacy_global_random_source_still_works_on_the_fast_path():
    result = apply_film_grain(flat(), **SETTINGS, fast=True)

    assert result.shape == flat().shape


def test_the_fast_path_is_clearly_faster_than_the_float64_path():
    source = np.random.default_rng(0).integers(0, 255, (256, 256, 3), dtype=np.uint8)
    image = Image.fromarray(source)
    rng = np.random.default_rng(0)

    def best(call):
        call()
        times = []
        for _ in range(5):
            started = time.perf_counter()
            call()
            times.append(time.perf_counter() - started)
        return min(times)

    exact = best(lambda: apply_film_grain(image, **SETTINGS, rng=rng))
    fast = best(lambda: apply_film_grain(source, **SETTINGS, rng=rng, fast=True))

    assert fast * 3 < exact
