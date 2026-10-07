from __future__ import annotations

import math

import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.arch.pixal3d.projection import (
    average,
    dense_grid_coords,
    distance_from_fov,
    front_camera,
    grid_to_world,
    orbit_camera,
    project_to_image,
    projection_grid,
    relative_cameras,
    sample_bilinear,
    to_sample_grid,
)

FOV = math.radians(49.13)
SIZE = 512

_RIG_DISTANCE = 3.1192049980163574
_RIG = {
    0.0: [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, -1.0, -_RIG_DISTANCE], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
    90.0: [[0.0, 0.0, 1.0, _RIG_DISTANCE], [1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
    180.0: [[-1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, _RIG_DISTANCE], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
    270.0: [[0.0, 0.0, -1.0, -_RIG_DISTANCE], [-1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
}


def _pixels(coords, resolution=5, fov=FOV, size=SIZE):
    camera = front_camera(distance_from_fov(fov))
    return project_to_image(grid_to_world(torch.tensor(coords), resolution), camera, fov, size)


def test_distance_puts_the_unit_cube_centre_slice_across_the_image_width():
    assert distance_from_fov(FOV) == pytest.approx(0.5 / math.tan(FOV / 2))
    assert distance_from_fov(math.radians(20.0)) == pytest.approx(2.835641, abs=1e-6)


def test_grid_indices_use_the_linspace_convention_not_cell_centres():
    world = grid_to_world(torch.tensor([[0, 0, 0], [4, 4, 4], [2, 2, 2]]), 5)
    assert torch.allclose(world[0], torch.tensor([-0.5, 0.5, -0.5]))
    assert torch.allclose(world[1], torch.tensor([0.5, -0.5, 0.5]))
    assert torch.allclose(world[2], torch.zeros(3))


def test_the_grid_centre_lands_on_the_image_centre():
    assert torch.allclose(_pixels([[2, 2, 2]]), torch.tensor([[SIZE / 2, SIZE / 2]]), atol=1e-3)


def test_the_centre_slice_edges_land_on_the_image_edges():
    pixels = _pixels([[0, 2, 2], [4, 2, 2], [2, 4, 2], [2, 0, 2]])
    assert torch.allclose(pixels[0], torch.tensor([0.0, SIZE / 2]), atol=1e-3)
    assert torch.allclose(pixels[1], torch.tensor([float(SIZE), SIZE / 2]), atol=1e-3)
    assert torch.allclose(pixels[2], torch.tensor([SIZE / 2, 0.0]), atol=1e-3)
    assert torch.allclose(pixels[3], torch.tensor([SIZE / 2, float(SIZE)]), atol=1e-3)


def test_grid_z_points_toward_the_camera_so_nearer_voxels_spread_wider():
    near, far = _pixels([[4, 2, 4], [4, 2, 0]])
    assert near[0] > SIZE
    assert SIZE / 2 < far[0] < SIZE


def test_a_known_off_axis_point_projects_by_the_pinhole_formula():
    fov = math.radians(30.0)
    distance = distance_from_fov(fov)
    pixel = _pixels([[3, 1, 4]], fov=fov)[0]
    x_world, y_world, z_world = 0.25, -0.5, -0.25
    depth = distance + y_world
    focal = 16 / math.tan(fov / 2) * SIZE / 32
    assert pixel[0] == pytest.approx(focal * x_world / depth + SIZE / 2, abs=1e-3)
    assert pixel[1] == pytest.approx(-focal * z_world / depth + SIZE / 2, abs=1e-3)


def test_pixels_normalise_with_the_half_pixel_offset_upstream_uses():
    grid = to_sample_grid(torch.tensor([[0.0, SIZE / 2]]), SIZE)
    assert torch.allclose(grid, torch.tensor([[0.5 / SIZE * 2 - 1, 1.0 / SIZE]]))


def test_bilinear_sampling_matches_grid_sample_with_border_padding():
    generator = torch.Generator().manual_seed(0)
    feature_map = torch.randn(7, 5, 6, generator=generator, dtype=torch.float64)
    grid = torch.rand(200, 2, generator=generator, dtype=torch.float64) * 2.6 - 1.3
    grid[:4] = torch.tensor([[-1.0, -1.0], [1.0, 1.0], [-1.0, 1.0], [1.0, -1.0]], dtype=torch.float64)
    expected = F.grid_sample(
        feature_map.permute(2, 0, 1).unsqueeze(0), grid.view(1, -1, 1, 2),
        mode="bilinear", padding_mode="border", align_corners=False,
    )[0, :, :, 0].T
    actual = sample_bilinear(feature_map, grid)
    assert torch.allclose(actual.double(), expected, atol=1e-6)


def test_bilinear_sampling_reads_low_precision_maps_in_fp32():
    feature_map = torch.randn(4, 4, 3).to(torch.bfloat16)
    out = sample_bilinear(feature_map, torch.zeros(2, 2))
    assert out.dtype == torch.float32


@pytest.mark.parametrize("azimuth", sorted(_RIG))
def test_the_orbit_rig_reproduces_upstreams_example_transforms(azimuth):
    camera = orbit_camera(azimuth, 0.0, _RIG_DISTANCE)
    assert torch.allclose(camera, torch.tensor(_RIG[azimuth]), atol=1e-6)


def test_the_front_of_the_orbit_is_the_canonical_front_camera():
    assert torch.allclose(orbit_camera(0.0, 0.0, 2.5), front_camera(2.5), atol=1e-6)


def test_relative_cameras_snap_view_zero_onto_the_front_camera():
    rotated = orbit_camera(30.0, 10.0, 2.0)
    side = orbit_camera(120.0, 10.0, 2.0)
    relative = relative_cameras(torch.stack([rotated, side]), 2.0)
    assert torch.allclose(relative[0], front_camera(2.0), atol=1e-6)
    expected = front_camera(2.0).double() @ torch.linalg.inv(rotated.double()) @ side.double()
    assert torch.allclose(relative[1].double(), expected, atol=1e-6)


def test_relative_cameras_leave_a_front_anchored_rig_unchanged():
    rig = torch.stack([orbit_camera(a, 0.0, _RIG_DISTANCE) for a in sorted(_RIG)])
    assert torch.allclose(relative_cameras(rig, _RIG_DISTANCE), rig, atol=1e-5)


def test_dense_coords_follow_the_row_major_flattening_of_the_latent():
    coords = dense_grid_coords(3)
    flat = torch.arange(27).reshape(3, 3, 3)
    assert coords.shape == (27, 3)
    for row, (x, y, z) in enumerate(coords.tolist()):
        assert flat[x, y, z] == row


def test_projecting_tokens_equals_projecting_the_dense_grid_then_gathering_rows():
    resolution = 6
    generator = torch.Generator().manual_seed(2)
    feature_map = torch.randn(8, 8, 4, generator=generator)
    camera = front_camera(distance_from_fov(FOV))
    dense = sample_bilinear(feature_map, projection_grid(dense_grid_coords(resolution), resolution, camera, FOV, 128))
    dense = dense.reshape(resolution, resolution, resolution, -1)
    tokens = torch.randint(0, resolution, (40, 3), generator=generator)
    sparse = sample_bilinear(feature_map, projection_grid(tokens, resolution, camera, FOV, 128))
    assert torch.allclose(sparse, dense[tokens[:, 0], tokens[:, 1], tokens[:, 2]], atol=1e-6)


def test_averaging_views_is_the_mean_in_fp32():
    views = [torch.full((3, 2), 1.0, dtype=torch.bfloat16), torch.full((3, 2), 2.0), torch.full((3, 2), 6.0)]
    out = average(views)
    assert out.dtype == torch.float32
    assert torch.allclose(out, torch.full((3, 2), 3.0))
