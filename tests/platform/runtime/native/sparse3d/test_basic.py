from fractions import Fraction

import pytest
import torch

from src.platform.runtime.native.sparse3d.basic import SparseTensor, sparse_cat, sparse_unbind


def _make(counts, channels=3, dtype=torch.float32):
    """Batch with `len(counts)` elements, `counts[i]` voxels each, coords on
    a small 3D grid so different batches don't collide."""
    feats = []
    coords = []
    for b, n in enumerate(counts):
        feats.append(torch.randn(n, channels, dtype=dtype))
        xyz = torch.stack([torch.arange(n), torch.arange(n) * 2 % 5, torch.zeros(n, dtype=torch.long)], dim=1)
        coords.append(torch.cat([torch.full((n, 1), b, dtype=torch.long), xyz], dim=1))
    return SparseTensor(torch.cat(feats, dim=0), torch.cat(coords, dim=0))


def test_construction_layout_seqlen_invariants():
    t = _make([2, 3, 1])
    assert t.shape[0] == 3
    assert t.layout == [slice(0, 2), slice(2, 5), slice(5, 6)]
    assert t.seqlen.tolist() == [2, 3, 1]
    assert t.cum_seqlen.tolist() == [0, 2, 5, 6]
    assert len(t) == 3


def test_layout_seqlen_cum_seqlen_cached_per_scale():
    t = _make([2, 3])
    layout_a = t.layout
    assert t.get_spatial_cache("layout") is layout_a
    t._scale = (Fraction(2, 1),) * 3
    assert t.get_spatial_cache("layout") is None


def test_elementwise_add_matches_dense_reference():
    t = _make([2, 3])
    other = _make([2, 3])
    out = t + other
    assert torch.allclose(out.feats, t.feats + other.feats)
    assert torch.equal(out.coords, t.coords)


def test_elementwise_scalar_ops():
    t = _make([2, 3])
    assert torch.allclose((t * 2.0).feats, t.feats * 2.0)
    assert torch.allclose((t - 1.0).feats, t.feats - 1.0)
    assert torch.allclose((1.0 - t).feats, 1.0 - t.feats)
    assert torch.allclose((-t).feats, -t.feats)


def test_elementwise_batch_broadcast():
    t = _make([2, 3], channels=4)
    per_batch = torch.arange(t.shape[0] * t.shape[1], dtype=torch.float32).reshape(t.shape[0], t.shape[1])
    out = t + per_batch
    expected = torch.cat([
        t.feats[0:2] + per_batch[0],
        t.feats[2:5] + per_batch[1],
    ], dim=0)
    assert torch.allclose(out.feats, expected)


def test_sparse_cat_and_unbind_round_trip():
    a = _make([2, 3])
    b = _make([1, 4])
    cat = sparse_cat([a, b])
    assert cat.shape[0] == 4
    assert cat.seqlen.tolist() == [2, 3, 1, 4]

    parts = sparse_unbind(cat, dim=0)
    assert len(parts) == 4
    assert torch.allclose(parts[0].feats, a.feats[0:2])
    assert torch.allclose(parts[1].feats, a.feats[2:5])
    assert torch.allclose(parts[2].feats, b.feats[0:1])
    assert torch.allclose(parts[3].feats, b.feats[1:5])
    for p in parts:
        assert torch.all(p.coords[:, 0] == 0)


def test_unbind_method_matches_module_function():
    t = _make([2, 3])
    assert [p.feats.shape for p in t.unbind(0)] == [p.feats.shape for p in sparse_unbind(t, 0)]


def test_replace_preserves_coords_by_default():
    t = _make([2, 3], channels=4)
    new_feats = torch.zeros(5, 4)
    out = t.replace(new_feats)
    assert torch.equal(out.coords, t.coords)
    assert torch.equal(out.feats, new_feats)


def test_type_and_device_casts():
    t = _make([2, 3])
    assert t.half().dtype == torch.float16
    assert t.float().dtype == torch.float32
    assert t.type(torch.float64).dtype == torch.float64
    moved = t.to(torch.float64)
    assert moved.dtype == torch.float64
    assert moved.device == t.device


def test_to_dense_scatters_feats_at_coords():
    t = _make([1], channels=2)
    dense = t.to_dense()
    assert dense.shape[0] == 1
    assert dense.shape[1] == 2
    coord = t.coords[0]
    assert torch.allclose(dense[coord[0], :, coord[1], coord[2], coord[3]], t.feats[0])


def test_row_mismatch_raises():
    feats = torch.randn(3, 2)
    coords = torch.zeros(4, 4, dtype=torch.long)
    with pytest.raises(AssertionError):
        SparseTensor(feats, coords)
