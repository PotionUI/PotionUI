"""Server-side thumbnail renderer for generated 3D meshes (`.glb`).

Pure-torch software rasterizer - no OpenGL/EGL/OSMesa context, so it runs
anywhere the rest of the native engine does (including headless CPU-only
workers). `trimesh` is not a project dependency yet, so loading sits behind a
seam: use it when importable, otherwise fall back to a minimal glTF-binary
parser that reads positions/indices/`COLOR_0`/`baseColorFactor` directly out
of the container (same chunk-reading shape as `src.platform.filesystem.
mesh_formats.probe_glb`). Textures, animations and skinning are intentionally
not read - the goal is a shaded-geometry preview, not a faithful renderer.
"""

from __future__ import annotations

import io
import json
import math
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import torch
from PIL import Image

PREVIEW_SIZE = 512

#: Matches `--canvas` (dark theme) in `frontend/src/lib/styles/tokens.css`.
BACKGROUND_RGB = (12, 13, 15)

#: A background job, not the generation hot path - but still bounded so one
#: pathological asset cannot pin a worker indefinitely. Triangles beyond this
#: are dropped by a fixed stride rather than randomly, so a render stays
#: deterministic for a given input.
MAX_TRIANGLES = 200_000

#: Triangles are rasterized in chunks rather than all vectorized at once - a
#: fully vectorized batch across every triangle and the whole image would be
#: an O(triangles * size^2) tensor.
CHUNK_TRIANGLES = 4096

GLB_MAGIC = b"glTF"
_GLB_HEADER_SIZE = 12
_GLB_CHUNK_HEADER_SIZE = 8
_GLB_CHUNK_TYPE_JSON = 0x4E4F534A
_GLB_CHUNK_TYPE_BIN = 0x004E4942

_DEFAULT_COLOR = (0.75, 0.75, 0.75)

# glTF accessor componentType -> (struct format char, byte size).
_COMPONENT_TYPES = {
    5120: ("b", 1),  # BYTE
    5121: ("B", 1),  # UNSIGNED_BYTE
    5122: ("h", 2),  # SHORT
    5123: ("H", 2),  # UNSIGNED_SHORT
    5125: ("I", 4),  # UNSIGNED_INT
    5126: ("f", 4),  # FLOAT
}
_TYPE_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
_MODE_TRIANGLES = 4


class MeshPreviewError(ValueError):
    """Raised when a mesh cannot be parsed well enough to render at all."""


@dataclass
class LoadedMesh:
    positions: torch.Tensor  # (N, 3) float32, object space
    faces: torch.Tensor  # (M, 3) int64, indices into positions
    colors: torch.Tensor  # (N, 3) float32 in [0, 1]


# --- Public entry point -----------------------------------------------------


def render_mesh_preview(
    source: "bytes | str", size: int = PREVIEW_SIZE, device: str = "cpu"
) -> bytes:
    """Render a `.glb` mesh (bytes, or a path to one) to a `size`x`size` PNG.

    Always returns bytes - a structurally valid but empty/degenerate mesh
    renders as a flat background instead of raising, so a gallery card gets
    *something* rather than staying stuck on the cube icon. Raises
    `MeshPreviewError` only when the container itself cannot be parsed
    (caller should treat that the same as a missing/corrupt source file).
    """
    data = source if isinstance(source, (bytes, bytearray)) else _read_file(source)
    mesh = load_glb(bytes(data))

    background = torch.tensor(
        [c / 255.0 for c in BACKGROUND_RGB], dtype=torch.float32, device=device
    )
    canvas = background.view(1, 1, 3).expand(size, size, 3).contiguous()

    if mesh is None or mesh.faces.shape[0] == 0 or mesh.positions.shape[0] == 0:
        return _to_png_bytes(canvas)

    return _rasterize_mesh(mesh, canvas, size, device)


def _read_file(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


# --- Loading seam: trimesh if available, else the minimal parser -----------


def load_glb(data: bytes) -> Optional[LoadedMesh]:
    try:
        import trimesh as _trimesh
    except ImportError:
        _trimesh = None

    if _trimesh is not None:
        return _load_with_trimesh(data, _trimesh)
    return _load_minimal(data)


def _load_with_trimesh(data: bytes, trimesh_module: Any) -> Optional[LoadedMesh]:
    try:
        loaded = trimesh_module.load(
            io.BytesIO(data), file_type="glb", process=False
        )
    except Exception as exc:
        raise MeshPreviewError(f"trimesh failed to parse glTF-binary: {exc}") from exc

    geometries = list(loaded.geometry.values()) if hasattr(loaded, "geometry") else [loaded]

    positions: List[Tuple[float, float, float]] = []
    colors: List[Tuple[float, float, float]] = []
    faces: List[Tuple[int, int, int]] = []

    for geometry in geometries:
        vertices = getattr(geometry, "vertices", None)
        triangles = getattr(geometry, "faces", None)
        if vertices is None or triangles is None or len(triangles) == 0:
            continue

        base_color = _DEFAULT_COLOR
        vertex_colors = None
        visual = getattr(geometry, "visual", None)
        if visual is not None:
            vc = getattr(visual, "vertex_colors", None)
            if vc is not None and len(vc) == len(vertices):
                vertex_colors = [(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0) for c in vc]
            else:
                material = getattr(visual, "material", None)
                factor = getattr(material, "baseColorFactor", None) if material is not None else None
                if factor is not None and len(factor) >= 3:
                    base_color = tuple(float(c) for c in factor[:3])

        offset = len(positions)
        for vertex in vertices:
            positions.append((float(vertex[0]), float(vertex[1]), float(vertex[2])))
        for i in range(len(vertices)):
            colors.append(vertex_colors[i] if vertex_colors else base_color)
        for tri in triangles:
            faces.append((int(tri[0]) + offset, int(tri[1]) + offset, int(tri[2]) + offset))

    if not positions or not faces:
        return None

    return LoadedMesh(
        positions=torch.tensor(positions, dtype=torch.float32),
        faces=torch.tensor(faces, dtype=torch.int64),
        colors=torch.tensor(colors, dtype=torch.float32).clamp(0.0, 1.0),
    )


# --- Minimal glTF-binary parser ---------------------------------------------


def _load_minimal(data: bytes) -> Optional[LoadedMesh]:
    if len(data) < _GLB_HEADER_SIZE or data[:4] != GLB_MAGIC:
        raise MeshPreviewError("not a glTF-binary file (bad magic)")

    version, _total_length = struct.unpack("<II", data[4:12])
    if version != 2:
        raise MeshPreviewError(f"unsupported glTF-binary version {version}")

    json_chunk: Optional[bytes] = None
    bin_chunk = b""
    offset = _GLB_HEADER_SIZE
    while offset + _GLB_CHUNK_HEADER_SIZE <= len(data):
        chunk_length, chunk_type = struct.unpack("<II", data[offset:offset + 8])
        payload_start = offset + _GLB_CHUNK_HEADER_SIZE
        payload_end = payload_start + chunk_length
        if payload_end > len(data):
            break
        payload = data[payload_start:payload_end]
        if chunk_type == _GLB_CHUNK_TYPE_JSON and json_chunk is None:
            json_chunk = payload
        elif chunk_type == _GLB_CHUNK_TYPE_BIN and not bin_chunk:
            bin_chunk = payload
        offset = payload_end

    if json_chunk is None:
        raise MeshPreviewError("glTF-binary file has no JSON chunk")

    try:
        document = json.loads(json_chunk.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MeshPreviewError("glTF-binary JSON chunk does not parse") from exc

    if not isinstance(document, dict):
        raise MeshPreviewError("glTF-binary JSON chunk is not an object")

    return _build_mesh(document, bin_chunk)


def _read_accessor(
    document: dict, bin_data: bytes, accessor_index: Any
) -> Optional[Tuple[List[float], int, int]]:
    """`(flat_values, components_per_element, element_count)`, or None if the
    accessor is missing, malformed, sparse-only, or references data past the
    end of the binary chunk."""
    if not isinstance(accessor_index, int):
        return None
    accessors = document.get("accessors") or []
    buffer_views = document.get("bufferViews") or []
    if not (0 <= accessor_index < len(accessors)):
        return None
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict):
        return None

    bv_index = accessor.get("bufferView")
    if not isinstance(bv_index, int) or not (0 <= bv_index < len(buffer_views)):
        return None
    buffer_view = buffer_views[bv_index]
    if not isinstance(buffer_view, dict):
        return None

    component_type = accessor.get("componentType")
    accessor_type = accessor.get("type")
    if component_type not in _COMPONENT_TYPES or accessor_type not in _TYPE_COMPONENTS:
        return None
    fmt_char, comp_size = _COMPONENT_TYPES[component_type]
    num_components = _TYPE_COMPONENTS[accessor_type]

    count = accessor.get("count")
    if not isinstance(count, int) or count <= 0:
        return None

    base_offset = buffer_view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
    element_size = comp_size * num_components
    stride = buffer_view.get("byteStride") or element_size

    values: List[float] = []
    for i in range(count):
        start = base_offset + i * stride
        chunk = bin_data[start:start + element_size]
        if len(chunk) < element_size:
            return None
        values.extend(struct.unpack("<" + fmt_char * num_components, chunk))

    if accessor.get("normalized"):
        if component_type == 5121:  # UNSIGNED_BYTE
            values = [v / 255.0 for v in values]
        elif component_type == 5123:  # UNSIGNED_SHORT
            values = [v / 65535.0 for v in values]
        elif component_type == 5120:  # BYTE
            values = [max(v / 127.0, -1.0) for v in values]
        elif component_type == 5122:  # SHORT
            values = [max(v / 32767.0, -1.0) for v in values]

    return values, num_components, count


Matrix4 = List[List[float]]


def _identity_matrix() -> Matrix4:
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def _matrix_multiply(a: Matrix4, b: Matrix4) -> Matrix4:
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
        for i in range(4)
    ]


def _compose_trs(t: List[float], r: List[float], s: List[float]) -> Matrix4:
    x, y, z, w = r
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    rotation = (
        (1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)),
        (2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)),
        (2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)),
    )
    matrix = _identity_matrix()
    for i in range(3):
        matrix[i][0] = rotation[i][0] * s[0]
        matrix[i][1] = rotation[i][1] * s[1]
        matrix[i][2] = rotation[i][2] * s[2]
        matrix[i][3] = t[i]
    return matrix


def _node_local_matrix(node: dict) -> Matrix4:
    matrix = node.get("matrix")
    if isinstance(matrix, list) and len(matrix) == 16:
        # glTF node matrices are column-major.
        return [[matrix[c * 4 + r] for c in range(4)] for r in range(4)]
    t = node.get("translation") or [0.0, 0.0, 0.0]
    r = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]
    s = node.get("scale") or [1.0, 1.0, 1.0]
    return _compose_trs(list(t), list(r), list(s))


def _apply_matrix(matrix: Matrix4, x: float, y: float, z: float) -> Tuple[float, float, float]:
    wx = matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z + matrix[0][3]
    wy = matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z + matrix[1][3]
    wz = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z + matrix[2][3]
    return wx, wy, wz


def _collect_mesh_instances(document: dict) -> List[Tuple[int, Matrix4]]:
    """`(mesh_index, world_matrix)` for every mesh-bearing node reachable
    from the active scene, walking the node hierarchy so authored node
    transforms (translation/rotation/scale, or a raw matrix) are applied.
    Falls back to identity-transformed top-level meshes for a document with
    no usable scene/node graph - malformed input renders anyway rather than
    refusing a preview outright."""
    nodes = document.get("nodes") or []
    scenes = document.get("scenes") or []
    scene_index = document.get("scene", 0)

    roots: List[int] = []
    if isinstance(scene_index, int) and 0 <= scene_index < len(scenes):
        scene = scenes[scene_index]
        if isinstance(scene, dict):
            roots = [n for n in (scene.get("nodes") or []) if isinstance(n, int)]
    if not roots and nodes:
        children = {c for node in nodes if isinstance(node, dict) for c in (node.get("children") or [])}
        roots = [i for i in range(len(nodes)) if i not in children]

    instances: List[Tuple[int, Matrix4]] = []
    visited: set = set()

    def walk(node_index: int, parent_matrix: Matrix4) -> None:
        if node_index in visited or not (0 <= node_index < len(nodes)):
            return
        visited.add(node_index)
        node = nodes[node_index]
        if not isinstance(node, dict):
            return
        world = _matrix_multiply(parent_matrix, _node_local_matrix(node))
        mesh_index = node.get("mesh")
        if isinstance(mesh_index, int):
            instances.append((mesh_index, world))
        for child in node.get("children") or []:
            if isinstance(child, int):
                walk(child, world)

    for root in roots:
        walk(root, _identity_matrix())

    if not instances:
        # No scene/node graph at all: render every mesh at identity.
        for mesh_index in range(len(document.get("meshes") or [])):
            instances.append((mesh_index, _identity_matrix()))

    return instances


def _build_mesh(document: dict, bin_data: bytes) -> Optional[LoadedMesh]:
    meshes = document.get("meshes") or []
    materials = document.get("materials") or []
    if not meshes:
        return None

    positions: List[Tuple[float, float, float]] = []
    colors: List[Tuple[float, float, float]] = []
    faces: List[Tuple[int, int, int]] = []

    for mesh_index, matrix in _collect_mesh_instances(document):
        if not (0 <= mesh_index < len(meshes)):
            continue
        mesh = meshes[mesh_index]
        if not isinstance(mesh, dict):
            continue
        for primitive in mesh.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            if primitive.get("mode", _MODE_TRIANGLES) != _MODE_TRIANGLES:
                continue
            attributes = primitive.get("attributes")
            if not isinstance(attributes, dict):
                continue

            pos_result = _read_accessor(document, bin_data, attributes.get("POSITION"))
            if pos_result is None:
                continue
            pos_values, pos_components, pos_count = pos_result
            if pos_components != 3:
                continue

            indices_index = primitive.get("indices")
            if indices_index is not None:
                idx_result = _read_accessor(document, bin_data, indices_index)
                if idx_result is None:
                    continue
                idx_values, _idx_components, _idx_count = idx_result
                indices = [int(v) for v in idx_values]
            else:
                indices = list(range(pos_count))

            vertex_colors = None
            col_result = _read_accessor(document, bin_data, attributes.get("COLOR_0"))
            if col_result is not None:
                col_values, col_components, col_count = col_result
                if col_count == pos_count and col_components in (3, 4):
                    vertex_colors = [
                        tuple(col_values[i * col_components: i * col_components + 3])
                        for i in range(col_count)
                    ]

            base_color = _DEFAULT_COLOR
            material_index = primitive.get("material")
            if isinstance(material_index, int) and 0 <= material_index < len(materials):
                material = materials[material_index]
                pbr = (material or {}).get("pbrMetallicRoughness") if isinstance(material, dict) else None
                factor = (pbr or {}).get("baseColorFactor") if isinstance(pbr, dict) else None
                if isinstance(factor, list) and len(factor) >= 3:
                    base_color = tuple(float(c) for c in factor[:3])

            vertex_offset = len(positions)
            for i in range(pos_count):
                x, y, z = pos_values[i * 3: i * 3 + 3]
                positions.append(_apply_matrix(matrix, x, y, z))
                colors.append(vertex_colors[i] if vertex_colors else base_color)

            for t in range(0, len(indices) - 2, 3):
                a, b, c = indices[t], indices[t + 1], indices[t + 2]
                if a >= pos_count or b >= pos_count or c >= pos_count:
                    continue
                faces.append((a + vertex_offset, b + vertex_offset, c + vertex_offset))

    if not positions or not faces:
        return None

    return LoadedMesh(
        positions=torch.tensor(positions, dtype=torch.float32),
        faces=torch.tensor(faces, dtype=torch.int64),
        colors=torch.tensor(colors, dtype=torch.float32).clamp(0.0, 1.0),
    )


# --- Rasterization -----------------------------------------------------------


def _rasterize_mesh(mesh: LoadedMesh, canvas: torch.Tensor, size: int, device: str) -> bytes:
    # Non-manifold / malformed source data (NaN or infinite coordinates,
    # bogus but in-range indices) renders anyway rather than raising -
    # replace anything non-finite with the origin instead of propagating it
    # into `math.floor`/`math.ceil` in the rasterizer below.
    positions = torch.nan_to_num(
        mesh.positions.to(device=device, dtype=torch.float32), nan=0.0, posinf=0.0, neginf=0.0
    )
    colors = mesh.colors.to(device=device, dtype=torch.float32).clamp(0.0, 1.0)
    faces = mesh.faces.to(device=device, dtype=torch.int64)

    if faces.shape[0] > MAX_TRIANGLES:
        stride = math.ceil(faces.shape[0] / MAX_TRIANGLES)
        faces = faces[::stride]

    bbox_min = positions.min(dim=0).values
    bbox_max = positions.max(dim=0).values
    center = (bbox_min + bbox_max) / 2.0
    extent = (bbox_max - bbox_min).max().item()
    if extent < 1e-8:
        extent = 1.0
    scale = 1.6 / extent
    normalized = (positions - center) * scale

    # Fixed 3/4 view: to the right, slightly above, looking at the origin.
    eye = torch.tensor([2.05, 1.35, 2.6], dtype=torch.float32, device=device)
    target = torch.zeros(3, dtype=torch.float32, device=device)
    up = torch.tensor([0.0, 1.0, 0.0], dtype=torch.float32, device=device)
    forward = torch.nn.functional.normalize(target - eye, dim=0, eps=1e-8)
    right = torch.nn.functional.normalize(torch.cross(forward, up, dim=0), dim=0, eps=1e-8)
    cam_up = torch.cross(right, forward, dim=0)
    view = torch.stack([right, cam_up, -forward], dim=0)

    cam = (normalized - eye) @ view.T

    zoom = size * 0.30
    cx = size / 2.0
    cy = size / 2.0
    screen_x = cx + cam[:, 0] * zoom
    screen_y = cy - cam[:, 1] * zoom
    depth = -cam[:, 2]

    v0, v1, v2 = faces[:, 0], faces[:, 1], faces[:, 2]
    p0, p1, p2 = cam[v0], cam[v1], cam[v2]

    # Flat per-face normal: double-sided lambert headlight + a slight
    # silhouette rim, both driven off how face-on the triangle is to the
    # (fixed, directional) camera light.
    face_normal = torch.nn.functional.normalize(
        torch.cross(p1 - p0, p2 - p0, dim=1), dim=1, eps=1e-8
    )
    ndotl = face_normal[:, 2].abs()
    lambert = 0.35 + 0.65 * ndotl
    rim = ((1.0 - ndotl).clamp(min=0.0) ** 3) * 0.28

    tri_screen_x = torch.stack([screen_x[v0], screen_x[v1], screen_x[v2]], dim=1)
    tri_screen_y = torch.stack([screen_y[v0], screen_y[v1], screen_y[v2]], dim=1)
    tri_depth = torch.stack([depth[v0], depth[v1], depth[v2]], dim=1)
    tri_color = torch.stack([colors[v0], colors[v1], colors[v2]], dim=1)
    shaded = (tri_color * lambert.view(-1, 1, 1) + rim.view(-1, 1, 1)).clamp(0.0, 1.0)

    depth_buf = torch.full((size, size), float("inf"), dtype=torch.float32, device=device)
    color_buf = canvas.clone()

    num_faces = tri_screen_x.shape[0]
    for start in range(0, num_faces, CHUNK_TRIANGLES):
        end = min(start + CHUNK_TRIANGLES, num_faces)
        for f in range(start, end):
            _rasterize_triangle(
                color_buf, depth_buf, size,
                tri_screen_x[f], tri_screen_y[f], tri_depth[f], shaded[f],
            )

    return _to_png_bytes(color_buf.cpu())


def _rasterize_triangle(
    color_buf: torch.Tensor,
    depth_buf: torch.Tensor,
    size: int,
    sx: torch.Tensor,
    sy: torch.Tensor,
    sz: torch.Tensor,
    col: torch.Tensor,
) -> None:
    x0, x1, x2 = sx.tolist()
    y0, y1, y2 = sy.tolist()
    z0, z1, z2 = sz.tolist()

    min_x = max(int(math.floor(min(x0, x1, x2))), 0)
    max_x = min(int(math.ceil(max(x0, x1, x2))), size - 1)
    min_y = max(int(math.floor(min(y0, y1, y2))), 0)
    max_y = min(int(math.ceil(max(y0, y1, y2))), size - 1)
    if min_x > max_x or min_y > max_y:
        return

    denom = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
    if abs(denom) < 1e-10:
        return

    device = color_buf.device
    ys = torch.arange(min_y, max_y + 1, dtype=torch.float32, device=device).view(-1, 1) + 0.5
    xs = torch.arange(min_x, max_x + 1, dtype=torch.float32, device=device).view(1, -1) + 0.5

    w0 = ((y1 - y2) * (xs - x2) + (x2 - x1) * (ys - y2)) / denom
    w1 = ((y2 - y0) * (xs - x2) + (x0 - x2) * (ys - y2)) / denom
    w2 = 1.0 - w0 - w1

    inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
    if not bool(inside.any()):
        return

    tri_depth = w0 * z0 + w1 * z1 + w2 * z2
    region_depth = depth_buf[min_y:max_y + 1, min_x:max_x + 1]
    closer = inside & (tri_depth < region_depth)
    if not bool(closer.any()):
        return

    col0, col1, col2 = col[0], col[1], col[2]
    r = w0 * col0[0] + w1 * col1[0] + w2 * col2[0]
    g = w0 * col0[1] + w1 * col1[1] + w2 * col2[1]
    b = w0 * col0[2] + w1 * col1[2] + w2 * col2[2]

    region_depth[closer] = tri_depth[closer]
    region_color = color_buf[min_y:max_y + 1, min_x:max_x + 1, :]
    region_color[..., 0] = torch.where(closer, r, region_color[..., 0])
    region_color[..., 1] = torch.where(closer, g, region_color[..., 1])
    region_color[..., 2] = torch.where(closer, b, region_color[..., 2])


def _to_png_bytes(canvas: torch.Tensor) -> bytes:
    array = (canvas.clamp(0.0, 1.0) * 255.0 + 0.5).to(torch.uint8).numpy()
    image = Image.fromarray(array, mode="RGB")
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=False)
    return buf.getvalue()
