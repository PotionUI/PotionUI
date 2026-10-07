# Derived from: microsoft/TRELLIS.2 (MIT) — o-voxel/o_voxel/postprocess.py (`to_glb`)
"""Mesh post-processing: clean, decimate, unwrap, bake PBR textures, export GLB.

Upstream's ``to_glb`` runs on four compiled CUDA extensions — ``cumesh`` for the
mesh surgery and UV unwrap, ``cumesh.cuBVH`` for closest-point projection,
``nvdiffrast`` for the UV-space bake, and ``flex_gemm`` for sparse trilinear
sampling. This is the same chain on CPU libraries and pure torch:

===================================  ===========================================
upstream                             here
===================================  ===========================================
``cumesh`` simplify / clean          ``pyfqmr`` + ``trimesh.repair``
``cumesh.uv_unwrap``                 :mod:`.charts` + ``xatlas`` per chart
``nvdiffrast.rasterize/interpolate`` :mod:`.uv_raster`
``flex_gemm`` ``grid_sample_3d``     ``sparse3d.sparse_grid_sample_3d``
``cumesh.cuBVH.unsigned_distance``   centroid grid, ``cKDTree`` fallback (below)
``cv2.inpaint``                      ``cv2`` when importable, else push-pull fill
===================================  ===========================================

Divergences worth knowing about:

* **Remeshing** (``remesh=True``, the default, as in upstream's ``example.py``
  and ``app.py``) ports ``cumesh.remeshing.remesh_narrow_band_dc`` to torch
  (:mod:`.remesh`): it runs on the attribute volume's device, so on the GPU
  in the generating process when there is one. Upstream's BVH distance query
  becomes an exact point-triangle scan of every grid corner inside each
  triangle's bounding box, and the active voxels are those around a sign
  change rather than a centre-distance test, so every crossing edge gets its
  quad. The remeshed material is single-sided and the ``remesh=False``
  branch's is double-sided, as upstream emits them.
* **No non-manifold-edge repair.** ``cumesh.repair_non_manifold_edges`` splits
  edges shared by more than two faces; trimesh has no equivalent. The cleanup
  here drops duplicate, degenerate and tiny-component faces, which removes the
  usual sources of such edges without reconstructing the ones that remain.
  xatlas tolerates them — it charts per face — so they survive into the output
  rather than failing the bake.
* **Hole filling** (:func:`_fill_small_holes`) closes every simple boundary
  loop whose mesh-space perimeter is at most upstream's ``3e-2`` cap, whatever
  its vertex count, as ``cumesh.fill_holes`` does; a loop sharing a vertex
  with another stays open. Upstream fans each loop from its centroid; here the
  loop is ear-clipped in its best-fit plane, which stays inside a concave hole
  where a fan would not. A loop that does not project to a simple polygon
  stays open.
* **Simplification does not preserve borders**, matching ``cumesh.simplify``:
  the flexible dual grid leaves voxel-scale tears wherever predicted edge
  flags disagree, and pinning every border vertex stalls ``pyfqmr`` far above
  the decimation target on such a mesh.
* **Projection to the source surface.** Upstream corrects decimation error by
  pushing every baked texel back onto the pre-decimation mesh with a CUDA BVH.
  Here a uniform grid of source-triangle centroids on the volume's device
  answers every texel within reach of the surface, a grid with doubled cells
  answers the few that are not, and only texels beyond every widening fall
  back to a ``scipy.spatial.cKDTree`` index over the source triangles with the
  exact point-triangle kernel on a provably sufficient candidate set (see
  :func:`_project_to_source`) — ``trimesh.proximity`` would do the same job but
  needs ``rtree``, a native dependency this project does not carry. Without
  SciPy that fallback is the exhaustive points x triangles scan — same answer,
  far slower. ``project_to_source=False`` takes texel positions from the
  decimated surface, where the error is bounded by the decimation error
  itself.
* **Texture format.** trimesh 4.12's glTF exporter takes ``extension_webp``, so
  textures are embedded as WebP (about a third the bytes of PNG) and the GLB
  declares ``EXT_texture_webp``. Consumers must understand that extension —
  three.js and ``@google/model-viewer`` do. Pass ``embed_webp=False`` for PNG.
* The V flip upstream applies when building glTF UVs is folded into
  :func:`.uv_raster.rasterize_uv_atlas`, which writes rows top-down rather than
  in nvdiffrast's bottom-up order. See that module.

The UV unwrap follows ``cumesh.uv_unwrap``: faces are first merged into
normal-cone charts (:mod:`.charts`) of at most 2048 faces each, and xatlas
then parametrises and packs every chart as a mesh of its own. Handed a
whole surface, xatlas grows its charts over one face group at a time and
scales with faces x charts in each, so a 500k-face remesh took 25s at a single
seeding pass and over 15 minutes with seed relocation; split this way the same
mesh unwraps in 6-7s at any quality, and the cost grows linearly with the face
count.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional, Sequence, Tuple, Union

import numpy as np
import torch
import torch.nn.functional as F

from src.platform.runtime.offload.mesh_diagnostics import StageRecorder, log_stage_row
from src.platform.runtime.offload.mesh_ops import (
    DEFAULT_UV_QUALITY,
    clean_and_decimate_arrays,
    clean_and_decimate_traced,
    decimate_traced,
    fill_holes_traced,
    unwrap_uv_arrays,
)
from src.platform.runtime.offload.pool import OffloadCancelled, run_offloaded

from ...errors import SamplingCancelled
from ...sparse3d import sparse_grid_sample_3d
from .charts import compute_charts
from .remesh import remesh_narrow_band_dc
from .uv_raster import interpolate_barycentric, rasterize_uv_atlas

__all__ = [
    "PBR_ATTR_LAYOUT",
    "build_textured_mesh",
    "clean_and_decimate",
    "inpaint_texture",
    "postprocess_to_glb",
    "unwrap_uv",
]

PBR_ATTR_LAYOUT: Dict[str, slice] = {
    "base_color": slice(0, 3),
    "metallic": slice(3, 4),
    "roughness": slice(4, 5),
    "alpha": slice(5, 6),
}


def _to_numpy(value) -> np.ndarray:
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().numpy()
    return np.asarray(value)


def _offload(func, *args, is_cancelled: Optional[Callable[[], bool]] = None):
    try:
        return run_offloaded(func, *args, is_cancelled=is_cancelled)
    except OffloadCancelled as exc:
        raise SamplingCancelled() from exc


def clean_and_decimate(
    vertices: Union[np.ndarray, torch.Tensor],
    faces: Union[np.ndarray, torch.Tensor],
    decimation_target: int = 1_000_000,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """Upstream's ``remesh=False`` cleaning chain: fill holes, simplify to 3x
    target, clean, simplify to target, clean, unify face orientations.

    Returns outward-wound ``(vertices, faces)`` — the flexible-dual-grid
    extraction winds inward, and ``fix_normals`` flips the whole shell so the
    signed volume comes out positive.
    """
    vertices = np.ascontiguousarray(_to_numpy(vertices), dtype=np.float32)
    faces = np.ascontiguousarray(_to_numpy(faces), dtype=np.int64)
    return _offload(clean_and_decimate_arrays, vertices, faces, int(decimation_target), is_cancelled=is_cancelled)


def unwrap_uv(
    vertices: np.ndarray,
    faces: np.ndarray,
    is_cancelled: Optional[Callable[[], bool]] = None,
    quality: str = DEFAULT_UV_QUALITY,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``xatlas`` UV atlas. Returns ``(vertices, faces, uvs, normals)`` for the
    cut vertex set — charts duplicate vertices along their seams, so the vertex
    count grows and normals are carried over through xatlas' vertex mapping."""
    charts = compute_charts(
        torch.from_numpy(np.ascontiguousarray(vertices, dtype=np.float32)),
        torch.from_numpy(np.ascontiguousarray(faces, dtype=np.int64)),
        is_cancelled=is_cancelled,
    )
    return _offload(unwrap_uv_arrays, vertices, faces, quality, charts.numpy(), is_cancelled=is_cancelled)


def _push_pull_fill(value: torch.Tensor, weight: torch.Tensor) -> torch.Tensor:
    """Fill every zero-weight texel of ``value`` ``[1, C, T, T]`` from its
    neighbourhood, coarsening to 1x1 and interpolating back down so even large
    uncovered regions resolve in ``log2(T)`` passes."""
    pyramid = [(value * weight, weight)]
    while pyramid[-1][0].shape[-1] > 1 or pyramid[-1][0].shape[-2] > 1:
        val, wgt = pyramid[-1]
        pyramid.append((F.avg_pool2d(val, 2, ceil_mode=True), F.avg_pool2d(wgt, 2, ceil_mode=True)))

    val, wgt = pyramid[-1]
    filled = val / wgt.clamp_min(1e-8)
    for val, wgt in reversed(pyramid[:-1]):
        coarse = F.interpolate(filled, size=val.shape[-2:], mode="bilinear", align_corners=False)
        filled = torch.where(wgt > 0, val / wgt.clamp_min(1e-8), coarse)
    return filled


def inpaint_texture(image: np.ndarray, mask: np.ndarray, radius: int) -> np.ndarray:
    """Fill the ``mask``-false texels of a ``[T, T, C]`` uint8 image.

    Uses ``cv2.inpaint`` (Telea) when OpenCV imports, matching upstream. OpenCV
    is imported lazily because its native dependencies are not always present;
    the fallback is a push-pull pyramid fill, which closes UV gutters just as
    well but does not reconstruct structure the way Telea does.
    """
    if mask.all():
        return image
    try:
        import cv2
    except Exception:
        cv2 = None

    if cv2 is not None:
        filled = cv2.inpaint(np.ascontiguousarray(image), (~mask).astype(np.uint8), radius, cv2.INPAINT_TELEA)
        return filled.reshape(image.shape)

    value = torch.from_numpy(image.astype(np.float32)).permute(2, 0, 1).unsqueeze(0)
    weight = torch.from_numpy(mask.astype(np.float32)).reshape(1, 1, *mask.shape)
    filled = _push_pull_fill(value, weight)
    return filled.clamp(0, 255).round().to(torch.uint8)[0].permute(1, 2, 0).numpy()


def _sample_attributes(
    positions: torch.Tensor,
    attr_volume: torch.Tensor,
    coords: torch.Tensor,
    aabb: torch.Tensor,
    voxel_size: torch.Tensor,
    grid_size: torch.Tensor,
) -> torch.Tensor:
    grid = ((positions - aabb[0]) / voxel_size).reshape(1, -1, 3)
    batched_coords = torch.cat([torch.zeros_like(coords[:, :1]), coords], dim=-1)
    return sparse_grid_sample_3d(attr_volume, batched_coords, grid_size, grid)[0]


def _closest_point_on_triangles(points: torch.Tensor, tris: torch.Tensor) -> torch.Tensor:
    """Closest point on each of ``tris`` ``[T, 3, 3]`` to each of ``points``
    ``[P, 3]``, reduced to the single nearest, returned ``[P, 3]``.

    The closest point of a triangle is either the in-plane projection, when its
    barycentrics are all non-negative, or a point on one of the three edges, so
    taking the nearest of those four candidates is exact — no need for the
    region-by-region case analysis, and it vectorizes.
    """
    eps = 1e-20
    p = points.unsqueeze(1)
    a, b, c = tris[:, 0].unsqueeze(0), tris[:, 1].unsqueeze(0), tris[:, 2].unsqueeze(0)
    ab, ac = b - a, c - a
    normal = torch.cross(ab, ac, dim=-1)
    norm_sq = (normal * normal).sum(-1)

    ap = p - a
    weight_c = (torch.cross(ab, ap, dim=-1) * normal).sum(-1) / norm_sq.clamp_min(eps)
    weight_b = (torch.cross(ap, ac, dim=-1) * normal).sum(-1) / norm_sq.clamp_min(eps)
    inside = (weight_b >= 0) & (weight_c >= 0) & (weight_b + weight_c <= 1) & (norm_sq > eps)
    interior = a + ab * weight_b.unsqueeze(-1) + ac * weight_c.unsqueeze(-1)

    def on_segment(start, end):
        direction = end - start
        t = ((p - start) * direction).sum(-1) / (direction * direction).sum(-1).clamp_min(eps)
        return start + direction * t.clamp(0, 1).unsqueeze(-1)

    candidates = torch.stack([interior, on_segment(a, b), on_segment(b, c), on_segment(c, a)], dim=2)
    distances = (candidates - p.unsqueeze(2)).pow(2).sum(-1)
    distances[..., 0] = torch.where(inside, distances[..., 0], torch.full_like(distances[..., 0], float("inf")))

    flat = distances.reshape(points.shape[0], -1)
    best = flat.argmin(dim=1)
    return candidates.reshape(points.shape[0], -1, 3)[torch.arange(points.shape[0]), best]


def _morton_order(points: np.ndarray) -> np.ndarray:
    """Indices visiting ``points`` along a Z-order curve.

    Batches are compared against the union of their members' candidate
    triangles, so the union is only small when the batch is spatially tight.
    Query points arrive in UV-atlas order, which is unrelated to 3D locality.
    """
    lo = points.min(axis=0)
    span = np.maximum(points.max(axis=0) - lo, 1e-12)
    grid = np.clip((points - lo) / span * 1023.0, 0, 1023).astype(np.uint64)
    key = np.zeros(points.shape[0], dtype=np.uint64)
    for bit in range(10):
        for axis in range(3):
            key |= ((grid[:, axis] >> np.uint64(bit)) & np.uint64(1)) << np.uint64(3 * bit + axis)
    return np.argsort(key, kind="stable")


def _source_spatial_index(tris: np.ndarray):
    """KD-trees over ``tris`` ``[T, 3, 3]``, or ``None`` when SciPy is absent.

    Returns a tree over the triangle corners, a tree over the triangle bounding
    sphere centres, and the largest bounding sphere radius in the mesh.
    """
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        return None
    centres = tris.mean(axis=1, dtype=np.float64)
    radii = np.linalg.norm(tris - centres[:, None, :], axis=2).max(axis=1)
    return cKDTree(tris.reshape(-1, 3)), cKDTree(centres), float(radii.max())


def _closest_over_triangle_chunks(points: torch.Tensor, tris: torch.Tensor, budget: int) -> torch.Tensor:
    """:func:`_closest_point_on_triangles` reduced over ``tris`` in slices of at
    most ``budget`` point-triangle pairs.

    Bit-identical to the single-call form. Each slice's winner is scored with
    the same ``(candidate - p)**2`` sum the kernel minimises internally, so the
    cross-slice comparison sees the values a single arg-min would have compared,
    and keeping the incumbent on equality preserves its lowest-face-index
    tie-break.
    """
    step = max(1, budget // max(1, points.shape[0]))
    best = best_sq = None
    for start in range(0, tris.shape[0], step):
        found = _closest_point_on_triangles(points, tris[start : start + step])
        found_sq = (found - points).pow(2).sum(-1)
        if best is None:
            best, best_sq = found, found_sq
            continue
        closer = found_sq < best_sq
        best = torch.where(closer.unsqueeze(-1), found, best)
        best_sq = torch.where(closer, found_sq, best_sq)
    return best


def _project_exhaustive(positions: torch.Tensor, tris: torch.Tensor, budget: int) -> torch.Tensor:
    chunk = max(1, budget // max(1, tris.shape[0]))
    return torch.cat(
        [
            _closest_over_triangle_chunks(positions[i : i + chunk], tris, budget)
            for i in range(0, positions.shape[0], chunk)
        ]
    )


def _project_to_source(
    positions: torch.Tensor,
    source_vertices: np.ndarray,
    source_faces: np.ndarray,
    budget: int = 4_000_000,
    point_batch: int = 1024,
) -> torch.Tensor:
    """CPU stand-in for upstream's CUDA BVH closest-point query.

    ``trimesh.proximity`` needs ``rtree``, a native dependency this project does
    not carry, so the acceleration structure is built here from
    ``scipy.spatial.cKDTree`` (already a hard dependency) and the exact
    point-triangle kernel then runs on candidates only. Without SciPy the
    fallback is the exhaustive point-times-triangle scan, chunked so peak memory
    stays near ``budget`` point-triangle pairs; it is the same answer, slowly,
    never an approximation.

    The candidate set is exact rather than heuristic. The distance ``d0`` from a
    query point to its nearest triangle corner bounds the distance to the
    nearest triangle from above, and a triangle enclosed by a sphere of centre
    ``c`` and radius ``r`` is no nearer than ``|p - c| - r``, so every triangle
    that could hold the closest point satisfies ``|p - c| <= d0 + r_max``. A ball
    query of that radius over the bounding sphere centres therefore contains
    every minimiser, and the kernel's arg-min over it (candidates stay in
    ascending face order) picks the same face and corner the exhaustive scan
    would.

    ``budget`` bounds discovery as well as the kernel. A ball can be broad — far
    query points, or one outsized triangle inflating ``r_max`` — so each batch is
    first *counted* (``return_length``, which allocates no neighbour lists) and
    halved until the counts sum inside the budget; only then are the lists
    materialised, and the union is re-checked against the budget before the
    kernel runs. A lone point still over budget skips materialisation entirely
    and is scanned against the whole mesh in triangle slices.
    """
    tris_np = np.ascontiguousarray(source_vertices[source_faces], dtype=np.float32)
    tris = torch.from_numpy(tris_np).to(positions.device)
    if positions.shape[0] == 0:
        return positions.new_zeros((0, 3))

    index = _source_spatial_index(tris_np) if tris.shape[0] else None
    if index is None:
        return _project_exhaustive(positions, tris, budget)
    corner_tree, centre_tree, max_radius = index

    query = positions.detach().to("cpu", torch.float64).numpy()
    # d0 is a geometric bound; the kernel works in float32, so widen it enough
    # that rounding cannot drop a triangle that ties for nearest.
    radius = corner_tree.query(query, workers=-1)[0] * (1.0 + 1e-6) + max_radius + 1e-6

    out = positions.new_empty((positions.shape[0], 3))
    order = _morton_order(query)
    pending = [order[i : i + point_batch] for i in range(0, order.shape[0], point_batch)]
    while pending:
        batch = pending.pop()
        found = int(centre_tree.query_ball_point(query[batch], radius[batch], workers=-1, return_length=True).sum())
        if batch.shape[0] > 1 and found > budget:
            half = batch.shape[0] // 2
            pending.extend([batch[:half], batch[half:]])
            continue
        rows = torch.from_numpy(batch).to(positions.device)
        if found > budget:
            # One point whose ball alone outruns the budget: materialising it
            # would cost `found` Python entries for a set that is by then most
            # of the mesh, so scan the whole mesh in triangle slices instead.
            # Peak stays at `budget` pairs and the answer is the same one the
            # candidate set would have given.
            out[rows] = _closest_over_triangle_chunks(positions[rows], tris, budget)
            continue
        balls = centre_tree.query_ball_point(query[batch], radius[batch], workers=-1)
        candidates = np.unique(np.concatenate([np.asarray(b, dtype=np.int64) for b in balls]))
        if batch.shape[0] > 1 and candidates.shape[0] * batch.shape[0] > budget:
            half = batch.shape[0] // 2
            pending.extend([batch[:half], batch[half:]])
            continue
        out[rows] = _closest_point_on_triangles(
            positions[rows], tris[torch.from_numpy(candidates).to(positions.device)]
        )
    return out


_WIDEN_STEPS = 5
_CELL_OFFSETS = torch.tensor(
    [[i, j, k] for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)], dtype=torch.long
)

def _closest_on_pairs(points: torch.Tensor, tris: torch.Tensor) -> torch.Tensor:
    eps = 1e-20
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    ab, ac = b - a, c - a
    normal = torch.cross(ab, ac, dim=-1)
    norm_sq = (normal * normal).sum(-1)
    ap = points - a
    weight_c = (torch.cross(ab, ap, dim=-1) * normal).sum(-1) / norm_sq.clamp_min(eps)
    weight_b = (torch.cross(ap, ac, dim=-1) * normal).sum(-1) / norm_sq.clamp_min(eps)
    inside = (weight_b >= 0) & (weight_c >= 0) & (weight_b + weight_c <= 1) & (norm_sq > eps)
    interior = a + ab * weight_b.unsqueeze(-1) + ac * weight_c.unsqueeze(-1)

    def on_segment(start, end):
        direction = end - start
        t = ((points - start) * direction).sum(-1) / (direction * direction).sum(-1).clamp_min(eps)
        return start + direction * t.clamp(0, 1).unsqueeze(-1)

    candidates = torch.stack([interior, on_segment(a, b), on_segment(b, c), on_segment(c, a)], dim=1)
    distances = (candidates - points.unsqueeze(1)).pow(2).sum(-1)
    distances[:, 0] = torch.where(inside, distances[:, 0], torch.full_like(distances[:, 0], float("inf")))
    best = distances.argmin(dim=1)
    return candidates[torch.arange(points.shape[0], device=points.device), best]

class _TriangleGrid:
    def __init__(self, vertices: torch.Tensor, faces: torch.Tensor, cell: float) -> None:
        self.vertices = vertices
        self.faces = faces
        self.cell = float(cell)
        centroids = (vertices[faces[:, 0]] + vertices[faces[:, 1]] + vertices[faces[:, 2]]) / 3.0
        self.centroids = centroids
        self.radii = (vertices[faces] - centroids.unsqueeze(1)).norm(dim=-1).amax(dim=1)
        self.origin = centroids.amin(dim=0) - self.cell
        self.dims = ((centroids.amax(dim=0) - self.origin) / self.cell).floor().to(torch.long) + 2
        codes = self._code(self._cell_of(centroids))
        self.sorted_codes, self.order = torch.sort(codes)

    def _cell_of(self, points: torch.Tensor) -> torch.Tensor:
        return ((points - self.origin) / self.cell).floor().to(torch.long)

    def _code(self, cells: torch.Tensor) -> torch.Tensor:
        return (cells[..., 0] * int(self.dims[1]) + cells[..., 1]) * int(self.dims[2]) + cells[..., 2]

    def _ranges(self, points: torch.Tensor):
        cells = self._cell_of(points).unsqueeze(1) + _CELL_OFFSETS.to(points.device)
        valid = ((cells >= 0) & (cells < self.dims.to(points.device))).all(dim=-1)
        codes = self._code(cells.clamp_min(0))
        low = torch.searchsorted(self.sorted_codes, codes.reshape(-1)).reshape(codes.shape)
        high = torch.searchsorted(self.sorted_codes, codes.reshape(-1), right=True).reshape(codes.shape)
        return low, torch.where(valid, high - low, torch.zeros_like(low))

    def nearest(self, points: torch.Tensor, budget: int) -> Tuple[torch.Tensor, torch.Tensor]:
        count = points.shape[0]
        out = points.clone()
        distance = torch.full((count,), float("inf"), dtype=points.dtype, device=points.device)
        pending = [torch.arange(count, device=points.device)]
        while pending:
            rows = pending.pop()
            low, size = self._ranges(points[rows])
            total = int(size.sum())
            if total > budget and rows.shape[0] > 1:
                half = rows.shape[0] // 2
                pending.extend([rows[:half], rows[half:]])
                continue
            if total == 0:
                continue
            flat_size = size.reshape(-1)
            slot = torch.repeat_interleave(torch.arange(flat_size.shape[0], device=points.device), flat_size)
            start = torch.cumsum(flat_size, dim=0) - flat_size
            position = low.reshape(-1)[slot] + torch.arange(total, device=points.device) - start[slot]
            owner = slot // _CELL_OFFSETS.shape[0]
            face = self.order[position]
            query = points[rows][owner]
            gap = (query - self.centroids[face]).norm(dim=-1)
            bound = torch.full((rows.shape[0],), float("inf"), dtype=gap.dtype, device=points.device)
            bound = bound.scatter_reduce(0, owner, gap, reduce="amin", include_self=True)
            near = gap - self.radii[face] <= bound[owner] * (1.0 + 1e-4) + 1e-6
            face, owner, query = face[near], owner[near], query[near]
            tris = self.vertices[self.faces[face]]
            closest = _closest_on_pairs(query, tris)
            squared = (closest - query).pow(2).sum(-1)
            best = torch.full((rows.shape[0],), float("inf"), dtype=squared.dtype, device=points.device)
            best = best.scatter_reduce(0, owner, squared, reduce="amin", include_self=True)
            tied = squared == best[owner]
            sentinel = self.faces.shape[0]
            first = torch.full((rows.shape[0],), sentinel, dtype=torch.long, device=points.device)
            first = first.scatter_reduce(
                0, owner, torch.where(tied, face, torch.full_like(face, sentinel)), reduce="amin", include_self=True,
            )
            chosen = tied & (face == first[owner])
            out[rows[owner[chosen]]] = closest[chosen]
            distance[rows[owner[chosen]]] = squared[chosen].sqrt()
        return out, distance

def _project_to_source_grid(
    positions: torch.Tensor,
    source_vertices: np.ndarray,
    source_faces: np.ndarray,
    cell: float,
    reach: float,
    budget: int = 6_000_000,
) -> torch.Tensor:
    if positions.shape[0] == 0 or source_faces.shape[0] == 0:
        return positions
    device = positions.device
    vertices_t = torch.from_numpy(np.ascontiguousarray(source_vertices, dtype=np.float32)).to(device)
    faces_t = torch.from_numpy(np.ascontiguousarray(source_faces, dtype=np.int64)).to(device)
    points = positions.to(torch.float32)
    projected, distance = _TriangleGrid(vertices_t, faces_t, cell).nearest(points, budget)
    missed = ~(distance <= reach)
    for step in range(1, _WIDEN_STEPS + 1):
        if not bool(missed.any()):
            return projected
        wide = cell * (2.0 ** step)
        rows = torch.nonzero(missed).flatten()
        found, found_distance = _TriangleGrid(vertices_t, faces_t, wide).nearest(points[rows], budget)
        projected[rows] = found
        missed[rows] = ~(found_distance <= reach + wide - cell)
    if bool(missed.any()):
        projected[missed] = _project_to_source(
            positions[missed].to(torch.float32), source_vertices, source_faces
        )
    return projected

def _resolve_volume_geometry(
    aabb, voxel_size, coords_device: torch.device
) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    aabb_t = torch.as_tensor(_to_numpy(aabb), dtype=torch.float32, device=coords_device).reshape(2, 3)
    if np.isscalar(voxel_size) or (isinstance(voxel_size, torch.Tensor) and voxel_size.dim() == 0):
        voxel = torch.full((3,), float(voxel_size), dtype=torch.float32, device=coords_device)
    else:
        voxel = torch.as_tensor(_to_numpy(voxel_size), dtype=torch.float32, device=coords_device).reshape(3)
    grid_size = ((aabb_t[1] - aabb_t[0]) / voxel).round().to(torch.long)
    return aabb_t, voxel, grid_size


def _remesh_and_decimate(
    vertices: np.ndarray,
    faces: np.ndarray,
    aabb: torch.Tensor,
    grid_size: torch.Tensor,
    band: float,
    decimation_target: int,
    device: torch.device,
    is_cancelled: Optional[Callable[[], bool]],
):
    filled_vertices, filled_faces, rows = _offload(fill_holes_traced, vertices, faces, is_cancelled=is_cancelled)
    resolution = int(grid_size.max())
    extent = float((aabb[1] - aabb[0]).max())
    started = time.perf_counter()
    remeshed_vertices, remeshed_faces = remesh_narrow_band_dc(
        torch.from_numpy(filled_vertices).to(device),
        torch.from_numpy(filled_faces).to(device),
        center=aabb.mean(dim=0),
        scale=(resolution + 3 * band) / resolution * extent,
        resolution=resolution,
        band=band,
        is_cancelled=is_cancelled,
    )
    remeshed_vertices = remeshed_vertices.cpu().numpy()
    remeshed_faces = remeshed_faces.cpu().numpy()
    elapsed = time.perf_counter() - started
    clean_vertices, clean_faces, decimate_rows = _offload(
        decimate_traced, remeshed_vertices, remeshed_faces, decimation_target, is_cancelled=is_cancelled
    )
    decimate_rows = list(decimate_rows)
    decimate_rows[0] = tuple(decimate_rows[0][:5]) + (elapsed,)
    return clean_vertices, clean_faces, list(rows) + decimate_rows


def build_textured_mesh(
    vertices: Union[np.ndarray, torch.Tensor],
    faces: Union[np.ndarray, torch.Tensor],
    attr_volume: torch.Tensor,
    coords: torch.Tensor,
    voxel_size: Union[float, Sequence[float], torch.Tensor],
    aabb: Union[Sequence, np.ndarray, torch.Tensor] = ((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)),
    decimation_target: int = 1_000_000,
    texture_size: int = 2048,
    attr_layout: Optional[Dict[str, slice]] = None,
    project_to_source: bool = True,
    is_cancelled: Optional[Callable[[], bool]] = None,
    remesh: bool = True,
    remesh_band: float = 1.0,
    uv_quality: str = DEFAULT_UV_QUALITY,
):
    """Run the whole chain and return the textured ``trimesh.Trimesh``.

    ``attr_volume`` is ``[L, C]`` and ``coords`` ``[L, 3]`` integer voxel coords;
    ``attr_layout`` slices the channels, defaulting to :data:`PBR_ATTR_LAYOUT`.
    """
    import trimesh
    import trimesh.visual
    from PIL import Image

    layout = attr_layout or PBR_ATTR_LAYOUT
    device = attr_volume.device
    aabb_t, voxel, grid_size = _resolve_volume_geometry(aabb, voxel_size, device)

    source_vertices = np.ascontiguousarray(_to_numpy(vertices), dtype=np.float32)
    source_faces = np.ascontiguousarray(_to_numpy(faces), dtype=np.int64)

    recorder = StageRecorder()
    log_stage_row(recorder.record("raw", source_vertices, source_faces))
    recorder.reset()
    if remesh:
        clean_vertices, clean_faces, clean_rows = _remesh_and_decimate(
            source_vertices, source_faces, aabb_t, grid_size, remesh_band, int(decimation_target), device, is_cancelled
        )
    else:
        clean_vertices, clean_faces, clean_rows = _offload(
            clean_and_decimate_traced,
            source_vertices,
            source_faces,
            int(decimation_target),
            is_cancelled=is_cancelled,
        )
    for row in clean_rows:
        log_stage_row(row)
    log_stage_row(recorder.record("decimated", clean_vertices, clean_faces))
    if clean_faces.shape[0] == 0:
        # trimesh's vertex_normals setter reduces over an empty array and dies
        # with a bare numpy ValueError several stages later; a decode that
        # produced no surface is a result the caller has to report, not a bug.
        raise ValueError(
            f"no geometry to post-process: {source_faces.shape[0]} input faces "
            "cleaned down to nothing"
        )
    recorder.reset()
    uv_vertices, uv_faces, uvs, uv_normals = unwrap_uv(clean_vertices, clean_faces, is_cancelled=is_cancelled, quality=uv_quality)
    log_stage_row(recorder.record("unwrap", uv_vertices, uv_faces))

    uvs_t = torch.from_numpy(uvs).to(device)
    faces_t = torch.from_numpy(uv_faces).to(device)
    face_id, bary = rasterize_uv_atlas(uvs_t, faces_t, texture_size)
    positions = interpolate_barycentric(torch.from_numpy(uv_vertices).to(device), faces_t, face_id, bary)

    covered = face_id >= 0
    mask = covered.cpu().numpy()
    attrs = torch.zeros((texture_size, texture_size, attr_volume.shape[1]), dtype=torch.float32, device=device)
    if bool(covered.any()):
        sampled_at = positions[covered]
        if project_to_source:
            reach = (1.0 + (remesh_band if remesh else 0.0)) * float(voxel.max())
            sampled_at = _project_to_source_grid(
                sampled_at, source_vertices, source_faces, cell=reach + float(voxel.max()), reach=reach
            )
        attrs[covered] = _sample_attributes(sampled_at, attr_volume, coords, aabb_t, voxel, grid_size)

    def channel(name: str, radius: int) -> np.ndarray:
        raw = np.clip(attrs[..., layout[name]].cpu().numpy() * 255, 0, 255).astype(np.uint8)
        return inpaint_texture(raw, mask, radius)

    base_color = channel("base_color", 3)
    metallic = channel("metallic", 1)
    roughness = channel("roughness", 1)
    alpha = channel("alpha", 1)

    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=Image.fromarray(np.concatenate([base_color, alpha], axis=-1)),
        baseColorFactor=np.array([255, 255, 255, 255], dtype=np.uint8),
        metallicRoughnessTexture=Image.fromarray(
            np.concatenate([np.zeros_like(metallic), roughness, metallic], axis=-1)
        ),
        metallicFactor=1.0,
        roughnessFactor=1.0,
        alphaMode="OPAQUE",
        doubleSided=not remesh,
    )

    # y-up to glTF's z-forward: (x, y, z) -> (x, z, -y). A proper rotation, so
    # the outward winding established by clean_and_decimate survives it.
    export_vertices = np.stack([uv_vertices[:, 0], uv_vertices[:, 2], -uv_vertices[:, 1]], axis=-1)
    export_normals = np.stack([uv_normals[:, 0], uv_normals[:, 2], -uv_normals[:, 1]], axis=-1)

    textured = trimesh.Trimesh(
        vertices=export_vertices,
        faces=uv_faces,
        vertex_normals=export_normals,
        process=False,
        visual=trimesh.visual.TextureVisuals(
            uv=np.stack([uvs[:, 0], 1.0 - uvs[:, 1]], axis=-1), material=material
        ),
    )
    log_stage_row(recorder.record("bake", export_vertices, uv_faces))
    return textured


def postprocess_to_glb(
    vertices: Union[np.ndarray, torch.Tensor],
    faces: Union[np.ndarray, torch.Tensor],
    attr_volume: torch.Tensor,
    coords: torch.Tensor,
    voxel_size: Union[float, Sequence[float], torch.Tensor],
    aabb: Union[Sequence, np.ndarray, torch.Tensor] = ((-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)),
    decimation_target: int = 1_000_000,
    texture_size: int = 2048,
    out_path: Optional[str] = None,
    attr_layout: Optional[Dict[str, slice]] = None,
    project_to_source: bool = True,
    embed_webp: bool = True,
    is_cancelled: Optional[Callable[[], bool]] = None,
    remesh: bool = True,
    uv_quality: str = DEFAULT_UV_QUALITY,
) -> None:
    """Write the post-processed, textured mesh to ``out_path`` as a GLB.

    ``decimation_target`` keeps upstream's GPU-sized default. See the module
    docstring for what the CPU unwrap costs.
    """
    assert out_path is not None, "out_path is required"
    mesh = build_textured_mesh(
        vertices,
        faces,
        attr_volume,
        coords,
        voxel_size,
        aabb=aabb,
        decimation_target=decimation_target,
        texture_size=texture_size,
        attr_layout=attr_layout,
        project_to_source=project_to_source,
        is_cancelled=is_cancelled,
        remesh=remesh,
        uv_quality=uv_quality,
    )
    recorder = StageRecorder()
    mesh.export(out_path, file_type="glb", extension_webp=embed_webp)
    log_stage_row(recorder.record("export", np.asarray(mesh.vertices), np.asarray(mesh.faces)))
