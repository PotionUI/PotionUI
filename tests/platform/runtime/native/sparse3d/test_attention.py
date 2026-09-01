import pytest
import torch
import torch.nn.functional as F

from src.platform.runtime.native.sparse3d.attention import sparse_scaled_dot_product_attention
from src.platform.runtime.native.sparse3d.basic import SparseTensor


def _make_batch(seqlens, heads, channels, dtype=torch.float32):
    feats_list = [torch.randn(n, heads, channels, dtype=dtype) for n in seqlens]
    coords_list = [
        torch.cat([
            torch.full((n, 1), b, dtype=torch.long),
            torch.arange(n, dtype=torch.long).unsqueeze(1),
            torch.zeros(n, 2, dtype=torch.long),
        ], dim=1)
        for b, n in enumerate(seqlens)
    ]
    return feats_list, torch.cat(coords_list, dim=0)


def _looped_dense_sdpa(feats_list_q, feats_list_k, feats_list_v):
    chunks = []
    for qf, kf, vf in zip(feats_list_q, feats_list_k, feats_list_v):
        qi = qf.transpose(0, 1).unsqueeze(0)
        ki = kf.transpose(0, 1).unsqueeze(0)
        vi = vf.transpose(0, 1).unsqueeze(0)
        oi = F.scaled_dot_product_attention(qi, ki, vi)
        chunks.append(oi.squeeze(0).transpose(0, 1))
    return torch.cat(chunks, dim=0)


def test_ragged_batch_matches_looped_dense_sdpa():
    torch.manual_seed(0)
    seqlens = [3, 5]
    heads, channels = 2, 4
    feats_q, coords = _make_batch(seqlens, heads, channels)
    feats_k, _ = _make_batch(seqlens, heads, channels)
    feats_v, _ = _make_batch(seqlens, heads, channels)

    q = SparseTensor(torch.cat(feats_q, dim=0), coords)
    k = SparseTensor(torch.cat(feats_k, dim=0), coords.clone())
    v = SparseTensor(torch.cat(feats_v, dim=0), coords.clone())

    out = sparse_scaled_dot_product_attention(q, k, v)
    expected = _looped_dense_sdpa(feats_q, feats_k, feats_v)

    assert torch.allclose(out.feats, expected, atol=1e-6)
    assert torch.equal(out.coords, q.coords)


def test_batch_of_one_collapses_to_single_unmasked_call():
    torch.manual_seed(1)
    seqlens = [4]
    heads, channels = 1, 8
    feats_q, coords = _make_batch(seqlens, heads, channels)
    feats_k, _ = _make_batch(seqlens, heads, channels)
    feats_v, _ = _make_batch(seqlens, heads, channels)

    q = SparseTensor(torch.cat(feats_q, dim=0), coords)
    k = SparseTensor(torch.cat(feats_k, dim=0), coords.clone())
    v = SparseTensor(torch.cat(feats_v, dim=0), coords.clone())

    out = sparse_scaled_dot_product_attention(q, k, v)

    qi = feats_q[0].transpose(0, 1).unsqueeze(0)
    ki = feats_k[0].transpose(0, 1).unsqueeze(0)
    vi = feats_v[0].transpose(0, 1).unsqueeze(0)
    expected = F.scaled_dot_product_attention(qi, ki, vi).squeeze(0).transpose(0, 1)

    assert torch.allclose(out.feats, expected, atol=1e-6)


def test_batch_size_mismatch_raises():
    feats_q, coords_q = _make_batch([3, 4], 1, 4)
    feats_k, coords_k = _make_batch([3], 1, 4)
    q = SparseTensor(torch.cat(feats_q, dim=0), coords_q)
    k = SparseTensor(torch.cat(feats_k, dim=0), coords_k)
    with pytest.raises(AssertionError):
        sparse_scaled_dot_product_attention(q, k, k)


def test_k_v_layout_mismatch_raises():
    feats_k, coords_k = _make_batch([3, 4], 1, 4)
    feats_v, coords_v = _make_batch([2, 5], 1, 4)  # same batch size, different per-seq lengths
    q = SparseTensor(torch.cat(feats_k, dim=0), coords_k.clone())
    k = SparseTensor(torch.cat(feats_k, dim=0), coords_k)
    v = SparseTensor(torch.cat(feats_v, dim=0), coords_v)
    with pytest.raises(AssertionError):
        sparse_scaled_dot_product_attention(q, k, v)
