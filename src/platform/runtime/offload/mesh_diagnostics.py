from __future__ import annotations

import logging
import time
from typing import List, Tuple

import numpy as np

logger = logging.getLogger(__name__)

MAX_COMPONENT_FACES = 2_000_000

StageRow = Tuple[str, int, int, int, int, float]


def _weld(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    used, inverse = np.unique(faces, return_inverse=True)
    positions = np.ascontiguousarray(vertices[used], dtype=np.float64)
    _, weld = np.unique(positions, axis=0, return_inverse=True)
    return np.asarray(weld).reshape(-1)[inverse].reshape(faces.shape)


def _edge_keys(welded: np.ndarray) -> np.ndarray:
    edges = np.concatenate([welded[:, [0, 1]], welded[:, [1, 2]], welded[:, [2, 0]]], axis=0)
    edges.sort(axis=1)
    return edges[:, 0].astype(np.int64) * (int(welded.max()) + 1) + edges[:, 1]


def boundary_and_components(vertices: np.ndarray, faces: np.ndarray) -> Tuple[int, int]:
    faces = np.asarray(faces)
    if faces.shape[0] == 0:
        return 0, 0
    welded = _weld(np.asarray(vertices), faces)
    keys = _edge_keys(welded)
    unique_keys, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    boundary = int((counts == 1).sum())
    if faces.shape[0] > MAX_COMPONENT_FACES:
        return boundary, -1
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
