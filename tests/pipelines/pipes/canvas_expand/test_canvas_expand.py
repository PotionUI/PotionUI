import numpy as np
import pytest
from PIL import Image

from src.pipelines.contracts import PipeInput
from src.pipelines.pipes.canvas_expand.geometry import (
    aspect_margins,
    build_canvas,
    build_mask,
    pixel_margins,
)
from src.pipelines.pipes.canvas_expand.main import CanvasExpandPipe


def _picture(w=64, h=32):
    rng = np.random.default_rng(3)
    return Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))


def _run(config, image=None):
    image = image or _picture()
    return CanvasExpandPipe(config).process(PipeInput(input={"image": image}), None).output


def test_sides_grow_the_canvas_and_keep_the_original_pixels_in_place():
    source = _picture()
    out = _run({"left": 10, "top": 4, "right": 6, "bottom": 2}, source)
    assert out["image"].size == (64 + 16, 32 + 6)
    assert np.array_equal(np.asarray(out["image"])[4:36, 10:74], np.asarray(source))
    assert out["images"] == [out["image"]]


def test_mask_is_white_on_the_new_area_and_black_deep_inside_the_original():
    out = _run({"left": 20, "overlap": 8})
    mask = np.asarray(out["mask"])
    assert mask.shape == (32, 84)
    assert (mask[:, :20] == 255).all()
    assert (mask[:, 44:] == 0).all()
    assert mask[16, 26] > 100


def test_zero_overlap_masks_exactly_the_new_area():
    mask = np.asarray(build_mask((8, 8), (4, 0, 0, 0), overlap=0))
    assert mask.shape == (8, 12)
    assert (mask[:, :4] == 255).all() and (mask[:, 4:] == 0).all()


def test_aspect_anchors_pick_the_side():
    assert aspect_margins(64, 64, "2:1", "left") == (0, 0, 64, 0)
    assert aspect_margins(64, 64, "2:1", "right") == (64, 0, 0, 0)
    assert aspect_margins(64, 64, "2:1", "center") == (32, 0, 32, 0)
    assert aspect_margins(64, 64, "1:2", "top") == (0, 0, 0, 64)
    assert aspect_margins(64, 64, "1:2", "bottom") == (0, 64, 0, 0)
    assert aspect_margins(64, 64, "1:2", "left") == (0, 32, 0, 32)


def test_aspect_result_has_the_requested_shape():
    left, top, right, bottom = aspect_margins(1000, 600, "9:16", "center")
    assert abs((1000 + left + right) / (600 + top + bottom) - 9 / 16) < 0.01


def test_aspect_already_met_and_bad_input_are_rejected():
    with pytest.raises(ValueError, match="already"):
        aspect_margins(64, 64, "1:1")
    with pytest.raises(ValueError, match="aspect"):
        aspect_margins(64, 64, "wide")
    with pytest.raises(ValueError, match="anchor"):
        aspect_margins(64, 64, "2:1", "middle")


def test_no_expansion_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        pixel_margins(0, 0, 0, 0)
    with pytest.raises(ValueError, match="at least one"):
        _run({})


def test_fills_differ_but_never_touch_the_original():
    source = _picture()
    gray = np.asarray(build_canvas(source, (8, 0, 0, 0), "gray"))
    edge = np.asarray(build_canvas(source, (8, 0, 0, 0), "edge"))
    assert (gray[:, :8] == 127).all()
    assert np.array_equal(edge[:, :8], np.repeat(np.asarray(source)[:, :1], 8, axis=1))
    assert np.array_equal(gray[:, 8:], edge[:, 8:])


def test_aspect_wins_over_the_sides_when_both_are_given():
    out = _run({"aspect": "2:1", "left": 99, "anchor": "left"}, _picture(64, 64))
    assert out["image"].size == (128, 64)


@pytest.mark.parametrize("aspect", ["1000:1", "1:1000", "inf:1", "1:nan", "nan:1", "0:1"])
def test_absurd_aspects_are_rejected(aspect):
    with pytest.raises(ValueError, match="aspect"):
        aspect_margins(64, 64, aspect)


def test_aspect_growth_is_capped_like_the_sides():
    margins = aspect_margins(4000, 100, "1:8", "top")
    assert sum(margins) == 8192
    assert sum(aspect_margins(100, 4000, "8:1", "left")) == 8192


def test_a_non_image_input_is_rejected():
    with pytest.raises(ValueError, match="picture"):
        CanvasExpandPipe({"left": 8}).process(PipeInput(input={"image": "x.png"}), None)
