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
``cumesh.uv_unwrap``                 ``xatlas.parametrize``
``nvdiffrast.rasterize/interpolate`` :mod:`.uv_raster`
``flex_gemm`` ``grid_sample_3d``     ``sparse3d.sparse_grid_sample_3d``
``cumesh.cuBVH.unsigned_distance``   ``cKDTree`` + exact kernel (opt-in, below)
``cv2.inpaint``                      ``cv2`` when importable, else push-pull fill
===================================  ===========================================

Divergences worth knowing about:

* **No remeshing.** Only upstream's ``remesh=False`` branch is ported; the
  narrow-band dual-contouring rebuild has no CPU equivalent. Because we never
  remesh, the material is always ``doubleSided=True``, matching what upstream
  emits on that branch.
* **No non-manifold-edge repair.** ``cumesh.repair_non_manifold_edges`` splits
  edges shared by more than two faces; trimesh has no equivalent. The cleanup
  here drops duplicate, degenerate and tiny-component faces, which removes the
  usual sources of such edges without reconstructing the ones that remain.
  xatlas tolerates them — it charts per face — so they survive into the output
  rather than failing the bake.
* **Hole filling** replicates ``trimesh.repair.fill_holes`` per boundary loop
  (:func:`_fill_small_holes`) instead of calling it directly: the installed
  trimesh (``repair.py``) triangulates every loop uniformly by vertex count —
  a 3- or 4-vertex loop is always fanned/quad-split regardless of its size, so
  it cannot tell a stray missing quad from a metre-wide window with a simple
  rectangular rim. This filters loops by mesh-space perimeter against
  upstream's ``3e-2`` cap first, so a genuinely large opening (cup mouth,
  arch, window) survives even when its rim happens to be a low-vertex-count
  loop, while voxel-scale pinholes are still closed. Triangulation stays
  ``use_fan=False`` (trimesh's own default): only 3- and 4-vertex loops are
  ever filled. A fan over a 5+-vertex loop is only correct for a convex hole
  — trimesh's own docs call out wrong answers on non-convex ones — and
  eligibility here is decided by perimeter, not convexity, so a large
  concave loop under the cap could otherwise be fanned wrong; such loops are
  left open, deferred rather than risked.
* **Projection to the source surface is off by default.** Upstream corrects
  decimation error by pushing every baked texel back onto the pre-decimation
  mesh with a CUDA BVH. The CPU stand-in indexes the source triangles with
  ``scipy.spatial.cKDTree`` and runs the exact point-triangle kernel on a
  provably sufficient candidate set (see :func:`_project_to_source`), which is
  what makes ``project_to_source=True`` usable rather than quadratic —
  ``trimesh.proximity`` would do the same job but needs ``rtree``, a native
  dependency this project does not carry. It is still a CPU query against a
  full-resolution mesh, so a 2048² bake against a 1M-face source stays a
  minutes-scale operation and stays opt-in. Without SciPy the fallback is the
  exhaustive points x triangles scan — same answer, far slower. The default
  takes texel positions from the decimated surface, where the error is bounded
  by the decimation error itself.
* **Texture format.** trimesh 4.12's glTF exporter takes ``extension_webp``, so
  textures are embedded as WebP (about a third the bytes of PNG) and the GLB
  declares ``EXT_texture_webp``. Consumers must understand that extension —
  three.js and ``@google/model-viewer`` do. Pass ``embed_webp=False`` for PNG.
* The V flip upstream applies when building glTF UVs is folded into
  :func:`.uv_raster.rasterize_uv_atlas`, which writes rows top-down rather than
  in nvdiffrast's bottom-up order. See that module.

``decimation_target`` is the parameter that decides whether this is usable.
Upstream's default of 1M faces assumes a GPU unwrapper; ``xatlas`` is CPU-only
and scales worse than linearly, so it dominates everything else here — measured
on this box, 50k faces unwrap in ~12s while 200k takes many minutes. Budget
around 50k-100k faces and the whole chain lands in well under a minute at
2048²; the texture size itself is comparatively cheap.
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple, Union

import numpy as np
import torch
import torch.nn.functional as F

from ...sparse3d import sparse_grid_sample_3d
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

_MIN_COMPONENT_AREA = 1e-5

#: Upstream's hole-fill perimeter cap. Mesh space here is the unit-side AABB
#: every TRELLIS.2 volume decodes into (``((-0.5,)*3, (0.5,)*3)``, see
#: ``image_to_mesh.AABB``), so this is 3% of that box's side length.
_MAX_HOLE_PERIMETER = 3e-2


def _to_numpy(value) -> np.ndarray:
    if isinstance(value, torch.Tensor):
        return value.detach().cpu().numpy()
    return np.asarray(value)


def _simplify(vertices: np.ndarray, faces: np.ndarray, target: int) -> Tuple[np.ndarray, np.ndarray]:
    import pyfqmr

    if faces.shape[0] <= target:
        return vertices, faces
    simplifier = pyfqmr.Simplify()
    simplifier.setMesh(vertices.astype(np.float64), faces.astype(np.int32))
    simplifier.simplify_mesh(target_count=int(target), aggressiveness=7, preserve_border=True, verbose=0)
    out_vertices, out_faces, _ = simplifier.getMesh()
    return np.ascontiguousarray(out_vertices, dtype=np.float32), np.ascontiguousarray(out_faces, dtype=np.int64)


def _drop_small_components(mesh) -> None:
    """Delete connected components whose surface area is below
    ``_MIN_COMPONENT_AREA`` (upstream's ``remove_small_connected_components``)."""
    import trimesh

    if mesh.faces.shape[0] == 0:
        return
    components = trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(mesh.faces.shape[0]))
    if len(components) <= 1:
        return
    areas = mesh.area_faces
    keep = np.zeros(mesh.faces.shape[0], dtype=bool)
    for component in components:
        if areas[component].sum() >= _MIN_COMPONENT_AREA:
            keep[component] = True
    if keep.any() and not keep.all():
        mesh.update_faces(keep)


def _fill_small_holes(mesh, max_perimeter: float = _MAX_HOLE_PERIMETER) -> None:
    """Close boundary loops under ``max_perimeter``, leaving larger geometric
    openings intact.

    Mirrors ``trimesh.repair.fill_holes`` (find boundary edges, take
    ``nx.cycle_basis`` of them, triangulate, fix winding against the original
    boundary, ``extend_faces``) but selects which loops to triangulate instead
    of handing it every loop trimesh finds. A loop is eligible only if every
    vertex on it has boundary-degree 2 — a higher degree means the vertex is
    shared with another boundary loop, which makes ``cycle_basis``'s split
    between them a topological artifact rather than a geometric fact, so
    such loops are left untouched rather than filled on a guess.

    Triangulation uses ``use_fan=False``: a loop over four vertices is left
    open even when its perimeter passes the cap. Fanning a polygon from its
    first vertex is only guaranteed correct when the polygon is convex; the
    perimeter cap says nothing about convexity, so a small concave loop (a
    keyhole, a slot) fanned this way can produce triangles that fold outside
    the hole. Filling those needs an actual triangulation (ear-clipping or
    similar), which is deferred rather than risked here.
    """
    import networkx as nx
    from trimesh.geometry import faces_to_edges, triangulate_quads
    from trimesh.grouping import group_rows, hashable_rows

    if len(mesh.faces) < 3 or mesh.is_watertight:
        return

    boundary_groups = group_rows(mesh.edges_sorted, require_count=1)
    if len(boundary_groups) < 3:
        return
    boundary = mesh.edges[boundary_groups]

    boundary_graph = nx.from_edgelist(boundary)
    vertices = mesh.vertices
    eligible = []
    for loop in nx.cycle_basis(boundary_graph):
        if len(loop) < 3 or any(boundary_graph.degree[v] != 2 for v in loop):
            continue
        ring = loop + [loop[0]]
        perimeter = np.linalg.norm(vertices[ring[1:]] - vertices[ring[:-1]], axis=1).sum()
        if perimeter <= max_perimeter:
            eligible.append(loop)
    if not eligible:
        return

    new_faces = triangulate_quads(eligible, use_fan=False)
    if len(new_faces) == 0:
        return

    # Same trick trimesh.repair.fill_holes uses: a new face whose edge already
    # appears (in the same order) on the old boundary is wound backwards.
    new_edges = faces_to_edges(new_faces)
    hashable_new = hashable_rows(new_edges)
    hashable_old = hashable_rows(boundary)
    needs_reverse = np.isin(hashable_new, hashable_old).reshape((-1, 3)).any(axis=1)
    new_faces[needs_reverse] = np.fliplr(new_faces[needs_reverse])

    mesh.extend_faces(new_faces)


def _tidy(mesh) -> None:
    mesh.update_faces(mesh.unique_faces())
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()
    _drop_small_components(mesh)
    _fill_small_holes(mesh)


def clean_and_decimate(
    vertices: Union[np.ndarray, torch.Tensor],
    faces: Union[np.ndarray, torch.Tensor],
    decimation_target: int = 1_000_000,
) -> Tuple[np.ndarray, np.ndarray]:
    """Upstream's ``remesh=False`` cleaning chain: fill holes, simplify to 3x
    target, clean, simplify to target, clean, unify face orientations.

    Returns outward-wound ``(vertices, faces)`` — the flexible-dual-grid
    extraction winds inward, and ``fix_normals`` flips the whole shell so the
    signed volume comes out positive.
    """
    import trimesh

    verts = np.ascontiguousarray(_to_numpy(vertices), dtype=np.float32)
    tris = np.ascontiguousarray(_to_numpy(faces), dtype=np.int64)

    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    # flexible_dual_grid_to_mesh emits a vertex per input voxel whether or not a
    # face references it, so most of the array is unreferenced on arrival.
    mesh.remove_unreferenced_vertices()
    _fill_small_holes(mesh)

    verts, tris = _simplify(np.asarray(mesh.vertices), np.asarray(mesh.faces), decimation_target * 3)
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    _tidy(mesh)

    verts, tris = _simplify(np.asarray(mesh.vertices), np.asarray(mesh.faces), decimation_target)
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    _tidy(mesh)

    trimesh.repair.fix_normals(mesh, multibody=True)
    return (
        np.ascontiguousarray(mesh.vertices, dtype=np.float32),
        np.ascontiguousarray(mesh.faces, dtype=np.int64),
    )


def unwrap_uv(
    vertices: np.ndarray, faces: np.ndarray
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``xatlas`` UV atlas. Returns ``(vertices, faces, uvs, normals)`` for the
    cut vertex set — charts duplicate vertices along their seams, so the vertex
    count grows and normals are carried over through xatlas' vertex mapping."""
    import trimesh
    import xatlas

    source = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    source_normals = np.asarray(source.vertex_normals, dtype=np.float32)

    vmapping, indices, uvs = xatlas.parametrize(
        np.ascontiguousarray(vertices, dtype=np.float32),
        np.ascontiguousarray(faces, dtype=np.uint32),
    )
    return (
        np.ascontiguousarray(vertices[vmapping], dtype=np.float32),
        np.ascontiguousarray(indices, dtype=np.int64),
        np.ascontiguousarray(uvs, dtype=np.float32),
        np.ascontiguousarray(source_normals[vmapping], dtype=np.float32),
    )


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


def _project_exhaustive(positions: torch.Tensor, tris: torch.Tensor, budget: int) -> torch.Tensor:
    chunk = max(1, budget // max(1, tris.shape[0]))
    return torch.cat(
        [_closest_point_on_triangles(positions[i : i + chunk], tris) for i in range(0, positions.shape[0], chunk)]
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
        balls = centre_tree.query_ball_point(query[batch], radius[batch], workers=-1)
        candidates = np.unique(np.concatenate([np.asarray(b, dtype=np.int64) for b in balls]))
        if batch.shape[0] > 1 and candidates.shape[0] * batch.shape[0] > budget:
            half = batch.shape[0] // 2
            pending.extend([batch[:half], batch[half:]])
            continue
        rows = torch.from_numpy(batch).to(positions.device)
        out[rows] = _closest_point_on_triangles(
            positions[rows], tris[torch.from_numpy(candidates).to(positions.device)]
        )
    return out


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
    project_to_source: bool = False,
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

    clean_vertices, clean_faces = clean_and_decimate(source_vertices, source_faces, decimation_target)
    if clean_faces.shape[0] == 0:
        # trimesh's vertex_normals setter reduces over an empty array and dies
        # with a bare numpy ValueError several stages later; a decode that
        # produced no surface is a result the caller has to report, not a bug.
        raise ValueError(
            f"no geometry to post-process: {source_faces.shape[0]} input faces "
            "cleaned down to nothing"
        )
    uv_vertices, uv_faces, uvs, uv_normals = unwrap_uv(clean_vertices, clean_faces)

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
            sampled_at = _project_to_source(sampled_at, source_vertices, source_faces)
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
        doubleSided=True,
    )

    # y-up to glTF's z-forward: (x, y, z) -> (x, z, -y). A proper rotation, so
    # the outward winding established by clean_and_decimate survives it.
    export_vertices = np.stack([uv_vertices[:, 0], uv_vertices[:, 2], -uv_vertices[:, 1]], axis=-1)
    export_normals = np.stack([uv_normals[:, 0], uv_normals[:, 2], -uv_normals[:, 1]], axis=-1)

    return trimesh.Trimesh(
        vertices=export_vertices,
        faces=uv_faces,
        vertex_normals=export_normals,
        process=False,
        visual=trimesh.visual.TextureVisuals(uv=uvs, material=material),
    )


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
    project_to_source: bool = False,
    embed_webp: bool = True,
) -> None:
    """Write the post-processed, textured mesh to ``out_path`` as a GLB.

    ``decimation_target`` keeps upstream's GPU-sized default; on CPU the xatlas
    unwrap makes anything much above ~100k faces impractical. See the module
    docstring.
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
    )
    mesh.export(out_path, file_type="glb", extension_webp=embed_webp)
