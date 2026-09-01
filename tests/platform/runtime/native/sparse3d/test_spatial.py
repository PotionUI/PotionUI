import torch

from src.platform.runtime.native.sparse3d.basic import SparseTensor
from src.platform.runtime.native.sparse3d.spatial import (
    SparseChannel2Spatial,
    SparseDownsample,
    SparseSpatial2Channel,
    SparseUpsample,
)


def _coord_feat_map(coords, feats):
    return {tuple(c.tolist()): f for c, f in zip(coords, feats)}


def _brute_pool(coords, feats, factor, mode):
    """Reference pooling: group rows by (batch, coord // factor), reduce."""
    groups = {}
    for c, f in zip(coords, feats):
        key = (c[0].item(), *(int(x) // factor for x in c[1:].tolist()))
        groups.setdefault(key, []).append(f)
    out = {}
    for key, vals in groups.items():
        stacked = torch.stack(vals)
        out[key] = stacked.mean(dim=0) if mode == "mean" else stacked.max(dim=0).values
    return out


def test_downsample_mean_matches_brute_force_on_ragged_grid():
    # Full 2x2 parent (all 4 children) + a partial parent (2 of 4 children) —
    # exercises scatter_reduce 'mean' averaging only present children.
    coords = torch.tensor([
        [0, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 1, 1, 0],
        [0, 2, 0, 0], [0, 3, 1, 0],
    ], dtype=torch.long)
    feats = torch.randn(6, 3)
    x = SparseTensor(feats, coords)

    out = SparseDownsample(factor=2, mode="mean")(x)
    actual = _coord_feat_map(out.coords, out.feats)
    expected = _brute_pool(coords, feats, factor=2, mode="mean")

    assert set(actual.keys()) == set(expected.keys())
    for key in expected:
        assert torch.allclose(actual[key], expected[key], atol=1e-6)


def test_downsample_max_matches_brute_force():
    coords = torch.tensor([[0, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 1, 1, 0]], dtype=torch.long)
    feats = torch.randn(4, 2)
    x = SparseTensor(feats, coords)

    out = SparseDownsample(factor=2, mode="max")(x)
    actual = _coord_feat_map(out.coords, out.feats)
    expected = _brute_pool(coords, feats, factor=2, mode="max")

    assert set(actual.keys()) == set(expected.keys())
    for key in expected:
        assert torch.allclose(actual[key], expected[key])


def test_upsample_paired_with_downsample_is_nearest_neighbor_of_parent():
    coords = torch.tensor([
        [0, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 1, 1, 0],
        [0, 2, 0, 0],
    ], dtype=torch.long)
    feats = torch.randn(5, 3)
    x = SparseTensor(feats, coords)

    down = SparseDownsample(factor=2, mode="mean")(x)
    up = SparseUpsample(factor=2)(down)

    assert torch.equal(up.coords, x.coords)
    down_map = _coord_feat_map(down.coords, down.feats)
    for orig_coord, up_feat in zip(x.coords, up.feats):
        parent_key = (orig_coord[0].item(), *(int(v) // 2 for v in orig_coord[1:].tolist()))
        assert torch.allclose(down_map[parent_key], up_feat)


def test_channel2spatial_scatters_children_into_8_child_layout():
    # Hand-built 2-voxel case: two children of the same parent, factor=2, DIM=3.
    # child0 coords (0,0,0) -> subidx 0; child1 coords (1,0,0) -> subidx 1.
    coords = torch.tensor([[0, 0, 0, 0], [0, 1, 0, 0]], dtype=torch.long)
    feats = torch.tensor([[1.0, 2.0], [3.0, 4.0]])
    x = SparseTensor(feats, coords)

    out = SparseSpatial2Channel(factor=2)(x)

    assert torch.equal(out.coords, torch.tensor([[0, 0, 0, 0]], dtype=torch.long))
    C = feats.shape[1]
    row = out.feats[0]
    assert torch.allclose(row[0 * C:1 * C], feats[0])   # subidx 0 slot
    assert torch.allclose(row[1 * C:2 * C], feats[1])   # subidx 1 slot
    assert torch.all(row[2 * C:] == 0)                  # unoccupied slots stay zero


def test_spatial2channel_then_channel2spatial_round_trips_exactly():
    coords = torch.tensor([[0, 0, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0]], dtype=torch.long)
    feats = torch.randn(3, 4)
    x = SparseTensor(feats, coords)

    coarse = SparseSpatial2Channel(factor=2)(x)
    fine = SparseChannel2Spatial(factor=2)(coarse)

    assert torch.equal(fine.coords, x.coords)
    assert torch.allclose(fine.feats, x.feats)


def test_channel2spatial_explicit_subdivision_matches_cached_path():
    coords = torch.tensor([[0, 0, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0]], dtype=torch.long)
    feats = torch.randn(3, 4)
    x = SparseTensor(feats, coords)

    s2c = SparseSpatial2Channel(factor=2)
    assert s2c.training  # default nn.Module state -> the subdivision cache branch runs
    coarse = s2c(x)
    subdivision_mask = coarse.get_spatial_cache("subdivision")
    assert subdivision_mask is not None

    subdivision = SparseTensor(subdivision_mask, coarse.coords.clone())
    fresh_coarse = SparseTensor(coarse.feats.clone(), coarse.coords.clone())

    explicit = SparseChannel2Spatial(factor=2)(fresh_coarse, subdivision=subdivision)
    cached = SparseChannel2Spatial(factor=2)(coarse)
    assert torch.equal(explicit.coords, cached.coords)
    assert torch.allclose(explicit.feats, cached.feats)
