import numpy as np
import pytest
from PIL import Image, ImageFilter

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.film_grain.main import FilmGrain
from src.platform.imaging.film_grain import apply_film_grain, create_grain_layer

CONFIGS = {
    "color": dict(intensity=0.3, grain_size=1.5, shadows_bias=0.6, highlights_protect=0.2, monochromatic=False, blur_amount=0.5),
    "mono": dict(intensity=0.25, grain_size=1.2, shadows_bias=0.7, highlights_protect=0.3, monochromatic=True, blur_amount=0.3),
    "fine": dict(intensity=0.5, grain_size=0.8, shadows_bias=0.4, highlights_protect=0.5, monochromatic=False, blur_amount=0.0),
    "unit": dict(intensity=0.4, grain_size=1.0, shadows_bias=0.6, highlights_protect=0.2, monochromatic=True, blur_amount=0.0),
}


def source():
    y, x = np.mgrid[0:40, 0:48]
    return Image.fromarray(np.stack([x * 5, y * 6, (x + y) * 3], axis=-1).astype(np.uint8))


def legacy_layer(size, intensity, grain_size):
    scale = 1 / grain_size
    scaled_size = (int(size[1] * scale), int(size[0] * scale))
    noise = np.random.normal(0, 1, scaled_size)
    if scale != 1:
        noise = Image.fromarray((noise * 255).astype(np.uint8))
        noise = noise.resize((size[0], size[1]), Image.BILINEAR)
        noise = np.array(noise).astype(float) / 255
    noise = (noise - noise.mean()) / noise.std()
    return noise * intensity


def legacy_apply(image, config):
    img_array = np.array(image).astype(float) / 255
    height, width = img_array.shape[:2]
    grain = legacy_layer((width, height), config["intensity"], config["grain_size"])
    if config["monochromatic"]:
        grain = np.repeat(np.expand_dims(grain, axis=-1), 3, axis=-1)
    else:
        grain = np.stack(
            [legacy_layer((width, height), config["intensity"], config["grain_size"]) for _ in range(3)],
            axis=-1,
        )
    luminance = np.mean(img_array, axis=2)
    shadows_mask = 1 - (luminance ** (1 - config["shadows_bias"]))
    highlights_mask = 1 - (1 - luminance) ** (1 - config["highlights_protect"])
    final_mask = np.stack([shadows_mask * highlights_mask] * 3, axis=-1)
    result = np.clip(img_array + grain * final_mask, 0, 1)
    result_image = Image.fromarray((result * 255).astype(np.uint8))
    if config["blur_amount"] > 0:
        result_image = result_image.filter(ImageFilter.GaussianBlur(radius=config["blur_amount"]))
    return result_image


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_the_pipe_output_is_what_it_was_before_the_grain_math_was_shared(name):
    config = CONFIGS[name]
    np.random.seed(1234)
    expected = legacy_apply(source(), config)
    np.random.seed(1234)

    actual = FilmGrain(dict(config))._apply_grain(source())

    assert np.array_equal(np.asarray(actual), np.asarray(expected))


def test_the_pipe_process_returns_the_grained_images():
    np.random.seed(5)
    expected = legacy_apply(source(), CONFIGS["mono"])
    np.random.seed(5)
    emitted = []

    output = FilmGrain(dict(CONFIGS["mono"], preset=None)).process(
        PipeInput(input={"image": source()}), emitted.append,
    )

    assert np.array_equal(np.asarray(output.output["image"][0]), np.asarray(expected))
    assert len(emitted) == 1


def test_a_seeded_generator_makes_the_grain_reproducible():
    config = CONFIGS["color"]

    first = apply_film_grain(source(), **config, rng=np.random.default_rng(3))
    again = apply_film_grain(source(), **config, rng=np.random.default_rng(3))
    other = apply_film_grain(source(), **config, rng=np.random.default_rng(4))

    assert np.array_equal(np.asarray(first), np.asarray(again))
    assert not np.array_equal(np.asarray(first), np.asarray(other))


def test_the_grain_layer_has_the_requested_spread_and_size():
    layer = create_grain_layer((40, 30), 0.2, 2.0, np.random.default_rng(1))

    assert layer.shape == (30, 40)
    assert layer.std() == pytest.approx(0.2)
    assert layer.mean() == pytest.approx(0.0, abs=1e-9)
