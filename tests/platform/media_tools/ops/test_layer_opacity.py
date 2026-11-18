from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from src.platform.media_tools import UnsupportedMedia, probe
from src.platform.media_tools.ops import paint_layer, place_patch

GOLDEN = Path(__file__).resolve().parents[4] / "frontend/src/lib/tool-tabs/image/layerParity.golden.json"
DATA = json.loads(GOLDEN.read_text())
WIDTH, HEIGHT = DATA["width"], DATA["height"]


def picture(pixels):
    image = Image.new("RGBA", (WIDTH, HEIGHT))
    image.putdata([tuple(pixel) for pixel in pixels])
    return image


def assert_within_one(image, expected):
    got = list(image.convert("RGBA").getdata())
    assert len(got) == len(expected)
    for index, (pixel, want) in enumerate(zip(got, expected)):
        for channel in range(4):
            assert abs(pixel[channel] - want[channel]) <= 1, (index, channel, pixel, want)


@pytest.fixture
def layer_media(tmp_path):
    path = tmp_path / "layer.png"
    picture(DATA["layer"]).save(path)
    return probe(path)


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda case: f"opacity-{case['opacity']}")
def test_paint_layer_matches_the_browser_formula(layer_media, case):
    base = picture(DATA["base"])
    result = paint_layer.PaintLayer().image(base, {"layer": layer_media, "opacity": case["opacity"]})
    assert_within_one(result, case["expected"])


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda case: f"opacity-{case['opacity']}")
def test_place_patch_matches_the_browser_formula(layer_media, case):
    base = picture(DATA["base"])
    params = {"patch": layer_media, "x": 0, "y": 0, "width": 1, "height": 1, "opacity": case["opacity"]}
    result = place_patch.PlacePatch().image(base, params)
    assert_within_one(result, case["expected"])


def test_no_opacity_is_the_old_full_strength_result(layer_media):
    base = picture(DATA["base"])
    old = Image.alpha_composite(base, picture(DATA["layer"]))
    assert list(paint_layer.PaintLayer().image(base, {"layer": layer_media}).getdata()) == list(old.getdata())
    assert list(paint_layer.PaintLayer().image(base, {"layer": layer_media, "opacity": 1}).getdata()) == list(old.getdata())


def test_opacity_is_clamped_and_junk_is_refused(layer_media):
    base = picture(DATA["base"])
    over = paint_layer.PaintLayer().image(base, {"layer": layer_media, "opacity": 7})
    assert list(over.getdata()) == list(paint_layer.PaintLayer().image(base, {"layer": layer_media}).getdata())
    with pytest.raises(UnsupportedMedia):
        paint_layer.PaintLayer().image(base, {"layer": layer_media, "opacity": "lots"})


def test_stacked_paint_layers_match_the_editor(tmp_path):
    stack = DATA["stack"]
    result = picture(DATA["base"])
    for index, (layer, opacity) in enumerate(zip(stack["layers"], stack["opacity"])):
        path = tmp_path / f"layer-{index}.png"
        picture(layer).save(path)
        result = paint_layer.PaintLayer().image(result, {"layer": probe(path), "opacity": opacity})
    assert_within_one(result, stack["expected"])
