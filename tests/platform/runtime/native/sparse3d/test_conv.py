import sys
from pathlib import Path

import pytest
import torch
import torch.nn as nn

from src.platform.runtime.native.sparse3d.basic import SparseTensor
from src.platform.runtime.native.sparse3d.conv import SparseConv3d

_CACHE_KEY_3x3x3 = "sparse_conv3d_neighbor_map_3x3x3_dilation1"


def _ragged_coords(counts, extent, seed):
    """Batch with `len(counts)` elements, each a random dedup'd voxel set on
    a `extent`^3 grid (includes edge-of-grid coords by construction, since
    `randint` covers the full [0, extent) range)."""
    gen = torch.Generator().manual_seed(seed)
    coords = []
    for b, n in enumerate(counts):
        c = torch.randint(0, extent, (n, 3), generator=gen)
        c = torch.unique(c, dim=0)
        coords.append(torch.cat([torch.full((c.shape[0], 1), b, dtype=torch.long), c], dim=1))
    return torch.cat(coords, dim=0)


def _dense_reference(conv: SparseConv3d, extent) -> nn.Conv3d:
    """A dense nn.Conv3d carrying `conv`'s weights, submanifold conv over a
    zero-filled dense grid is identical to a padded dense conv evaluated only
    at the active sites."""
    ref = nn.Conv3d(conv.in_channels, conv.out_channels, conv.kernel_size, padding=1, bias=conv.bias is not None)
    with torch.no_grad():
        ref.weight.copy_(conv.weight.permute(0, 4, 1, 2, 3))
        if conv.bias is not None:
            ref.bias.copy_(conv.bias)
    return ref


def test_dense_oracle_matches_at_active_sites():
    torch.manual_seed(0)
    extent = 8
    coords = _ragged_coords([120, 90], extent, seed=1)
    n, cin, cout = coords.shape[0], 5, 7
    feats = torch.randn(n, cin)
    x = SparseTensor(feats, coords)

    conv = SparseConv3d(cin, cout, kernel_size=3, bias=True)
    ref = _dense_reference(conv, extent)

    out = conv(x)
    assert torch.equal(out.coords, coords)

    for b in range(2):
        mask = coords[:, 0] == b
        bc = coords[mask][:, 1:]
        dense_in = torch.zeros(1, cin, extent, extent, extent)
        dense_in[0, :, bc[:, 0], bc[:, 1], bc[:, 2]] = feats[mask].T
        dense_out = ref(dense_in)
        expected = dense_out[0, :, bc[:, 0], bc[:, 1], bc[:, 2]].T
        assert torch.allclose(out.feats[mask], expected, atol=1e-5, rtol=1e-5)


def test_neighbor_map_cache_is_built_once_and_reused():
    torch.manual_seed(0)
    coords = _ragged_coords([50], 6, seed=2)
    x = SparseTensor(torch.randn(coords.shape[0], 4), coords)

    assert x.get_spatial_cache(_CACHE_KEY_3x3x3) is None
    conv_a = SparseConv3d(4, 6, kernel_size=3)
    conv_a(x)
    cached = x.get_spatial_cache(_CACHE_KEY_3x3x3)
    assert cached is not None

    conv_b = SparseConv3d(4, 6, kernel_size=3)
    conv_b(x)
    assert x.get_spatial_cache(_CACHE_KEY_3x3x3) is cached


def test_isolated_voxel_equals_center_tap_only():
    feats = torch.randn(1, 4)
    coords = torch.tensor([[0, 4, 4, 4]], dtype=torch.long)
    x = SparseTensor(feats, coords)

    conv = SparseConv3d(4, 6, kernel_size=3, bias=True)
    out = conv(x)

    center = conv.weight[:, 1, 1, 1, :]
    expected = feats @ center.T + conv.bias
    assert torch.allclose(out.feats, expected, atol=1e-6)


def test_bias_disabled_omits_bias_term():
    feats = torch.randn(1, 4)
    coords = torch.tensor([[0, 4, 4, 4]], dtype=torch.long)
    x = SparseTensor(feats, coords)

    conv = SparseConv3d(4, 6, kernel_size=3, bias=False)
    assert conv.bias is None
    out = conv(x)

    center = conv.weight[:, 1, 1, 1, :]
    expected = feats @ center.T
    assert torch.allclose(out.feats, expected, atol=1e-6)


def test_non_cubic_grid_extents_match_dense_oracle():
    torch.manual_seed(0)
    gen = torch.Generator().manual_seed(3)
    xy = torch.randint(0, 4, (70, 2), generator=gen)
    z = torch.randint(0, 20, (70, 1), generator=gen)
    xyz = torch.unique(torch.cat([xy, z], dim=1), dim=0)
    coords = torch.cat([torch.zeros(xyz.shape[0], 1, dtype=torch.long), xyz], dim=1)
    n, cin, cout = coords.shape[0], 3, 5
    feats = torch.randn(n, cin)
    x = SparseTensor(feats, coords)

    conv = SparseConv3d(cin, cout, kernel_size=3, bias=True)
    ref = nn.Conv3d(cin, cout, 3, padding=1, bias=True)
    with torch.no_grad():
        ref.weight.copy_(conv.weight.permute(0, 4, 1, 2, 3))
        ref.bias.copy_(conv.bias)

    out = conv(x)
    ex, ey, ez = 4, 4, 20
    dense_in = torch.zeros(1, cin, ex, ey, ez)
    dense_in[0, :, coords[:, 1], coords[:, 2], coords[:, 3]] = feats.T
    dense_out = ref(dense_in)
    expected = dense_out[0, :, coords[:, 1], coords[:, 2], coords[:, 3]].T
    assert torch.allclose(out.feats, expected, atol=1e-5, rtol=1e-5)


def test_vendored_sparse_conv3d_key_space_matches_when_constructible():
    """Guarded like tests/platform/runtime/native/arch/trellis2/_vendor.py:
    the flex_gemm/spconv/torchsparse backends aren't installed in this
    environment, so the vendored module's __init__ (which imports its
    backend at construction time) is expected to fail here and the test
    skips rather than asserting nothing."""
    vendor_root = Path(__file__).resolve().parents[6] / "content/plugins/local/trellis2/vendor/TRELLIS.2"
    if not vendor_root.is_dir():
        pytest.skip(f"TRELLIS.2 vendor checkout not present at {vendor_root}")

    vendor_str = str(vendor_root)
    added = vendor_str not in sys.path
    if added:
        sys.path.insert(0, vendor_str)
    try:
        from trellis2.modules.sparse.conv.conv import SparseConv3d as VendorSparseConv3d
    finally:
        if added:
            sys.path.remove(vendor_str)

    try:
        vendor_conv = VendorSparseConv3d(4, 6, kernel_size=3, bias=True)
    except Exception as exc:  # flex_gemm/spconv/torchsparse not installed
        pytest.skip(f"vendored SparseConv3d not constructible in this environment: {exc}")

    ours = SparseConv3d(4, 6, kernel_size=3, bias=True)
    assert set(ours.state_dict().keys()) == set(vendor_conv.state_dict().keys())
    assert ours.weight.shape == vendor_conv.weight.shape
    assert ours.bias.shape == vendor_conv.bias.shape
