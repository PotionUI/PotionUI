from __future__ import annotations

import math
from typing import Sequence

import torch

__all__ = [
    "GRID_TO_WORLD",
    "average",
    "dense_grid_coords",
    "distance_from_fov",
    "front_camera",
    "grid_to_world",
    "orbit_camera",
    "project_to_image",
    "projection_grid",
    "relative_cameras",
    "sample_bilinear",
    "to_sample_grid",
]

GRID_TO_WORLD = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))

_SENSOR_WIDTH_MM = 32.0
_DEPTH_EPS = 1e-8


def distance_from_fov(fov: float) -> float:
    return 0.5 / math.tan(fov / 2.0)


def front_camera(distance: float, *, dtype: torch.dtype = torch.float32, device=None) -> torch.Tensor:
    return torch.tensor(
        [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, -1.0, -float(distance)],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=dtype,
        device=device,
    )


def orbit_camera(
    azimuth_deg: float, elevation_deg: float, distance: float, *, dtype: torch.dtype = torch.float32
) -> torch.Tensor:
    azimuth = math.radians(azimuth_deg)
    elevation = math.radians(elevation_deg)
    back = torch.tensor(
        [math.sin(azimuth) * math.cos(elevation), -math.cos(azimuth) * math.cos(elevation), math.sin(elevation)],
        dtype=torch.float64,
    )
    right = torch.tensor([math.cos(azimuth), math.sin(azimuth), 0.0], dtype=torch.float64)
    up = torch.linalg.cross(back, right)
    c2w = torch.eye(4, dtype=torch.float64)
    c2w[:3, :3] = torch.stack([right, up, back], dim=-1)
    c2w[:3, 3] = back * float(distance)
    return c2w.to(dtype)


def relative_cameras(cameras: torch.Tensor, distance: float) -> torch.Tensor:
    cameras = cameras.double()
    front = front_camera(distance, dtype=torch.float64, device=cameras.device)
    relative = torch.linalg.inv(cameras[0]) @ cameras
    return (front @ relative).float()


def dense_grid_coords(resolution: int, device=None) -> torch.Tensor:
    axis = torch.arange(resolution, device=device)
    grid = torch.meshgrid(axis, axis, axis, indexing="ij")
    return torch.stack(grid, dim=-1).reshape(-1, 3)


def grid_to_world(coords: torch.Tensor, resolution: int, mesh_scale: float = 1.0) -> torch.Tensor:
    grid = coords.to(torch.float32) * (2.0 / (resolution - 1)) - 1.0
    rotation = torch.tensor(GRID_TO_WORLD, dtype=grid.dtype, device=grid.device)
    return grid @ rotation.T / mesh_scale / 2


def project_to_image(points: torch.Tensor, camera: torch.Tensor, fov: float, image_size: int) -> torch.Tensor:
    homogeneous = torch.cat([points, torch.ones_like(points[:, :1])], dim=-1)
    world_to_camera = torch.linalg.inv(camera.to(device=points.device, dtype=torch.float32))
    in_camera = homogeneous @ world_to_camera.T
    focal = 16.0 / math.tan(fov / 2.0) * image_size / _SENSOR_WIDTH_MM
    depth = -in_camera[:, 2] + _DEPTH_EPS
    x = focal * in_camera[:, 0] / depth + image_size / 2.0
    y = -focal * in_camera[:, 1] / depth + image_size / 2.0
    return torch.stack([x, y], dim=-1)


def to_sample_grid(pixels: torch.Tensor, image_size: int) -> torch.Tensor:
    return (pixels + 0.5) / image_size * 2 - 1


def projection_grid(
    coords: torch.Tensor, resolution: int, camera: torch.Tensor, fov: float, image_size: int
) -> torch.Tensor:
    return to_sample_grid(project_to_image(grid_to_world(coords, resolution), camera, fov, image_size), image_size)


def sample_bilinear(feature_map: torch.Tensor, grid: torch.Tensor) -> torch.Tensor:
    height, width, channels = feature_map.shape
    flat = feature_map.reshape(height * width, channels)
    x = (((grid[:, 0].float() + 1) * width - 1) / 2).clamp(0, width - 1)
    y = (((grid[:, 1].float() + 1) * height - 1) / 2).clamp(0, height - 1)
    x0 = x.floor()
    y0 = y.floor()
    wx = (x - x0).unsqueeze(-1)
    wy = (y - y0).unsqueeze(-1)
    x0 = x0.long()
    y0 = y0.long()
    x1 = (x0 + 1).clamp(max=width - 1)
    y1 = (y0 + 1).clamp(max=height - 1)
    out = flat[y0 * width + x0].float() * ((1 - wx) * (1 - wy))
    out += flat[y0 * width + x1].float() * (wx * (1 - wy))
    out += flat[y1 * width + x0].float() * ((1 - wx) * wy)
    out += flat[y1 * width + x1].float() * (wx * wy)
    return out


def average(views: Sequence[torch.Tensor]) -> torch.Tensor:
    total = views[0].float().clone()
    for view in views[1:]:
        total += view.float()
    return total / len(views)
