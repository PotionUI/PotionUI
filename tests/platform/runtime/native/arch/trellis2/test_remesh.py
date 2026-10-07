import numpy as np
import pytest
import torch
import trimesh

from src.platform.runtime.native.arch.trellis2 import remesh as remesh_module
from src.platform.runtime.native.arch.trellis2.dual_grid import flexible_dual_grid_to_mesh
from src.platform.runtime.native.arch.trellis2.postprocess import _closest_point_on_triangles
from src.platform.runtime.native.arch.trellis2.remesh import point_triangle_distance_sq, remesh_narrow_band_dc
from src.platform.runtime.native.errors import SamplingCancelled

from ._fdg_shapes import dual_grid_fields, edge_counts, sphere_sdf, tear_quad_pairs, torus_sdf, welded

RESOLUTION = 32


def _torn(shape):
    coords, offsets, flags, lerp = dual_grid_fields(shape, RESOLUTION)
    vertices, triangles = flexible_dual_grid_to_mesh(
        coords, offsets, flags, lerp, aabb=[[-0.5] * 3, [0.5] * 3], grid_size=RESOLUTION,
    )
    faces, torn = tear_quad_pairs(triangles.numpy(), 10_000)
    assert torn > 50
    assert (edge_counts(welded(vertices.numpy(), faces)) == 1).sum() == torn * 6
    return vertices, torch.from_numpy(faces)


def _remesh(vertices, faces, **kwargs):
    return remesh_narrow_band_dc(
        vertices, faces, torch.zeros(3), (RESOLUTION + 3) / RESOLUTION, RESOLUTION, **kwargs
    )


def test_point_triangle_distance_matches_the_closest_point_oracle():
    generator = torch.Generator().manual_seed(0)
    tris = torch.rand((64, 3, 3), generator=generator, dtype=torch.float64)
    tris[:8, 2] = tris[:8, 0] + 0.5 * (tris[:8, 1] - tris[:8, 0])
    tris[8:12, 1] = tris[8:12, 0]
    tris[8:12, 2] = tris[8:12, 0]
    points = torch.rand((200, 3), generator=generator, dtype=torch.float64) * 1.6 - 0.3

    a = tris[:, 0][None]
    got = point_triangle_distance_sq(points[:, None] - a, (tris[:, 1] - tris[:, 0])[None], (tris[:, 2] - tris[:, 0])[None])

    expected = torch.stack(
        [((_closest_point_on_triangles(points, tris[t : t + 1]) - points) ** 2).sum(-1) for t in range(tris.shape[0])],
        dim=1,
    )
    assert torch.allclose(got, expected, atol=1e-10)


@pytest.mark.parametrize("shape_name, shell_euler", [("sphere", 2), ("torus", 0)])
def test_a_parity_torn_surface_remeshes_closed_manifold_and_consistently_wound(shape_name, shell_euler):
    shape = sphere_sdf(0.37) if shape_name == "sphere" else torus_sdf(0.27, 0.11)
    vertices, faces = _torn(shape)

    out_vertices, out_faces = _remesh(vertices, faces)

    out_faces = out_faces.numpy()
    counts = edge_counts(welded(out_vertices.numpy(), out_faces))
    mesh = trimesh.Trimesh(out_vertices.numpy(), out_faces, process=False)
    shells = mesh.split(only_watertight=False)
    assert (counts == 2).all()
    assert mesh.is_winding_consistent
    assert len(shells) == 2
    for shell in shells:
        assert shell.is_watertight
        assert shell.euler_number == shell_euler
    outer, inner = sorted(shells, key=lambda s: -abs(s.volume))
    assert outer.volume > 0
    assert inner.volume < 0


def test_a_small_pair_budget_changes_nothing(monkeypatch):
    vertices, faces = _torn(sphere_sdf(0.37))
    reference = _remesh(vertices, faces)

    monkeypatch.setattr(remesh_module, "PAIR_BUDGET", 4096)
    chunked = _remesh(vertices, faces)

    assert torch.equal(reference[1], chunked[1])
    assert torch.allclose(reference[0], chunked[0])


def test_remeshing_polls_for_cancellation():
    vertices, faces = _torn(sphere_sdf(0.37))

    with pytest.raises(SamplingCancelled):
        _remesh(vertices, faces, is_cancelled=lambda: True)


def test_a_mesh_without_surface_remeshes_to_nothing():
    vertices = torch.zeros((0, 3))
    faces = torch.zeros((0, 3), dtype=torch.long)

    out_vertices, out_faces = _remesh(vertices, faces)

    assert out_vertices.shape == (0, 3)
    assert out_faces.shape == (0, 3)


def test_a_triangle_far_larger_than_a_voxel_is_scanned_within_the_pair_budget(monkeypatch):
    monkeypatch.setattr(remesh_module, "PAIR_BUDGET", 256)
    largest = []
    exact = remesh_module.point_triangle_distance_sq

    def recording(ap, ab, ac):
        largest.append(ap[..., 0].numel())
        return exact(ap, ab, ac)

    monkeypatch.setattr(remesh_module, "point_triangle_distance_sq", recording)
    box = trimesh.creation.box(extents=(0.6, 0.6, 0.6))
    vertices = torch.from_numpy(np.asarray(box.vertices, dtype=np.float32))
    faces = torch.from_numpy(np.asarray(box.faces, dtype=np.int64))

    out_vertices, out_faces = _remesh(vertices, faces)

    mesh = trimesh.Trimesh(out_vertices.numpy(), out_faces.numpy(), process=False)
    assert (edge_counts(out_faces.numpy()) == 2).all()
    assert len(mesh.split(only_watertight=False)) == 2
    assert mesh.is_winding_consistent
    assert max(largest) <= 256
