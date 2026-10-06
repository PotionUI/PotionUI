from __future__ import annotations

from typing import Tuple

import numpy as np

_MIN_COMPONENT_AREA = 1e-5
_MAX_HOLE_PERIMETER = 3e-2


def _simplify(vertices: np.ndarray, faces: np.ndarray, target: int) -> Tuple[np.ndarray, np.ndarray]:
    import pyfqmr
    if faces.shape[0] <= target:
        return (vertices, faces)
    simplifier = pyfqmr.Simplify()
    simplifier.setMesh(vertices.astype(np.float64), faces.astype(np.int32))
    simplifier.simplify_mesh(target_count=int(target), aggressiveness=7, preserve_border=True, verbose=0)
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


def _fill_small_holes(mesh, max_perimeter: float=_MAX_HOLE_PERIMETER) -> None:
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
        if len(loop) < 3 or any((boundary_graph.degree[v] != 2 for v in loop)):
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
    new_edges = faces_to_edges(new_faces)
    hashable_new = hashable_rows(new_edges)
    hashable_old = hashable_rows(boundary)
    needs_reverse = np.isin(hashable_new, hashable_old).reshape((-1, 3)).any(axis=1)
    new_faces[needs_reverse] = np.fliplr(new_faces[needs_reverse])
    mesh.extend_faces(new_faces)


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
