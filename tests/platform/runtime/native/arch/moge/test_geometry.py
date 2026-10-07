from __future__ import annotations

import math

import pytest
import torch

from src.platform.runtime.native.arch.moge.geometry import fov_from_focal, normalized_view_plane_uv, recover_focal


def _pinhole_points(height: int, width: int, fov_x_deg: float, shift: float = 0.7) -> tuple[torch.Tensor, float]:
    aspect = width / height
    diagonal = (1 + aspect ** 2) ** 0.5
    fx = 0.5 / math.tan(math.radians(fov_x_deg) / 2)
    focal = fx * 2 * aspect / diagonal
    uv = normalized_view_plane_uv(width, height)
    depth = 2.0 + 0.4 * uv[..., 0] - 0.3 * uv[..., 1] + 0.05 * torch.sin(9 * uv[..., 0])
    xy = uv * depth[..., None] / focal
    return torch.cat([xy, (depth - shift)[..., None]], dim=-1)[None], focal


def test_a_square_focal_of_root_two_is_a_53_13_degree_fov():
    fov_x, fov_y = fov_from_focal(math.sqrt(2.0), 1.0)
    assert fov_x == pytest.approx(53.13010235415598, abs=1e-9)
    assert fov_y == pytest.approx(53.13010235415598, abs=1e-9)


def test_a_two_to_one_image_splits_into_90_horizontal_and_53_13_vertical():
    fov_x, fov_y = fov_from_focal(2.0 / math.sqrt(5.0), 2.0)
    assert fov_x == pytest.approx(90.0, abs=1e-9)
    assert fov_y == pytest.approx(53.13010235415598, abs=1e-9)


def test_the_uv_grid_spans_pixel_centres_out_to_the_half_diagonal():
    uv = normalized_view_plane_uv(4, 2)
    assert uv.shape == (2, 4, 2)
    span_x, span_y = 2 / math.sqrt(5), 1 / math.sqrt(5)
    assert uv[0, 0].tolist() == pytest.approx([-span_x * 3 / 4, -span_y / 2])
    assert uv[1, 3].tolist() == pytest.approx([span_x * 3 / 4, span_y / 2])
    assert torch.equal(uv[0, :, 0], uv[1, :, 0])
    assert torch.equal(uv[:, 0, 1], uv[:, 2, 1])


@pytest.mark.parametrize("size,fov", [((256, 256), 40.0), ((192, 320), 65.0), ((320, 192), 25.0)])
def test_the_focal_is_recovered_from_a_shifted_pinhole_point_map(size, fov):
    height, width = size
    points, focal = _pinhole_points(height, width, fov)
    mask = torch.ones(1, height, width, dtype=torch.bool)
    [recovered] = recover_focal(points, mask)
    assert recovered == pytest.approx(focal, rel=1e-5)
    assert fov_from_focal(recovered, width / height)[0] == pytest.approx(fov, abs=1e-3)


def test_masked_out_points_do_not_pull_the_solution():
    points, focal = _pinhole_points(128, 128, 40.0)
    mask = torch.ones(1, 128, 128, dtype=torch.bool)
    mask[:, :40] = False
    points[:, :40] = torch.rand(1, 40, 128, 3) * 50
    [recovered] = recover_focal(points, mask)
    assert recovered == pytest.approx(focal, rel=1e-5)


def test_non_finite_points_are_left_out():
    points, focal = _pinhole_points(128, 128, 30.0)
    points[:, :8, :8] = float("inf")
    [recovered] = recover_focal(points)
    assert recovered == pytest.approx(focal, rel=1e-5)


def test_a_mask_with_fewer_than_two_points_recovers_nothing():
    points, _ = _pinhole_points(64, 64, 40.0)
    assert recover_focal(points, torch.zeros(1, 64, 64, dtype=torch.bool)) == [None]


def test_a_point_map_behind_the_camera_recovers_nothing():
    points, _ = _pinhole_points(64, 64, 40.0)
    points[..., :2] = -points[..., :2]
    assert recover_focal(points) == [None]


def test_a_batch_recovers_one_focal_per_image():
    narrow, narrow_focal = _pinhole_points(96, 96, 20.0)
    wide, wide_focal = _pinhole_points(96, 96, 70.0)
    recovered = recover_focal(torch.cat([narrow, wide]))
    assert recovered == [pytest.approx(narrow_focal, rel=1e-5), pytest.approx(wide_focal, rel=1e-5)]
