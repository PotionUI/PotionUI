"""Mesh fixtures - a real, minimal glTF-binary asset built as bytes.

`trimesh` is not a dependency of this project, and a `.glb` is a simple enough
container to author directly: a 12-byte header, a JSON chunk holding the glTF
document, and a BIN chunk holding the geometry. Everything here is a genuine
`.glb` a browser would load, not a stub - tests that assert on the save and
serve paths need the real thing or they prove nothing about either.

Spec: https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html
"""

import json
import struct
from typing import Optional

import pytest

_CHUNK_TYPE_JSON = 0x4E4F534A
_CHUNK_TYPE_BIN = 0x004E4942

_COMPONENT_TYPE_FLOAT = 5126
_COMPONENT_TYPE_UNSIGNED_SHORT = 5123
_TARGET_ARRAY_BUFFER = 34962
_TARGET_ELEMENT_ARRAY_BUFFER = 34963

# One triangle: three vertices, three indices.
TRIANGLE_VERTEX_COUNT = 3
TRIANGLE_FACE_COUNT = 1


def _pad(payload: bytes, filler: bytes) -> bytes:
    """Pad a chunk out to the 4-byte boundary the spec requires."""
    remainder = len(payload) % 4
    if remainder == 0:
        return payload
    return payload + filler * (4 - remainder)


def build_minimal_glb(version: int = 2, declared_length: Optional[int] = None) -> bytes:
    """A complete, valid `.glb` containing a single triangle.

    Args:
        version: glTF-binary container version written into the header.
            Overridable so a test can produce a file that is well-formed
            except for its version.
        declared_length: Overrides the header's total-length field, so a test
            can produce a file whose declared and actual sizes disagree.

    Returns:
        The full file as bytes.
    """
    positions = struct.pack(
        '<9f',
        0.0, 0.0, 0.0,
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
    )
    indices = struct.pack('<3H', 0, 1, 2)
    binary = _pad(positions + indices, b'\x00')

    document = {
        "asset": {"version": "2.0", "generator": "potionui-tests"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": _COMPONENT_TYPE_FLOAT,
                "count": TRIANGLE_VERTEX_COUNT,
                "type": "VEC3",
                "min": [0.0, 0.0, 0.0],
                "max": [1.0, 1.0, 0.0],
            },
            {
                "bufferView": 1,
                "componentType": _COMPONENT_TYPE_UNSIGNED_SHORT,
                "count": 3,
                "type": "SCALAR",
            },
        ],
        "bufferViews": [
            {
                "buffer": 0,
                "byteOffset": 0,
                "byteLength": len(positions),
                "target": _TARGET_ARRAY_BUFFER,
            },
            {
                "buffer": 0,
                "byteOffset": len(positions),
                "byteLength": len(indices),
                "target": _TARGET_ELEMENT_ARRAY_BUFFER,
            },
        ],
        "buffers": [{"byteLength": len(positions) + len(indices)}],
    }

    json_chunk = _pad(json.dumps(document, separators=(',', ':')).encode('utf-8'), b' ')

    body = (
        struct.pack('<II', len(json_chunk), _CHUNK_TYPE_JSON) + json_chunk
        + struct.pack('<II', len(binary), _CHUNK_TYPE_BIN) + binary
    )
    total_length = 12 + len(body)

    header = b'glTF' + struct.pack('<II', version, declared_length if declared_length is not None else total_length)
    return header + body


@pytest.fixture
def minimal_glb_bytes() -> bytes:
    """A valid single-triangle `.glb` as bytes."""
    return build_minimal_glb()


@pytest.fixture
def minimal_glb_file(tmp_path, minimal_glb_bytes):
    """A valid single-triangle `.glb` written to a temporary path."""
    path = tmp_path / "source_mesh.glb"
    path.write_bytes(minimal_glb_bytes)
    return path
