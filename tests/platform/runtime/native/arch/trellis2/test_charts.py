import math

import numpy as np
import pytest
import torch
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from src.platform.runtime.native.arch.trellis2.charts import compute_charts
from src.platform.runtime.native.errors import SamplingCancelled


def _tensors(mesh):
    return (
        torch.from_numpy(np.asarray(mesh.vertices, dtype=np.float32)),
        torch.from_numpy(np.asarray(mesh.faces, dtype=np.int64)),
    )


def _sphere(subdivisions=3):
    return trimesh.creation.icosphere(subdivisions=subdivisions, radius=0.4)


def _grid(cells=12):
    xs, ys = np.meshgrid(np.linspace(0, 1, cells + 1), np.linspace(0, 1, cells + 1), indexing="ij")
    vertices = np.stack([xs.ravel(), ys.ravel(), np.zeros(xs.size)], axis=1)
    index = np.arange(xs.size).reshape(cells + 1, cells + 1)
    a, b = index[:-1, :-1].ravel(), index[1:, :-1].ravel()
    c, d = index[1:, 1:].ravel(), index[:-1, 1:].ravel()
    faces = np.concatenate([np.stack([a, b, c], axis=1), np.stack([a, c, d], axis=1)])
    return trimesh.Trimesh(vertices, faces, process=False)


def _chart_components(mesh, charts):
    adjacency = np.asarray(mesh.face_adjacency)
    same = adjacency[charts[adjacency[:, 0]] == charts[adjacency[:, 1]]]
    count = mesh.faces.shape[0]
    graph = coo_matrix((np.ones(same.shape[0]), (same[:, 0], same[:, 1])), shape=(count, count))
    return connected_components(graph, directed=False)[0]


def test_every_face_gets_one_compact_chart_id():
    charts = compute_charts(*_tensors(_sphere())).numpy()
    assert charts.shape == (_sphere().faces.shape[0],)
    assert set(np.unique(charts).tolist()) == set(range(int(charts.max()) + 1))


def test_every_chart_is_one_edge_connected_patch():
    mesh = _sphere(4)
    charts = compute_charts(*_tensors(mesh), max_faces=200).numpy()
    assert _chart_components(mesh, charts) == int(charts.max()) + 1


def test_a_tight_cone_splits_a_cube_into_its_six_sides():
    mesh = trimesh.creation.box(extents=(0.6, 0.6, 0.6)).subdivide().subdivide()
    charts = compute_charts(*_tensors(mesh), cone_half_angle=math.radians(10.0)).numpy()
    assert int(charts.max()) + 1 == 6
    normals = np.asarray(mesh.face_normals)
    for chart in range(6):
        assert np.allclose(normals[charts == chart], normals[charts == chart][0], atol=1e-6)


def test_a_flat_grid_is_a_single_chart():
    charts = compute_charts(*_tensors(_grid())).numpy()
    assert np.all(charts == 0)


def test_no_chart_grows_past_the_face_cap():
    charts = compute_charts(*_tensors(_sphere(4)), max_faces=128).numpy()
    sizes = np.bincount(charts)
    assert sizes.max() <= 128
    assert sizes.shape[0] < 5120 // 16


def test_the_cone_bounds_the_normal_spread_inside_every_chart():
    mesh = _sphere(4)
    cone = math.radians(25.0)
    charts = compute_charts(*_tensors(mesh), cone_half_angle=cone).numpy()
    normals = np.asarray(mesh.face_normals)
    assert int(charts.max()) + 1 > 6
    for chart in range(int(charts.max()) + 1):
        members = normals[charts == chart]
        cosines = np.clip(members @ members.T, -1.0, 1.0)
        assert np.arccos(cosines).max() <= 2.0 * cone + 1e-4


def test_disconnected_shells_never_share_a_chart():
    first = _sphere(2)
    second = _sphere(2)
    second.apply_translation((2.0, 0.0, 0.0))
    mesh = trimesh.util.concatenate([first, second])
    charts = compute_charts(*_tensors(mesh), max_faces=10_000).numpy()
    half = first.faces.shape[0]
    assert not set(charts[:half].tolist()) & set(charts[half:].tolist())


def test_a_zero_area_face_joins_its_neighbours_instead_of_cutting_them_apart():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0.5, 0.5, 0]], dtype=np.float32)
    faces = np.array([[0, 1, 4], [1, 2, 4], [0, 4, 2], [0, 2, 3]], dtype=np.int64)
    charts = compute_charts(torch.from_numpy(vertices), torch.from_numpy(faces)).numpy()
    assert np.all(charts == 0)


def test_clustering_is_deterministic():
    tensors = _tensors(_sphere(4))
    assert torch.equal(compute_charts(*tensors, max_faces=300), compute_charts(*tensors, max_faces=300))


def test_an_empty_mesh_has_no_charts():
    charts = compute_charts(torch.zeros((0, 3)), torch.zeros((0, 3), dtype=torch.int64))
    assert charts.shape == (0,)


def test_clustering_stops_when_cancelled():
    with pytest.raises(SamplingCancelled):
        compute_charts(*_tensors(_sphere()), is_cancelled=lambda: True)
