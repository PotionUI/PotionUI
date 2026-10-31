import numpy as np
import pytest

from src.platform.runtime.pose.geometry import fit_transform, map_points, resolve_output_size, snap


def test_snap_rounds_or_floors_to_multiple_and_never_below_one_multiple():
    assert snap(100, 32) == 96
    assert snap(112, 32) == 128
    assert snap(127, 32, floor=True) == 96
    assert snap(3, 32) == 32


def test_explicit_width_and_height_win():
    assert resolve_output_size(1920, 1080, width=960, height=544, max_pixels=1000) == (960, 544)


def test_explicit_width_alone_keeps_the_aspect():
    assert resolve_output_size(1920, 1080, width=640) == (640, 352)


def test_default_follows_the_source_size_snapped():
    assert resolve_output_size(1000, 700) == (992, 704)


def test_max_pixels_caps_the_area_on_the_multiple_grid():
    width, height = resolve_output_size(1920, 1080, max_pixels=522240)
    assert width * height <= 522240
    assert width % 32 == 0 and height % 32 == 0
    assert (width, height) == (960, 544)


@pytest.mark.parametrize("source", [(1920, 1080), (1080, 1920), (1000, 700), (1280, 720), (720, 1280), (1024, 768), (3000, 2000)])
@pytest.mark.parametrize("cap", [100000, 200000, 300000, 522240, 600000, 921600])
def test_max_pixels_is_never_exceeded(source, cap):
    width, height = resolve_output_size(*source, max_pixels=cap)
    assert width * height <= cap
    assert width % 32 == 0 and height % 32 == 0


def test_max_pixels_leaves_a_small_source_alone():
    assert resolve_output_size(640, 480, max_pixels=10**7) == (640, 480)


def test_contain_letterboxes_and_cover_crops():
    scale, ox, oy = fit_transform(200, 100, 100, 100, "contain")
    assert (scale, ox, oy) == (0.5, 0.0, 25.0)
    scale, ox, oy = fit_transform(200, 100, 100, 100, "cover")
    assert (scale, ox, oy) == (1.0, -50.0, 0.0)


def test_unknown_fit_is_rejected():
    with pytest.raises(ValueError):
        fit_transform(10, 10, 10, 10, "stretch")


def test_map_points_scales_and_offsets_xy_only():
    poses = np.array([[[10.0, 20.0, 0.7]]], dtype=np.float32)
    mapped = map_points(poses, 2.0, 3.0, 1.0, -1.0)
    assert mapped[0, 0].tolist() == [21.0, 59.0, pytest.approx(0.7)]
    assert poses[0, 0, 0] == 10.0
