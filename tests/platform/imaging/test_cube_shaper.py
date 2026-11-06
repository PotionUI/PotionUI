from __future__ import annotations

import numpy as np
import pytest

from src.platform.imaging.filters.cube import (
    MAX_CUBE_BYTES,
    MAX_HEADER_LINES,
    MAX_LINE_CHARS,
    MAX_SHAPER_SIZE,
    MAX_TITLE_CHARS,
    CubeError,
    parse_cube,
)
from src.platform.imaging.filters.lut import apply_cube, bake_cube, lattice, mix_cube, serialize_cube


def rows(data) -> str:
    return "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in data)


def identity(size: int) -> str:
    return f"LUT_3D_SIZE {size}\n{rows(lattice(size))}\n"


def shaped(shaper_size=16, size=5, power=0.5) -> str:
    x = np.linspace(0, 1, shaper_size)
    shaper = np.stack([x ** power] * 3, 1)
    grid = lattice(size)
    return (
        f"LUT_1D_SIZE {shaper_size}\nLUT_1D_INPUT_RANGE 0 1\nLUT_3D_SIZE {size}\nLUT_3D_INPUT_RANGE 0 1\n"
        f"{rows(shaper)}\n{rows(grid ** 2)}\n"
    )


@pytest.mark.parametrize("size", [17, 33, 65])
def test_the_common_sizes_parse_and_bake_to_themselves(size):
    cube = parse_cube(identity(size))

    baked = bake_cube(cube)

    assert cube.size == size and cube.data.shape == (size ** 3, 3)
    assert baked.size == size and np.allclose(baked.data, lattice(size), atol=1e-6)


def test_a_shaper_is_refused_unless_asked_for():
    with pytest.raises(CubeError, match="1D LUTs are not supported"):
        parse_cube(shaped())
    with pytest.raises(CubeError, match="LUT_3D_INPUT_RANGE is not supported"):
        parse_cube("LUT_3D_SIZE 2\nLUT_3D_INPUT_RANGE 0 1\n" + rows(lattice(2)))


def test_a_shaper_is_read_and_baked_into_the_3d_table():
    cube = parse_cube(shaped(), allow_shaper=True)

    baked = bake_cube(cube)

    assert cube.shaper.shape == (16, 3) and cube.data.shape == (125, 3)
    assert baked.size == 33 and baked.shaper is None
    grid = lattice(33)
    assert np.allclose(baked.data, grid, atol=0.03)


def test_a_one_dimensional_lut_alone_is_refused():
    x = np.linspace(0, 1, 8)
    with pytest.raises(CubeError, match="1D-only"):
        parse_cube(f"LUT_1D_SIZE 8\n{rows(np.stack([x] * 3, 1))}\n", allow_shaper=True)


@pytest.mark.parametrize(
    "text, message",
    [
        ("LUT_3D_SIZE 1\n0 0 0\n", "outside"),
        ("LUT_3D_SIZE 66\n", "outside"),
        ("LUT_3D_SIZE 9999999\n", "one whole number"),
        ("LUT_3D_SIZE 2\n" + "nan 0 0\n" + rows(lattice(2)[1:]), "'nan'"),
        ("LUT_3D_SIZE 2\n" + "0 1e999 0\n" + rows(lattice(2)[1:]), "finite"),
        ("LUT_3D_SIZE 2\n" + "0 0 -1e999\n" + rows(lattice(2)[1:]), "finite"),
        ("LUT_3D_SIZE 2\n" + rows(lattice(2)) + "\nTITLE \"late\"\n", "after the data"),
        ("TITLE \"" + "x" * 300 + "\"\nLUT_3D_SIZE 2\n" + rows(lattice(2)), "TITLE is longer"),
        ("# " + "x" * 2000 + "\nLUT_3D_SIZE 2\n" + rows(lattice(2)), "longer than"),
        ("DOMAIN_MIN 0 0 0\n" * 65 + "LUT_3D_SIZE 2\n" + rows(lattice(2)), "header lines"),
        ("LUT_1D_SIZE 70000\nLUT_3D_SIZE 2\n", "outside"),
        ("LUT_1D_INPUT_RANGE 1 0\nLUT_3D_SIZE 2\n", "must rise"),
        ("LUT_3D_SIZE 2\n" + rows(lattice(2)[1:]), "expected 8 data rows"),
    ],
)
def test_bad_or_hostile_files_are_refused_with_a_reason(text, message):
    with pytest.raises(CubeError, match=message):
        parse_cube(text, allow_shaper=True)


TABLE = "LUT_3D_SIZE 2\n" + rows(lattice(2))


def with_shaper(size: int) -> str:
    x = np.linspace(0, 1, size)
    return f"LUT_1D_SIZE {size}\nLUT_3D_SIZE 2\n{rows(np.stack([x] * 3, 1))}\n{rows(lattice(2))}"


@pytest.mark.parametrize(
    "build, cap, message",
    [
        (lambda n: "#" * n + "\n" + TABLE, MAX_LINE_CHARS, "longer than"),
        (lambda n: 'TITLE "' + "t" * n + '"\n' + TABLE, MAX_TITLE_CHARS, "TITLE is longer"),
        (lambda n: "DOMAIN_MIN 0 0 0\n" * (n - 1) + TABLE, MAX_HEADER_LINES, "header lines"),
        (with_shaper, MAX_SHAPER_SIZE, "outside"),
    ],
    ids=["line", "title", "headers", "shaper"],
)
def test_each_parser_cap_takes_its_limit_and_refuses_one_more(build, cap, message):
    assert parse_cube(build(cap), allow_shaper=True).size == 2
    with pytest.raises(CubeError, match=message):
        parse_cube(build(cap + 1), allow_shaper=True)


def test_a_file_over_the_size_limit_is_refused_before_it_is_read():
    with pytest.raises(CubeError, match="larger than"):
        parse_cube("#" * (MAX_CUBE_BYTES + 1), allow_shaper=True)


def test_mixing_moves_the_table_towards_identity_and_serialising_round_trips():
    grid = lattice(5)
    cube = parse_cube(f"LUT_3D_SIZE 5\n{rows(1 - grid)}\n")

    half = mix_cube(cube, 0.5)
    again = parse_cube(serialize_cube(half))

    assert np.allclose(half.data, np.full_like(grid, 0.5), atol=1e-6)
    assert np.allclose(again.data, half.data, atol=1e-6)


def test_applying_a_cube_truncates_like_ffmpeg_and_the_identity_keeps_pixels():
    pixels = np.arange(256, dtype=np.uint8).repeat(3).reshape(16, 16, 3)
    invert = parse_cube(f"LUT_3D_SIZE 2\n{rows(1 - lattice(2))}\n")

    assert np.abs(apply_cube(pixels, parse_cube(identity(17))).astype(int) - pixels).max() <= 1
    assert np.abs(apply_cube(pixels, invert).astype(int) - (255 - pixels.astype(int))).max() <= 1
