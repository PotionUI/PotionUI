from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src.platform.media_tools import InvalidToolParams, probe
from src.platform.media_tools.ops import place_patch


def patch_media(tmp_path, size, covered):
    array = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    array[:, :] = (200, 30, 30, 255)
    left, top, right, bottom = covered
    array[top:bottom, left:right, 3] = 0
    path = tmp_path / "patch.png"
    Image.fromarray(array).save(path)
    return probe(path)


def test_the_picture_is_placed_on_a_larger_canvas_and_the_patch_fills_the_new_area(tmp_path):
    base = Image.new("RGB", (40, 30), (10, 120, 10))
    media = patch_media(tmp_path, (60, 90), (10, 20, 50, 50))
    params = {"patch": media, "x": 0, "y": 0, "width": 1, "height": 1, "canvas_width": 60, "canvas_height": 90, "canvas_x": 10, "canvas_y": 20}

    result = place_patch.PlacePatch().image(base, params)

    assert result.size == (60, 90) and result.mode == "RGB"
    pixels = np.asarray(result)
    assert (pixels[20:50, 10:50] == (10, 120, 10)).all()
    assert (pixels[0:20] == (200, 30, 30)).all() and (pixels[50:] == (200, 30, 30)).all()


def test_without_canvas_params_the_picture_keeps_its_size(tmp_path):
    base = Image.new("RGB", (40, 30), (10, 120, 10))
    media = patch_media(tmp_path, (40, 30), (0, 0, 40, 30))

    result = place_patch.PlacePatch().image(base, {"patch": media, "x": 0, "y": 0, "width": 1, "height": 1})

    assert result.size == (40, 30)
    assert (np.asarray(result) == (10, 120, 10)).all()


def test_the_picture_position_is_kept_inside_the_canvas(tmp_path):
    base = Image.new("RGB", (40, 30), (10, 120, 10))
    media = patch_media(tmp_path, (60, 90), (20, 60, 60, 90))
    params = {"patch": media, "x": 0, "y": 0, "width": 1, "height": 1, "canvas_width": 60, "canvas_height": 90, "canvas_x": 500, "canvas_y": 500}

    result = place_patch.PlacePatch().image(base, params)

    assert (np.asarray(result)[60:90, 20:60] == (10, 120, 10)).all()


def test_a_canvas_smaller_than_the_picture_is_refused(tmp_path):
    base = Image.new("RGB", (40, 30), (10, 120, 10))
    media = patch_media(tmp_path, (20, 20), (0, 0, 0, 0))

    with pytest.raises(InvalidToolParams):
        place_patch.PlacePatch().image(base, {"patch": media, "canvas_width": 20, "canvas_height": 20})
