import logging

import numpy as np
import pytest
import trimesh

from src.platform.runtime.offload import mesh_diagnostics
from src.platform.runtime.offload.mesh_diagnostics import (
    StageRecorder,
    boundary_and_components,
    format_stage_line,
    log_stage_row,
)
from src.platform.runtime.offload.mesh_ops import clean_and_decimate_traced


@pytest.fixture(scope="module")
def sphere():
    mesh = trimesh.creation.icosphere(subdivisions=2)
    return np.asarray(mesh.vertices, dtype=np.float64), np.asarray(mesh.faces, dtype=np.int64)


def test_closed_sphere_has_no_boundary_edges_and_one_component(sphere):
    assert boundary_and_components(*sphere) == (0, 1)


def test_sphere_with_faces_removed_has_boundary_edges(sphere):
    vertices, faces = sphere
    boundary, components = boundary_and_components(vertices, faces[10:])
    assert boundary > 0
    assert components == 2


def test_vertices_duplicated_by_position_are_welded_before_counting(sphere):
    vertices, faces = sphere
    split_vertices = vertices[faces].reshape(-1, 3)
    split_faces = np.arange(split_vertices.shape[0]).reshape(-1, 3)
    assert boundary_and_components(split_vertices, split_faces) == (0, 1)


def test_two_disjoint_spheres_are_two_components(sphere):
    vertices, faces = sphere
    both_vertices = np.concatenate([vertices, vertices + 5.0])
    both_faces = np.concatenate([faces, faces + vertices.shape[0]])
    assert boundary_and_components(both_vertices, both_faces) == (0, 2)


def test_components_are_skipped_above_the_face_limit(sphere, monkeypatch):
    monkeypatch.setattr(mesh_diagnostics, "MAX_COMPONENT_FACES", 5)
    assert boundary_and_components(*sphere) == (0, -1)


def test_stage_line_format(sphere, caplog):
    recorder = StageRecorder()
    row = recorder.record("raw", *sphere)
    assert format_stage_line(row).startswith(
        f"[TRELLIS2_MESH] stage=raw faces={sphere[1].shape[0]} verts={sphere[0].shape[0]} "
        "boundary_edges=0 components=1 seconds="
    )
    with caplog.at_level(logging.INFO, logger=mesh_diagnostics.logger.name):
        log_stage_row(row)
    assert "[TRELLIS2_MESH] stage=raw" in caplog.text


def test_traced_cleanup_reports_every_substep(sphere):
    vertices, faces = sphere
    _, out_faces, rows = clean_and_decimate_traced(vertices.astype(np.float32), faces, 100)
    names = [row[0] for row in rows]
    assert names[0] == "clean_unreferenced"
    assert "decimate_1x" in names and "tidy_1x_hole_fill" in names and names[-1] == "fix_normals"
    assert rows[-1][1] == out_faces.shape[0]


def test_signed_zero_coordinates_weld_together(sphere):
    vertices, faces = sphere
    split_vertices = vertices[faces].reshape(-1, 3)
    split_vertices[np.isclose(split_vertices, 0.0)] = 0.0
    negated = split_vertices.copy()
    negated[negated == 0.0] = -0.0
    split_vertices[1::2] = negated[1::2]
    split_faces = np.arange(split_vertices.shape[0]).reshape(-1, 3)
    assert np.signbit(split_vertices[split_vertices == 0.0]).any()
    assert boundary_and_components(split_vertices, split_faces) == (0, 1)


def test_boundary_count_above_the_face_limit_matches_the_count_below_it(sphere, monkeypatch):
    vertices, faces = sphere
    opened = faces[np.arange(faces.shape[0]) % 7 != 0]
    below = boundary_and_components(vertices, opened)
    monkeypatch.setattr(mesh_diagnostics, "MAX_COMPONENT_FACES", 5)
    assert below[0] > 0
    assert boundary_and_components(vertices, opened) == (below[0], -1)
