# Derived from: microsoft/TRELLIS.2 (MIT) — o-voxel/o_voxel/convert/flexible_dual_grid.py
"""Pure-torch ``flexible_dual_grid_to_mesh``: voxels + decoder-head outputs (dual
vertex offset, per-axis edge-intersection flag, quad diagonal-split weight) to a
triangle mesh.

Replaces upstream's two CUDA hashmap kernels (``hashmap_insert_3d_idx_as_val_cuda``,
``hashmap_lookup_3d_cuda`` — a coord -> row-index map) with linearized int64 codes
plus ``torch.sort``/``torch.searchsorted``, the idiom used by
``src/platform/runtime/native/sparse3d/conv.py``. Everything else — dual-vertex
placement, per-axis edge-neighbor quad assembly, the quad -> two-triangle diagonal
split, and aabb/grid_size scaling to world space — is ported as-is. Only the
``train=False`` (non-differentiable) path is implemented.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple, Union

import torch

__all__ = ["flexible_dual_grid_to_mesh"]

_EDGE_NEIGHBOR_OFFSET = torch.tensor(
    [
        [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]],
        [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
        [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]],
    ],
    dtype=torch.long,
)

_QUAD_SPLIT_1 = torch.tensor([0, 1, 2, 0, 2, 3], dtype=torch.long)
_QUAD_SPLIT_2 = torch.tensor([0, 1, 3, 3, 1, 2], dtype=torch.long)


def _as_int_triple(value: Union[int, Sequence[int], torch.Tensor], device: torch.device) -> torch.Tensor:
    if isinstance(value, torch.Tensor):
        return value.to(device=device, dtype=torch.long).reshape(3)
    if isinstance(value, int):
        return torch.tensor([value, value, value], dtype=torch.long, device=device)
    return torch.tensor(list(value), dtype=torch.long, device=device)


def _as_aabb(aabb: Union[Sequence, torch.Tensor], device: torch.device) -> torch.Tensor:
    if isinstance(aabb, torch.Tensor):
        return aabb.to(device=device, dtype=torch.float32)
    return torch.tensor(aabb, dtype=torch.float32, device=device)


def _linearize(coords: torch.Tensor, extent: torch.Tensor) -> torch.Tensor:
    ex, ey, ez = int(extent[0]), int(extent[1]), int(extent[2])
    return (coords[:, 0] * ey + coords[:, 1]) * ez + coords[:, 2]


def _lookup(
    query_codes: torch.Tensor, sorted_codes: torch.Tensor, sort_pos: torch.Tensor, n: int
) -> Tuple[torch.Tensor, torch.Tensor]:
    if n == 0:
        idx = torch.zeros_like(query_codes)
        valid = torch.zeros_like(query_codes, dtype=torch.bool)
        return idx, valid
    pos = torch.searchsorted(sorted_codes, query_codes).clamp(max=n - 1)
    matched = sorted_codes[pos] == query_codes
    idx = torch.where(matched, sort_pos[pos], torch.zeros_like(pos))
    return idx, matched


def flexible_dual_grid_to_mesh(
    coords: torch.Tensor,
    vertices: torch.Tensor,
    intersected: torch.Tensor,
    quad_lerp: Optional[torch.Tensor],
    aabb: Union[Sequence, torch.Tensor],
    grid_size: Union[int, Sequence[int], torch.Tensor],
    train: bool = False,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Args:
        coords: ``[N, 3]`` integer voxel coordinates, no batch prefix. The
            decoder call site (``fdg_vae.py``'s ``FlexiDualGridVaeDecoder.forward``)
            unbinds a batched ``SparseTensor`` per sample and passes
            ``v.coords[:, 1:]`` — i.e. the batch column is already stripped, one
            call per mesh.
        vertices: ``[N, 3]`` per-voxel dual vertex offset (upstream's
            ``dual_vertices``).
        intersected: ``[N, 3]`` bool, per-axis (x, y, z) edge-intersection flag.
        quad_lerp: ``[N, 1]`` diagonal-split weight (upstream's ``split_weight``),
            or ``None`` to fall back to the normal-alignment heuristic.
        aabb: ``[2, 3]`` world-space bounding box (min, max rows).
        grid_size: int or 3-sequence, voxel grid resolution per axis.
        train: only ``False`` (the differentiable branch is unported).

    Returns:
        mesh_vertices: ``[N, 3]`` float, world-space dual vertex positions (one
            per input voxel, whether or not any face references it).
        mesh_triangles: ``[F, 3]`` long, triangle vertex indices into
            ``mesh_vertices``.
    """
    assert not train, "train=True (differentiable) path is not ported"
    device = coords.device
    n = coords.shape[0]

    aabb_t = _as_aabb(aabb, device)
    grid_size_t = _as_int_triple(grid_size, device)
    voxel_size = (aabb_t[1] - aabb_t[0]) / grid_size_t.to(torch.float32)

    coords_long = coords.to(torch.long)
    mesh_vertices = (coords_long.to(torch.float32) + vertices) * voxel_size + aabb_t[0].reshape(1, 3)

    if n == 0:
        return mesh_vertices, torch.zeros((0, 3), dtype=torch.long, device=device)

    extent = grid_size_t + 1
    codes = _linearize(coords_long, extent)
    sorted_codes, sort_pos = torch.sort(codes)

    offset = _EDGE_NEIGHBOR_OFFSET.to(device=device)
    edge_neighbor_voxel = coords_long.reshape(n, 1, 1, 3) + offset.unsqueeze(0)  # (N, 3, 4, 3)
    connected_voxel = edge_neighbor_voxel[intersected.to(torch.bool)]  # (M, 4, 3)
    m = connected_voxel.shape[0]
    if m == 0:
        return mesh_vertices, torch.zeros((0, 3), dtype=torch.long, device=device)

    query_codes = _linearize(connected_voxel.reshape(-1, 3), extent)
    row_idx, matched = _lookup(query_codes, sorted_codes, sort_pos, n)
    row_idx = row_idx.reshape(m, 4)
    matched = matched.reshape(m, 4)
    quad_indices = row_idx[matched.all(dim=1)]
    if quad_indices.shape[0] == 0:
        return mesh_vertices, torch.zeros((0, 3), dtype=torch.long, device=device)

    quad_split_1 = _QUAD_SPLIT_1.to(device=device)
    quad_split_2 = _QUAD_SPLIT_2.to(device=device)

    if quad_lerp is None:
        attempt0 = quad_indices[:, quad_split_1]
        normal0 = torch.cross(
            mesh_vertices[attempt0[:, 1]] - mesh_vertices[attempt0[:, 0]],
            mesh_vertices[attempt0[:, 2]] - mesh_vertices[attempt0[:, 0]],
            dim=-1,
        )
        normal1 = torch.cross(
            mesh_vertices[attempt0[:, 2]] - mesh_vertices[attempt0[:, 1]],
            mesh_vertices[attempt0[:, 3]] - mesh_vertices[attempt0[:, 1]],
            dim=-1,
        )
        align0 = (normal0 * normal1).sum(dim=1, keepdim=True).abs()

        attempt1 = quad_indices[:, quad_split_2]
        normal0 = torch.cross(
            mesh_vertices[attempt1[:, 1]] - mesh_vertices[attempt1[:, 0]],
            mesh_vertices[attempt1[:, 2]] - mesh_vertices[attempt1[:, 0]],
            dim=-1,
        )
        normal1 = torch.cross(
            mesh_vertices[attempt1[:, 2]] - mesh_vertices[attempt1[:, 1]],
            mesh_vertices[attempt1[:, 3]] - mesh_vertices[attempt1[:, 1]],
            dim=-1,
        )
        align1 = (normal0 * normal1).sum(dim=1, keepdim=True).abs()

        mesh_triangles = torch.where(align0 > align1, attempt0, attempt1).reshape(-1, 3)
    else:
        split_weight = quad_lerp[quad_indices]
        weight_02 = split_weight[:, 0] * split_weight[:, 2]
        weight_13 = split_weight[:, 1] * split_weight[:, 3]
        mesh_triangles = torch.where(
            weight_02 > weight_13,
            quad_indices[:, quad_split_1],
            quad_indices[:, quad_split_2],
        ).reshape(-1, 3)

    return mesh_vertices, mesh_triangles
