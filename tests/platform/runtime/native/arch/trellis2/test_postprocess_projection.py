"""Exactness and acceleration of the closest-point projection to the source mesh.

The accelerated path must be indistinguishable from the exhaustive scan it
replaces, so every case here diffs the two bit for bit rather than within a
tolerance: the candidate set provably contains every minimiser and preserves
face order, which leaves nothing for rounding to disagree about.
"""

import sys

import numpy as np
import pytest
import torch
import trimesh

from src.platform.runtime.native.arch.trellis2 import postprocess
from src.platform.runtime.native.arch.trellis2.postprocess import (
    _closest_over_triangle_chunks,
    _closest_point_on_triangles,
    _project_exhaustive,
    _project_to_source,
    _source_spatial_index,
    build_textured_mesh,
)


def _tris(vertices, faces):
    return torch.from_numpy(np.ascontiguousarray(vertices[faces], dtype=np.float32))


def _reference(positions, vertices, faces):
    """Ground truth: one kernel call against every triangle, no chunking and no
    index, so nothing the code under test does can shift it."""
    return _closest_point_on_triangles(positions, _tris(vertices, faces))


def _assert_matches_exhaustive(positions, vertices, faces, **kwargs):
    expected = _reference(positions, vertices, faces)
    got = _project_to_source(positions, vertices, faces, **kwargs)
    assert torch.equal(got, expected)
    return got


def _sphere(subdivisions=2, radius=0.4):
    mesh = trimesh.creation.icosphere(subdivisions=subdivisions, radius=radius)
    return (
        np.ascontiguousarray(mesh.vertices, dtype=np.float32),
        np.ascontiguousarray(mesh.faces, dtype=np.int64),
    )


def test_scipy_backs_the_accelerated_path_in_this_environment():
    """Every exactness case below is vacuous if the suite silently ran the
    fallback, so pin the backend the rest of the module assumes."""
    vertices, faces = _sphere()
    assert _source_spatial_index(np.ascontiguousarray(vertices[faces], dtype=np.float32)) is not None


def test_projection_is_bit_equal_to_the_exhaustive_scan():
    vertices, faces = _sphere(subdivisions=3)
    rng = np.random.default_rng(7)
    points = torch.from_numpy(rng.normal(scale=0.35, size=(500, 3)).astype(np.float32))

    _assert_matches_exhaustive(points, vertices, faces, point_batch=64)


def test_degenerate_faces_stay_in_the_candidate_set():
    """A zero-area face has a zero-radius bounding sphere and no plane, so it is
    exactly where a centroid ball query would drop a triangle if the bound were
    sloppy. It still has to answer with a point on the segment it collapsed to."""
    vertices = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 1.0]],
        dtype=np.float32,
    )
    faces = np.array([[0, 1, 2], [0, 1, 3], [0, 0, 0], [1, 3, 4]], dtype=np.int64)
    points = torch.tensor(
        [[0.5, 3.0, 0.0], [-5.0, 1.0, 0.0], [0.5, 0.2, 0.4], [0.0, 0.0, 0.0]], dtype=torch.float32
    )

    _assert_matches_exhaustive(points, vertices, faces, point_batch=2)


def test_exact_ties_resolve_to_the_same_face_as_the_exhaustive_scan():
    """Two faces equidistant from a point: the exhaustive arg-min takes the lower
    face index, and the candidate set has to keep that ordering to agree."""
    vertices = np.array(
        [
            [-1.0, -1.0, 1.0], [1.0, -1.0, 1.0], [0.0, 1.0, 1.0],
            [-1.0, -1.0, -1.0], [1.0, -1.0, -1.0], [0.0, 1.0, -1.0],
            [0.0, 0.0, 2.0],
        ],
        dtype=np.float32,
    )
    # 0/1 straddle the origin at distance 1; 2 shares an edge with 0 so a point
    # above that edge ties across adjacent faces too.
    faces = np.array([[0, 1, 2], [3, 4, 5], [0, 1, 6]], dtype=np.int64)
    points = torch.tensor([[0.0, 0.0, 0.0], [0.0, -1.0, 1.5], [0.0, -1.0, 1.0]], dtype=torch.float32)

    got = _assert_matches_exhaustive(points, vertices, faces, point_batch=1)
    assert torch.allclose(got[0], torch.tensor([0.0, 0.0, 1.0]))


def test_boundary_and_far_outside_points_stay_exact():
    """Points sitting exactly on a vertex, on an edge, and far outside the mesh —
    the last one drives the ball radius up to nearly the whole mesh."""
    vertices, faces = _sphere(subdivisions=2)
    on_vertex = vertices[faces[0, 0]]
    on_edge = (vertices[faces[0, 0]] + vertices[faces[0, 1]]) / 2.0
    points = torch.from_numpy(
        np.stack([on_vertex, on_edge, np.array([50.0, -80.0, 12.0], dtype=np.float32)])
    )

    _assert_matches_exhaustive(points, vertices, faces, point_batch=3)


def test_empty_query_returns_an_empty_result():
    vertices, faces = _sphere()

    got = _project_to_source(torch.zeros((0, 3)), vertices, faces)

    assert got.shape == (0, 3)
    assert got.dtype == torch.float32


def test_a_single_face_mesh_projects_without_an_index_query_degenerating():
    vertices = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    points = torch.tensor([[0.25, 0.25, 3.0], [-4.0, -4.0, 0.0]], dtype=torch.float32)

    _assert_matches_exhaustive(points, vertices, faces)


def test_the_candidate_set_is_a_small_fraction_of_the_mesh():
    """The point of the index: the exact kernel must see far fewer
    point-triangle pairs than the exhaustive scan would."""
    vertices, faces = _sphere(subdivisions=4)
    rng = np.random.default_rng(3)
    surface = vertices[rng.integers(0, vertices.shape[0], 4000)] * rng.uniform(
        0.98, 1.02, (4000, 1)
    ).astype(np.float32)
    points = torch.from_numpy(np.ascontiguousarray(surface, dtype=np.float32))

    pairs = []
    kernel = postprocess._closest_point_on_triangles

    def counting_kernel(batch_points, batch_tris):
        pairs.append(batch_points.shape[0] * batch_tris.shape[0])
        return kernel(batch_points, batch_tris)

    postprocess._closest_point_on_triangles = counting_kernel
    try:
        got = _project_to_source(points, vertices, faces, point_batch=256)
    finally:
        postprocess._closest_point_on_triangles = kernel

    assert torch.equal(got, _reference(points, vertices, faces))
    assert sum(pairs) < points.shape[0] * faces.shape[0] / 5


def test_projection_falls_back_to_the_exhaustive_scan_without_scipy(monkeypatch):
    """A missing backend must cost speed, never accuracy — and it must be the
    exhaustive scan that answers, not an approximate index."""
    monkeypatch.setitem(sys.modules, "scipy.spatial", None)
    vertices, faces = _sphere(subdivisions=3)
    rng = np.random.default_rng(11)
    points = torch.from_numpy(rng.normal(scale=0.35, size=(200, 3)).astype(np.float32))

    assert _source_spatial_index(np.ascontiguousarray(vertices[faces], dtype=np.float32)) is None

    calls = []
    exhaustive = postprocess._project_exhaustive
    monkeypatch.setattr(
        postprocess,
        "_project_exhaustive",
        lambda p, t, b: calls.append(t.shape[0]) or exhaustive(p, t, b),
    )
    got = postprocess._project_to_source(points, vertices, faces)

    assert calls == [faces.shape[0]]
    assert torch.equal(got, _reference(points, vertices, faces))


def _volume_and_coords(grid=24):
    """A linear colour field over the unit cube, the fixture shape
    ``build_textured_mesh`` expects."""
    axis = np.arange(grid)
    coords = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1).reshape(-1, 3)
    positions = (coords + 0.5) / grid - 0.5
    keep = np.abs(np.linalg.norm(positions, axis=1) - 0.4) < 0.12
    coords, positions = coords[keep], positions[keep]
    attrs = np.zeros((coords.shape[0], 6), dtype=np.float32)
    attrs[:, :3] = positions + 0.5
    attrs[:, 3:] = 1.0
    return torch.from_numpy(attrs), torch.from_numpy(coords).long(), 1.0 / grid


@pytest.mark.parametrize("texture_size", [32, 64])
def test_projection_is_opt_in_and_changes_nothing_but_the_sampled_positions(texture_size):
    vertices, faces = _sphere(subdivisions=3)
    attrs, coords, voxel_size = _volume_and_coords()
    kwargs = dict(
        voxel_size=voxel_size, decimation_target=400, texture_size=texture_size
    )

    calls = []
    real = postprocess._project_to_source
    postprocess._project_to_source = lambda *a, **k: calls.append(1) or real(*a, **k)
    try:
        plain = build_textured_mesh(vertices, faces, attrs, coords, **kwargs)
        assert calls == []
        projected = build_textured_mesh(
            vertices, faces, attrs, coords, project_to_source=True, **kwargs
        )
        assert calls == [1]
    finally:
        postprocess._project_to_source = real

    assert np.array_equal(plain.faces, projected.faces)
    assert np.allclose(plain.vertices, projected.vertices)
    for mesh in (plain, projected):
        material = mesh.visual.material
        assert material.baseColorTexture.size == (texture_size, texture_size)
        assert material.baseColorTexture.mode == "RGBA"
        assert material.metallicRoughnessTexture.size == (texture_size, texture_size)
        assert material.doubleSided is True
        assert material.alphaMode == "OPAQUE"
    # Projection moves where the volume is sampled, so the texels shift; it must
    # be a correction to the same image, not a different one.
    plain_rgb = np.asarray(plain.visual.material.baseColorTexture)[..., :3].astype(np.int16)
    projected_rgb = np.asarray(projected.visual.material.baseColorTexture)[..., :3].astype(np.int16)
    assert not np.array_equal(plain_rgb, projected_rgb)
    assert np.abs(plain_rgb - projected_rgb).mean() < 8


def _broad_ball_mesh():
    """An icosphere plus one triangle big enough that ``r_max`` alone makes every
    ball query cover the entire mesh — the shape that makes candidate discovery,
    not the kernel, the thing that blows up."""
    vertices, faces = _sphere(subdivisions=2)
    huge = np.array([[-60.0, -60.0, -60.0], [60.0, -55.0, -58.0], [-58.0, 60.0, 61.0]], dtype=np.float32)
    return (
        np.concatenate([vertices, huge]),
        np.concatenate([faces, np.arange(vertices.shape[0], vertices.shape[0] + 3)[None, :]]),
    )


class _CountingTree:
    """Wraps a ``cKDTree`` and records how many neighbour entries were actually
    materialised, as opposed to merely counted."""

    def __init__(self, tree):
        self._tree = tree
        self.materialised = 0

    def query(self, *args, **kwargs):
        return self._tree.query(*args, **kwargs)

    def query_ball_point(self, points, radius, **kwargs):
        result = self._tree.query_ball_point(points, radius, **kwargs)
        if not kwargs.get("return_length"):
            self.materialised += int(sum(len(entry) for entry in result))
        return result


def _instrumented_projection(monkeypatch, positions, vertices, faces, budget, point_batch):
    """Run the projection with the neighbour lists and the kernel both counted."""
    trees = []
    real_index = postprocess._source_spatial_index

    def wrapped_index(tris):
        index = real_index(tris)
        if index is None:
            return None
        corner_tree, centre_tree, max_radius = index
        counting = _CountingTree(centre_tree)
        trees.append(counting)
        return corner_tree, counting, max_radius

    pairs = []
    real_kernel = postprocess._closest_point_on_triangles

    def wrapped_kernel(batch_points, batch_tris):
        pairs.append(batch_points.shape[0] * batch_tris.shape[0])
        return real_kernel(batch_points, batch_tris)

    monkeypatch.setattr(postprocess, "_source_spatial_index", wrapped_index)
    monkeypatch.setattr(postprocess, "_closest_point_on_triangles", wrapped_kernel)
    got = postprocess._project_to_source(
        positions, vertices, faces, budget=budget, point_batch=point_batch
    )
    return got, trees[0].materialised, pairs


def test_a_broad_ball_never_materialises_more_than_the_budget(monkeypatch):
    """Every point sees the whole mesh, so the old order of work would have
    retained ``points x faces`` neighbour entries before any budget check."""
    vertices, faces = _broad_ball_mesh()
    rng = np.random.default_rng(19)
    points = torch.from_numpy((rng.normal(scale=6.0, size=(64, 3))).astype(np.float32))
    budget = 200

    got, materialised, pairs = _instrumented_projection(
        monkeypatch, points, vertices, faces, budget=budget, point_batch=64
    )

    assert materialised <= budget
    assert max(pairs) <= budget
    assert torch.equal(got, _reference(points, vertices, faces))


def test_a_single_point_over_budget_skips_materialising_its_ball(monkeypatch):
    """One point whose ball is most of the mesh cannot be split any further, so
    the list must never be built; the mesh is scanned in triangle slices."""
    vertices, faces = _broad_ball_mesh()
    points = torch.tensor([[14.0, -9.0, 3.0]], dtype=torch.float32)
    budget = 32

    got, materialised, pairs = _instrumented_projection(
        monkeypatch, points, vertices, faces, budget=budget, point_batch=1
    )

    assert faces.shape[0] > budget
    assert materialised == 0
    assert max(pairs) <= budget
    assert len(pairs) > 1
    assert torch.equal(got, _reference(points, vertices, faces))


def test_triangle_slicing_matches_a_single_kernel_call_including_ties():
    """The slice-by-slice reduction is the reference for the over-budget path, so
    it has to agree bit for bit — tie-break included."""
    vertices = np.array(
        [
            [-1.0, -1.0, 1.0], [1.0, -1.0, 1.0], [0.0, 1.0, 1.0],
            [-1.0, -1.0, -1.0], [1.0, -1.0, -1.0], [0.0, 1.0, -1.0],
            [0.0, 0.0, 3.0], [2.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )
    faces = np.array([[0, 1, 2], [3, 4, 5], [0, 1, 6], [1, 4, 7], [2, 5, 6]], dtype=np.int64)
    tris = _tris(vertices, faces)
    rng = np.random.default_rng(23)
    points = torch.from_numpy(
        np.concatenate([np.zeros((1, 3)), rng.normal(scale=1.5, size=(60, 3))]).astype(np.float32)
    )

    expected = _closest_point_on_triangles(points, tris)
    for budget in (1, 2, 61, 122, 1 << 20):
        assert torch.equal(_closest_over_triangle_chunks(points, tris, budget), expected)
        assert torch.equal(_project_exhaustive(points, tris, budget), expected)
