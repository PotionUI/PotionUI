import sys

import numpy as np
import pytest
import torch
import trimesh

from src.platform.runtime.native.arch.trellis2.postprocess import (
    _closest_point_on_triangles,
    build_textured_mesh,
    clean_and_decimate,
    inpaint_texture,
    postprocess_to_glb,
    unwrap_uv,
)

# Upstream's `to_glb` needs cumesh/nvdiffrast/flex_gemm and cannot run on CPU, so
# there is no oracle to diff against. Instead every stage is checked against an
# analytic property: a sphere's signed volume, a linear colour field the volume
# encodes exactly, and the identity that a baked texel must carry the colour of
# the 3D position that sampled it.

_SHELL_INNER = 0.28
_SHELL_OUTER = 0.52
_SPHERE_RADIUS = 0.4


def _inward_sphere(subdivisions=4):
    """An icosphere wound inward, the way `flexible_dual_grid_to_mesh` extracts."""
    sphere = trimesh.creation.icosphere(subdivisions=subdivisions, radius=_SPHERE_RADIUS)
    vertices = np.asarray(sphere.vertices, dtype=np.float32)
    faces = np.ascontiguousarray(np.asarray(sphere.faces, dtype=np.int64)[:, ::-1])
    assert trimesh.Trimesh(vertices, faces, process=False).volume < 0
    return vertices, faces


def _linear_colour_shell(resolution=32):
    """A sparse shell around the sphere whose base colour is `position + 0.5` —
    linear in space, so trilinear sampling reproduces it exactly, and metallic /
    roughness / alpha are constants that must survive channel packing."""
    voxel_size = 1.0 / resolution
    grid = np.stack(
        np.meshgrid(*[np.arange(resolution)] * 3, indexing="ij"), axis=-1
    ).reshape(-1, 3)
    centres = (grid + 0.5) * voxel_size - 0.5
    radius = np.linalg.norm(centres, axis=1)
    keep = (radius > _SHELL_INNER) & (radius < _SHELL_OUTER)
    grid, centres = grid[keep], centres[keep]

    attrs = np.zeros((grid.shape[0], 6), dtype=np.float32)
    attrs[:, 0:3] = centres + 0.5
    attrs[:, 3] = 0.25  # metallic
    attrs[:, 4] = 0.75  # roughness
    attrs[:, 5] = 1.0  # alpha
    return torch.from_numpy(attrs), torch.from_numpy(grid), voxel_size


def _undo_export_transform(vertices):
    """`build_textured_mesh` writes (x, z, -y); invert it to recover the colour
    field's input coordinates."""
    return np.stack([vertices[:, 0], -vertices[:, 2], vertices[:, 1]], axis=-1)


def test_decimation_hits_the_face_target():
    vertices, faces = _inward_sphere()

    out_vertices, out_faces = clean_and_decimate(vertices, faces, decimation_target=1200)

    assert out_faces.shape[0] <= 1200
    assert out_faces.shape[0] > 600
    assert out_faces.max() < out_vertices.shape[0]


def test_a_target_above_the_input_leaves_the_mesh_alone():
    vertices, faces = _inward_sphere(subdivisions=2)

    _, out_faces = clean_and_decimate(vertices, faces, decimation_target=1_000_000)

    assert out_faces.shape[0] == faces.shape[0]


def test_unreferenced_vertices_from_extraction_are_dropped():
    """`flexible_dual_grid_to_mesh` returns one vertex per input voxel regardless
    of whether a face uses it, so the mesh arrives mostly unreferenced."""
    vertices, faces = _inward_sphere(subdivisions=3)
    padded = np.concatenate([vertices, np.full((5000, 3), 9.0, dtype=np.float32)], axis=0)

    out_vertices, out_faces = clean_and_decimate(padded, faces, decimation_target=1_000_000)

    assert out_vertices.shape[0] == vertices.shape[0]
    assert trimesh.Trimesh(out_vertices, out_faces, process=False).volume > 0


def test_an_empty_mesh_survives_the_chain():
    out_vertices, out_faces = clean_and_decimate(
        np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.int64), decimation_target=100
    )

    assert out_vertices.shape[0] == 0
    assert out_faces.shape[0] == 0


def test_inward_extraction_comes_out_wound_outward():
    """Flexible-dual-grid extraction winds inward, so a decoded shell has negative
    signed volume until `fix_normals` flips it. Positive volume is the invariant
    a glTF consumer needs for backface culling and normals to agree."""
    vertices, faces = _inward_sphere()

    out_vertices, out_faces = clean_and_decimate(vertices, faces, decimation_target=2000)

    mesh = trimesh.Trimesh(out_vertices, out_faces, process=False)
    assert mesh.is_watertight
    assert mesh.volume > 0
    assert mesh.volume == pytest.approx(4 / 3 * np.pi * _SPHERE_RADIUS**3, rel=0.05)


def test_orientation_is_normalized_regardless_of_input_winding():
    """`fix_normals` decides from signed volume, so an already-outward mesh must
    come out unflipped — the chain normalizes orientation, it does not invert."""
    vertices, inward = _inward_sphere(subdivisions=3)
    outward = np.ascontiguousarray(inward[:, ::-1])

    from_inward = clean_and_decimate(vertices, inward, decimation_target=600)
    from_outward = clean_and_decimate(vertices, outward, decimation_target=600)

    assert trimesh.Trimesh(*from_inward, process=False).volume > 0
    assert trimesh.Trimesh(*from_outward, process=False).volume > 0


def test_an_inward_cube_comes_out_wound_outward():
    box = trimesh.creation.box(extents=(0.6, 0.4, 0.5))
    faces = np.ascontiguousarray(np.asarray(box.faces, dtype=np.int64)[:, ::-1])

    out_vertices, out_faces = clean_and_decimate(
        np.asarray(box.vertices, dtype=np.float32), faces, decimation_target=1000
    )

    assert trimesh.Trimesh(out_vertices, out_faces, process=False).volume == pytest.approx(
        0.6 * 0.4 * 0.5, rel=1e-3
    )


def test_unwrap_produces_normalized_uvs_over_a_cut_vertex_set():
    vertices, faces = clean_and_decimate(*_inward_sphere(subdivisions=3), decimation_target=800)

    uv_vertices, uv_faces, uvs, normals = unwrap_uv(vertices, faces)

    assert uv_faces.shape == faces.shape
    assert uv_vertices.shape[0] >= vertices.shape[0]  # charts duplicate seam vertices
    assert uvs.shape == (uv_vertices.shape[0], 2)
    assert normals.shape == uv_vertices.shape
    assert uvs.min() >= 0.0 and uvs.max() <= 1.0
    assert np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-4)


def test_inpaint_fills_every_masked_texel_and_keeps_the_known_ones(monkeypatch):
    """Forces the no-cv2 path: OpenCV's native deps are not guaranteed present,
    and the push-pull fallback has to close UV gutters on its own."""
    monkeypatch.setitem(sys.modules, "cv2", None)
    image = np.zeros((16, 16, 3), dtype=np.uint8)
    mask = np.zeros((16, 16), dtype=bool)
    mask[4:8, 4:8] = True
    image[mask] = np.array([200, 100, 50], dtype=np.uint8)

    filled = inpaint_texture(image, mask, radius=3)

    assert filled.shape == image.shape
    assert filled.dtype == np.uint8
    assert np.array_equal(filled[mask], image[mask])
    assert (filled[~mask] > 0).any(), "uncovered texels were left black"


def test_inpaint_returns_the_image_untouched_when_nothing_is_masked():
    image = np.full((8, 8, 1), 42, dtype=np.uint8)

    assert np.array_equal(inpaint_texture(image, np.ones((8, 8), dtype=bool), radius=1), image)


@pytest.mark.parametrize(
    "vertices, faces",
    [
        (np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.int64)),
        (np.zeros((3, 3), dtype=np.float32), np.array([[0, 1, 2]], dtype=np.int64)),
    ],
    ids=["no-faces", "one-collapsed-face"],
)
def test_a_decode_with_no_surface_is_reported_not_crashed(vertices, faces):
    """Without this guard the chain dies four stages later inside trimesh's
    vertex_normals setter with a bare numpy reduction error, which tells the
    caller nothing about what went wrong."""
    attrs = torch.zeros((4, 6))
    coords = torch.tensor([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])

    with pytest.raises(ValueError, match="no geometry to post-process"):
        build_textured_mesh(
            vertices, faces, attrs, coords, voxel_size=1 / 32, decimation_target=100, texture_size=32
        )


def test_baked_texels_carry_the_colour_of_the_position_that_sampled_them():
    """The end-to-end oracle. Read each vertex's texel back through the glTF UV
    convention (u -> column, v -> row from the top) and compare against the
    colour the volume encodes at that vertex's 3D position. A texture that is
    flipped, transposed, or sampled from the wrong voxel fails this outright."""
    vertices, faces = _inward_sphere()
    attrs, coords, voxel_size = _linear_colour_shell()

    mesh = build_textured_mesh(
        vertices, faces, attrs, coords, voxel_size=voxel_size, decimation_target=2000, texture_size=512
    )

    texture = np.asarray(mesh.visual.material.baseColorTexture)
    size = texture.shape[0]
    uvs = np.asarray(mesh.visual.uv)
    expected = _undo_export_transform(np.asarray(mesh.vertices)) + 0.5

    col = np.clip((uvs[:, 0] * size).astype(int), 0, size - 1)
    row = np.clip((uvs[:, 1] * size).astype(int), 0, size - 1)
    error = np.abs(texture[row, col, :3].astype(np.float32) / 255.0 - expected)

    assert np.median(error) < 0.01
    assert np.percentile(error, 95) < 0.05


def test_material_channels_land_where_gltf_expects_them():
    vertices, faces = _inward_sphere(subdivisions=3)
    attrs, coords, voxel_size = _linear_colour_shell(resolution=24)

    mesh = build_textured_mesh(
        vertices, faces, attrs, coords, voxel_size=voxel_size, decimation_target=800, texture_size=256
    )

    material = mesh.visual.material
    covered = np.asarray(material.baseColorTexture)[..., 3] > 250
    packed = np.asarray(material.metallicRoughnessTexture)[covered] / 255.0

    assert packed[:, 0].max() == 0.0, "red channel of metallicRoughness must be unused"
    assert packed[:, 1].mean() == pytest.approx(0.75, abs=0.02), "green channel carries roughness"
    assert packed[:, 2].mean() == pytest.approx(0.25, abs=0.02), "blue channel carries metallic"
    assert material.alphaMode == "OPAQUE"
    assert material.doubleSided is True


def test_glb_round_trips_through_trimesh_with_texture_and_outward_winding(tmp_path):
    vertices, faces = _inward_sphere()
    attrs, coords, voxel_size = _linear_colour_shell()
    out_path = tmp_path / "mesh.glb"

    postprocess_to_glb(
        vertices,
        faces,
        attrs,
        coords,
        voxel_size=voxel_size,
        decimation_target=2000,
        texture_size=256,
        out_path=str(out_path),
    )

    assert b"EXT_texture_webp" in out_path.read_bytes()
    reloaded = trimesh.load(str(out_path), process=False, force="mesh")
    assert reloaded.faces.shape[0] == 2000
    assert reloaded.volume > 0
    assert reloaded.visual.material.baseColorTexture.size == (256, 256)
    assert reloaded.visual.uv.shape[0] == reloaded.vertices.shape[0]


def test_png_export_is_available_for_consumers_without_the_webp_extension(tmp_path):
    vertices, faces = _inward_sphere(subdivisions=3)
    attrs, coords, voxel_size = _linear_colour_shell(resolution=24)
    out_path = tmp_path / "mesh.glb"

    postprocess_to_glb(
        vertices, faces, attrs, coords, voxel_size=voxel_size, decimation_target=800,
        texture_size=128, out_path=str(out_path), embed_webp=False,
    )

    assert b"EXT_texture_webp" not in out_path.read_bytes()
    assert trimesh.load(str(out_path), process=False, force="mesh").visual.material.baseColorTexture.size == (128, 128)


def test_closest_point_on_a_triangle_matches_a_densely_sampled_surface():
    """Query points on every side of a triangle, checked against the nearest of
    ~80k points sampled over its surface — this covers the face, edge and vertex
    regions the four-candidate formulation has to get right."""
    triangle = torch.tensor([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]])
    axis = torch.linspace(0, 1, 401)
    u, v = torch.meshgrid(axis, axis, indexing="ij")
    interior = u + v <= 1
    surface = torch.stack([u[interior], v[interior], torch.zeros(int(interior.sum()))], dim=-1)
    gen = torch.Generator().manual_seed(0)
    points = (torch.rand((300, 3), generator=gen) - 0.5) * 4

    got = _closest_point_on_triangles(points, triangle)

    nearest = surface[((surface.unsqueeze(0) - points.unsqueeze(1)) ** 2).sum(-1).argmin(1)]
    assert torch.allclose((got - points).norm(dim=1), (nearest - points).norm(dim=1), atol=1e-3)


def test_closest_point_survives_a_degenerate_triangle():
    """Decimated meshes carry slivers; a zero-area triangle must still answer
    with a point on the segment it collapsed to, not a division by zero."""
    collapsed = torch.tensor([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]]])

    got = _closest_point_on_triangles(torch.tensor([[0.5, 3.0, 0.0], [-5.0, 1.0, 0.0]]), collapsed)

    assert torch.allclose(got, torch.tensor([[0.5, 0.0, 0.0], [0.0, 0.0, 0.0]]))


def test_projection_to_source_pulls_texels_onto_the_undecimated_surface():
    """The opt-in stand-in for upstream's CUDA BVH. Decimating a sphere pulls the
    surface inside the true radius; projecting the sampled positions back onto
    the source mesh restores it."""
    vertices, faces = _inward_sphere()
    attrs, coords, voxel_size = _linear_colour_shell()
    kwargs = dict(voxel_size=voxel_size, decimation_target=300, texture_size=64)

    plain = build_textured_mesh(vertices, faces, attrs, coords, **kwargs)
    projected = build_textured_mesh(vertices, faces, attrs, coords, project_to_source=True, **kwargs)

    def radial_error(mesh):
        texture = np.asarray(mesh.visual.material.baseColorTexture)
        covered = texture[..., 3] > 250
        sampled = texture[covered][:, :3].astype(np.float32) / 255.0 - 0.5
        return np.abs(np.linalg.norm(sampled, axis=1) - _SPHERE_RADIUS).mean()

    assert radial_error(projected) < radial_error(plain)
