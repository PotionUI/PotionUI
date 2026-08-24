"""
Registry of self-contained 3D mesh formats accepted for storage.

Lives in `platform`, not `features`: `FileStore` (`src/platform/filesystem/
file_store.py`) needs to classify a file's extension, and the layering rule
in CLAUDE.md runs one way only - `platform` may not import `src.features`.
Byte-level container parsing has no feature dependency of its own, so the
probe lives here too rather than being split into a platform-side extension
list plus a features-side probe table that would have to be kept in lockstep
by hand.

Admission rule for a new entry - read this before adding one: a mesh format
is eligible ONLY if it is SELF-CONTAINED - geometry, PBR materials and
textures inside one binary file - so a mesh is exactly one file in the
storage directory, never a base mesh plus loose material/texture files
sitting alongside it (an `.obj` + `.mtl` + texture directory is explicitly
NOT eligible under this rule; nothing here should ever try to make that shape
work). A candidate that meets the bar is a single `mesh_format_registry.
register()` call, not a change to every mesh-aware call site.
"""

from __future__ import annotations

import json
import os
import struct
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

# path -> (vertex_count, face_count); raises InvalidMeshError on a malformed
# file. Counts are best-effort and may legitimately come back as None.
MeshProbe = Callable[[str], Tuple[Optional[int], Optional[int]]]


class InvalidMeshError(ValueError):
    """Raised when a file does not parse as its claimed mesh format."""


@dataclass(frozen=True)
class MeshFormat:
    """One registered, self-contained mesh format."""

    extension: str  # e.g. '.glb', lowercase, including the dot
    mime_type: str
    probe: MeshProbe


class MeshFormatRegistry:
    """Extension -> `MeshFormat` lookup."""

    def __init__(self) -> None:
        self._by_extension: Dict[str, MeshFormat] = {}

    def register(self, fmt: MeshFormat) -> None:
        self._by_extension[fmt.extension.lower()] = fmt

    def get(self, extension: str) -> Optional[MeshFormat]:
        """Look up a registered format by extension (e.g. '.glb'), or None."""
        return self._by_extension.get(extension.lower())

    def is_registered(self, extension: str) -> bool:
        return extension.lower() in self._by_extension

    def extensions(self) -> Tuple[str, ...]:
        return tuple(self._by_extension.keys())

    def mime_types(self) -> Dict[str, str]:
        """Extension -> mime type, for every registered format."""
        return {ext: fmt.mime_type for ext, fmt in self._by_extension.items()}


mesh_format_registry = MeshFormatRegistry()


# --- glTF binary (.glb) ---------------------------------------------------
#
# Reading the container needs no third-party library: a .glb is a 12-byte
# header followed by length-prefixed chunks, the first of which is the glTF
# JSON document. `trimesh` is not a dependency of this project and is not
# worth becoming one to read two integers out of a JSON blob.
#
# Spec: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#glb-file-format-specification

GLB_MAGIC = b'glTF'
_GLB_HEADER_SIZE = 12
_GLB_CHUNK_HEADER_SIZE = 8
_GLB_CHUNK_TYPE_JSON = 0x4E4F534A
_GLTF_MODE_TRIANGLES = 4

# The JSON chunk of a sane asset is kilobytes; the geometry lives in the
# binary chunk. Refuse to buffer a pathological one into memory.
_MAX_JSON_CHUNK_BYTES = 64 * 1024 * 1024


def probe_glb(path: str) -> Tuple[Optional[int], Optional[int]]:
    """Validate `path` as a glTF-binary file and read its geometry counts.

    Raises `InvalidMeshError` if the file is not a well-formed .glb - the
    container is checked, so a pipe cannot get arbitrary bytes stored under a
    `.glb` name. The counts themselves are best-effort and may come back None
    for an otherwise-valid file (a scene built from extensions this doesn't
    understand, say); a None count is not a validation failure.

    Counts are per-primitive as authored, so an asset that instantiates one
    mesh from several nodes is counted once, not once per instance.
    """
    with open(path, 'rb') as f:
        header = f.read(_GLB_HEADER_SIZE)
        if len(header) < _GLB_HEADER_SIZE or header[:4] != GLB_MAGIC:
            raise InvalidMeshError(f"not a glTF-binary file (bad magic): {path}")

        version, total_length = struct.unpack('<II', header[4:12])
        if version != 2:
            raise InvalidMeshError(f"unsupported glTF-binary version {version}: {path}")

        actual_length = os.path.getsize(path)
        if total_length != actual_length:
            raise InvalidMeshError(
                f"glTF-binary length field {total_length} != file size {actual_length}: {path}"
            )

        chunk_header = f.read(_GLB_CHUNK_HEADER_SIZE)
        if len(chunk_header) < _GLB_CHUNK_HEADER_SIZE:
            raise InvalidMeshError(f"glTF-binary file has no chunks: {path}")

        chunk_length, chunk_type = struct.unpack('<II', chunk_header)
        if chunk_type != _GLB_CHUNK_TYPE_JSON:
            raise InvalidMeshError(f"first glTF-binary chunk is not JSON: {path}")
        if chunk_length > _MAX_JSON_CHUNK_BYTES:
            raise InvalidMeshError(f"glTF-binary JSON chunk is implausibly large: {path}")

        raw_json = f.read(chunk_length)
        if len(raw_json) < chunk_length:
            raise InvalidMeshError(f"glTF-binary JSON chunk is truncated: {path}")

    try:
        document = json.loads(raw_json.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InvalidMeshError(f"glTF-binary JSON chunk does not parse: {path}") from e

    if not isinstance(document, dict):
        raise InvalidMeshError(f"glTF-binary JSON chunk is not an object: {path}")

    return _count_geometry(document)


def _count_geometry(document: dict) -> Tuple[Optional[int], Optional[int]]:
    """(vertex_count, face_count) from a parsed glTF document, or (None, None)."""
    accessors = document.get('accessors')
    meshes = document.get('meshes')
    if not isinstance(accessors, list) or not isinstance(meshes, list):
        return None, None

    def accessor_count(index) -> Optional[int]:
        if not isinstance(index, int) or not 0 <= index < len(accessors):
            return None
        accessor = accessors[index]
        count = accessor.get('count') if isinstance(accessor, dict) else None
        return count if isinstance(count, int) and count >= 0 else None

    vertices = 0
    faces = 0
    saw_primitive = False

    for mesh in meshes:
        for primitive in (mesh.get('primitives') or []) if isinstance(mesh, dict) else []:
            if not isinstance(primitive, dict):
                continue
            attributes = primitive.get('attributes')
            positions = accessor_count(attributes.get('POSITION')) if isinstance(attributes, dict) else None
            if positions is None:
                continue

            saw_primitive = True
            vertices += positions

            if primitive.get('mode', _GLTF_MODE_TRIANGLES) != _GLTF_MODE_TRIANGLES:
                continue
            indexed = accessor_count(primitive.get('indices'))
            faces += (indexed if indexed is not None else positions) // 3

    if not saw_primitive:
        return None, None
    return vertices, faces


mesh_format_registry.register(
    MeshFormat(extension='.glb', mime_type='model/gltf-binary', probe=probe_glb)
)
