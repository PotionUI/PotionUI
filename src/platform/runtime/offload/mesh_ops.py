from __future__ import annotations

import os
from itertools import chain
from typing import List, Tuple

import numpy as np

_MIN_COMPONENT_AREA = 1e-5
_MAX_HOLE_PERIMETER = 3e-2
_PIECE_MIN_FACES = 4_000_000
_PIECE_FACES = 1_250_000
_PIECE_BUDGET = 6
_PIECE_WORKERS = 4
_PIECE_SETTLE_ROUNDS = 6

UV_QUALITIES = ("fast", "balanced", "best")
DEFAULT_UV_QUALITY = "balanced"
_UV_ATLAS_OPTIONS = {
    "fast": ({"max_iterations": 0}, {}),
    "balanced": ({"max_iterations": 0}, {"resolution": 2048, "padding": 2}),
    "best": ({}, {"resolution": 2048, "padding": 2}),
}


def _simplify(vertices: np.ndarray, faces: np.ndarray, target: int, preserve_border: bool = False) -> Tuple[np.ndarray, np.ndarray]:
    import pyfqmr
    if faces.shape[0] <= target:
        return (vertices, faces)
    simplifier = pyfqmr.Simplify()
    simplifier.setMesh(vertices.astype(np.float64), faces.astype(np.int32))
    simplifier.simplify_mesh(target_count=int(target), aggressiveness=7, preserve_border=preserve_border, verbose=0)
    out_vertices, out_faces, _ = simplifier.getMesh()
    return (np.ascontiguousarray(out_vertices, dtype=np.float32), np.ascontiguousarray(out_faces, dtype=np.int64))


def _simplify_piece(vertices: np.ndarray, faces: np.ndarray, target: int) -> Tuple[np.ndarray, np.ndarray]:
    return _simplify(vertices, faces, target, preserve_border=True)


def _split_faces(points: np.ndarray, parts: int) -> np.ndarray:
    owner = np.zeros(points.shape[0], dtype=np.int16)
    pending = [(np.arange(points.shape[0], dtype=np.int64), 0, parts)]
    while pending:
        index, first, span = pending.pop()
        if span <= 1 or index.shape[0] < 2:
            owner[index] = first
            continue
        sample = points[index[:: max(index.shape[0] // 65536, 1)]]
        axis = int(np.argmax(sample.max(axis=0) - sample.min(axis=0)))
        half = index.shape[0] // 2
        order = np.argpartition(points[index, axis], half)
        pending.append((index[order[half:]], first + span // 2, span // 2))
        pending.append((index[order[:half]], first, span // 2))
    return owner


def _match_rows(points: np.ndarray, table: np.ndarray) -> np.ndarray:
    rows = np.ascontiguousarray(np.concatenate([table, points]), dtype=np.float32)
    keys = rows.view(np.dtype((np.void, rows.dtype.itemsize * 3))).reshape(-1)
    _, first, inverse = np.unique(keys, return_index=True, return_inverse=True)
    hit = first[np.asarray(inverse).reshape(-1)[table.shape[0]:]]
    return np.where(hit < table.shape[0], hit, -1)


def _exit_with_parent(parent) -> None:
    parent.join()
    os._exit(1)


def _watch_parent() -> None:
    import multiprocessing
    import threading
    parent = multiprocessing.parent_process()
    if parent is not None:
        threading.Thread(target=_exit_with_parent, args=(parent,), daemon=True).start()


def _run_pieces(jobs: List[Tuple[np.ndarray, np.ndarray, int]]) -> List[Tuple[np.ndarray, np.ndarray]]:
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor
    workers = min(_PIECE_WORKERS, len(jobs), max((os.cpu_count() or 1) // 2, 1))
    if workers <= 1 or multiprocessing.parent_process() is None:
        return [_simplify_piece(*job) for job in jobs]
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"), initializer=_watch_parent) as pool:
        return list(pool.map(_simplify_piece, *zip(*jobs)))


def _shared_vertices(faces: np.ndarray, owner: np.ndarray, parts: int, vertex_count: int) -> np.ndarray:
    last = np.empty(vertex_count, dtype=owner.dtype)
    shared = np.zeros(vertex_count, dtype=bool)
    for column in range(3):
        last[faces[:, column]] = owner
    for column in range(3):
        corner = faces[:, column]
        shared[corner[last[corner] != owner]] = True
    return shared


def _unpinned_vertices(faces: np.ndarray, owner: np.ndarray, shared: np.ndarray, parts: int) -> Tuple[np.ndarray, np.ndarray]:
    touching = np.flatnonzero(shared[faces].any(axis=1))
    local = faces[touching]
    local_owner = owner[touching]
    vertex_count = shared.shape[0]
    src = local.reshape(-1)
    dst = np.roll(local, -1, axis=1).reshape(-1)
    edge_owner = np.repeat(local_owner, 3)
    keys = (np.minimum(src, dst) * vertex_count + np.maximum(src, dst)) * parts + edge_owner
    unique_keys, counts = np.unique(keys, return_counts=True)
    single = unique_keys[counts == 1]
    edge = single // parts
    single_owner = single % parts
    pinned = np.unique(np.concatenate([(edge // vertex_count) * parts + single_owner, (edge % vertex_count) * parts + single_owner]))
    corner = src[shared[src]] * parts + edge_owner[shared[src]]
    loose = np.setdiff1d(np.unique(corner), pinned, assume_unique=True)
    return np.unique(loose // parts), touching


def _settle_owners(faces: np.ndarray, owner: np.ndarray, parts: int, vertex_count: int) -> np.ndarray:
    for _ in range(_PIECE_SETTLE_ROUNDS):
        shared = _shared_vertices(faces, owner, parts, vertex_count)
        loose, touching = _unpinned_vertices(faces, owner, shared, parts)
        if loose.shape[0] == 0:
            return shared
        marked = np.zeros(vertex_count, dtype=bool)
        marked[loose] = True
        moving = touching[marked[faces[touching]].any(axis=1)]
        target = np.full(vertex_count, parts, dtype=np.int64)
        corners = faces[moving]
        np.minimum.at(target, corners.reshape(-1), np.repeat(owner[moving], 3))
        owner[moving] = np.where(marked[corners], target[corners], parts).min(axis=1)
    return None


def _simplify_closed(vertices: np.ndarray, faces: np.ndarray, target: int) -> Tuple[np.ndarray, np.ndarray]:
    count = faces.shape[0]
    if count < _PIECE_MIN_FACES or count <= target:
        return _simplify(vertices, faces, target)
    vertices = np.ascontiguousarray(vertices, dtype=np.float32)
    faces = np.ascontiguousarray(faces, dtype=np.int64)
    parts = 1 << max(int(np.ceil(np.log2(count / _PIECE_FACES))), 1)
    owner = _split_faces(vertices[faces[:, 0]], parts)
    shared = _settle_owners(faces, owner, parts, vertices.shape[0])
    if shared is None:
        return _simplify(vertices, faces, target)
    budget = max(target * _PIECE_BUDGET, target + 1)
    remap = np.empty(vertices.shape[0], dtype=np.int64)
    mark = np.zeros(vertices.shape[0], dtype=bool)
    order = np.argsort(owner, kind="stable")
    bounds = np.searchsorted(owner[order], np.arange(parts + 1))
    jobs = []
    for part in range(parts):
        piece_faces = faces[order[bounds[part]:bounds[part + 1]]]
        if piece_faces.shape[0] == 0:
            continue
        mark[piece_faces.reshape(-1)] = True
        used = np.flatnonzero(mark)
        mark[used] = False
        remap[used] = np.arange(used.shape[0], dtype=np.int64)
        jobs.append((vertices[used], remap[piece_faces], max(budget * piece_faces.shape[0] // count, 4)))
    del remap, mark, owner, order
    shared = vertices[np.flatnonzero(shared)]
    merged_vertices = [shared]
    merged_faces = []
    base = shared.shape[0]
    for piece_vertices, piece_faces in _run_pieces(jobs):
        mapping = _match_rows(piece_vertices, shared)
        fresh = np.flatnonzero(mapping < 0)
        mapping[fresh] = base + np.arange(fresh.shape[0], dtype=np.int64)
        base += fresh.shape[0]
        merged_vertices.append(piece_vertices[fresh])
        merged_faces.append(mapping[piece_faces])
    return _simplify(np.concatenate(merged_vertices), np.concatenate(merged_faces), target)


def _drop_small_components(mesh) -> None:
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
    if keep.any() and (not keep.all()):
        mesh.update_faces(keep)


def _boundary_edges(faces: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    faces = np.asarray(faces, dtype=np.int64)
    src = np.concatenate([faces[:, 0], faces[:, 1], faces[:, 2]])
    dst = np.concatenate([faces[:, 1], faces[:, 2], faces[:, 0]])
    lo = np.minimum(src, dst)
    hi = np.maximum(src, dst)
    keys = lo * (int(hi.max(initial=0)) + 1) + hi
    order = np.argsort(keys, kind="stable")
    ordered = keys[order]
    fresh = np.ones(ordered.shape[0] + 1, dtype=bool)
    np.not_equal(ordered[1:], ordered[:-1], out=fresh[1:-1])
    once = np.sort(order[fresh[:-1] & fresh[1:]])
    return np.stack([lo[once], hi[once]], axis=1), np.stack([src[once], dst[once]], axis=1)


def _simple_loops(edges: np.ndarray) -> List[List[int]]:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    if edges.shape[0] == 0:
        return []
    labels, local = np.unique(edges, return_inverse=True)
    local = np.asarray(local, dtype=np.int64).reshape(-1, 2)
    count = labels.shape[0]
    ends = local.reshape(-1)
    order = np.argsort(ends, kind="stable")
    other = local[:, ::-1].reshape(-1)[order]
    degree = np.bincount(ends, minlength=count)
    starts = np.cumsum(degree) - degree
    graph = coo_matrix((np.ones(local.shape[0], dtype=np.int8), (local[:, 0], local[:, 1])), shape=(count, count))
    _, component = connected_components(graph, directed=False)
    rejected = np.zeros(int(component.max()) + 1, dtype=bool)
    rejected[component[degree != 2]] = True
    rejected |= np.bincount(component) < 3
    roots, first = np.unique(component, return_index=True)
    seeds = np.sort(first[~rejected[roots]])
    if seeds.shape[0] == 0:
        return []
    simple = degree == 2
    head = np.where(simple, other[np.minimum(starts, other.shape[0] - 1)], -1).tolist()
    tail = np.where(simple, other[np.minimum(starts + 1, other.shape[0] - 1)], -1).tolist()
    names = labels.tolist()
    loops = []
    for start in seeds.tolist():
        loop = [names[start]]
        previous, current = start, head[start]
        while current != start:
            loop.append(names[current])
            a = head[current]
            previous, current = current, (tail[current] if a == previous else a)
        loops.append(loop)
    return loops


def _row_norms(rows: np.ndarray) -> np.ndarray:
    return np.sqrt(np.fromiter((row.dot(row) for row in rows), dtype=np.float64, count=rows.shape[0]))


def _project_rings(points: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    normal = np.cross(points, np.roll(points, -1, axis=1)).sum(axis=1)
    length = _row_norms(normal)
    valid = length >= 1e-30
    normal = normal[valid] / length[valid, None]
    points = points[valid]
    seed = np.where((np.abs(normal[:, 0]) < 0.9)[:, None], np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]))
    u = np.cross(normal, seed)
    u = u / _row_norms(u)[:, None]
    v = np.cross(normal, u)
    centred = points - points.mean(axis=1)[:, None, :]
    ring = np.stack([np.matmul(centred, u[:, :, None])[..., 0], np.matmul(centred, v[:, :, None])[..., 0]], axis=2)
    return ring, valid


def _cross2(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _ear_clip(ring: np.ndarray) -> List[Tuple[int, int, int]]:
    flat = ring.tolist()
    remaining = list(range(len(flat)))
    triangles = []
    while len(remaining) > 3:
        count = len(remaining)
        for k in range(count):
            i, j, l = remaining[k - 1], remaining[k], remaining[(k + 1) % count]
            a, b, c = flat[i], flat[j], flat[l]
            if _cross2(a, b, c) <= 0.0:
                continue
            blocked = False
            for m in remaining:
                if m in (i, j, l):
                    continue
                p = flat[m]
                if _cross2(a, b, p) >= 0.0 and _cross2(b, c, p) >= 0.0 and _cross2(c, a, p) >= 0.0:
                    blocked = True
                    break
            if not blocked:
                triangles.append((i, j, l))
                del remaining[k]
                break
        else:
            return []
    triangles.append(tuple(remaining))
    return triangles


def _loop_patches(ids: np.ndarray, vertices: np.ndarray, directed_keys: np.ndarray, max_perimeter: float) -> List[Tuple[int, np.ndarray]]:
    size = ids.shape[1]
    points = vertices[ids]
    closed = np.concatenate([points, points[:, :1]], axis=1)
    perimeter = np.linalg.norm(closed[:, 1:] - closed[:, :-1], axis=2).sum(axis=1)
    rows = np.flatnonzero(perimeter <= max_perimeter)
    if rows.shape[0] == 0:
        return []
    rings, valid = _project_rings(points[rows])
    rows = rows[valid]
    loops = ids[rows]
    successor_keys = loops * vertices.shape[0] + np.roll(loops, -1, axis=1)
    slot = np.minimum(np.searchsorted(directed_keys, successor_keys), max(directed_keys.shape[0] - 1, 0))
    same = (directed_keys[slot] == successor_keys).sum(axis=1)
    reverse = 2 * same > size
    patches = []
    for row, ring, loop, flip in zip(rows.tolist(), rings, loops, reverse.tolist()):
        triangles = _ear_clip(ring)
        if not triangles:
            continue
        out = loop[np.asarray(triangles, dtype=np.int64)]
        patches.append((row, out[:, ::-1] if flip else out))
    return patches


def _fill_small_holes(mesh, max_perimeter: float=_MAX_HOLE_PERIMETER) -> None:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if faces.shape[0] < 3:
        return
    edges, directed_edges = _boundary_edges(faces)
    if edges.shape[0] < 3:
        return
    loops = _simple_loops(edges)
    if not loops:
        return
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    directed_keys = np.sort(directed_edges[:, 0] * vertices.shape[0] + directed_edges[:, 1])
    lengths = np.fromiter(map(len, loops), dtype=np.int64, count=len(loops))
    flat = np.fromiter(chain.from_iterable(loops), dtype=np.int64, count=int(lengths.sum()))
    offsets = np.cumsum(lengths) - lengths
    found = []
    for size in np.unique(lengths).tolist():
        members = np.flatnonzero(lengths == size)
        ids = flat[offsets[members, None] + np.arange(size)]
        found.extend((int(members[row]), patch) for row, patch in _loop_patches(ids, vertices, directed_keys, max_perimeter))
    if found:
        found.sort(key=lambda item: item[0])
        mesh.faces = np.concatenate([faces, *(patch for _, patch in found)]).astype(np.int64)


def _breadth_first(root: int, neighbours: List[int], relation: List[bool], bounds: List[int], mark: bytearray, flip) -> List[int]:
    mark[root] = 1
    order = [root]
    head = 0
    while head < len(order):
        parent = order[head]
        head += 1
        parent_flip = flip[parent] if flip is not None else False
        for k in range(bounds[parent], bounds[parent + 1]):
            child = neighbours[k]
            if not mark[child]:
                mark[child] = 1
                if flip is not None:
                    flip[child] = parent_flip ^ relation[k]
                order.append(child)
    return order


def _fix_winding(mesh) -> None:
    if mesh.is_winding_consistent:
        return
    adjacency = np.asarray(mesh.face_adjacency, dtype=np.int64)
    if adjacency.shape[0] == 0:
        return
    faces = np.asarray(mesh.faces, dtype=np.int64).copy()
    count = faces.shape[0]
    keys = np.minimum(adjacency[:, 0], adjacency[:, 1]) * count + np.maximum(adjacency[:, 0], adjacency[:, 1])
    _, first = np.unique(keys, return_index=True)
    pairs = adjacency[np.sort(first)]
    left = faces[pairs[:, 0]]
    right = faces[pairs[:, 1]]
    left_edges = np.stack([left, np.roll(left, -1, axis=1)], axis=2)
    right_edges = np.stack([right, np.roll(right, -1, axis=1)], axis=2)
    same = (left_edges[:, :, None, :] == right_edges[:, None, :, :]).all(axis=3).any(axis=(1, 2))
    ends = pairs.reshape(-1)
    order = np.argsort(ends, kind="stable")
    neighbours = pairs[:, ::-1].reshape(-1)[order].tolist()
    relation = np.repeat(same, 2)[order].tolist()
    bounds = np.searchsorted(ends[order], np.arange(count + 1)).tolist()
    _, first_seen = np.unique(ends, return_index=True)
    roots = ends[np.sort(first_seen)].tolist()
    total = len(roots)
    flip = [False] * count
    seen = bytearray(count)
    walked = bytearray(count)
    for root in roots:
        if seen[root]:
            continue
        component = _breadth_first(root, neighbours, relation, bounds, seen, None)
        start = root
        if 2 * len(component) < total:
            start = next(iter(set(node for node in set(component))))
        _breadth_first(start, neighbours, relation, bounds, walked, flip)
    mask = np.asarray(flip, dtype=bool)
    if mask.any():
        faces[mask] = faces[mask][:, ::-1]
        mesh.faces = faces


def _fix_normals(mesh) -> None:
    import trimesh
    _fix_winding(mesh)
    trimesh.repair.fix_inversion(mesh, multibody=True)


def _tidy(mesh, recorder=None, prefix: str = "tidy") -> None:
    mesh.update_faces(mesh.unique_faces())
    _record(recorder, f"{prefix}_unique_faces", mesh)
    mesh.update_faces(mesh.nondegenerate_faces())
    _record(recorder, f"{prefix}_nondegenerate", mesh)
    mesh.remove_unreferenced_vertices()
    _record(recorder, f"{prefix}_unreferenced", mesh)
    _drop_small_components(mesh)
    _record(recorder, f"{prefix}_small_components", mesh)
    _fill_small_holes(mesh)
    _record(recorder, f"{prefix}_hole_fill", mesh)


def _record(recorder, name: str, mesh) -> None:
    if recorder is not None:
        recorder.record(name, np.asarray(mesh.vertices), np.asarray(mesh.faces))


def _clean_and_decimate(vertices: np.ndarray, faces: np.ndarray, decimation_target: int, recorder=None) -> Tuple[np.ndarray, np.ndarray]:
    import trimesh
    verts = np.ascontiguousarray(vertices, dtype=np.float32)
    tris = np.ascontiguousarray(faces, dtype=np.int64)
    if recorder is not None:
        recorder.reset()
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    mesh.remove_unreferenced_vertices()
    _record(recorder, "clean_unreferenced", mesh)
    _fill_small_holes(mesh)
    _record(recorder, "clean_hole_fill", mesh)
    verts, tris = _simplify(np.asarray(mesh.vertices), np.asarray(mesh.faces), decimation_target * 3)
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    _record(recorder, "decimate_3x", mesh)
    _tidy(mesh, recorder, "tidy_3x")
    verts, tris = _simplify(np.asarray(mesh.vertices), np.asarray(mesh.faces), decimation_target)
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    _record(recorder, "decimate_1x", mesh)
    _tidy(mesh, recorder, "tidy_1x")
    _fix_normals(mesh)
    _record(recorder, "fix_normals", mesh)
    return (np.ascontiguousarray(mesh.vertices, dtype=np.float32), np.ascontiguousarray(mesh.faces, dtype=np.int64))


def fill_holes_traced(vertices: np.ndarray, faces: np.ndarray):
    import trimesh
    from src.platform.runtime.offload.mesh_diagnostics import StageRecorder

    recorder = StageRecorder()
    mesh = trimesh.Trimesh(vertices=np.ascontiguousarray(vertices, dtype=np.float32), faces=np.ascontiguousarray(faces, dtype=np.int64), process=False)
    if mesh.faces.shape[0]:
        mesh.update_faces(mesh.nondegenerate_faces())
    _fill_small_holes(mesh)
    _record(recorder, "fill_holes", mesh)
    return (np.ascontiguousarray(mesh.vertices, dtype=np.float32), np.ascontiguousarray(mesh.faces, dtype=np.int64), recorder.rows)


def decimate_traced(vertices: np.ndarray, faces: np.ndarray, decimation_target: int, source_stage: str = "remesh"):
    import trimesh
    from src.platform.runtime.offload.mesh_diagnostics import StageRecorder

    recorder = StageRecorder()
    verts = np.ascontiguousarray(vertices, dtype=np.float32)
    tris = np.ascontiguousarray(faces, dtype=np.int64)
    recorder.record(source_stage, verts, tris)
    recorder.reset()
    verts, tris = _simplify_closed(verts, tris, decimation_target)
    mesh = trimesh.Trimesh(vertices=verts, faces=tris, process=False)
    mesh.remove_unreferenced_vertices()
    _record(recorder, "decimate", mesh)
    return (np.ascontiguousarray(mesh.vertices, dtype=np.float32), np.ascontiguousarray(mesh.faces, dtype=np.int64), recorder.rows)


def clean_and_decimate_arrays(vertices: np.ndarray, faces: np.ndarray, decimation_target: int) -> Tuple[np.ndarray, np.ndarray]:
    return _clean_and_decimate(vertices, faces, decimation_target)


def clean_and_decimate_traced(vertices: np.ndarray, faces: np.ndarray, decimation_target: int):
    from src.platform.runtime.offload.mesh_diagnostics import StageRecorder

    recorder = StageRecorder()
    out_vertices, out_faces = _clean_and_decimate(vertices, faces, decimation_target, recorder)
    return out_vertices, out_faces, recorder.rows


def uv_atlas_options(quality: str):
    import xatlas
    if quality not in _UV_ATLAS_OPTIONS:
        raise ValueError(f"unknown uv quality {quality!r}; expected one of {', '.join(UV_QUALITIES)}")
    chart_values, pack_values = _UV_ATLAS_OPTIONS[quality]
    chart, pack = xatlas.ChartOptions(), xatlas.PackOptions()
    for name, value in chart_values.items():
        setattr(chart, name, value)
    for name, value in pack_values.items():
        setattr(pack, name, value)
    return chart, pack


def unwrap_uv_arrays(vertices: np.ndarray, faces: np.ndarray, quality: str = DEFAULT_UV_QUALITY) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    import trimesh
    import xatlas
    chart_options, pack_options = uv_atlas_options(quality)
    source = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    source_normals = np.asarray(source.vertex_normals, dtype=np.float32)
    atlas = xatlas.Atlas()
    atlas.add_mesh(np.ascontiguousarray(vertices, dtype=np.float32), np.ascontiguousarray(faces, dtype=np.uint32))
    atlas.generate(chart_options, pack_options)
    vmapping, indices, uvs = atlas.get_mesh(0)
    return (np.ascontiguousarray(vertices[vmapping], dtype=np.float32), np.ascontiguousarray(indices, dtype=np.int64), np.ascontiguousarray(uvs, dtype=np.float32), np.ascontiguousarray(source_normals[vmapping], dtype=np.float32))
