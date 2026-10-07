from __future__ import annotations

from typing import Callable, Optional, Tuple

import torch

from ...errors import SamplingCancelled

__all__ = ["remesh_narrow_band_dc", "point_triangle_distance_sq"]

_EDGE_CORNER = torch.tensor([[0, 1, 1], [1, 0, 1], [1, 1, 0]], dtype=torch.long)
_EDGE_RING = torch.tensor(
    [
        [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]],
        [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
        [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]],
    ],
    dtype=torch.long,
)
_SPLIT_1_N = torch.tensor([0, 1, 2, 0, 2, 3], dtype=torch.long)
_SPLIT_1_P = torch.tensor([0, 2, 1, 0, 3, 2], dtype=torch.long)
_SPLIT_2_N = torch.tensor([0, 1, 3, 3, 1, 2], dtype=torch.long)
_SPLIT_2_P = torch.tensor([0, 3, 1, 3, 2, 1], dtype=torch.long)
PAIR_BUDGET = 1 << 23


def point_triangle_distance_sq(ap: torch.Tensor, ab: torch.Tensor, ac: torch.Tensor) -> torch.Tensor:
    tiny = torch.finfo(ap.dtype).tiny
    aa = (ab * ab).sum(-1)
    cc = (ac * ac).sum(-1)
    bc = (ab * ac).sum(-1)
    d1 = (ab * ap).sum(-1)
    d2 = (ac * ap).sum(-1)
    pp = (ap * ap).sum(-1)

    def quadric(v, w):
        return pp - 2.0 * (v * d1 + w * d2) + v * v * aa + 2.0 * v * w * bc + w * w * cc

    t_ab = (d1 / aa.clamp_min(tiny)).clamp(0.0, 1.0)
    t_ac = (d2 / cc.clamp_min(tiny)).clamp(0.0, 1.0)
    t_bc = ((d2 - d1 - bc + aa) / (aa + cc - 2.0 * bc).clamp_min(tiny)).clamp(0.0, 1.0)
    edges = torch.minimum(
        torch.minimum(quadric(t_ab, torch.zeros_like(t_ab)), quadric(torch.zeros_like(t_ac), t_ac)),
        quadric(1.0 - t_bc, t_bc),
    )
    d3 = d1 - aa
    d4 = d2 - bc
    d5 = d1 - bc
    d6 = d2 - cc
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    total = va + vb + vc
    inside = (va >= 0) & (vb >= 0) & (vc >= 0) & (total > 0)
    safe = torch.where(inside, total, torch.ones_like(total))
    face = quadric(vb / safe, vc / safe)
    return torch.where(inside, torch.minimum(face, edges), edges).clamp_min(0.0)


def _encode(cells: torch.Tensor, extent: int) -> torch.Tensor:
    return (cells[..., 0] * extent + cells[..., 1]) * extent + cells[..., 2]


def _decode(codes: torch.Tensor, extent: int) -> torch.Tensor:
    return torch.stack([codes // (extent * extent), codes // extent % extent, codes % extent], dim=-1)


def _lookup(sorted_codes: torch.Tensor, queries: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
    if sorted_codes.numel() == 0:
        return torch.zeros_like(queries), torch.zeros_like(queries, dtype=torch.bool)
    pos = torch.searchsorted(sorted_codes, queries).clamp_(max=sorted_codes.numel() - 1)
    return pos, sorted_codes[pos] == queries


def _merge_min(codes: torch.Tensor, values: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
    unique, inverse = torch.unique(codes, return_inverse=True)
    reduced = torch.full(unique.shape, float("inf"), dtype=values.dtype, device=values.device)
    reduced.scatter_reduce_(0, inverse, values, reduce="amin")
    return unique, reduced


def _corner_distances(
    tris: torch.Tensor, origin: torch.Tensor, cell: float, resolution: int, reach: float,
    is_cancelled: Optional[Callable[[], bool]],
) -> Tuple[torch.Tensor, torch.Tensor]:
    device = tris.device
    extent = resolution + 1
    lo = torch.ceil((tris.amin(1) - reach - origin) / cell).long().clamp_(0, resolution)
    hi = torch.floor((tris.amax(1) + reach - origin) / cell).long().clamp_(0, resolution)
    span = (hi - lo + 1).clamp_min(1)
    shape_code = (span[:, 0] * (extent + 1) + span[:, 1]) * (extent + 1) + span[:, 2]
    order = torch.argsort(shape_code)
    tris, lo, span, shape_code = tris[order], lo[order], span[order], shape_code[order]
    _, run_lengths = torch.unique_consecutive(shape_code, return_counts=True)
    codes = torch.zeros(0, dtype=torch.long, device=device)
    values = torch.zeros(0, dtype=tris.dtype, device=device)
    pending_codes, pending_values, pending = [], [], 0
    reach_sq = reach * reach
    run_start = 0
    for run_length in run_lengths.tolist():
        run_span = span[run_start].tolist()
        axes = [torch.arange(s, device=device) for s in run_span]
        offsets = torch.stack(torch.meshgrid(*axes, indexing="ij"), -1).reshape(-1, 3)
        per_chunk = max(1, PAIR_BUDGET // offsets.shape[0])
        per_slice = max(1, PAIR_BUDGET // min(per_chunk, run_length))
        for start in range(run_start, run_start + run_length, per_chunk):
            stop = min(run_start + run_length, start + per_chunk)
            a = tris[start:stop, 0]
            ab = (tris[start:stop, 1] - a)[:, None]
            ac = (tris[start:stop, 2] - a)[:, None]
            for first in range(0, offsets.shape[0], per_slice):
                corner = lo[start:stop, None] + offsets[None, first : first + per_slice]
                dist = point_triangle_distance_sq(corner.to(tris.dtype) * cell + origin - a[:, None], ab, ac)
                keep = dist < reach_sq
                if bool(keep.any()):
                    chunk_codes, chunk_values = _merge_min(_encode(corner[keep], extent), dist[keep])
                    pending_codes.append(chunk_codes)
                    pending_values.append(chunk_values)
                    pending += chunk_codes.numel()
                if pending >= max(codes.numel(), PAIR_BUDGET):
                    codes, values = _merge_min(torch.cat([codes, *pending_codes]), torch.cat([values, *pending_values]))
                    pending_codes, pending_values, pending = [], [], 0
                if is_cancelled is not None and is_cancelled():
                    raise SamplingCancelled()
        run_start += run_length
    if pending:
        codes, values = _merge_min(torch.cat([codes, *pending_codes]), torch.cat([values, *pending_values]))
    return codes, values.sqrt()


def _surface_voxels(corner_codes: torch.Tensor, values: torch.Tensor, resolution: int) -> torch.Tensor:
    device = corner_codes.device
    extent = resolution + 1
    negative = _decode(corner_codes[values < 0], extent)
    owners = []
    for k in range(3):
        for step in (-1, 1):
            neighbour = negative.clone()
            neighbour[:, k] += step
            inside = (neighbour[:, k] >= 0) & (neighbour[:, k] <= resolution)
            pos, hit = _lookup(corner_codes, _encode(neighbour.clamp(0, resolution), extent))
            crossing = inside & (~hit | (values[pos] >= 0))
            low = torch.minimum(negative[crossing], neighbour[crossing])
            owner = low - _EDGE_CORNER[k].to(device)
            ring = owner[:, None, :] + _EDGE_RING[k].to(device)[None]
            owners.append(ring.reshape(-1, 3))
    voxels = torch.cat(owners)
    voxels = voxels[((voxels >= 0) & (voxels < resolution)).all(1)]
    return _decode(torch.unique(_encode(voxels, resolution)), resolution)


def remesh_narrow_band_dc(
    vertices: torch.Tensor,
    faces: torch.Tensor,
    center: torch.Tensor,
    scale: float,
    resolution: int,
    band: float = 1.0,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> Tuple[torch.Tensor, torch.Tensor]:
    device = vertices.device
    tris = vertices.to(torch.float32)[faces.to(device=device, dtype=torch.long)]
    center = center.to(device=device, dtype=torch.float32).reshape(3)
    cell = scale / resolution
    origin = center - 0.5 * scale
    eps = band * cell
    empty = (torch.zeros((0, 3), dtype=torch.float32, device=device), torch.zeros((0, 3), dtype=torch.long, device=device))

    corner_codes, distances = _corner_distances(tris, origin, cell, resolution, eps + cell, is_cancelled)
    del tris
    values = distances - eps
    if not bool((values < 0).any()):
        return empty
    coords = _surface_voxels(corner_codes, values, resolution)
    corner_extent = resolution + 1
    far = torch.tensor(cell, dtype=values.dtype, device=device)

    def corner_value(offset: torch.Tensor) -> torch.Tensor:
        pos, hit = _lookup(corner_codes, _encode(coords + offset.to(device), corner_extent))
        return torch.where(hit, values[pos], far)

    total = torch.zeros((coords.shape[0], 3), dtype=torch.float32, device=device)
    count = torch.zeros(coords.shape[0], dtype=torch.float32, device=device)
    direction = torch.zeros((coords.shape[0], 3), dtype=torch.long, device=device)
    base = coords.to(torch.float32)
    for k in range(3):
        others = [j for j in range(3) if j != k]
        for u in (0, 1):
            for v in (0, 1):
                low = torch.zeros(3, dtype=torch.long)
                low[others[0]] = u
                low[others[1]] = v
                high = low.clone()
                high[k] = 1
                v1 = corner_value(low)
                v2 = corner_value(high)
                crossing = (v1 < 0) != (v2 < 0)
                t = torch.where(crossing, -v1 / torch.where(crossing, v2 - v1, torch.ones_like(v1)), torch.zeros_like(v1))
                point = base + low.to(device=device, dtype=torch.float32)
                point[:, k] += t
                total += point * crossing.unsqueeze(1)
                count += crossing
                if u == 1 and v == 1:
                    direction[:, k] = torch.where(
                        (v1 < 0) & (v2 >= 0),
                        torch.ones_like(direction[:, k]),
                        torch.where((v1 >= 0) & (v2 < 0), -torch.ones_like(direction[:, k]), torch.zeros_like(direction[:, k])),
                    )
    dual = torch.where((count > 0).unsqueeze(1), total / count.clamp_min(1).unsqueeze(1), base + 0.5)

    voxel_codes = _encode(coords, resolution + 2)
    ring = _EDGE_RING.to(device)
    owner, axis = (direction != 0).nonzero(as_tuple=True)
    signs = direction[owner, axis]
    pos, hit = _lookup(voxel_codes, _encode(coords[owner][:, None, :] + ring[axis], resolution + 2))
    complete = hit.all(dim=1)
    quads = pos[complete]
    signs = signs[complete]
    if quads.shape[0] == 0:
        return empty

    used, remap = torch.unique(quads.reshape(-1), return_inverse=True)
    quads = remap.reshape(-1, 4)
    mesh_vertices = dual[used] * cell + origin
    positive = (signs == 1).unsqueeze(1)
    attempt_1 = torch.where(positive, quads[:, _SPLIT_1_P.to(device)], quads[:, _SPLIT_1_N.to(device)])
    attempt_2 = torch.where(positive, quads[:, _SPLIT_2_P.to(device)], quads[:, _SPLIT_2_N.to(device)])

    def alignment(attempt: torch.Tensor) -> torch.Tensor:
        p = mesh_vertices[attempt]
        n0 = torch.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0], dim=-1)
        n1 = torch.cross(p[:, 2] - p[:, 1], p[:, 3] - p[:, 1], dim=-1)
        return (n0 * n1).sum(-1).abs()

    triangles = torch.where((alignment(attempt_1) > alignment(attempt_2)).unsqueeze(1), attempt_1, attempt_2)
    return mesh_vertices, triangles.reshape(-1, 3)
