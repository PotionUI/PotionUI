"""Tests for the mesh format registry and its built-in `.glb` entry.

`probe_glb` itself is exercised more heavily (through real save/reject paths)
by tests/features/generation/handlers/test_mesh_handler.py; this file covers
the registry object - membership, lookup, and the one bundled format - plus a
direct pass/fail check of `probe_glb` against a real `.glb`.
"""

import pytest

from src.platform.filesystem.mesh_formats import (
    InvalidMeshError,
    MeshFormat,
    MeshFormatRegistry,
    mesh_format_registry,
    probe_glb,
)
from tests.fixtures.mesh_fixtures import TRIANGLE_FACE_COUNT, TRIANGLE_VERTEX_COUNT, build_minimal_glb


class TestGlbIsRegistered:
    """The shared singleton carries exactly the one format the project ships."""

    def test_glb_is_registered(self):
        assert mesh_format_registry.is_registered('.glb')
        assert mesh_format_registry.is_registered('.GLB')

    def test_glb_mime_type(self):
        fmt = mesh_format_registry.get('.glb')
        assert fmt is not None
        assert fmt.mime_type == 'model/gltf-binary'

    def test_unregistered_extension(self):
        assert mesh_format_registry.get('.ply') is None
        assert not mesh_format_registry.is_registered('.ply')

    def test_mime_types_map(self):
        assert mesh_format_registry.mime_types()['.glb'] == 'model/gltf-binary'


class TestMeshFormatRegistry:
    """A private instance behaves the same way as the shared singleton."""

    def test_register_and_get(self):
        registry = MeshFormatRegistry()
        fmt = MeshFormat(extension='.ply', mime_type='application/x-ply', probe=lambda path: (1, 1))

        registry.register(fmt)

        assert registry.is_registered('.ply')
        assert registry.get('.PLY') is fmt
        assert registry.extensions() == ('.ply',)

    def test_get_unregistered_returns_none(self):
        registry = MeshFormatRegistry()
        assert registry.get('.glb') is None

    def test_register_is_case_normalized(self):
        registry = MeshFormatRegistry()
        registry.register(MeshFormat(extension='.PLY', mime_type='application/x-ply', probe=lambda p: (None, None)))

        assert registry.is_registered('.ply')
        assert registry.get('.ply') is not None


class TestProbeGlb:
    """Direct exercise of the registered `.glb` probe."""

    def test_valid_glb_returns_counts(self, tmp_path):
        path = tmp_path / "mesh.glb"
        path.write_bytes(build_minimal_glb())

        vertex_count, face_count = probe_glb(str(path))

        assert vertex_count == TRIANGLE_VERTEX_COUNT
        assert face_count == TRIANGLE_FACE_COUNT

    def test_bad_magic_raises_invalid_mesh_error(self, tmp_path):
        path = tmp_path / "mesh.glb"
        path.write_bytes(b'NOPE' + build_minimal_glb()[4:])

        with pytest.raises(InvalidMeshError):
            probe_glb(str(path))

    def test_registered_probe_matches_module_function(self):
        """The callable in the registry is the same one this module exports."""
        assert mesh_format_registry.get('.glb').probe is probe_glb
