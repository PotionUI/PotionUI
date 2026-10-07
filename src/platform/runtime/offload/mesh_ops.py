from __future__ import annotations

from typing import List, Tuple

import numpy as np

_MIN_COMPONENT_AREA = 1e-5
_MAX_HOLE_PERIMETER = 3e-2


def _simplify(vertices: np.ndarray, faces: np.ndarray, target: int) -> Tuple[np.ndarray, np.ndarray]:
    import pyfqmr
    if faces.shape[0] <= target:
        return (vertices, faces)
    simplifier = pyfqmr.Simplify()
    simplifier.setMesh(vertices.astype(np.float64), faces.astype(np.int32))
    simplifier.simplify_mesh(target_count=int(target), aggressiveness=7, preserve_border=False, verbose=0)
    out_vertices, out_faces, _ = simplifier.getMesh()
    return (np.ascontiguousarray(out_vertices, dtype=np.float32), np.ascontiguousarray(out_faces, dtype=np.int64))


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
    directed = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    undirected = np.sort(directed, axis=1)
    _, inverse, counts = np.unique(undirected, axis=0, return_inverse=True, return_counts=True)
    once = counts[np.asarray(inverse).reshape(-1)] == 1
    return undirected[once], directed[once]


def _simple_loops(edges: np.ndarray) -> List[List[int]]:
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


def _project_ring(points: np.ndarray) -> np.ndarray:
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


def _triangulate_loop(loop: List[int], vertices: np.ndarray, directed: set) -> np.ndarray:
    points = np.asarray(vertices[loop], dtype=np.float64)
    ring = _project_ring(points)
    if ring is None:
        return None
    triangles = _ear_clip(ring)
    if not triangles:
        return None
    ids = np.asarray(loop, dtype=np.int64)
    out = ids[np.asarray(triangles, dtype=np.int64)]
    successors = loop[1:] + loop[:1]
    same = sum(1 for a, b in zip(loop, successors) if (a, b) in directed)
    if 2 * same > len(loop):
        out = out[:, ::-1]
    return out


def _fill_small_holes(mesh, max_perimeter: float=_MAX_HOLE_PERIMETER) -> None:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    if faces.shape[0] < 3:
        return
    edges, directed_edges = _boundary_edges(faces)
    if edges.shape[0] < 3:
        return
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    directed = set(map(tuple, directed_edges.tolist()))
    new_faces = []
    for loop in _simple_loops(edges):
        ring = vertices[loop + loop[:1]]
        if np.linalg.norm(ring[1:] - ring[:-1], axis=1).sum() > max_perimeter:
            continue
        patch = _triangulate_loop(loop, vertices, directed)
        if patch is not None:
            new_faces.append(patch)
    if new_faces:
        mesh.faces = np.concatenate([faces, *new_faces]).astype(np.int64)


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
    trimesh.repair.fix_normals(mesh, multibody=True)
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
    verts, tris = _simplify(verts, tris, decimation_target)
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


def unwrap_uv_arrays(vertices: np.ndarray, faces: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    import trimesh
    import xatlas
    source = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    source_normals = np.asarray(source.vertex_normals, dtype=np.float32)
    vmapping, indices, uvs = xatlas.parametrize(np.ascontiguousarray(vertices, dtype=np.float32), np.ascontiguousarray(faces, dtype=np.uint32))
    return (np.ascontiguousarray(vertices[vmapping], dtype=np.float32), np.ascontiguousarray(indices, dtype=np.int64), np.ascontiguousarray(uvs, dtype=np.float32), np.ascontiguousarray(source_normals[vmapping], dtype=np.float32))
