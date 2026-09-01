# Derived from: microsoft/TRELLIS.2 (MIT) — o-voxel/o_voxel/postprocess.py (nvdiffrast UV-space bake pass)
"""Pure-torch rasterization of a UV atlas, replacing ``nvdiffrast.torch``.

Upstream renders the unwrapped mesh in UV space (``uv * 2 - 1`` as clip-space
xy) to a ``texture_size`` square, then interpolates vertex attributes at every
covered texel. That is all this does, without a GPU rasterizer: for every
triangle, walk the texels its UV bounding box covers, keep the ones whose center
falls inside, and record the winning triangle plus its barycentric weights.

Deliberate differences from the nvdiffrast pass:

* **Row order.** nvdiffrast writes row 0 at the bottom of the image, which is
  why upstream flips ``v`` when building the glTF UVs. Here row ``r`` is
  ``v = (r + 0.5) / texture_size`` — top-down, the order PIL and glTF's
  upper-left UV origin both expect — so the flip is folded in here and the
  export stage does not repeat it.
* **Overlap tie-break.** Upstream composites chunk by chunk so the last chunk to
  cover a texel wins. Chunked ``scatter_reduce(amax)`` here makes the highest
  triangle index win instead: equally arbitrary, but independent of chunk size
  and deterministic. A valid atlas has no overlapping charts, so this only
  decides shared-edge texels.
* **Chunking.** Upstream splits at a fixed 100k faces; this splits on cumulative
  candidate-texel count, which is what actually bounds memory. See
  ``_TEXEL_BUDGET``.
"""

from __future__ import annotations

from typing import List, Tuple

import torch

__all__ = ["rasterize_uv_atlas", "interpolate_barycentric"]

# Peak memory is driven by candidate texels, not face count: upstream's fixed
# 100k-face chunks bound nothing, because one chart-spanning triangle can put
# texture_size^2 candidates in a chunk on its own. Chunks are cut on cumulative
# bounding-box area instead, so this is a real ceiling.
_TEXEL_BUDGET = 8_000_000


def _chunk_bounds(counts: torch.Tensor) -> List[Tuple[int, int]]:
    """Half-open face ranges whose candidate-texel totals stay under
    ``_TEXEL_BUDGET``. A single face over budget still gets its own chunk."""
    total = int(counts.sum().item())
    if total <= _TEXEL_BUDGET:
        return [(0, counts.shape[0])]

    cumulative = torch.cumsum(counts, dim=0)
    bounds = []
    start = 0
    while start < counts.shape[0]:
        consumed = int(cumulative[start - 1].item()) if start > 0 else 0
        stop = int(torch.searchsorted(cumulative, consumed + _TEXEL_BUDGET, right=True).item())
        bounds.append((start, max(stop, start + 1)))
        start = bounds[-1][1]
    return bounds


def _barycentric(points: torch.Tensor, tris: torch.Tensor) -> torch.Tensor:
    """Weights of ``points`` ``[M, 2]`` against triangles ``tris`` ``[M, 3, 2]``,
    returned as ``[M, 3]`` aligned with the triangle's three corners."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    e0 = b - a
    e1 = c - a
    e2 = points - a
    den = e0[:, 0] * e1[:, 1] - e1[:, 0] * e0[:, 1]
    safe_den = torch.where(den.abs() > 0, den, torch.ones_like(den))
    wb = (e2[:, 0] * e1[:, 1] - e1[:, 0] * e2[:, 1]) / safe_den
    wc = (e0[:, 0] * e2[:, 1] - e2[:, 0] * e0[:, 1]) / safe_den
    wa = 1.0 - wb - wc
    bary = torch.stack([wa, wb, wc], dim=-1)
    # Zero-area triangles get a negative sentinel, not zeros: the caller's
    # coverage test is `bary >= 0`, which all-zero weights would pass, letting a
    # degenerate triangle claim every texel in its bounding box.
    return torch.where((den.abs() > 0).unsqueeze(-1), bary, torch.full_like(bary, -1.0))


def rasterize_uv_atlas(
    uvs: torch.Tensor,
    faces: torch.Tensor,
    texture_size: int,
) -> Tuple[torch.Tensor, torch.Tensor]:
    """
    Args:
        uvs: ``[V, 2]`` per-vertex UVs in ``[0, 1]``.
        faces: ``[F, 3]`` triangle vertex indices.
        texture_size: side length of the square texture.

    Returns:
        face_id: ``[T, T]`` int64, index of the covering triangle or ``-1``.
        bary: ``[T, T, 3]`` float32 barycentric weights, zero where uncovered.
    """
    device = uvs.device
    size = int(texture_size)
    uv_px = uvs.to(torch.float32) * size
    faces_long = faces.to(device=device, dtype=torch.long)
    num_faces = faces_long.shape[0]

    winner = torch.full((size * size,), -1, dtype=torch.long, device=device)
    if num_faces == 0:
        return winner.reshape(size, size), torch.zeros((size, size, 3), dtype=torch.float32, device=device)

    all_tris = uv_px[faces_long]  # [F, 3, 2]
    lo = torch.floor(all_tris.amin(dim=1) - 0.5).to(torch.long).clamp(0, size - 1)
    hi = torch.ceil(all_tris.amax(dim=1) - 0.5).to(torch.long).clamp(0, size - 1)
    span = hi - lo + 1
    counts = span[:, 0] * span[:, 1]

    for start, stop in _chunk_bounds(counts):
        chunk_counts = counts[start:stop]
        total = int(chunk_counts.sum().item())
        if total == 0:
            continue

        local_face = torch.repeat_interleave(
            torch.arange(stop - start, dtype=torch.long, device=device), chunk_counts
        )
        starts = torch.cumsum(chunk_counts, dim=0) - chunk_counts
        offset = torch.arange(total, dtype=torch.long, device=device) - starts[local_face]
        face = local_face + start
        width = span[face, 0]
        px = lo[face, 0] + offset % width
        py = lo[face, 1] + offset // width

        centers = torch.stack([px, py], dim=-1).to(torch.float32) + 0.5
        bary = _barycentric(centers, all_tris[face])
        inside = (bary >= 0.0).all(dim=-1)
        if not bool(inside.any()):
            continue

        flat = py[inside] * size + px[inside]
        winner.scatter_reduce_(0, flat, face[inside], reduce="amax", include_self=True)

    face_id = winner.reshape(size, size)
    covered = winner >= 0
    bary_flat = torch.zeros((size * size, 3), dtype=torch.float32, device=device)
    if bool(covered.any()):
        texel = torch.nonzero(covered, as_tuple=False).reshape(-1)
        centers = torch.stack([texel % size, texel // size], dim=-1).to(torch.float32) + 0.5
        bary_flat[texel] = _barycentric(centers, uv_px[faces_long[winner[covered]]])

    return face_id, bary_flat.reshape(size, size, 3)


def interpolate_barycentric(
    attributes: torch.Tensor,
    faces: torch.Tensor,
    face_id: torch.Tensor,
    bary: torch.Tensor,
) -> torch.Tensor:
    """Per-vertex ``attributes`` ``[V, C]`` sampled at every covered texel.

    Returns ``[T, T, C]``, zero where ``face_id`` is ``-1``.
    """
    size = face_id.shape[0]
    out = torch.zeros((size * size, attributes.shape[1]), dtype=attributes.dtype, device=attributes.device)
    flat_id = face_id.reshape(-1)
    covered = flat_id >= 0
    if not bool(covered.any()):
        return out.reshape(size, size, attributes.shape[1])
    corners = attributes[faces.to(torch.long)[flat_id[covered]]]  # [M, 3, C]
    weights = bary.reshape(-1, 3)[covered].to(attributes.dtype)
    out[covered] = (corners * weights.unsqueeze(-1)).sum(dim=1)
    return out.reshape(size, size, attributes.shape[1])
