from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.platform.media_tools import panels

GOLDENS = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "slice_panel_goldens.json").read_text(encoding="utf-8")
)
CASES = {case["name"]: case for case in GOLDENS["cases"]}


def rasterise(case) -> Image.Image:
    width, height = case["width"], case["height"]
    if case["transparent"]:
        pixels = np.zeros((height, width, 4), dtype=np.uint8)
        for box in case["boxes"]:
            pixels[box["y0"] : box["y1"], box["x0"] : box["x1"]] = [*box["color"], 255]
        return Image.fromarray(pixels, "RGBA")
    pixels = np.empty((height, width, 3), dtype=np.int64)
    pixels[:] = case["background"]
    for box in case["boxes"]:
        pixels[box["y0"] : box["y1"], box["x0"] : box["x1"]] = box["color"]
    noise = case["noise"]
    if noise:
        index = np.arange(width * height * 3, dtype=np.uint64)
        h = (index * np.uint64(2654435761) + np.uint64(noise["seed"] * 97)) & np.uint64(0xFFFFFFFF)
        h = ((h ^ (h >> np.uint64(15))) * np.uint64(2246822519)) & np.uint64(0xFFFFFFFF)
        h = h ^ (h >> np.uint64(13))
        amplitude = noise["amplitude"]
        offsets = (h % np.uint64(2 * amplitude + 1)).astype(np.int64) - amplitude
        pixels = pixels + offsets.reshape(height, width, 3)
    return Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")


@pytest.mark.parametrize("name", list(CASES))
def test_golden_panels_match_the_shared_fixture(name):
    case = CASES[name]
    found = panels.detect(rasterise(case), case["tolerance"])["panels"]
    assert len(found) == len(case["expected"])
    for got, want in zip(found, case["expected"]):
        for key in ("x", "y", "w", "h"):
            assert got[key] == pytest.approx(want[key], abs=case["epsilon"]), (name, key)


def test_detection_is_deterministic():
    image = rasterise(CASES["noisy_background"])
    assert panels.detect(image) == panels.detect(image)


def test_result_reports_source_size_background_and_gutters():
    result = panels.detect(rasterise(CASES["regular_grid_3x2"]))
    assert (result["width"], result["height"]) == (600, 400)
    assert result["background"] == "#f0e6d2"
    inner_x = [band for band in result["gutters"]["x"] if not band["edge"]]
    assert len(inner_x) == 2
    assert inner_x[0]["a"] == pytest.approx(200 / 600) and inner_x[0]["b"] == pytest.approx(220 / 600)


def test_source_size_override_is_reported_for_downscaled_input():
    result = panels.detect(rasterise(CASES["regular_grid_3x2"]), source_size=(6000, 4000))
    assert (result["width"], result["height"]) == (6000, 4000)


def test_a_large_picture_is_analysed_on_a_reduced_copy():
    image = rasterise(CASES["regular_grid_3x2"]).resize((2400, 1600), Image.Resampling.NEAREST)
    found = panels.detect(image)["panels"]
    assert len(found) == 6
    assert found[0]["x"] == pytest.approx(20 / 600, abs=0.01)


def test_a_blank_picture_has_no_panels():
    assert panels.detect(Image.new("RGB", (200, 120), (255, 255, 255)))["panels"] == []


def test_a_transparent_sheet_reports_no_background_colour():
    assert panels.detect(rasterise(CASES["transparent_sheet_dark_panels"]))["background"] is None


def test_grid_cells_snap_to_the_gutters():
    result = panels.detect(rasterise(CASES["uneven_gutters"]), grid=(3, 2))
    cells = result["grid_cells"]
    assert len(cells) == 6
    first_row = [cell for cell in cells if cell["y"] < 0.3]
    assert first_row[0]["x"] + first_row[0]["w"] == pytest.approx(260 / 800, abs=0.004)
    assert first_row[1]["x"] == pytest.approx(278 / 800, abs=0.004)


def test_grid_without_a_matching_gutter_falls_back_to_an_even_line():
    result = panels.detect(rasterise(CASES["wide_merged_panel"]), grid=(2, 1))
    left, right = result["grid_cells"]
    assert left["x"] + left["w"] == pytest.approx(right["x"], abs=0.002)
    assert left["w"] == pytest.approx(0.5, abs=0.03)


def test_a_panel_split_by_a_thin_line_is_merged_back():
    image = Image.new("RGB", (400, 300), (240, 230, 210))
    image.paste((30, 60, 120), (40, 40, 360, 260))
    image.paste((240, 230, 210), (199, 40, 201, 260))
    found = panels.detect(image)["panels"]
    assert len(found) == 1


def test_reading_order_groups_rows_then_orders_by_left():
    image = Image.new("RGB", (600, 400), (240, 230, 210))
    image.paste((30, 60, 120), (320, 20, 580, 190))
    image.paste((30, 60, 120), (20, 26, 300, 190))
    image.paste((30, 60, 120), (20, 220, 580, 380))
    found = panels.detect(image)["panels"]
    assert [round(panel["x"] * 600) for panel in found] == [20, 320, 20]


def test_a_gap_narrower_than_the_minimum_gutter_is_not_a_gutter():
    image = Image.new("RGB", (600, 400), (240, 230, 210))
    image.paste((30, 60, 120), (20, 20, 299, 380))
    image.paste((30, 60, 120), (301, 20, 580, 380))
    analysis = panels.analyse(image)
    inner = [band for band in panels.bands(analysis, "x", (20, 20, 580, 380)) if not band.edge]
    assert analysis.min_gutter > 2
    assert inner == []


def test_a_gap_at_the_minimum_gutter_is_a_gutter():
    image = Image.new("RGB", (600, 400), (240, 230, 210))
    image.paste((30, 60, 120), (20, 20, 290, 380))
    image.paste((30, 60, 120), (300, 20, 580, 380))
    analysis = panels.analyse(image)
    inner = [band for band in panels.bands(analysis, "x", (20, 20, 580, 380)) if not band.edge]
    assert [(band.a, band.b) for band in inner] == [(290, 300)]
