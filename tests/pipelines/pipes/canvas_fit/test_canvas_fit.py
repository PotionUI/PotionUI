"""Tests for the canvas_fit pipe: the LIST-in/LIST-out array contract
(matching the `media_loader` producer / `gallery` consumer this family sits
between), the exact `scale_percent` definition (a percentage of the CANVAS's
short side that the image's long side should occupy - pinned here since it's
the number a user actually dials), anchor placement across all 9 positions,
the two `fill` modes, and aspect-ratio preservation."""

import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import IOType, PipeInput
from src.pipelines.pipes._shared.imaging.alpha import alpha_bbox
from src.pipelines.pipes.canvas_fit.main import (
    ANCHOR_CHOICES,
    CanvasFitPipe,
    compute_scaled_size,
    parse_anchor,
    parse_fill,
)


def _solid(size, color=(200, 60, 60)):
    return Image.new("RGB", size, color)


def _placed_bbox(result_image):
    """Bounding box of the non-transparent region on a fill='transparent'
    canvas - where the resized image actually landed."""
    return alpha_bbox(result_image, threshold=0)


# -- scale_percent definition (pinned) ----------------------------------

def test_scale_percent_definition_worked_example():
    # canvas 800x600 -> short side 600; scale_percent=50 -> target long = 300.
    # image 400x200 (long=400) -> factor 300/400=0.75 -> 300x150.
    new_w, new_h = compute_scaled_size(400, 200, 800, 600, 50.0)
    assert (new_w, new_h) == (300, 150)


def test_scale_percent_100_long_side_fills_canvas_short_side():
    new_w, new_h = compute_scaled_size(400, 200, 1000, 1000, 100.0)
    assert max(new_w, new_h) == 1000  # canvas short side


def test_scale_percent_bite_check_uses_short_side_not_long_side():
    """A rectangular (non-square) canvas is the discriminator: using the
    LONG canvas side here would give a different, wrong, answer."""
    canvas_w, canvas_h = 1200, 600  # short side 600
    new_w, new_h = compute_scaled_size(100, 100, canvas_w, canvas_h, 100.0)
    assert (new_w, new_h) == (600, 600)  # not 1200x1200


def test_aspect_ratio_is_preserved():
    new_w, new_h = compute_scaled_size(300, 100, 1000, 1000, 50.0)
    # Rounded independently to whole pixels, so only approximately exact.
    assert new_w / new_h == pytest.approx(300 / 100, rel=1e-2)


def test_scale_percent_end_to_end_through_the_pipe():
    image = _solid((400, 200))
    pipe = CanvasFitPipe({"width": 800, "height": 600, "scale_percent": 50.0,
                          "anchor": "center", "fill": "transparent"})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)
    x, y, w, h = _placed_bbox(result.output["image"][0])
    assert (w, h) == (300, 150)


# -- array contract: process EVERY image, never just the first --------------

def test_image_io_is_declared_as_an_array():
    """The upstream producer (`media_loader`) always emits a LIST and the
    downstream consumer (`gallery`) always iterates one - a scalar `image`
    spec here is exactly the `'Image' object is not iterable` bug."""
    ins = {s.name: s for s in CanvasFitPipe.inputs()}
    outs = {s.name: s for s in CanvasFitPipe.outputs()}
    assert ins["image"].is_array is True
    assert outs["image"].is_array is True


def test_two_image_list_returns_two_images_bite_check():
    """Feed a two-image list with distinct sizes (forcing distinct scaled
    sizes at the same scale_percent); both must come back placed
    independently, in order. Bite-check: reverting to unwrapping `image[0]`
    and returning a bare `PipeOutput(output={"image": result})` makes this
    go red (a one-element result / a crash iterating a bare Image
    downstream)."""
    image_a = _solid((100, 50))
    image_b = _solid((50, 100))
    pipe = CanvasFitPipe({"width": 200, "height": 200, "scale_percent": 50.0,
                          "anchor": "center", "fill": "transparent"})

    result = pipe.process(PipeInput(input={"image": [image_a, image_b]}), lambda o: None)

    images = result.output["image"]
    assert isinstance(images, list) and len(images) == 2
    assert images[0].size == (200, 200) and images[1].size == (200, 200)  # canvas size

    bbox_a = _placed_bbox(images[0])
    bbox_b = _placed_bbox(images[1])
    assert bbox_a[2:4] == (100, 50)  # landscape source -> landscape placement
    assert bbox_b[2:4] == (50, 100)  # portrait source -> portrait placement


def test_bare_image_input_is_still_accepted_and_wrapped():
    image = _solid((50, 50))
    pipe = CanvasFitPipe({"width": 100, "height": 100})
    result = pipe.process(PipeInput(input={"image": image}), lambda o: None)
    assert isinstance(result.output["image"], list)
    assert len(result.output["image"]) == 1


# -- anchor placement -----------------------------------------------------

def test_parse_anchor_all_documented_choices():
    for anchor in ANCHOR_CHOICES:
        h, v = parse_anchor(anchor)
        assert 0.0 <= h <= 1.0 and 0.0 <= v <= 1.0


@pytest.mark.parametrize("anchor,expected", [
    ("center", (0.5, 0.5)),
    ("top", (0.5, 0.0)),
    ("bottom", (0.5, 1.0)),
    ("left", (0.0, 0.5)),
    ("right", (1.0, 0.5)),
    ("top_left", (0.0, 0.0)),
    ("top_right", (1.0, 0.0)),
    ("bottom_left", (0.0, 1.0)),
    ("bottom_right", (1.0, 1.0)),
])
def test_parse_anchor_exact_fractions(anchor, expected):
    assert parse_anchor(anchor) == expected


def test_unknown_anchor_raises():
    with pytest.raises(ValueError):
        parse_anchor("diagonal")


@pytest.mark.parametrize("anchor", ["top_left", "top_right", "bottom_left", "bottom_right", "center"])
def test_anchor_placement_end_to_end(anchor):
    image = _solid((100, 50))  # long side 100
    canvas_w, canvas_h = 200, 200
    pipe = CanvasFitPipe({"width": canvas_w, "height": canvas_h, "scale_percent": 25.0,
                          "anchor": anchor, "fill": "transparent"})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)

    new_w, new_h = compute_scaled_size(100, 50, canvas_w, canvas_h, 25.0)
    h_anchor, v_anchor = parse_anchor(anchor)
    expected_x = round((canvas_w - new_w) * h_anchor)
    expected_y = round((canvas_h - new_h) * v_anchor)

    x, y, w, h = _placed_bbox(result.output["image"][0])
    assert (x, y, w, h) == (expected_x, expected_y, new_w, new_h)


# -- fill modes -------------------------------------------------------------

def test_fill_transparent_keeps_alpha_outside_the_image():
    image = _solid((50, 50))
    pipe = CanvasFitPipe({"width": 200, "height": 200, "scale_percent": 25.0,
                          "anchor": "center", "fill": "transparent"})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)
    out = result.output["image"][0]
    assert out.mode == "RGBA"
    corner_alpha = np.array(out)[0, 0, 3]
    assert corner_alpha == 0


def test_fill_hex_color_is_fully_opaque_rgb():
    image = _solid((50, 50), color=(10, 10, 10))
    pipe = CanvasFitPipe({"width": 200, "height": 200, "scale_percent": 25.0,
                          "anchor": "top_left", "fill": "#112233"})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)
    out = result.output["image"][0]
    assert out.mode == "RGB"

    arr = np.array(out)
    # A corner far from the placed image is pure fill colour.
    assert tuple(arr[-1, -1]) == (0x11, 0x22, 0x33)
    # No alpha channel to leak transparency through.
    assert arr.shape[-1] == 3


def test_invalid_fill_color_raises():
    image = _solid((50, 50))
    pipe = CanvasFitPipe({"width": 100, "height": 100, "fill": "not-a-color"})
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={"image": [image]}), lambda o: None)


# -- parse_fill: strict 'transparent' | '#RRGGBB' contract -------------------

def test_parse_fill_transparent_is_case_insensitive():
    assert parse_fill("transparent") is None
    assert parse_fill("Transparent") is None
    assert parse_fill(" TRANSPARENT ") is None


def test_parse_fill_hex_with_and_without_hash():
    assert parse_fill("#112233") == (0x11, 0x22, 0x33)
    assert parse_fill("112233") == (0x11, 0x22, 0x33)
    assert parse_fill("#AABBCC") == (0xAA, 0xBB, 0xCC)


@pytest.mark.parametrize("bad", ["red", "#fff", "fff", "rgb(1,2,3)", "", "#gggggg"])
def test_parse_fill_rejects_anything_else(bad):
    with pytest.raises(ValueError):
        parse_fill(bad)


# -- guards -------------------------------------------------------------

def test_missing_image_raises():
    pipe = CanvasFitPipe(CanvasFitPipe.get_default_config())
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={}), lambda o: None)


@pytest.mark.parametrize("width,height", [(0, 100), (100, 0), (-5, 100)])
def test_non_positive_dimensions_raise(width, height):
    pipe = CanvasFitPipe({"width": width, "height": height})
    with pytest.raises(ValueError):
        pipe.process(PipeInput(input={"image": [_solid((10, 10))]}), lambda o: None)


def test_scale_percent_over_100_overflows_without_crashing():
    image = _solid((100, 100))
    pipe = CanvasFitPipe({"width": 50, "height": 50, "scale_percent": 200.0, "fill": "transparent"})
    result = pipe.process(PipeInput(input={"image": [image]}), lambda o: None)
    assert result.output["image"][0].size == (50, 50)  # canvas size is authoritative


# -- config / IO contract -----------------------------------------------------

def test_name_and_contract():
    assert CanvasFitPipe.name == "canvas_fit"
    ins = {s.name: s.io_type for s in CanvasFitPipe.inputs()}
    outs = {s.name: s.io_type for s in CanvasFitPipe.outputs()}
    assert ins == {"image": IOType.IMAGE}
    assert outs == {"image": IOType.IMAGE}


def test_config_spec_matches_contract():
    specs = {s.name: s for s in CanvasFitPipe.configuration()}
    assert set(specs) == {"width", "height", "scale_percent", "anchor", "fill"}
    assert specs["scale_percent"].default == 100.0
    assert specs["anchor"].default == "center"
    assert specs["fill"].default == "transparent"
    assert set(specs["anchor"].choices) == set(ANCHOR_CHOICES)
