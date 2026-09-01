import torch

from src.platform.runtime.native.sparse3d.basic import SparseTensor
from src.platform.runtime.native.sparse3d.rope import SparseRotaryPositionEmbedder


def _dense_rotary_reference(feats, coords_xyz, head_dim, dim, rope_freq):
    """Independent per-token rotary embedding: for each of `dim` axes, build
    `freq_dim = head_dim//2//dim` unit-magnitude phases from that axis's
    coordinate, concatenate axis-major, then rotate consecutive feat pairs."""
    freq_dim = head_dim // 2 // dim
    base = torch.arange(freq_dim, dtype=torch.float32) / freq_dim
    freqs = rope_freq[0] / (rope_freq[1] ** base)

    out = torch.empty_like(feats)
    for token in range(feats.shape[0]):
        angles = []
        for axis in range(dim):
            coord = coords_xyz[token, axis].float()
            angles.append(coord * freqs)
        phase_angles = torch.cat(angles)  # (dim * freq_dim,) == head_dim // 2
        phases = torch.polar(torch.ones_like(phase_angles), phase_angles)
        for head in range(feats.shape[1]):
            pairs = feats[token, head].reshape(-1, 2)
            complex_pairs = torch.view_as_complex(pairs.float())
            rotated = complex_pairs * phases
            out[token, head] = torch.view_as_real(rotated).reshape(-1).to(feats.dtype)
    return out


def test_rope_matches_dense_formula_per_token():
    head_dim, dim = 12, 3  # freq_dim = 2, 3 axes * 2 = 6 == head_dim // 2, no padding
    coords = torch.tensor([
        [0, 1, 2, 3],
        [0, 4, 0, 1],
        [0, 0, 0, 0],
    ], dtype=torch.long)
    feats = torch.randn(3, 1, head_dim)
    q = SparseTensor(feats, coords)

    embedder = SparseRotaryPositionEmbedder(head_dim=head_dim, dim=dim)
    out = embedder(q)

    expected = _dense_rotary_reference(feats, coords[:, 1:], head_dim, dim, (1.0, 10000.0))
    assert torch.allclose(out.feats, expected, atol=1e-5)


def test_rope_applies_same_phases_to_q_and_k():
    head_dim, dim = 12, 3
    coords = torch.tensor([[0, 2, 1, 0], [0, 0, 3, 2]], dtype=torch.long)
    q_feats = torch.randn(2, 2, head_dim)
    k_feats = torch.randn(2, 2, head_dim)
    q = SparseTensor(q_feats, coords)
    k = SparseTensor(k_feats, coords)

    embedder = SparseRotaryPositionEmbedder(head_dim=head_dim, dim=dim)
    q_out, k_out = embedder(q, k)

    expected_q = _dense_rotary_reference(q_feats, coords[:, 1:], head_dim, dim, (1.0, 10000.0))
    expected_k = _dense_rotary_reference(k_feats, coords[:, 1:], head_dim, dim, (1.0, 10000.0))
    assert torch.allclose(q_out.feats, expected_q, atol=1e-5)
    assert torch.allclose(k_out.feats, expected_k, atol=1e-5)


def test_rope_caches_phases_on_the_query_tensor():
    head_dim, dim = 12, 3
    coords = torch.tensor([[0, 1, 1, 1]], dtype=torch.long)
    q = SparseTensor(torch.randn(1, 1, head_dim), coords)
    embedder = SparseRotaryPositionEmbedder(head_dim=head_dim, dim=dim)
    embedder(q)
    cache_name = f"rope_phase_{dim}d_freq1.0-10000.0_hd{head_dim}"
    assert q.get_spatial_cache(cache_name) is not None
