from __future__ import annotations

import logging
import time
from typing import List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

MAX_COMPONENT_FACES = 2_000_000

StageRow = Tuple[str, int, int, int, int, float]


def _weld(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    faces = np.asarray(faces, dtype=np.int64)
    count = int(vertices.shape[0])
    used = np.zeros(count, dtype=bool)
    for column in range(3):
        used[faces[:, column]] = True
    used = np.flatnonzero(used)
    bits = (np.ascontiguousarray(vertices[used], dtype=np.float64) + 0.0).view(np.uint64)
    order = np.lexsort((bits[:, 2], bits[:, 1], bits[:, 0]))
    ordered = bits[order]
    del bits
    fresh = np.ones(order.shape[0], dtype=bool)
    fresh[1:] = (ordered[1:] != ordered[:-1]).any(axis=1)
    del ordered
    remap = np.empty(count, dtype=np.int64)
    remap[used[order]] = np.cumsum(fresh) - 1
    return remap[faces]


def _edge_keys(welded: np.ndarray) -> np.ndarray:
    count = welded.shape[0]
    stride = int(welded.max()) + 1
    keys = np.empty(3 * count, dtype=np.int64)
    scratch = np.empty(count, dtype=np.int64)
    for slot, (a, b) in enumerate(((0, 1), (1, 2), (2, 0))):
        part = keys[slot * count:(slot + 1) * count]
        np.minimum(welded[:, a], welded[:, b], out=part)
        part *= stride
        np.maximum(welded[:, a], welded[:, b], out=scratch)
        part += scratch
    return keys


def boundary_and_components(vertices: np.ndarray, faces: np.ndarray) -> Tuple[int, int]:
    faces = np.asarray(faces)
    if faces.shape[0] == 0:
        return 0, 0
    welded = _weld(np.asarray(vertices), faces)
    keys = _edge_keys(welded)
    if faces.shape[0] > MAX_COMPONENT_FACES:
        del welded
        keys.sort()
        fresh = np.ones(keys.shape[0] + 1, dtype=bool)
        np.not_equal(keys[1:], keys[:-1], out=fresh[1:-1])
        return int(np.count_nonzero(fresh[:-1] & fresh[1:])), -1
    unique_keys, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    boundary = int((counts == 1).sum())
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    face_count = faces.shape[0]
    face_ids = np.tile(np.arange(face_count), 3)
    graph = coo_matrix(
        (np.ones(face_ids.shape[0], dtype=np.int8), (face_ids, inverse.reshape(-1) + face_count)),
        shape=(face_count + unique_keys.shape[0],) * 2,
    )
    count, _ = connected_components(graph, directed=False)
    return boundary, int(count)


def format_stage_line(row: StageRow) -> str:
    name, faces, verts, boundary, components, seconds = row
    return (
        f"[TRELLIS2_MESH] stage={name} faces={faces} verts={verts} "
        f"boundary_edges={boundary} components={components} seconds={seconds:.2f}"
    )


def log_stage_row(row: StageRow) -> None:
    logger.info(format_stage_line(row))


class StageRecorder:
    def __init__(self) -> None:
        self.rows: List[StageRow] = []
        self._mark = time.perf_counter()

    def reset(self) -> None:
        self._mark = time.perf_counter()

    def record(self, name: str, vertices: np.ndarray, faces: np.ndarray) -> StageRow:
        seconds = time.perf_counter() - self._mark
        faces = np.asarray(faces)
        boundary, components = boundary_and_components(np.asarray(vertices), faces)
        row = (name, int(faces.shape[0]), int(np.asarray(vertices).shape[0]), boundary, components, seconds)
        self.rows.append(row)
        self._mark = time.perf_counter()
        return row
