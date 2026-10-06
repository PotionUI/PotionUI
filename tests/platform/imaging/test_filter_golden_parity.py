import json
from pathlib import Path

import numpy as np
import pytest

from src.platform.imaging.filters import (
    CORE_OPS,
    CubeError,
    apply_filter,
    compile_lut,
    grain_cell,
    grain_noise,
    lowbias32,
    parse_cube,
)
from tests.fixtures.filters import generate_golden

GOLDEN = Path(generate_golden.GOLDEN_DIR)


def load(name):
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def image_of(payload):
    return np.array(payload["pixels"], dtype=np.uint8).reshape(payload["height"], payload["width"], 3)


def check_samples(lut, samples):
    assert lut.size == samples["size"]
    for index, expected in zip(samples["indices"], samples["values"]):
        assert np.abs(lut.data[index].astype(np.float64) - np.array(expected)).max() < 1e-4


def colour_only(steps):
    return [s for s in steps if CORE_OPS[s["op"]].kind == "colour"]


def test_committed_goldens_are_what_the_generator_produces(tmp_path):
    generate_golden.write(tmp_path)
    for name in ("ops.json", "filters.json", "spatial.json", "hash.json", "cube.json"):
        assert (tmp_path / name).read_text(encoding="utf-8") == (GOLDEN / name).read_text(encoding="utf-8"), name


def test_catalogue_matches_the_registry():
    assert load("ops.json")["catalogue"] == [spec.to_dict() for spec in CORE_OPS.values()]


@pytest.mark.parametrize("case", load("ops.json")["cases"], ids=lambda c: c["name"])
def test_every_op_case_matches_the_golden(case):
    file = load("ops.json")
    out = apply_filter(image_of(file["image"]), case["steps"], case["intensity"])
    assert np.abs(out.astype(int).reshape(-1) - np.array(case["expected"])).max() <= file["tolerance"]
    if "lut_samples" in case:
        check_samples(compile_lut(case["steps"], None, case["intensity"]), case["lut_samples"])


def test_every_core_op_has_a_golden_case():
    used = {s["op"] for case in load("ops.json")["cases"] for s in case["steps"]}
    assert used == set(CORE_OPS)


@pytest.mark.parametrize("case", load("filters.json")["cases"], ids=lambda c: c["name"])
def test_builtin_filter_matches_the_golden_at_each_intensity(case):
    file = load("filters.json")
    image = image_of(file["image"])
    out = apply_filter(image, case["steps"], case["intensity"])
    assert np.abs(out.astype(int).reshape(-1) - np.array(case["expected"])).max() <= file["tolerance"]
    if case["intensity"] == 0:
        assert np.array_equal(out, image)
    check_samples(compile_lut(colour_only(case["steps"]), None, case["intensity"]), case["lut_samples"])


def test_goldens_cover_twelve_filters_at_three_intensities():
    cases = load("filters.json")["cases"]
    assert len({c["filter"] for c in cases}) == 12
    assert sorted({c["intensity"] for c in cases}) == [0, 50, 100]


@pytest.mark.parametrize("case", load("spatial.json")["cases"], ids=lambda c: c["name"])
def test_spatial_case_matches_the_golden(case):
    image = np.empty((case["height"], case["width"], 3), dtype=np.uint8)
    image[:, :] = case["fill"]
    out = apply_filter(image, case["steps"], case["intensity"])
    crop = case["crop"]
    window = out[crop["y"]:crop["y"] + crop["height"], crop["x"]:crop["x"] + crop["width"]]
    exact = all(step["op"] == "grain" for step in case["steps"])
    assert np.abs(window.astype(int).reshape(-1) - np.array(case["expected"])).max() <= (0 if exact else 1)


def test_grain_hash_vectors_are_exact():
    file = load("hash.json")
    assert [int(v) for v in lowbias32(np.array(file["inputs"], dtype=np.uint32))] == file["hashes"]
    for (cx, cy, seed), expected in zip(file["cells"], file["noise"]):
        assert float(grain_noise(np.array(cx), np.array(cy), seed)) == expected


def test_grain_cell_rounds_half_up():
    assert grain_cell(4, 625, 625) == 3
    assert grain_cell(1, 24, 16) == 1
    assert grain_cell(4, 400, 400) == 2


@pytest.mark.parametrize("case", load("cube.json")["cases"], ids=lambda c: c["name"])
def test_cube_case_matches_the_golden(case):
    file = load("cube.json")
    cube = parse_cube(case["cube"])
    out = apply_filter(image_of(file["image"]), case["steps"], case["intensity"], cube=cube)
    assert np.abs(out.astype(int).reshape(-1) - np.array(case["expected"])).max() <= file["tolerance"]
    check_samples(compile_lut(colour_only(case["steps"]), cube, case["intensity"]), case["lut_samples"])


@pytest.mark.parametrize("case", load("cube.json")["bad"], ids=lambda c: c["name"])
def test_bad_cube_is_rejected_with_a_clear_message(case):
    with pytest.raises(CubeError, match=case["error"]):
        parse_cube(case["cube"])
