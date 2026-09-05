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
    _project_exhaustive,
    _project_to_source,
    _source_spatial_index,
    build_textured_mesh,
)


def _tris(vertices, faces):
    return torch.from_numpy(np.ascontiguousarray(vertices[faces], dtype=np.float32))


def _reference(positions, vertices, faces):
    """The pre-acceleration behaviour: one chunk against every triangle."""
    return _project_exhaustive(positions, _tris(vertices, faces), budget=1 << 40)


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
