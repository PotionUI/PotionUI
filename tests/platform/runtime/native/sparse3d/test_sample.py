import torch
import torch.nn.functional as F

from src.platform.runtime.native.sparse3d.sample import sparse_grid_sample_3d

# flex_gemm's own `grid_sample_torch.py` reference cannot serve as an oracle here:
# it calls the compiled CUDA hashmap kernels. The oracle below is instead
# `F.grid_sample`, which agrees with the CUDA kernel's convention exactly when the
# sparse set is dense — voxel `c` centred at `c + 0.5`, i.e. align_corners=False.


def _densify(extent, channels, seed):
    """A fully-occupied volume as both a dense `[1, C, W, H, D]` tensor and the
    `(feats, coords)` sparse pair addressing the same voxels."""
    gen = torch.Generator().manual_seed(seed)
    w, h, d = extent
    dense = torch.randn((1, channels, w, h, d), generator=gen)
    coords = torch.stack(
        torch.meshgrid(torch.arange(w), torch.arange(h), torch.arange(d), indexing="ij"), dim=-1
    ).reshape(-1, 3)
    feats = dense[0].permute(1, 2, 3, 0).reshape(-1, channels).contiguous()
    coords4 = torch.cat([torch.zeros((coords.shape[0], 1), dtype=torch.long), coords], dim=1)
    return dense, feats, coords4


def _grid_sample_oracle(dense, query, extent):
    """`F.grid_sample` at the same points. Its grid's last axis indexes the
    tensor's last spatial dim, so (x, y, z) has to be reversed."""
    size = torch.tensor(extent, dtype=torch.float32)
    normalized = 2 * query / size - 1
    channels = dense.shape[1]
    out = F.grid_sample(
        dense,
        normalized.flip(-1).reshape(1, 1, 1, -1, 3),
        mode="bilinear",
        align_corners=False,
        padding_mode="border",
    )
    return out.reshape(channels, -1).T.reshape(1, -1, channels)


def test_dense_volume_matches_torch_grid_sample():
    extent = (6, 7, 5)
    dense, feats, coords = _densify(extent, 4, seed=0)
    gen = torch.Generator().manual_seed(1)
    # Interior only: `padding_mode="border"` makes the oracle clamp outside the
    # half-voxel margin, while we renormalize instead.
    query = 0.5 + torch.rand((1, 400, 3), generator=gen) * (torch.tensor(extent) - 1.0)

    got = sparse_grid_sample_3d(feats, coords, extent, query)

    assert torch.allclose(got, _grid_sample_oracle(dense, query, extent), atol=1e-5)


def test_corner_cell_is_floored_not_truncated():
    """Below half a voxel the low corner is `floor(q - 0.5) == -1`. flex_gemm's
    torch reference truncates to 0 there and disagrees with its own CUDA kernel;
    we follow the kernel, so the dense oracle still matches."""
    extent = (4, 4, 4)
    dense, feats, coords = _densify(extent, 2, seed=2)
    query = torch.tensor([[[0.30, 0.49, 0.20], [0.10, 0.10, 0.10], [0.55, 2.5, 3.4]]])

    got = sparse_grid_sample_3d(feats, coords, extent, query)

    assert torch.allclose(got, _grid_sample_oracle(dense, query, extent), atol=1e-5)


def test_absent_neighbours_renormalize_rather_than_darken():
    """The kernel divides by the sum of the *surviving* weights, so a point whose
    only present neighbours are two equal-weighted voxels reads their mean at
    full strength, not a fraction of it."""
    feats = torch.tensor([[1.0], [3.0]])
    coords = torch.tensor([[0, 0, 0, 0], [0, 1, 0, 0]])

    midpoint = sparse_grid_sample_3d(feats, coords, (4, 4, 4), torch.tensor([[[1.0, 0.5, 0.5]]]))
    assert torch.allclose(midpoint, torch.tensor([[[2.0]]]))

    # Shifting off-axis kills the four y-neighbours; the two survivors keep their
    # 50/50 split, so the value is unchanged rather than scaled by 0.3.
    off_axis = sparse_grid_sample_3d(feats, coords, (4, 4, 4), torch.tensor([[[1.0, 1.2, 0.5]]]))
    assert torch.allclose(off_axis, torch.tensor([[[2.0]]]))


def test_point_with_no_present_neighbour_reads_zero():
    feats = torch.tensor([[1.0], [3.0]])
    coords = torch.tensor([[0, 0, 0, 0], [0, 1, 0, 0]])

    got = sparse_grid_sample_3d(feats, coords, (8, 8, 8), torch.tensor([[[6.5, 6.5, 6.5]]]))

    assert torch.equal(got, torch.zeros((1, 1, 1)))


def test_corners_outside_grid_size_are_treated_as_absent():
    """`grid_size` bounds the lookup even when a matching coord exists — the
    kernel bounds-checks before probing the hashmap."""
    feats = torch.tensor([[1.0], [5.0]])
    coords = torch.tensor([[0, 0, 0, 0], [0, 4, 0, 0]])

    unbounded = sparse_grid_sample_3d(feats, coords, (8, 8, 8), torch.tensor([[[4.5, 0.5, 0.5]]]))
    assert torch.allclose(unbounded, torch.tensor([[[5.0]]]))

    # A grid of 2 puts coord x=4 out of range, leaving only the voxel at x=0.
    bounded = sparse_grid_sample_3d(feats, coords, (2, 8, 8), torch.tensor([[[4.5, 0.5, 0.5]]]))
    assert torch.equal(bounded, torch.zeros((1, 1, 1)))


def test_batches_do_not_leak_into_each_other():
    feats = torch.tensor([[1.0], [9.0]])
    coords = torch.tensor([[0, 2, 2, 2], [1, 2, 2, 2]])
    query = torch.tensor([[[2.5, 2.5, 2.5]], [[2.5, 2.5, 2.5]]])

    got = sparse_grid_sample_3d(feats, coords, (8, 8, 8), query)

    assert torch.allclose(got, torch.tensor([[[1.0]], [[9.0]]]))


def test_nearest_truncates_toward_zero():
    """The nearest kernel uses `static_cast<int>`, not `floor` — a query at 0.9
    lands in voxel 0 either way, but the distinction is the documented one."""
    feats = torch.tensor([[7.0], [8.0]])
    coords = torch.tensor([[0, 0, 0, 0], [0, 1, 0, 0]])

    got = sparse_grid_sample_3d(
        feats, coords, (4, 4, 4), torch.tensor([[[0.9, 0.5, 0.5], [1.9, 0.5, 0.5]]]), mode="nearest"
    )

    assert torch.allclose(got, torch.tensor([[[7.0], [8.0]]]))


def test_empty_sparse_set_returns_zeros():
    feats = torch.zeros((0, 3))
    coords = torch.zeros((0, 4), dtype=torch.long)

    got = sparse_grid_sample_3d(feats, coords, (4, 4, 4), torch.rand((2, 5, 3)))

    assert got.shape == (2, 5, 3)
    assert torch.equal(got, torch.zeros((2, 5, 3)))
