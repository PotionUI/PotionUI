from __future__ import annotations

import math
from typing import Callable, Optional, Tuple

import torch

from ...errors import SamplingCancelled

__all__ = ["CHART_AREA_WEIGHT", "CHART_CONE_HALF_ANGLE", "CHART_MAX_FACES", "CHART_PERIMETER_WEIGHT", "compute_charts"]

CHART_CONE_HALF_ANGLE = math.radians(90.0)
CHART_AREA_WEIGHT = 0.1
CHART_PERIMETER_WEIGHT = 1e-4
CHART_MAX_FACES = 2048
_EPS = 1e-20
_DEGENERATE = 1e-6


def _face_frames(vertices: torch.Tensor, faces: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
    tris = vertices[faces]
    cross = torch.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0], dim=-1)
    double_area = cross.norm(dim=-1)
    longest = (tris - tris.roll(-1, dims=1)).pow(2).sum(-1).amax(dim=-1)
    flat = double_area > _DEGENERATE * longest
    normals = torch.where(flat[:, None], cross / double_area.clamp_min(_EPS)[:, None], torch.zeros_like(cross))
    return normals, 0.5 * double_area


def _manifold_adjacency(vertices: torch.Tensor, faces: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    device = faces.device
    src = faces.reshape(-1)
    dst = faces.roll(-1, dims=1).reshape(-1)
    lo = torch.minimum(src, dst)
    hi = torch.maximum(src, dst)
    keys = lo * vertices.shape[0] + hi
    ordered, order = torch.sort(keys, stable=True)
    _, counts = torch.unique_consecutive(ordered, return_counts=True)
    starts = torch.cumsum(counts, 0) - counts
    pairs = starts[counts == 2]
    owner = torch.arange(faces.shape[0], device=device).repeat_interleave(3)
    first = order[pairs]
    second = order[pairs + 1]
    f0 = owner[first]
    f1 = owner[second]
    keep = f0 != f1
    f0, f1, first = f0[keep], f1[keep], first[keep]
    length = (vertices[lo[first]] - vertices[hi[first]]).norm(dim=-1)
    return torch.minimum(f0, f1), torch.maximum(f0, f1), length


def _merge_edges(a: torch.Tensor, b: torch.Tensor, length: torch.Tensor, count: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    keys, inverse = torch.unique(a * count + b, return_inverse=True)
    merged = torch.zeros(keys.shape[0], dtype=length.dtype, device=length.device).index_add_(0, inverse, length)
    return keys // count, keys % count, merged


def _pack(cost: torch.Tensor) -> torch.Tensor:
    bits = cost.to(torch.float32).contiguous().view(torch.int32).to(torch.int64)
    return (bits << 32) | torch.arange(cost.shape[0], dtype=torch.int64, device=cost.device)


def compute_charts(
    vertices: torch.Tensor,
    faces: torch.Tensor,
    cone_half_angle: float = CHART_CONE_HALF_ANGLE,
    area_weight: float = CHART_AREA_WEIGHT,
    perimeter_weight: float = CHART_PERIMETER_WEIGHT,
    max_faces: int = CHART_MAX_FACES,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> torch.Tensor:
    device = faces.device
    face_count = faces.shape[0]
    if face_count == 0:
        return torch.zeros(0, dtype=torch.int64, device=device)
    vertices = vertices.to(torch.float32)
    faces = faces.to(torch.int64)
    normals, areas = _face_frames(vertices, faces)
    flat = normals.abs().sum(-1) > 0
    a, b, length = _manifold_adjacency(vertices, faces)
    charts = torch.arange(face_count, device=device)
    count = face_count
    normal_sum = normals.clone()
    area = areas.to(torch.float64)
    sizes = torch.ones(face_count, dtype=torch.int64, device=device)
    while a.shape[0]:
        if is_cancelled is not None and is_cancelled():
            raise SamplingCancelled()
        a, b, length = _merge_edges(a, b, length, count)
        span = normal_sum.norm(dim=-1)
        oriented = span > _EPS
        axis = normal_sum / span.clamp_min(_EPS)[:, None]
        spread = torch.where(flat, torch.acos(((axis[charts] * normals).sum(-1)).clamp(-1.0, 1.0)), torch.zeros_like(areas))
        half = torch.zeros(count, dtype=spread.dtype, device=device).scatter_reduce_(0, charts, spread, reduce="amax")
        perimeter = torch.zeros(count, dtype=length.dtype, device=device).index_add_(0, a, length).index_add_(0, b, length)
        between = torch.where(oriented[a] & oriented[b], torch.acos(((axis[a] * axis[b]).sum(-1)).clamp(-1.0, 1.0)), torch.zeros_like(length))
        low = torch.minimum(-half[a], between - half[b])
        high = torch.maximum(half[a], between + half[b])
        joined = (area[a] + area[b]).to(torch.float32)
        joined_perimeter = perimeter[a] + perimeter[b] - 2.0 * length
        cost = 0.5 * (high - low) + area_weight * joined + perimeter_weight * (joined_perimeter * joined_perimeter / joined.clamp_min(_EPS))
        cost = torch.where(sizes[a] + sizes[b] > max_faces, torch.full_like(cost, float("inf")), cost)
        packed = _pack(cost)
        best = torch.full((count,), torch.iinfo(torch.int64).max, dtype=torch.int64, device=device)
        best.scatter_reduce_(0, a, packed, reduce="amin").scatter_reduce_(0, b, packed, reduce="amin")
        collapse = (cost <= cone_half_angle) & (best[a] == packed) & (best[b] == packed)
        if not bool(collapse.any()):
            break
        target = torch.arange(count, device=device)
        target[b[collapse]] = a[collapse]
        _, target = torch.unique(target, return_inverse=True)
        count = int(target.max()) + 1
        charts = target[charts]
        normal_sum = torch.zeros((count, 3), dtype=normal_sum.dtype, device=device).index_add_(0, target, normal_sum)
        area = torch.zeros(count, dtype=area.dtype, device=device).index_add_(0, target, area)
        sizes = torch.zeros(count, dtype=sizes.dtype, device=device).index_add_(0, target, sizes)
        a, b = target[a], target[b]
        keep = a != b
        a, b, length = torch.minimum(a[keep], b[keep]), torch.maximum(a[keep], b[keep]), length[keep]
    return charts
