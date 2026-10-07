import copy

import pytest
import torch
from torch.utils._python_dispatch import TorchDispatchMode
from torch.utils._pytree import tree_leaves

from src.platform.runtime.native.sparse3d import conv as conv_module
from src.platform.runtime.native.sparse3d.basic import SparseTensor
from src.platform.runtime.native.sparse3d.conv import SparseConv3d, _build_neighbor_map


def _old_neighbor_map(coords, kernel_size, dilation):
    device = coords.device
    n = coords.shape[0]
    coords = coords.to(torch.long)
    batch = coords[:, 0]
    spatial = coords[:, 1:]
    kd, kh, kw = kernel_size
    half = ((kd // 2) * dilation, (kh // 2) * dilation, (kw // 2) * dilation)
    if n > 0:
        max_extent = spatial.max(dim=0).values
    else:
        max_extent = torch.zeros(3, dtype=torch.long, device=device)
    ex = int(max_extent[0].item()) + 2 * half[0] + 1
    ey = int(max_extent[1].item()) + 2 * half[1] + 1
    ez = int(max_extent[2].item()) + 2 * half[2] + 1
    shift = torch.tensor(half, dtype=torch.long, device=device)
    shifted = spatial + shift
    codes = (batch * ex + shifted[:, 0]) * ey * ez + shifted[:, 1] * ez + shifted[:, 2]
    rd = (torch.arange(kd, device=device) - kd // 2) * dilation
    rh = (torch.arange(kh, device=device) - kh // 2) * dilation
    rw = (torch.arange(kw, device=device) - kw // 2) * dilation
    taps = torch.stack(torch.meshgrid(rd, rh, rw, indexing="ij"), dim=-1).reshape(-1, 3)
    delta = taps[:, 0] * ey * ez + taps[:, 1] * ez + taps[:, 2]
    neighbor_codes = codes.unsqueeze(0) + delta.unsqueeze(1)
    if n == 0:
        return torch.zeros_like(neighbor_codes), torch.zeros_like(neighbor_codes, dtype=torch.bool)
    sorted_codes, sort_pos = torch.sort(codes)
    pos = torch.searchsorted(sorted_codes, neighbor_codes).clamp(max=n - 1)
    matched = sorted_codes[pos] == neighbor_codes
    row_idx = torch.where(matched, sort_pos[pos], torch.zeros_like(pos))
    return row_idx, matched


def _old_forward(conv, x):
    row_idx, valid = _old_neighbor_map(x.coords, conv.kernel_size, conv.dilation)
    gathered = x.feats[row_idx] * valid.unsqueeze(-1).to(x.feats.dtype)
    weight_flat = conv.weight.reshape(conv.out_channels, -1, conv.in_channels)
    out = torch.einsum("kni,oki->no", gathered, weight_flat)
    if conv.bias is not None:
        out = out + conv.bias
    return out


def _coords(counts, extent, seed):
    gen = torch.Generator().manual_seed(seed)
    rows = []
    for b, n in enumerate(counts):
        c = torch.unique(torch.randint(0, extent, (n, 3), generator=gen), dim=0)
        rows.append(torch.cat([torch.full((c.shape[0], 1), b, dtype=torch.long), c], dim=1))
    return torch.cat(rows, dim=0)


def _shell_coords(extent):
    axis = torch.arange(extent)
    grid = torch.stack(torch.meshgrid(axis, axis, axis, indexing="ij"), dim=-1).reshape(-1, 3)
    centre = (extent - 1) / 2
    radius = (grid.float() - centre).norm(dim=1)
    shell = grid[(radius > extent * 0.35) & (radius < extent * 0.45)]
    return torch.cat([torch.zeros(shell.shape[0], 1, dtype=torch.long), shell], dim=1)


def _conv(cin, cout, kernel_size=3, dilation=1, bias=True, dtype=torch.float32):
    torch.manual_seed(0)
    module = SparseConv3d(cin, cout, kernel_size=kernel_size, dilation=dilation, bias=bias)
    return module.to(dtype)


@pytest.mark.parametrize(
    "kernel_size,dilation",
    [(3, 1), (3, 2), ((3, 1, 3), 1), (1, 1), (5, 1)],
)
def test_the_per_tap_neighbor_map_equals_the_vectorised_one(kernel_size, dilation):
    coords = _coords([150, 90, 40], 9, seed=4)
    triple = SparseConv3d(2, 2, kernel_size=kernel_size).kernel_size
    row_idx, valid = _build_neighbor_map(coords, triple, dilation)
    old_idx, old_valid = _old_neighbor_map(coords, triple, dilation)
    assert torch.equal(valid, old_valid)
    assert torch.equal(row_idx, old_idx)
    assert row_idx.dtype == old_idx.dtype


def test_an_empty_voxel_set_maps_and_convolves_to_nothing():
    coords = torch.zeros((0, 4), dtype=torch.long)
    row_idx, valid = _build_neighbor_map(coords, (3, 3, 3), 1)
    old_idx, old_valid = _old_neighbor_map(coords, (3, 3, 3), 1)
    assert row_idx.shape == old_idx.shape == (27, 0)
    assert valid.shape == old_valid.shape
    out = _conv(4, 6)(SparseTensor(torch.zeros(0, 4), coords))
    assert out.feats.shape == (0, 6)


@pytest.mark.parametrize("chunk_bytes", [None, 1, 4096, 30000])
@pytest.mark.parametrize("bias", [True, False])
@pytest.mark.parametrize("kernel_size,dilation", [(3, 1), (3, 2), ((1, 3, 3), 1)])
def test_fp32_output_equals_the_old_implementation(monkeypatch, chunk_bytes, bias, kernel_size, dilation):
    if chunk_bytes is not None:
        monkeypatch.setattr(conv_module, "_CHUNK_BYTES", chunk_bytes)
    coords = _coords([300, 120], 10, seed=5)
    conv = _conv(12, 7, kernel_size=kernel_size, dilation=dilation, bias=bias)
    feats = torch.randn(coords.shape[0], 12, generator=torch.Generator().manual_seed(6))
    expected = _old_forward(conv, SparseTensor(feats, coords))
    got = conv(SparseTensor(feats, coords)).feats
    assert got.dtype == expected.dtype
    assert torch.allclose(got, expected, atol=1e-5, rtol=1e-5)


def test_fp64_output_equals_the_old_implementation_to_rounding(monkeypatch):
    monkeypatch.setattr(conv_module, "_CHUNK_BYTES", 2048)
    coords = _coords([400], 11, seed=7)
    conv = _conv(9, 5, dtype=torch.float64)
    feats = torch.randn(coords.shape[0], 9, dtype=torch.float64, generator=torch.Generator().manual_seed(8))
    expected = _old_forward(conv, SparseTensor(feats, coords))
    got = conv(SparseTensor(feats, coords)).feats
    assert torch.allclose(got, expected, atol=1e-12, rtol=1e-12)


@pytest.mark.parametrize("dtype", [torch.bfloat16, torch.float16])
@pytest.mark.parametrize("chunk_bytes", [None, 4096])
def test_half_precision_output_matches_the_old_implementation_and_stays_in_dtype(monkeypatch, dtype, chunk_bytes):
    if chunk_bytes is not None:
        monkeypatch.setattr(conv_module, "_CHUNK_BYTES", chunk_bytes)
    coords = _shell_coords(14)
    conv = _conv(32, 16, dtype=dtype)
    feats = torch.randn(coords.shape[0], 32, generator=torch.Generator().manual_seed(9)).to(dtype)
    old = _old_forward(conv, SparseTensor(feats, coords))
    got = conv(SparseTensor(feats, coords)).feats
    exact = _old_forward(copy.deepcopy(conv).double(), SparseTensor(feats.double(), coords))
    assert got.dtype == dtype
    scale = exact.abs().max()
    old_error = (old.double() - exact).abs().max() / scale
    new_error = (got.double() - exact).abs().max() / scale
    assert torch.allclose(got.float(), old.float(), atol=0.05 * float(scale), rtol=0.02)
    assert new_error <= old_error * 1.5 + 1e-3


class _AllocationRecorder(TorchDispatchMode):
    def __init__(self):
        super().__init__()
        self.sizes = []

    def __torch_dispatch__(self, func, types, args=(), kwargs=None):
        kwargs = kwargs or {}
        out = func(*args, **kwargs)
        inputs = {
            t.untyped_storage().data_ptr()
            for t in tree_leaves((args, kwargs))
            if isinstance(t, torch.Tensor) and t.untyped_storage().nbytes() > 0
        }
        for t in tree_leaves(out):
            if not isinstance(t, torch.Tensor) or not t.is_floating_point():
                continue
            storage = t.untyped_storage()
            if storage.nbytes() > 0 and storage.data_ptr() not in inputs:
                self.sizes.append(storage.nbytes())
        return out


def _largest_float_allocations(conv, x):
    conv(x)
    recorder = _AllocationRecorder()
    with torch.no_grad(), recorder:
        out = conv(x)
    return sorted(recorder.sizes, reverse=True), out.feats.untyped_storage().nbytes()


@pytest.mark.parametrize("extent", [18, 30])
def test_no_feature_buffer_scales_with_the_kernel_volume(monkeypatch, extent):
    monkeypatch.setattr(conv_module, "_CHUNK_BYTES", 64 * 1024)
    coords = _shell_coords(extent)
    n, cin, cout = coords.shape[0], 64, 64
    conv = _conv(cin, cout, dtype=torch.bfloat16)
    x = SparseTensor(torch.randn(n, cin).to(torch.bfloat16), coords)

    sizes, out_bytes = _largest_float_allocations(conv, x)

    assert 27 * n * cin * 2 > 30 * 64 * 1024
    assert sizes[0] == out_bytes == n * cout * 2
    assert all(size <= 64 * 1024 for size in sizes[1:])


def test_the_old_implementation_allocates_the_full_kernel_volume_gather():
    coords = _shell_coords(18)
    n, cin = coords.shape[0], 64
    conv = _conv(cin, 64, dtype=torch.bfloat16)
    x = SparseTensor(torch.randn(n, cin).to(torch.bfloat16), coords)
    recorder = _AllocationRecorder()
    with torch.no_grad(), recorder:
        _old_forward(conv, x)
    assert max(recorder.sizes) >= 27 * n * cin * 2
