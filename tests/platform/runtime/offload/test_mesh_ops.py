from typing import List

import numpy as np
import pytest
import trimesh
import xatlas

from src.platform.runtime.offload import mesh_ops


def _reference_boundary_edges(faces):
    directed = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    undirected = np.sort(directed, axis=1)
    _, inverse, counts = np.unique(undirected, axis=0, return_inverse=True, return_counts=True)
    once = counts[np.asarray(inverse).reshape(-1)] == 1
    return undirected[once], directed[once]


def _reference_simple_loops(edges) -> List[List[int]]:
    ends = edges.reshape(-1)
    order = np.argsort(ends, kind="stable")
    sorted_ends = ends[order]
    starts = np.searchsorted(sorted_ends, sorted_ends, side="left")
    degree = np.searchsorted(sorted_ends, sorted_ends, side="right") - starts
    other = edges[order // 2, 1 - order % 2]
    neighbours = {}
    simple = set()
    for vertex, deg, nxt in zip(sorted_ends.tolist(), degree.tolist(), other.tolist()):
        neighbours.setdefault(vertex, []).append(nxt)
        if deg == 2:
            simple.add(vertex)
    loops = []
    visited = set()
    for start in neighbours:
        if start in visited:
            continue
        loop = [start]
        visited.add(start)
        closed = start in simple
        previous, current = start, neighbours[start][0]
        while closed and current != start:
            if current in visited or current not in simple:
                closed = False
                break
            visited.add(current)
            loop.append(current)
            a, b = neighbours[current]
            previous, current = current, (b if a == previous else a)
        if not closed:
            stack = [start]
            while stack:
                vertex = stack.pop()
                for nxt in neighbours[vertex]:
                    if nxt not in visited:
                        visited.add(nxt)
                        stack.append(nxt)
            continue
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def _reference_project_ring(points):
    rolled = np.roll(points, -1, axis=0)
    normal = np.cross(points, rolled).sum(axis=0)
    length = np.linalg.norm(normal)
    if length < 1e-30:
        return None
    normal = normal / length
    seed = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(normal, seed)
    u = u / np.linalg.norm(u)
    v = np.cross(normal, u)
    centred = points - points.mean(axis=0)
    return np.stack([centred @ u, centred @ v], axis=1)


def _reference_triangulate_loop(loop, vertices, directed):
    points = np.asarray(vertices[loop], dtype=np.float64)
    ring = _reference_project_ring(points)
    if ring is None:
        return None
    triangles = mesh_ops._ear_clip(ring)
    if not triangles:
        return None
    ids = np.asarray(loop, dtype=np.int64)
    out = ids[np.asarray(triangles, dtype=np.int64)]
    successors = loop[1:] + loop[:1]
    same = sum(1 for a, b in zip(loop, successors) if (a, b) in directed)
    if 2 * same > len(loop):
        out = out[:, ::-1]
    return out


def _reference_fill_small_holes(mesh, max_perimeter=mesh_ops._MAX_HOLE_PERIMETER):
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if faces.shape[0] < 3:
        return
    edges, directed_edges = _reference_boundary_edges(faces)
    if edges.shape[0] < 3:
        return
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    directed = set(map(tuple, directed_edges.tolist()))
    new_faces = []
    for loop in _reference_simple_loops(edges):
        ring = vertices[loop + loop[:1]]
        if np.linalg.norm(ring[1:] - ring[:-1], axis=1).sum() > max_perimeter:
            continue
        patch = _reference_triangulate_loop(loop, vertices, directed)
        if patch is not None:
            new_faces.append(patch)
    if new_faces:
        mesh.faces = np.concatenate([faces, *new_faces]).astype(np.int64)


def _holed_grid(seed, size=26, spacing=5e-3):
    rng = np.random.default_rng(seed)
    xs, ys = np.meshgrid(np.arange(size), np.arange(size), indexing="ij")
    vertices = np.stack([xs.ravel() * spacing, ys.ravel() * spacing, np.zeros(size * size)], axis=1)
    vertices += rng.normal(scale=spacing * 0.2, size=vertices.shape)
    corner = (np.arange(size - 1)[:, None] * size + np.arange(size - 1)[None, :]).ravel()
    faces = np.concatenate([
        np.stack([corner, corner + size, corner + 1], axis=1),
        np.stack([corner + 1, corner + size, corner + size + 1], axis=1),
    ])
    keep = rng.random(faces.shape[0]) > 0.1
    return vertices, faces[keep]


def _holed_sphere(seed, radius=0.02, flipped=0.05):
    rng = np.random.default_rng(seed)
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=radius)
    vertices = np.asarray(sphere.vertices) + rng.normal(scale=radius * 0.01, size=sphere.vertices.shape)
    faces = np.asarray(sphere.faces, dtype=np.int64)
    flips = rng.random(faces.shape[0]) < flipped
    faces[flips] = faces[flips][:, ::-1]
    keep = rng.random(faces.shape[0]) > 0.06
    return vertices, faces[keep]


def _mobius(segments=14, width=0.01, radius=0.05, offset=0.0):
    angles = np.linspace(0.0, 2.0 * np.pi, segments, endpoint=False)
    vertices = []
    for angle in angles:
        centre = np.array([np.cos(angle), np.sin(angle), 0.0]) * radius
        across = np.array([np.cos(angle) * np.cos(angle / 2), np.sin(angle) * np.cos(angle / 2), np.sin(angle / 2)]) * width
        vertices.extend([centre - across, centre + across])
    faces = []
    for i in range(segments):
        a, b = 2 * i, 2 * i + 1
        if i + 1 < segments:
            c, d = 2 * i + 2, 2 * i + 3
        else:
            c, d = 1, 0
        faces.extend([[a, c, b], [b, c, d]])
    return np.asarray(vertices) + offset, np.asarray(faces, dtype=np.int64)


def _scene(seed):
    rng = np.random.default_rng(seed)
    parts = [_mobius(offset=np.array([0.3, 0.0, 0.0]))]
    for k in range(4):
        sphere = trimesh.creation.icosphere(subdivisions=1 + k % 2, radius=0.01 * (k + 1))
        parts.append((np.asarray(sphere.vertices) + np.array([0.0, 0.1 * k, 0.0]), np.asarray(sphere.faces, dtype=np.int64)))
    parts.insert(2, _mobius(segments=9, offset=np.array([-0.3, 0.0, 0.0])))
    vertices, faces, base = [], [], 0
    for part_vertices, part_faces in parts:
        vertices.append(part_vertices)
        faces.append(part_faces + base)
        base += part_vertices.shape[0]
    faces = np.concatenate(faces)
    faces = faces[rng.permutation(faces.shape[0])]
    flips = rng.random(faces.shape[0]) < 0.3
    faces[flips] = faces[flips][:, ::-1]
    return np.concatenate(vertices), faces


@pytest.mark.parametrize("seed", range(4))
def test_boundary_edges_match_the_previous_implementation_on_a_face_soup(seed):
    rng = np.random.default_rng(seed)
    faces = rng.integers(0, 25, size=(300, 3)).astype(np.int64)
    expected = _reference_boundary_edges(faces)
    actual = mesh_ops._boundary_edges(faces)
    assert np.array_equal(actual[0], expected[0])
    assert np.array_equal(actual[1], expected[1])


@pytest.mark.parametrize("seed", range(6))
def test_simple_loops_match_the_previous_implementation(seed):
    rng = np.random.default_rng(seed)
    edges = set()
    labels = rng.permutation(400)
    cursor = 0
    for length in rng.integers(3, 9, size=8).tolist():
        ring = labels[cursor:cursor + length].tolist()
        cursor += length
        edges.update(tuple(sorted((ring[i], ring[(i + 1) % length]))) for i in range(length))
    for length in rng.integers(2, 6, size=4).tolist():
        path = labels[cursor:cursor + length].tolist()
        cursor += length
        edges.update(tuple(sorted((path[i], path[i + 1]))) for i in range(length - 1))
    chorded = labels[cursor:cursor + 5].tolist()
    edges.update(tuple(sorted((chorded[i], chorded[(i + 1) % 5]))) for i in range(5))
    edges.add(tuple(sorted((chorded[0], chorded[2]))))
    edges.add((int(labels[cursor + 6]), int(labels[cursor + 6])))
    edges = np.asarray(sorted(edges), dtype=np.int64)[rng.permutation(len(edges))]
    expected = _reference_simple_loops(edges)
    assert len(expected) >= 8
    assert mesh_ops._simple_loops(edges) == expected


def _scrambled_sphere(seed):
    return _holed_sphere(seed, flipped=0.5)


@pytest.mark.parametrize("builder", [_holed_grid, _holed_sphere, _scrambled_sphere])
@pytest.mark.parametrize("seed", range(5))
def test_hole_fill_matches_the_previous_implementation(builder, seed):
    vertices, faces = builder(seed)
    expected = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    actual = trimesh.Trimesh(vertices=vertices, faces=faces.copy(), process=False)
    _reference_fill_small_holes(expected)
    mesh_ops._fill_small_holes(actual)
    assert expected.faces.shape[0] > faces.shape[0]
    assert np.array_equal(np.asarray(actual.faces), np.asarray(expected.faces))


@pytest.mark.parametrize("seed", range(4))
def test_fix_normals_matches_trimesh_on_flipped_non_orientable_scenes(seed):
    vertices, faces = _scene(seed)
    expected = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    actual = trimesh.Trimesh(vertices=vertices, faces=faces.copy(), process=False)
    trimesh.repair.fix_normals(expected, multibody=True)
    mesh_ops._fix_normals(actual)
    assert not np.array_equal(np.asarray(expected.faces), faces)
    assert np.array_equal(np.asarray(actual.faces), np.asarray(expected.faces))


def test_fix_normals_matches_trimesh_on_a_single_non_orientable_component():
    vertices, faces = _mobius(segments=17)
    rng = np.random.default_rng(3)
    flips = rng.random(faces.shape[0]) < 0.4
    faces[flips] = faces[flips][:, ::-1]
    expected = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    actual = trimesh.Trimesh(vertices=vertices, faces=faces.copy(), process=False)
    trimesh.repair.fix_normals(expected, multibody=True)
    mesh_ops._fix_normals(actual)
    assert np.array_equal(np.asarray(actual.faces), np.asarray(expected.faces))


def test_cleanup_is_deterministic():
    vertices, faces = _holed_sphere(7, radius=0.3)
    first = mesh_ops.clean_and_decimate_arrays(vertices.astype(np.float32), faces, 400)
    second = mesh_ops.clean_and_decimate_arrays(vertices.astype(np.float32), faces, 400)
    assert np.array_equal(first[0], second[0]) and np.array_equal(first[1], second[1])


@pytest.fixture(scope="module")
def closed_sphere():
    sphere = trimesh.creation.icosphere(subdivisions=3, radius=0.4)
    return np.asarray(sphere.vertices, dtype=np.float32), np.asarray(sphere.faces, dtype=np.int64)


def test_the_default_uv_quality_is_balanced_and_every_quality_is_declared():
    assert mesh_ops.DEFAULT_UV_QUALITY == "balanced"
    assert mesh_ops.UV_QUALITIES == ("fast", "balanced", "best")


def test_each_uv_quality_maps_to_its_xatlas_options():
    chart_defaults, pack_defaults = xatlas.ChartOptions(), xatlas.PackOptions()
    chart_names = ("max_iterations", "max_cost", "normal_deviation_weight", "roundness_weight", "straightness_weight", "normal_seam_weight", "texture_seam_weight")
    pack_names = ("padding", "resolution", "texels_per_unit", "bruteForce", "blockAlign", "bilinear", "rotate_charts", "rotate_charts_to_axis")
    expected = {
        "fast": ({"max_iterations": 0}, {}),
        "balanced": ({"max_iterations": 0}, {"resolution": 2048, "padding": 2}),
        "best": ({}, {"resolution": 2048, "padding": 2}),
    }
    for quality, (chart_changes, pack_changes) in expected.items():
        chart, pack = mesh_ops.uv_atlas_options(quality)
        for name in chart_names:
            assert getattr(chart, name) == pytest.approx(chart_changes.get(name, getattr(chart_defaults, name))), (quality, name)
        for name in pack_names:
            assert getattr(pack, name) == pytest.approx(pack_changes.get(name, getattr(pack_defaults, name))), (quality, name)


def test_an_unknown_uv_quality_is_refused():
    with pytest.raises(ValueError, match="uv quality"):
        mesh_ops.uv_atlas_options("ultra")


def test_fast_quality_is_the_plain_parametrize_call_without_chart_relocation(closed_sphere):
    vertices, faces = closed_sphere
    atlas = xatlas.Atlas()
    atlas.add_mesh(vertices, faces.astype(np.uint32))
    chart = xatlas.ChartOptions()
    chart.max_iterations = 0
    atlas.generate(chart, xatlas.PackOptions())
    vmapping, indices, uvs = atlas.get_mesh(0)
    uv_vertices, uv_faces, uv_coords, _ = mesh_ops.unwrap_uv_arrays(vertices, faces, "fast")
    assert np.array_equal(uv_vertices, vertices[vmapping])
    assert np.array_equal(uv_faces, indices.astype(np.int64))
    assert np.array_equal(uv_coords, uvs)


@pytest.mark.parametrize("quality", ["fast", "balanced", "best"])
def test_unwrap_is_deterministic_and_normalized_for_every_quality(closed_sphere, quality):
    vertices, faces = closed_sphere
    first = mesh_ops.unwrap_uv_arrays(vertices, faces, quality)
    second = mesh_ops.unwrap_uv_arrays(vertices, faces, quality)
    for a, b in zip(first, second):
        assert np.array_equal(a, b)
    uv_vertices, uv_faces, uvs, normals = first
    assert uv_faces.shape == faces.shape
    assert uvs.min() >= 0.0 and uvs.max() <= 1.0
    assert normals.shape == uv_vertices.shape


def _closed_blob(subdivisions=4):
    sphere = trimesh.creation.icosphere(subdivisions=subdivisions, radius=0.3)
    vertices = np.asarray(sphere.vertices)
    vertices = vertices * (1.0 + 0.2 * np.sin(5.0 * vertices[:, :1]) * np.cos(3.0 * vertices[:, 1:2]))
    return vertices.astype(np.float32), np.asarray(sphere.faces, dtype=np.int64)


def _pinched_pair():
    first_vertices, first_faces = _closed_blob(3)
    second_vertices = first_vertices.copy()
    second_vertices[:, 0] += 0.6 + 2 * float(first_vertices[:, 0].max())
    tip = int(np.argmax(first_vertices[:, 0]))
    far = int(np.argmin(second_vertices[:, 0]))
    second_vertices += first_vertices[tip] - second_vertices[far]
    second_faces = first_faces + first_vertices.shape[0]
    second_faces[second_faces == far + first_vertices.shape[0]] = tip
    return np.concatenate([first_vertices, second_vertices]), np.concatenate([first_faces, second_faces])


@pytest.fixture
def small_pieces(monkeypatch):
    monkeypatch.setattr(mesh_ops, "_PIECE_MIN_FACES", 1000)
    monkeypatch.setattr(mesh_ops, "_PIECE_FACES", 1500)


@pytest.mark.parametrize("builder", [_closed_blob, _pinched_pair])
def test_piecewise_decimation_keeps_a_closed_surface_closed(small_pieces, builder):
    from src.platform.runtime.offload.mesh_diagnostics import boundary_and_components
    vertices, faces = builder()
    assert boundary_and_components(vertices, faces)[0] == 0
    out_vertices, out_faces = mesh_ops._simplify_closed(vertices, faces, 600)
    assert 0 < out_faces.shape[0] <= 600
    assert boundary_and_components(out_vertices, out_faces)[0] == 0


def test_a_pinched_vertex_is_moved_into_one_piece_before_splitting(small_pieces):
    vertices, faces = _pinched_pair()
    count = faces.shape[0]
    owner = np.where(np.arange(count) < count // 2, 0, 1).astype(np.int64)
    shared = mesh_ops._shared_vertices(faces, owner, 2, vertices.shape[0])
    loose, _ = mesh_ops._unpinned_vertices(faces, owner, shared, 2)
    assert loose.tolist() == [int(np.argmax(vertices[: vertices.shape[0] // 2, 0]))]
    settled_owner = owner.copy()
    settled = mesh_ops._settle_owners(faces, settled_owner, 2, vertices.shape[0])
    assert settled is not None
    assert mesh_ops._unpinned_vertices(faces, settled_owner, settled, 2)[0].shape[0] == 0
    assert (settled_owner != owner).any()


def test_piecewise_decimation_is_deterministic(small_pieces):
    vertices, faces = _closed_blob()
    first = mesh_ops._simplify_closed(vertices, faces, 800)
    second = mesh_ops._simplify_closed(vertices, faces, 800)
    assert np.array_equal(first[0], second[0]) and np.array_equal(first[1], second[1])


def test_small_inputs_take_the_single_pass(monkeypatch):
    vertices, faces = _closed_blob(3)
    expected = mesh_ops._simplify(vertices, faces, 300)
    actual = mesh_ops._simplify_closed(vertices, faces, 300)
    assert np.array_equal(actual[0], expected[0]) and np.array_equal(actual[1], expected[1])


def test_pieces_run_in_worker_processes_give_the_same_result_as_inline(small_pieces, monkeypatch):
    import multiprocessing
    vertices, faces = _closed_blob()
    inline = mesh_ops._simplify_closed(vertices, faces, 800)
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())
    pooled = mesh_ops._simplify_closed(vertices, faces, 800)
    assert np.array_equal(inline[0], pooled[0]) and np.array_equal(inline[1], pooled[1])


def test_a_large_closed_surface_is_split_into_border_preserving_pieces(small_pieces, monkeypatch):
    calls = []
    real = mesh_ops._run_pieces

    def record(jobs):
        calls.append([job[1].shape[0] for job in jobs])
        return real(jobs)

    monkeypatch.setattr(mesh_ops, "_run_pieces", record)
    vertices, faces = _closed_blob()
    mesh_ops._simplify_closed(vertices, faces, 800)
    assert len(calls) == 1 and len(calls[0]) >= 4
    assert sum(calls[0]) == faces.shape[0]


def test_a_piece_worker_exits_as_soon_as_its_parent_is_gone(monkeypatch):
    exits = []
    monkeypatch.setattr(mesh_ops.os, "_exit", exits.append)

    class GoneParent:
        def join(self):
            return None

    mesh_ops._exit_with_parent(GoneParent())
    assert exits == [1]


def test_the_parent_watchdog_only_starts_inside_a_child_process(monkeypatch):
    import multiprocessing
    import threading
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self._target))
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: None)
    mesh_ops._watch_parent()
    assert started == []
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())
    mesh_ops._watch_parent()
    assert started == [mesh_ops._exit_with_parent]


def test_piece_pools_start_the_parent_watchdog_in_every_worker(small_pieces, monkeypatch):
    import concurrent.futures
    import multiprocessing
    created = {}

    class RecordingPool:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def map(self, fn, *iterables):
            return map(fn, *iterables)

    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", RecordingPool)
    monkeypatch.setattr(multiprocessing, "parent_process", lambda: object())
    vertices, faces = _closed_blob()
    mesh_ops._simplify_closed(vertices, faces, 800)
    assert created["initializer"] is mesh_ops._watch_parent
    assert created["max_workers"] > 1
