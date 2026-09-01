import torch

from src.platform.runtime.native.arch.trellis2 import uv_raster
from src.platform.runtime.native.arch.trellis2.uv_raster import interpolate_barycentric, rasterize_uv_atlas

# nvdiffrast is CUDA-only and cannot be imported here, so coverage is checked
# against hand-enumerated texel centres and against the identity that a triangle's
# interpolated UV attribute must reproduce the texel centre that sampled it.


def _unit_square():
    uvs = torch.tensor([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    faces = torch.tensor([[0, 1, 2], [0, 2, 3]])
    return uvs, faces


def test_two_triangles_covering_uv_space_cover_every_texel():
    uvs, faces = _unit_square()

    face_id, _ = rasterize_uv_atlas(uvs, faces, 8)

    assert face_id.shape == (8, 8)
    assert bool((face_id >= 0).all())


def test_row_index_maps_to_v_top_down():
    """Row `r` is `v = (r + 0.5) / T`. The upstream bake runs on nvdiffrast,
    whose rows are bottom-up, which is why it flips V when writing glTF UVs; the
    flip lives here instead, so row 0 must be v ~ 0."""
    uvs, faces = _unit_square()

    face_id, bary = rasterize_uv_atlas(uvs, faces, 8)
    sampled = interpolate_barycentric(uvs, faces, face_id, bary)

    assert torch.allclose(sampled[0, 0], torch.tensor([0.5 / 8, 0.5 / 8]), atol=1e-6)
    assert torch.allclose(sampled[7, 7], torch.tensor([7.5 / 8, 7.5 / 8]), atol=1e-6)
    assert torch.allclose(sampled[2, 5], torch.tensor([5.5 / 8, 2.5 / 8]), atol=1e-6)


def test_interpolated_uv_reproduces_the_texel_centre():
    """The strongest self-consistency check available without nvdiffrast: feeding
    the UVs back through as the interpolated attribute must return each covered
    texel's own centre."""
    gen = torch.Generator().manual_seed(0)
    uvs = torch.rand((16, 2), generator=gen)
    faces = torch.randint(0, 16, (12, 3), generator=gen)
    size = 32

    face_id, bary = rasterize_uv_atlas(uvs, faces, size)
    sampled = interpolate_barycentric(uvs, faces, face_id, bary)

    covered = face_id >= 0
    assert bool(covered.any())
    rows, cols = torch.nonzero(covered, as_tuple=True)
    centres = torch.stack([(cols + 0.5) / size, (rows + 0.5) / size], dim=-1)
    assert torch.allclose(sampled[covered], centres, atol=1e-5)


def test_barycentric_weights_are_a_partition_of_unity_where_covered():
    gen = torch.Generator().manual_seed(1)
    uvs = torch.rand((10, 2), generator=gen)
    faces = torch.randint(0, 10, (8, 3), generator=gen)

    face_id, bary = rasterize_uv_atlas(uvs, faces, 24)

    covered = face_id >= 0
    assert torch.allclose(bary[covered].sum(dim=-1), torch.ones(int(covered.sum())), atol=1e-5)
    assert bool((bary[covered] >= -1e-6).all())
    assert torch.equal(bary[~covered], torch.zeros_like(bary[~covered]))


def test_half_covering_triangle_leaves_the_other_half_empty():
    """A single triangle over the u + v <= 1 half of UV space. Texel (1, 7) has
    centre (0.9375, 0.1875), clear of the hypotenuse; (0, 7) is skipped because
    its centre sums to exactly 1 and lands on the edge, which counts as covered."""
    uvs = torch.tensor([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])
    faces = torch.tensor([[0, 1, 2]])

    face_id, _ = rasterize_uv_atlas(uvs, faces, 8)

    assert int(face_id[0, 0]) == 0
    assert int(face_id[1, 7]) == -1
    assert int(face_id[7, 7]) == -1
    covered = (face_id >= 0).sum().item()
    assert 24 <= covered <= 40, covered


def test_chunking_over_faces_does_not_change_the_result(monkeypatch):
    gen = torch.Generator().manual_seed(2)
    uvs = torch.rand((40, 2), generator=gen)
    faces = torch.randint(0, 40, (60, 3), generator=gen)

    whole_id, whole_bary = rasterize_uv_atlas(uvs, faces, 32)
    monkeypatch.setattr(uv_raster, "_TEXEL_BUDGET", 64)
    chunked_id, chunked_bary = rasterize_uv_atlas(uvs, faces, 32)

    assert torch.equal(whole_id, chunked_id)
    assert torch.allclose(whole_bary, chunked_bary, atol=1e-6)


def test_chunks_are_cut_on_candidate_texels_not_face_count(monkeypatch):
    """The budget is what bounds peak memory, so a single face that exceeds it
    has to be isolated rather than merged into a neighbouring chunk."""
    assert uv_raster._chunk_bounds(torch.tensor([1, 2, 3])) == [(0, 3)]

    oversized = torch.tensor([uv_raster._TEXEL_BUDGET * 2, 5, 5])
    assert uv_raster._chunk_bounds(oversized) == [(0, 1), (1, 3)]

    monkeypatch.setattr(uv_raster, "_TEXEL_BUDGET", 10)
    assert uv_raster._chunk_bounds(torch.tensor([4, 4, 4, 4, 4])) == [(0, 2), (2, 4), (4, 5)]


def test_degenerate_and_empty_inputs_produce_no_coverage():
    empty_id, empty_bary = rasterize_uv_atlas(torch.zeros((0, 2)), torch.zeros((0, 3), dtype=torch.long), 4)
    assert torch.equal(empty_id, torch.full((4, 4), -1, dtype=torch.long))
    assert torch.equal(empty_bary, torch.zeros((4, 4, 3)))

    # Three collinear UVs have zero area and must not claim any texel.
    collinear = torch.tensor([[0.0, 0.0], [0.5, 0.5], [1.0, 1.0]])
    face_id, _ = rasterize_uv_atlas(collinear, torch.tensor([[0, 1, 2]]), 8)
    assert bool((face_id < 0).all())
