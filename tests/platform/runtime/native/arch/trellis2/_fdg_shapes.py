from __future__ import annotations

import numpy as np
import torch

_CORNER = np.array([[0, 1, 1], [1, 0, 1], [1, 1, 0]])
_RING = np.array([
    [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]],
    [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]],
    [[0, 0, 0], [0, 1, 0], [1, 1, 0], [1, 0, 0]],
])


def sphere_sdf(radius):
    def sdf(p):
        return np.linalg.norm(p, axis=-1) - radius

    def project(p):
        return p * (radius / np.maximum(np.linalg.norm(p, axis=-1, keepdims=True), 1e-12))

    return sdf, project


def torus_sdf(major, minor):
    def sdf(p):
        ring = np.linalg.norm(p[..., [0, 2]], axis=-1) - major
        return np.sqrt(ring**2 + p[..., 1] ** 2) - minor

    def project(p):
        planar = p[..., [0, 2]]
        planar_len = np.maximum(np.linalg.norm(planar, axis=-1, keepdims=True), 1e-12)
        centre = np.zeros_like(p)
        centre[..., [0, 2]] = planar / planar_len * major
        offset = p - centre
        return centre + offset * (minor / np.maximum(np.linalg.norm(offset, axis=-1, keepdims=True), 1e-12))

    return sdf, project


def tear_quad_pairs(faces, count, seed=0):
    faces = np.asarray(faces, dtype=np.int64)
    quads = faces.reshape(-1, 6)
    quad_vertices = [set(q.tolist()) for q in quads]
    by_vertex = {}
    for index, verts in enumerate(quad_vertices):
        for v in verts:
            by_vertex.setdefault(v, []).append(index)
    rng = np.random.default_rng(seed)
    claimed = set()
    removed = []
    for index in rng.permutation(len(quads)).tolist():
        if len(removed) == 2 * count:
            break
        if quad_vertices[index] & claimed:
            continue
        partners = {
            other for v in quad_vertices[index] for other in by_vertex[v]
            if other != index and len(quad_vertices[other] & quad_vertices[index]) == 2
        }
        partners = [p for p in sorted(partners) if not (quad_vertices[p] - quad_vertices[index]) & claimed]
        if not partners:
            continue
        patch = quad_vertices[index] | quad_vertices[partners[0]]
        ring = {v for p in patch for q in by_vertex[p] for v in quad_vertices[q]}
        claimed |= ring
        removed += [index, partners[0]]
    keep = np.ones(len(quads), dtype=bool)
    keep[removed] = False
    return quads[keep].reshape(-1, 3), len(removed) // 2


def dual_grid_fields(shape, resolution):
    sdf, project = shape
    axis = np.arange(resolution)
    cells = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1).reshape(-1, 3)
    to_world = lambda corner: corner / resolution - 0.5
    flags = np.zeros((cells.shape[0], 3), dtype=bool)
    for k in range(3):
        start = cells + _CORNER[k]
        end = start.copy()
        end[:, k] += 1
        flags[:, k] = (sdf(to_world(start)) < 0) != (sdf(to_world(end)) < 0)
        flags[:, k] &= (end < resolution).all(axis=1) & (start < resolution).all(axis=1)
    active = np.zeros((resolution,) * 3, dtype=bool)
    for k in range(3):
        owners = cells[flags[:, k]]
        for offset in _RING[k]:
            ring = owners + offset
            ring = ring[(ring < resolution).all(axis=1)]
            active[ring[:, 0], ring[:, 1], ring[:, 2]] = True
    coords = np.argwhere(active)
    linear = (cells[:, 0] * resolution + cells[:, 1]) * resolution + cells[:, 2]
    lookup = np.full(resolution**3, -1)
    lookup[linear] = np.arange(cells.shape[0])
    rows = lookup[(coords[:, 0] * resolution + coords[:, 1]) * resolution + coords[:, 2]]
    centres = to_world(coords + 0.5)
    offsets = (project(centres) + 0.5) * resolution - coords
    return (
        torch.from_numpy(coords).long(),
        torch.from_numpy(offsets.astype(np.float32)),
        torch.from_numpy(flags[rows]),
        torch.ones((coords.shape[0], 1)),
    )


def edge_counts(faces):
    faces = np.asarray(faces, dtype=np.int64)
    edges = np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return counts


def welded(vertices, faces):
    _, inverse = np.unique(np.asarray(vertices, dtype=np.float64), axis=0, return_inverse=True)
    return np.asarray(inverse).reshape(-1)[np.asarray(faces, dtype=np.int64)]


def directed_duplicates(faces):
    faces = np.asarray(faces, dtype=np.int64)
    directed = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    _, counts = np.unique(directed, axis=0, return_counts=True)
    return int((counts > 1).sum())
