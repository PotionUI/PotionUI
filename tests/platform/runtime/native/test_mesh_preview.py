"""Tests for the pure-torch mesh preview renderer.

No GPU, no GL context - these are analytic checks (known geometry lands in
the expected screen region, output is deterministic) rather than golden-image
comparisons, per the project's no-goldens-for-renderers convention.
"""

import io
import json
import struct

import pytest
from PIL import Image

from src.platform.filesystem.mesh_formats import probe_glb
from src.platform.runtime.native.mesh_preview import (
    BACKGROUND_RGB,
    MeshPreviewError,
    load_glb,
    render_mesh_preview,
)
from tests.fixtures.mesh_fixtures import (
    TRIANGLE_FACE_COUNT,
    TRIANGLE_VERTEX_COUNT,
    build_minimal_glb,
)

_CHUNK_TYPE_JSON = 0x4E4F534A
_CHUNK_TYPE_BIN = 0x004E4942
_COMPONENT_TYPE_FLOAT = 5126
_COMPONENT_TYPE_UNSIGNED_SHORT = 5123


def _pad(payload: bytes, filler: bytes = b"\x00") -> bytes:
    remainder = len(payload) % 4
    if remainder == 0:
        return payload
    return payload + filler * (4 - remainder)


def build_cube_glb(color: "tuple[float, float, float, float] | None" = (1.0, 0.2, 0.2, 1.0)) -> bytes:
    """A real, minimal axis-aligned cube `.glb` - 8 vertices, 12 triangles,
    optionally with a `baseColorFactor` material so shading has a real
    (non-default) input color to interpolate."""
    verts = [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    faces = [
        (0, 1, 2), (0, 2, 3),  # back
        (4, 6, 5), (4, 7, 6),  # front
        (0, 4, 5), (0, 5, 1),  # bottom
        (3, 2, 6), (3, 6, 7),  # top
        (0, 3, 7), (0, 7, 4),  # left
        (1, 5, 6), (1, 6, 2),  # right
    ]

    positions = struct.pack(f"<{len(verts) * 3}f", *[c for v in verts for c in v])
    indices = struct.pack(f"<{len(faces) * 3}H", *[i for f in faces for i in f])
    binary = _pad(positions) + _pad(indices)
    positions_padded = _pad(positions)

    material = None
    if color is not None:
        material = {"pbrMetallicRoughness": {"baseColorFactor": list(color)}}

    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{
            "primitives": [{
                "attributes": {"POSITION": 0},
                "indices": 1,
                **({"material": 0} if material else {}),
            }],
        }],
        "materials": [material] if material else [],
        "accessors": [
            {
                "bufferView": 0, "componentType": _COMPONENT_TYPE_FLOAT,
                "count": len(verts), "type": "VEC3",
                "min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0],
            },
            {
                "bufferView": 1, "componentType": _COMPONENT_TYPE_UNSIGNED_SHORT,
                "count": len(faces) * 3, "type": "SCALAR",
            },
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions_padded), "byteLength": len(indices)},
        ],
        "buffers": [{"byteLength": len(binary)}],
    }

    json_chunk = _pad(json.dumps(document, separators=(",", ":")).encode("utf-8"), b" ")
    body = (
        struct.pack("<II", len(json_chunk), _CHUNK_TYPE_JSON) + json_chunk
        + struct.pack("<II", len(binary), _CHUNK_TYPE_BIN) + binary
    )
    header = b"glTF" + struct.pack("<II", 2, 12 + len(body))
    return header + body


def build_inward_cube_glb(color: "tuple[float, float, float, float] | None" = (1.0, 0.2, 0.2, 1.0)) -> bytes:
    """Same cube as `build_cube_glb`, every triangle's winding reversed - the
    TRELLIS dual-grid export produces inward-facing normals intrinsically, so
    a preview that isn't honestly double-sided renders this shape black."""
    verts = [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    faces = [
        (0, 2, 1), (0, 3, 2),
        (4, 5, 6), (4, 6, 7),
        (0, 5, 4), (0, 1, 5),
        (3, 6, 2), (3, 7, 6),
        (0, 7, 3), (0, 4, 7),
        (1, 6, 5), (1, 2, 6),
    ]

    positions = struct.pack(f"<{len(verts) * 3}f", *[c for v in verts for c in v])
    indices = struct.pack(f"<{len(faces) * 3}H", *[i for f in faces for i in f])
    binary = _pad(positions) + _pad(indices)
    positions_padded = _pad(positions)

    material = None
    if color is not None:
        material = {"pbrMetallicRoughness": {"baseColorFactor": list(color)}}

    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{
            "primitives": [{
                "attributes": {"POSITION": 0},
                "indices": 1,
                **({"material": 0} if material else {}),
            }],
        }],
        "materials": [material] if material else [],
        "accessors": [
            {
                "bufferView": 0, "componentType": _COMPONENT_TYPE_FLOAT,
                "count": len(verts), "type": "VEC3",
                "min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0],
            },
            {
                "bufferView": 1, "componentType": _COMPONENT_TYPE_UNSIGNED_SHORT,
                "count": len(faces) * 3, "type": "SCALAR",
            },
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions_padded), "byteLength": len(indices)},
        ],
        "buffers": [{"byteLength": len(binary)}],
    }

    json_chunk = _pad(json.dumps(document, separators=(",", ":")).encode("utf-8"), b" ")
    body = (
        struct.pack("<II", len(json_chunk), _CHUNK_TYPE_JSON) + json_chunk
        + struct.pack("<II", len(binary), _CHUNK_TYPE_BIN) + binary
    )
    header = b"glTF" + struct.pack("<II", 2, 12 + len(body))
    return header + body


def build_textured_quad_glb() -> bytes:
    """A single quad (2 triangles, 4 vertices) with a 2x2 lossless-WebP
    texture behind `EXT_texture_webp` - the same texture-delivery shape as a
    real TRELLIS export - and UVs that put one distinct color at each vertex."""
    verts = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)]
    faces = [(0, 1, 2), (0, 2, 3)]
    # glTF UV origin is the image's top-left corner.
    uv = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]

    texture = Image.new("RGB", (2, 2))
    texture.putpixel((0, 0), (255, 0, 0))
    texture.putpixel((1, 0), (0, 255, 0))
    texture.putpixel((1, 1), (0, 0, 255))
    texture.putpixel((0, 1), (255, 255, 0))
    webp_buf = io.BytesIO()
    texture.save(webp_buf, format="WEBP", lossless=True)
    webp_bytes = webp_buf.getvalue()

    positions = struct.pack(f"<{len(verts) * 3}f", *[c for v in verts for c in v])
    indices = struct.pack(f"<{len(faces) * 3}H", *[i for f in faces for i in f])
    uvs_packed = struct.pack(f"<{len(uv) * 2}f", *[c for p in uv for c in p])

    positions_padded = _pad(positions)
    indices_padded = _pad(indices)
    uvs_padded = _pad(uvs_packed)
    webp_padded = _pad(webp_bytes)
    binary = positions_padded + indices_padded + uvs_padded + webp_padded

    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{
            "primitives": [{
                "attributes": {"POSITION": 0, "TEXCOORD_0": 2},
                "indices": 1,
                "material": 0,
            }],
        }],
        "materials": [{
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0},
                "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
            },
        }],
        "textures": [{"extensions": {"EXT_texture_webp": {"source": 0}}}],
        "images": [{"bufferView": 3, "mimeType": "image/webp"}],
        "accessors": [
            {
                "bufferView": 0, "componentType": _COMPONENT_TYPE_FLOAT,
                "count": len(verts), "type": "VEC3",
                "min": [-1.0, -1.0, 0.0], "max": [1.0, 1.0, 0.0],
            },
            {
                "bufferView": 1, "componentType": _COMPONENT_TYPE_UNSIGNED_SHORT,
                "count": len(faces) * 3, "type": "SCALAR",
            },
            {
                "bufferView": 2, "componentType": _COMPONENT_TYPE_FLOAT,
                "count": len(uv), "type": "VEC2",
            },
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
            {"buffer": 0, "byteOffset": len(positions_padded), "byteLength": len(indices)},
            {
                "buffer": 0, "byteOffset": len(positions_padded) + len(indices_padded),
                "byteLength": len(uvs_packed),
            },
            {
                "buffer": 0,
                "byteOffset": len(positions_padded) + len(indices_padded) + len(uvs_padded),
                "byteLength": len(webp_bytes),
            },
        ],
        "buffers": [{"byteLength": len(binary)}],
    }

    json_chunk = _pad(json.dumps(document, separators=(",", ":")).encode("utf-8"), b" ")
    body = (
        struct.pack("<II", len(json_chunk), _CHUNK_TYPE_JSON) + json_chunk
        + struct.pack("<II", len(binary), _CHUNK_TYPE_BIN) + binary
    )
    header = b"glTF" + struct.pack("<II", 2, 12 + len(body))
    return header + body


def _pixels(png_bytes: bytes):
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    return image


def _foreground_pixels(png_bytes: bytes):
    image = _pixels(png_bytes)
    pixels = image.load()
    bg = BACKGROUND_RGB
    out = []
    for y in range(image.height):
        for x in range(image.width):
            p = pixels[x, y]
            if p != bg:
                out.append(p)
    return out


class TestSingleTriangle:
    def test_parses_expected_geometry(self):
        mesh = load_glb(build_minimal_glb())
        assert mesh is not None
        assert mesh.positions.shape == (TRIANGLE_VERTEX_COUNT, 3)
        assert mesh.faces.shape == (TRIANGLE_FACE_COUNT, 3)

    def test_renders_nonbackground_pixels_in_expected_region(self):
        png = render_mesh_preview(build_minimal_glb())
        image = _pixels(png)
        assert image.size == (512, 512)

        pixels = image.load()
        bg = BACKGROUND_RGB
        hit_xs, hit_ys = [], []
        for y in range(512):
            for x in range(512):
                if pixels[x, y] != bg:
                    hit_xs.append(x)
                    hit_ys.append(y)

        assert hit_xs, "triangle produced no visible pixels"
        # The single triangle (positions at the origin, +X, +Y) sits below and
        # left of the screen center once projected through the 3/4 view - it
        # must not paint the whole canvas or land dead-center like a full mesh
        # would.
        assert len(hit_xs) < (512 * 512) // 4
        assert 0 <= min(hit_xs) and max(hit_xs) < 512
        assert 0 <= min(hit_ys) and max(hit_ys) < 512


class TestCube:
    def test_parses_expected_geometry_matches_probe_glb(self, tmp_path):
        data = build_cube_glb()
        path = tmp_path / "cube.glb"
        path.write_bytes(data)

        probe_vertices, probe_faces = probe_glb(str(path))
        mesh = load_glb(data)

        assert mesh is not None
        assert mesh.positions.shape[0] == probe_vertices
        assert mesh.faces.shape[0] == probe_faces

    def test_renders_with_coverage_and_centered_mass(self):
        png = render_mesh_preview(build_cube_glb())
        image = _pixels(png)
        pixels = image.load()
        bg = BACKGROUND_RGB

        hit_xs, hit_ys = [], []
        for y in range(512):
            for x in range(512):
                if pixels[x, y] != bg:
                    hit_xs.append(x)
                    hit_ys.append(y)

        total = 512 * 512
        coverage = len(hit_xs) / total
        assert coverage > 0.05, f"cube covered too little of the canvas ({coverage:.3%})"

        centroid_x = sum(hit_xs) / len(hit_xs)
        centroid_y = sum(hit_ys) / len(hit_ys)
        # A centered, auto-scaled cube should land near the middle of the
        # frame - generous tolerance since the view is a 3/4 perspective-free
        # projection, not dead-on.
        assert abs(centroid_x - 256) < 100
        assert abs(centroid_y - 256) < 100

    def test_deterministic_bytes_for_same_input(self):
        data = build_cube_glb()
        first = render_mesh_preview(data)
        second = render_mesh_preview(data)
        assert first == second

    def test_material_base_color_used_when_no_vertex_colors(self):
        mesh = load_glb(build_cube_glb(color=(1.0, 0.0, 0.0, 1.0)))
        assert mesh is not None
        assert mesh.colors[0].tolist() == pytest.approx([1.0, 0.0, 0.0], abs=1e-5)


class TestEmptyMesh:
    def test_no_primitives_renders_background_only(self):
        document = {
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": []}],
            "nodes": [],
            "meshes": [],
            "accessors": [],
            "bufferViews": [],
            "buffers": [{"byteLength": 0}],
        }
        json_chunk = _pad(json.dumps(document).encode("utf-8"), b" ")
        body = struct.pack("<II", len(json_chunk), _CHUNK_TYPE_JSON) + json_chunk
        header = b"glTF" + struct.pack("<II", 2, 12 + len(body))
        data = header + body

        assert load_glb(data) is None

        png = render_mesh_preview(data)
        image = _pixels(png)
        assert image.size == (512, 512)
        assert set(image.getdata()) == {BACKGROUND_RGB}

    def test_corrupt_container_raises(self):
        with pytest.raises(MeshPreviewError):
            render_mesh_preview(b"not a real glb file")


class TestMinimalParserSourceSelection:
    def test_uses_minimal_parser_when_trimesh_unavailable(self, monkeypatch):
        """Import-seam sanity check: with `trimesh` unimportable (its actual
        state in this project today), `load_glb` must still parse - i.e. it
        really did fall through to the minimal parser rather than raising."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "trimesh":
                raise ImportError("no trimesh in this environment")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        mesh = load_glb(build_minimal_glb())
        assert mesh is not None
        assert mesh.faces.shape == (TRIANGLE_FACE_COUNT, 3)


@pytest.fixture
def no_trimesh(monkeypatch):
    """Forces the minimal-parser loading path regardless of whether
    `trimesh` happens to be importable in the environment this test runs
    in."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "trimesh":
            raise ImportError("no trimesh in this environment")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


class TestDoubleSidedShading:
    """TRELLIS's dual-grid export produces inward-facing triangle winding
    intrinsically - a preview that lights a triangle by `max(0, n.l)` instead
    of `abs(n.l)` renders it black because every normal faces away from the
    camera-headlight. Flipping every face's winding must not change the
    render at all: the shading term already only depends on how face-on a
    triangle is, not which way it points."""

    def test_inward_winding_renders_identically_to_outward(self):
        outward = render_mesh_preview(build_cube_glb())
        inward = render_mesh_preview(build_inward_cube_glb())
        assert outward == inward

    def test_inward_winding_still_produces_lit_pixels(self):
        png = render_mesh_preview(build_inward_cube_glb())
        image = _pixels(png)
        foreground = _foreground_pixels(png)
        assert foreground, "inward-wound cube produced no visible pixels"
        # Every foreground pixel should show real shading, not the flat rim
        # sliver a `max(0, n.l)` regression would leave behind.
        coverage = len(foreground) / (image.width * image.height)
        assert coverage > 0.05


class TestTexturedMesh:
    """`baseColorTexture` (WebP behind `EXT_texture_webp`, as TRELLIS exports
    it) must be sampled into the render - without it every triangle falls
    back to a flat, colorless `baseColorFactor`."""

    def _assert_multiple_hues_present(self, png: bytes) -> None:
        foreground = _foreground_pixels(png)
        assert foreground, "textured quad produced no visible pixels"

        def dominant(p):
            r, g, b = p
            if r > g + 20 and r > b + 20:
                return "red"
            if g > r + 20 and g > b + 20:
                return "green"
            if b > r + 20 and b > g + 20:
                return "blue"
            if r > b + 20 and g > b + 20 and abs(int(r) - int(g)) < 30:
                return "yellow"
            return None

        hues = {dominant(p) for p in foreground} - {None}
        assert len(hues) >= 3, f"expected multiple distinct texture hues, saw {hues}"

        spreads = [max(p) - min(p) for p in foreground]
        assert sum(1 for s in spreads if s > 10) / len(spreads) > 0.3

    def test_texture_colors_appear_via_live_loader(self):
        png = render_mesh_preview(build_textured_quad_glb())
        self._assert_multiple_hues_present(png)

    def test_texture_colors_appear_via_minimal_parser(self, no_trimesh):
        png = render_mesh_preview(build_textured_quad_glb())
        self._assert_multiple_hues_present(png)

    def test_missing_texture_falls_back_to_flat_base_color(self):
        mesh = load_glb(build_cube_glb(color=(0.0, 1.0, 0.0, 1.0)))
        assert mesh is not None
        assert mesh.colors[0].tolist() == pytest.approx([0.0, 1.0, 0.0], abs=1e-5)
