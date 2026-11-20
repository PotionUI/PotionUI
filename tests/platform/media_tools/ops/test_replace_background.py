from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from src.platform.media_tools import InvalidToolParams, UnsupportedMedia, probe
from src.platform.media_tools.ops import core_specs, replace_background
from src.platform.media_tools.types import EDITOR_SCOPE

SIZE = (40, 30)
TOOL = replace_background.ReplaceBackground()


def subject_picture():
    pixels = np.zeros((SIZE[1], SIZE[0], 3), dtype=np.uint8)
    pixels[...] = (200, 40, 40)
    return Image.fromarray(pixels)


def soft_matte():
    levels = np.zeros((SIZE[1], SIZE[0]), dtype=np.uint8)
    levels[:, :10] = 255
    levels[:, 10:20] = np.linspace(255, 0, 10).astype(np.uint8)
    return levels


@pytest.fixture
def matte_media(tmp_path):
    path = tmp_path / "matte.png"
    Image.fromarray(soft_matte()).save(path)
    return probe(path)


def test_the_op_is_an_editor_scope_tool_with_its_params():
    found = next(item for item in core_specs() if item.id == "replace_background")

    assert found.scopes == (EDITOR_SCOPE,)
    assert found.label == "Replace background"
    names = [param["name"] for param in found.params]
    assert names == ["kind", "color", "picture", "matte", "opacity"]
    assert next(p for p in found.params if p["name"] == "color")["default"] == "#ffffff"


def test_a_colour_fills_where_the_matte_is_white_and_keeps_the_subject(matte_media):
    result = np.asarray(TOOL.image(subject_picture(), {"kind": "color", "color": "#0000ff", "matte": matte_media}))

    assert result.shape == (SIZE[1], SIZE[0], 3)
    assert tuple(result[5, 2]) == (0, 0, 255)
    assert tuple(result[5, 30]) == (200, 40, 40)


def test_the_soft_edge_of_the_matte_is_used_as_it_is(matte_media):
    result = np.asarray(TOOL.image(subject_picture(), {"kind": "color", "color": "#0000ff", "matte": matte_media})).astype(int)
    row = result[5, 10:20, 2]
    weights = soft_matte()[5, 10:20] / 255.0
    expected = 40 * (1 - weights) + 255 * weights

    assert np.abs(row - expected).max() <= 1
    assert len(set(row.tolist())) >= 8
    assert (np.diff(row) <= 0).all()


def test_the_default_colour_is_white(matte_media):
    result = np.asarray(TOOL.image(subject_picture(), {"matte": matte_media}))

    assert tuple(result[0, 0]) == (255, 255, 255)


def test_a_picture_is_cover_fitted_and_composited_by_the_matte(tmp_path, matte_media):
    backdrop = np.zeros((20, 100, 3), dtype=np.uint8)
    backdrop[:, :50] = (0, 255, 0)
    backdrop[:, 50:] = (0, 0, 255)
    path = tmp_path / "backdrop.png"
    Image.fromarray(backdrop).save(path)

    result = np.asarray(TOOL.image(subject_picture(), {"kind": "picture", "picture": probe(path), "matte": matte_media})).astype(int)

    assert result.shape == (SIZE[1], SIZE[0], 3)
    assert tuple(result[15, 2]) == (0, 255, 0) or abs(result[15, 2, 1] - 255) <= 2
    assert tuple(result[15, 30]) == (200, 40, 40)
    mixed = result[15, 12:18]
    assert 40 < mixed[:, 1].max() < 255


def test_a_picture_is_cropped_to_cover_not_stretched():
    picture = Image.new("RGB", (100, 20), (0, 0, 0))
    picture.paste((255, 255, 255), (46, 0, 54, 20))

    fitted = replace_background.cover_fit(picture, (30, 30))

    assert fitted.size == (30, 30)
    assert fitted.getpixel((15, 15))[0] >= 245
    assert fitted.getpixel((2, 15))[0] <= 10


def test_transparent_multiplies_the_alpha_by_one_minus_the_matte(matte_media):
    result = TOOL.image(subject_picture(), {"kind": "transparent", "matte": matte_media})
    alpha = np.asarray(result)[..., 3].astype(int)
    expected = 255 - soft_matte().astype(int)

    assert result.mode == "RGBA"
    assert np.abs(alpha - expected).max() <= 1
    assert alpha[5, 2] == 0 and alpha[5, 30] == 255


def test_transparent_keeps_an_alpha_that_is_already_there(matte_media):
    base = subject_picture().convert("RGBA")
    base.putalpha(128)

    alpha = np.asarray(TOOL.image(base, {"kind": "transparent", "matte": matte_media}))[..., 3]

    assert alpha[5, 30] == 128 and alpha[5, 2] == 0


def test_opacity_scales_the_replacement(matte_media):
    result = np.asarray(
        TOOL.image(subject_picture(), {"kind": "color", "color": "#000000", "matte": matte_media, "opacity": 0.5})
    ).astype(int)

    assert abs(result[5, 2, 0] - 100) <= 1


def test_a_matte_of_another_size_is_resampled(tmp_path):
    path = tmp_path / "small.png"
    Image.fromarray(np.full((3, 4), 255, dtype=np.uint8)).save(path)

    result = np.asarray(TOOL.image(subject_picture(), {"kind": "color", "color": "#123456", "matte": probe(path)}))

    assert (result == (0x12, 0x34, 0x56)).all()


def test_a_missing_matte_or_picture_is_refused(matte_media):
    with pytest.raises(UnsupportedMedia):
        TOOL.image(subject_picture(), {"kind": "color"})
    with pytest.raises(UnsupportedMedia):
        TOOL.image(subject_picture(), {"kind": "picture", "matte": matte_media})


def test_a_bad_kind_or_colour_is_refused(matte_media):
    with pytest.raises(InvalidToolParams):
        TOOL.image(subject_picture(), {"kind": "sky", "matte": matte_media})
    with pytest.raises(InvalidToolParams):
        TOOL.image(subject_picture(), {"kind": "color", "color": "not-a-colour", "matte": matte_media})
