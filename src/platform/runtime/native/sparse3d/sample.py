# Derived from: microsoft/TRELLIS.2 (MIT) — flex_gemm/ops/grid_sample + kernels/cuda/grid_sample/grid_sample.cu
"""Pure-torch ``grid_sample_3d`` over a sparse voxel volume.

Reimplements flex_gemm's ``grid_sample_3d`` — a hashmap lookup of the 8 corner
voxels around each continuous query point — using the linearized-int64-code
``sort``/``searchsorted`` membership test that ``conv.py`` uses in place of the
CUDA hashmap.

Semantics are taken from the CUDA kernel, not from flex_gemm's
``grid_sample_torch.py`` reference (which truncates rather than floors when
locating the corner cell, and so disagrees with the kernel for query points
below half a voxel):

* A voxel at integer coord ``c`` has its **center** at ``c + 0.5`` in query
  space, so the low corner is ``floor(q - 0.5)``.
* A corner outside ``[0, grid_size)`` or absent from the sparse set gets weight
  zero, and the surviving weights are **renormalized to sum to 1**. Sampling
  near the boundary of the active set therefore returns the full-strength
  average of the present neighbors rather than fading toward zero; a point with
  no present neighbor at all returns zeros.
* ``nearest`` truncates toward zero (``static_cast<int>``) rather than flooring,
  matching the kernel.
"""

from __future__ import annotations

from typing import Sequence, Union

import torch

__all__ = ["sparse_grid_sample_3d"]

_CORNER_OFFSETS = torch.tensor(
    [[i & 1, (i >> 1) & 1, (i >> 2) & 1] for i in range(8)],
    dtype=torch.long,
)


def _as_extent(grid_size: Union[int, Sequence[int], torch.Tensor], device: torch.device) -> torch.Tensor:
    if isinstance(grid_size, torch.Tensor):
        return grid_size.to(device=device, dtype=torch.long).reshape(3)
    if isinstance(grid_size, int):
        return torch.tensor([grid_size] * 3, dtype=torch.long, device=device)
    return torch.tensor([int(v) for v in grid_size], dtype=torch.long, device=device)


def _linearize(batch: torch.Tensor, spatial: torch.Tensor, extent: torch.Tensor) -> torch.Tensor:
    w, h, d = int(extent[0]), int(extent[1]), int(extent[2])
    return ((batch * w + spatial[..., 0]) * h + spatial[..., 1]) * d + spatial[..., 2]


def _lookup(query_codes: torch.Tensor, sorted_codes: torch.Tensor, sort_pos: torch.Tensor):
    n = sorted_codes.shape[0]
    pos = torch.searchsorted(sorted_codes, query_codes.reshape(-1).contiguous()).clamp(max=n - 1)
    matched = sorted_codes[pos] == query_codes.reshape(-1)
    idx = torch.where(matched, sort_pos[pos], torch.zeros_like(pos))
    return idx.reshape(query_codes.shape), matched.reshape(query_codes.shape)


def sparse_grid_sample_3d(
    feats: torch.Tensor,
    coords: torch.Tensor,
    grid_size: Union[int, Sequence[int], torch.Tensor],
    grid: torch.Tensor,
    mode: str = "trilinear",
) -> torch.Tensor:
    """
    Args:
        feats: ``[N, C]`` per-voxel features.
        coords: ``[N, 4]`` integer voxel coords, ``(batch, x, y, z)`` — the
            layout ``SparseTensor.coords`` uses.
        grid_size: int or 3-sequence ``(W, H, D)``; corners outside it are
            treated as absent.
        grid: ``[B, L, 3]`` query points in continuous voxel-index space.
        mode: ``"trilinear"`` or ``"nearest"``.

    Returns:
        ``[B, L, C]`` sampled features.
    """
    assert mode in ("trilinear", "nearest"), f"unsupported mode {mode!r}"
    assert feats.dim() == 2, f"feats must be [N, C], got {tuple(feats.shape)}"
    assert coords.dim() == 2 and coords.shape[1] == 4, f"coords must be [N, 4], got {tuple(coords.shape)}"
    assert grid.dim() == 3 and grid.shape[2] == 3, f"grid must be [B, L, 3], got {tuple(grid.shape)}"
    assert feats.shape[0] == coords.shape[0], "feats and coords disagree on N"

    device = grid.device
    b, length = grid.shape[:2]
    channels = feats.shape[1]
    extent = _as_extent(grid_size, device)

    if feats.shape[0] == 0:
        return torch.zeros((b, length, channels), dtype=feats.dtype, device=device)

    coords_long = coords.to(device=device, dtype=torch.long)
    codes = _linearize(coords_long[:, 0], coords_long[:, 1:], extent)
    sorted_codes, sort_pos = torch.sort(codes)

    batch_ids = torch.arange(b, dtype=torch.long, device=device).reshape(b, 1)

    if mode == "nearest":
        cell = grid.to(torch.float32).trunc().to(torch.long)
        in_bounds = ((cell >= 0) & (cell < extent)).all(dim=-1)
        query_codes = _linearize(batch_ids.expand(b, length), cell.clamp_min(0), extent)
        idx, matched = _lookup(query_codes, sorted_codes, sort_pos)
        valid = in_bounds & matched
        return feats[idx] * valid.unsqueeze(-1).to(feats.dtype)

    q = grid.to(torch.float32)
    base = torch.floor(q - 0.5).to(torch.long)
    corners = base.unsqueeze(2) + _CORNER_OFFSETS.to(device)  # [B, L, 8, 3]

    in_bounds = ((corners >= 0) & (corners < extent)).all(dim=-1)  # [B, L, 8]
    query_codes = _linearize(batch_ids.reshape(b, 1, 1).expand(b, length, 8), corners.clamp_min(0), extent)
    idx, matched = _lookup(query_codes, sorted_codes, sort_pos)
    valid = in_bounds & matched

    weight = (1.0 - (q.unsqueeze(2) - corners.to(q.dtype) - 0.5).abs()).prod(dim=-1)
    weight = torch.where(valid, weight, torch.zeros_like(weight))
    weight = weight / weight.sum(dim=-1, keepdim=True).clamp_min(1e-12)

    gathered = feats[idx.reshape(-1)].reshape(b, length, 8, channels)
    return (gathered * weight.unsqueeze(-1).to(gathered.dtype)).sum(dim=2)
