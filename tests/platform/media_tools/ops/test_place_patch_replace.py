from __future__ import annotations

from PIL import Image

from src.platform.media_tools import probe
from src.platform.media_tools.ops import place_patch


def patch_media(tmp_path, size, color):
    path = tmp_path / "patch.png"
    Image.new("RGBA", size, color).save(path)
    return probe(path)


def test_replace_makes_the_patch_the_picture_at_its_own_size(tmp_path):
    base = Image.new("RGB", (4, 3), (10, 20, 30))
    media = patch_media(tmp_path, (16, 12), (200, 100, 50, 255))
    result = place_patch.PlacePatch().image(base, {"patch": media, "replace": True})
    assert result.size == (16, 12)
    assert result.getpixel((5, 5)) == (200, 100, 50)


def test_replace_at_half_opacity_blends_with_the_enlarged_picture(tmp_path):
    base = Image.new("RGB", (4, 3), (0, 0, 0))
    media = patch_media(tmp_path, (8, 6), (200, 200, 200, 255))
    result = place_patch.PlacePatch().image(base, {"patch": media, "replace": True, "opacity": 0.5})
    assert result.size == (8, 6)
    assert all(abs(channel - 100) <= 1 for channel in result.getpixel((3, 3)))


def test_without_replace_the_patch_still_fits_the_picture(tmp_path):
    base = Image.new("RGB", (4, 3), (0, 0, 0))
    media = patch_media(tmp_path, (16, 12), (255, 255, 255, 255))
    result = place_patch.PlacePatch().image(base, {"patch": media, "x": 0, "y": 0, "width": 1, "height": 1})
    assert result.size == (4, 3)
