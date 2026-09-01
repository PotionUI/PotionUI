import torch

from src.platform.runtime.native.arch.trellis2.dual_grid import flexible_dual_grid_to_mesh

# The vendored o_voxel package (content/plugins/local/trellis2/vendor/TRELLIS.2/o-voxel)
# imports `_C` unconditionally at module scope (flexible_dual_grid.py:4), so it cannot be
# imported at all without the compiled CUDA extension — there is no CPU-importable oracle
# to cross-check against. All cases below are hand-derived from the vendored source.


def _cube_coords() -> torch.Tensor:
    return torch.tensor(
        [[x, y, z] for x in range(2) for y in range(2) for z in range(2)],
        dtype=torch.long,
    )


def _index_map(coords: torch.Tensor) -> dict:
    return {tuple(c.tolist()): i for i, c in enumerate(coords)}


def test_single_voxel_fully_intersected_yields_three_quads():
    coords = _cube_coords()
    idx = _index_map(coords)
    n = coords.shape[0]
    vertices = torch.zeros((n, 3))
    intersected = torch.zeros((n, 3), dtype=torch.bool)
    intersected[idx[(0, 0, 0)]] = torch.tensor([True, True, True])
    quad_lerp = torch.ones((n, 1))

    mesh_vertices, mesh_triangles = flexible_dual_grid_to_mesh(
        coords, vertices, intersected, quad_lerp,
        aabb=[[0, 0, 0], [2, 2, 2]], grid_size=2, train=False,
    )

    assert torch.equal(mesh_vertices, coords.to(torch.float32))
    expected_triangles = torch.tensor([
        [0, 1, 2], [2, 1, 3],
        [0, 4, 1], [1, 4, 5],
        [0, 2, 4], [4, 2, 6],
    ], dtype=torch.long)
    assert torch.equal(mesh_triangles, expected_triangles)


def test_shared_face_pair_produces_one_consistent_quad_each():
    coords = torch.tensor(
        [[x, y, 0] for x in range(3) for y in range(2)], dtype=torch.long
    )
    idx = _index_map(coords)
    n = coords.shape[0]
    vertices = torch.zeros((n, 3))
    intersected = torch.zeros((n, 3), dtype=torch.bool)
    intersected[idx[(0, 0, 0)], 2] = True
    intersected[idx[(1, 0, 0)], 2] = True
    quad_lerp = torch.ones((n, 1))

    mesh_vertices, mesh_triangles = flexible_dual_grid_to_mesh(
        coords, vertices, intersected, quad_lerp,
        aabb=[[0, 0, 0], [3, 2, 1]], grid_size=[3, 2, 1], train=False,
    )

    assert mesh_triangles.shape == (4, 3)
    expected_triangles = torch.tensor([
        [0, 1, 2], [2, 1, 3],
        [2, 3, 4], [4, 3, 5],
    ], dtype=torch.long)
    assert torch.equal(mesh_triangles, expected_triangles)

    shared_a, shared_b = idx[(1, 0, 0)], idx[(1, 1, 0)]
    faces_with_a = (mesh_triangles == shared_a).any(dim=1)
    faces_with_b = (mesh_triangles == shared_b).any(dim=1)
    assert faces_with_a.sum().item() == 3
    assert faces_with_b.sum().item() == 3
    assert (faces_with_a & faces_with_b).sum().item() == 2


def test_2x2x2_solid_cube_is_a_closed_manifold_with_correct_volume():
    coords = _cube_coords()
    idx = _index_map(coords)
    n = coords.shape[0]
    vertices = torch.zeros((n, 3))
    intersected = torch.zeros((n, 3), dtype=torch.bool)
    for base, axis in [
        ((0, 0, 0), 0), ((1, 0, 0), 0),
        ((0, 0, 0), 1), ((0, 1, 0), 1),
        ((0, 0, 0), 2), ((0, 0, 1), 2),
    ]:
        intersected[idx[base], axis] = True
    quad_lerp = torch.ones((n, 1))

    mesh_vertices, mesh_triangles = flexible_dual_grid_to_mesh(
        coords, vertices, intersected, quad_lerp,
        aabb=[[0, 0, 0], [2, 2, 2]], grid_size=2, train=False,
    )

    assert mesh_vertices.shape == (8, 3)
    assert mesh_triangles.shape == (12, 3)

    edges = set()
    for a, b, c in mesh_triangles.tolist():
        for u, v in ((a, b), (b, c), (c, a)):
            edges.add((min(u, v), max(u, v)))
    v_count, e_count, f_count = mesh_vertices.shape[0], len(edges), mesh_triangles.shape[0]
    assert v_count - e_count + f_count == 2

    v0 = mesh_vertices[mesh_triangles[:, 0]]
    v1 = mesh_vertices[mesh_triangles[:, 1]]
    v2 = mesh_vertices[mesh_triangles[:, 2]]
    signed_volume = (v0 * torch.cross(v1, v2, dim=-1)).sum(dim=-1).sum() / 6.0
    # The vendored per-axis offset table (edge_neighbor_voxel_offset) and quad-split
    # tables fix winding independent of which side of the axis a face sits on, so a
    # fully-closed cube built this way comes out consistently *inward*-facing (not a
    # bug in this port — asserting -volume, not +volume, is intentional and matches
    # the exact enclosed volume of the unit-voxel-sized cube, i.e. a true closed
    # manifold, not partial face cancellation).
    assert abs(signed_volume.item() - (-1.0)) < 1e-5


def test_quad_lerp_split_heuristic_picks_larger_diagonal_product():
    coords = _cube_coords()
    idx = _index_map(coords)
    n = coords.shape[0]
    vertices = torch.zeros((n, 3))
    intersected = torch.zeros((n, 3), dtype=torch.bool)
    intersected[idx[(0, 0, 0)], 2] = True
    quad_lerp = torch.ones((n, 1))
    quad_lerp[idx[(0, 0, 0)]] = 5.0
    quad_lerp[idx[(1, 1, 0)]] = 5.0
    quad_lerp[idx[(0, 1, 0)]] = 1.0
    quad_lerp[idx[(1, 0, 0)]] = 1.0

    mesh_vertices, mesh_triangles = flexible_dual_grid_to_mesh(
        coords, vertices, intersected, quad_lerp,
        aabb=[[0, 0, 0], [2, 2, 2]], grid_size=2, train=False,
    )

    quad = [idx[(0, 0, 0)], idx[(0, 1, 0)], idx[(1, 1, 0)], idx[(1, 0, 0)]]
    w = quad_lerp[quad].flatten()
    assert (w[0] * w[2]) > (w[1] * w[3])
    expected_triangles = torch.tensor([
        [quad[0], quad[1], quad[2]],
        [quad[0], quad[2], quad[3]],
    ], dtype=torch.long)
    assert torch.equal(mesh_triangles, expected_triangles)


def test_no_active_voxels_returns_empty_mesh():
    coords = torch.zeros((0, 3), dtype=torch.long)
    vertices = torch.zeros((0, 3))
    intersected = torch.zeros((0, 3), dtype=torch.bool)
    quad_lerp = torch.zeros((0, 1))

    mesh_vertices, mesh_triangles = flexible_dual_grid_to_mesh(
        coords, vertices, intersected, quad_lerp,
        aabb=[[0, 0, 0], [1, 1, 1]], grid_size=1, train=False,
    )

    assert mesh_vertices.shape == (0, 3)
    assert mesh_triangles.shape == (0, 3)


def test_active_voxels_with_no_intersection_yield_vertices_but_no_faces():
    coords = _cube_coords()
    n = coords.shape[0]
    vertices = torch.zeros((n, 3))
    intersected = torch.zeros((n, 3), dtype=torch.bool)
    quad_lerp = torch.ones((n, 1))

    mesh_vertices, mesh_triangles = flexible_dual_grid_to_mesh(
        coords, vertices, intersected, quad_lerp,
        aabb=[[0, 0, 0], [2, 2, 2]], grid_size=2, train=False,
    )

    assert mesh_vertices.shape == (n, 3)
    assert mesh_triangles.shape == (0, 3)


def test_train_mode_is_rejected():
    coords = _cube_coords()
    n = coords.shape[0]
    vertices = torch.zeros((n, 3))
    intersected = torch.zeros((n, 3), dtype=torch.bool)
    quad_lerp = torch.ones((n, 1))

    try:
        flexible_dual_grid_to_mesh(
            coords, vertices, intersected, quad_lerp,
            aabb=[[0, 0, 0], [2, 2, 2]], grid_size=2, train=True,
        )
        raised = False
    except AssertionError:
        raised = True
    assert raised
