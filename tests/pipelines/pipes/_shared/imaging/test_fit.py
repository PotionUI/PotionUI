from __future__ import annotations

import pytest
from PIL import Image

from src.pipelines.pipes._shared.imaging.fit import PAD_FILL, crop_box, fit_image, pad_layout


@pytest.mark.parametrize("src, target, box", [
    ((300, 100), (100, 100), (100, 0, 200, 100)),
    ((100, 300), (100, 100), (0, 100, 100, 200)),
    ((1920, 1080), (1024, 1024), (420, 0, 1500, 1080)),
    ((1000, 1000), (1664, 928), (0, 221, 1000, 779)),
    ((800, 450), (1600, 900), (0, 0, 800, 450)),
    ((101, 100), (100, 100), (0, 0, 100, 100)),
])
def test_crop_box_is_the_centred_largest_box_of_the_target_aspect(src, target, box):
    assert crop_box(src, target) == box


@pytest.mark.parametrize("src, target, layout", [
    ((300, 100), (300, 300), (0, 100, 300, 100)),
    ((100, 300), (300, 300), (100, 0, 100, 300)),
    ((1000, 1000), (1664, 928), (368, 0, 928, 928)),
    ((1920, 1080), (1024, 1024), (0, 224, 1024, 576)),
    ((640, 360), (1664, 936), (0, 0, 1664, 936)),
])
def test_pad_layout_centres_the_scaled_image_inside_the_target(src, target, layout):
    assert pad_layout(src, target) == layout


def _bands(size, colours):
    w, h = size
    image = Image.new("RGB", size)
    step = w // len(colours)
    for i, colour in enumerate(colours):
        image.paste(colour, (i * step, 0, (i + 1) * step, h))
    return image


def test_crop_keeps_only_the_centre_and_never_stretches():
    src = _bands((300, 100), [(255, 0, 0), (0, 255, 0), (0, 0, 255)])
    out = fit_image(src, (64, 64), "crop")
    assert out.size == (64, 64)
    assert set(out.getdata()) == {(0, 255, 0)}


def test_pad_keeps_the_whole_image_on_grey_borders():
    src = Image.new("RGB", (200, 100), (200, 10, 10))
    out = fit_image(src, (200, 200), "pad")
    assert out.size == (200, 200)
    assert out.getpixel((100, 0)) == PAD_FILL
    assert out.getpixel((100, 199)) == PAD_FILL
    assert out.getpixel((100, 100)) == (200, 10, 10)
    assert out.getpixel((0, 50)) == (200, 10, 10)
    assert out.getpixel((0, 49)) == PAD_FILL


def test_pad_fill_is_neutral_grey():
    assert PAD_FILL == (128, 128, 128)


def test_same_aspect_only_resizes_for_both_fits():
    src = Image.new("RGB", (320, 180), (5, 6, 7))
    for fit in ("crop", "pad"):
        out = fit_image(src, (1664, 936), fit)
        assert out.size == (1664, 936)
        assert set(out.getdata()) == {(5, 6, 7)}


def test_unknown_fit_is_rejected():
    with pytest.raises(ValueError):
        fit_image(Image.new("RGB", (8, 8)), (8, 8), "stretch")
