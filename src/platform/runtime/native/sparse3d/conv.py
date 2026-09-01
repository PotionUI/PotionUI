# Derived from: microsoft/TRELLIS.2 (MIT) — trellis2/modules/sparse/conv/
"""Pure-torch submanifold sparse Conv3d.

Reimplements the semantics of upstream's flex_gemm-backed ``SparseConv3d``
(``conv_flex_gemm.py``) without the compiled kernel: output coords equal
input coords (no dilation of the active set), and the feature at each active
site is the sum over the K^3 kernel taps of ``W[tap] @ feats[neighbor]``,
with inactive neighbors contributing zero. Only submanifold conv (stride 1)
is supported, matching the constraint upstream's flex_gemm backend enforces.

``weight`` keeps upstream's stored layout — ``(out_channels, *kernel_size,
in_channels)`` — so a checkpoint trained against the flex_gemm module loads
here unchanged.
"""

from __future__ import annotations

import math
from typing import Tuple, Union

import torch
import torch.nn as nn

from .basic import SparseTensor

__all__ = ["SparseConv3d"]

_KernelSize = Union[int, Tuple[int, int, int]]


def _as_triple(value: _KernelSize) -> Tuple[int, int, int]:
    if isinstance(value, (list, tuple)):
        return (int(value[0]), int(value[1]), int(value[2]))
    return (int(value), int(value), int(value))


def _build_neighbor_map(
    coords: torch.Tensor, kernel_size: Tuple[int, int, int], dilation: int
) -> Tuple[torch.Tensor, torch.Tensor]:
    """Row index (clamped to 0 where absent) and validity mask, both
    ``[K, N]`` with ``K = kd*kh*kw``, for every kernel tap at every active
    site. Coordinates are linearized to a single int64 code per site so
    membership becomes a sort + searchsorted instead of a hash map; the
    per-axis shift (``half``) is sized so an offset neighbor's code never
    wraps into an adjacent axis or batch element."""
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
        row_idx = torch.zeros_like(neighbor_codes)
        valid = torch.zeros_like(neighbor_codes, dtype=torch.bool)
        return row_idx, valid

    sorted_codes, sort_pos = torch.sort(codes)
    pos = torch.searchsorted(sorted_codes, neighbor_codes).clamp(max=n - 1)
    matched = sorted_codes[pos] == neighbor_codes
    row_idx = torch.where(matched, sort_pos[pos], torch.zeros_like(pos))
    return row_idx, matched


class SparseConv3d(nn.Module):
    """Submanifold sparse 3D convolution (stride 1, ``padding=kernel_size//2``
    implicit — the active set is preserved)."""

    def __init__(
        self,
        in_channels: int,
        out_channels: int,
        kernel_size: _KernelSize = 3,
        stride: int = 1,
        dilation: int = 1,
        bias: bool = True,
    ):
        super().__init__()
        assert stride == 1, "SparseConv3d only supports submanifold convolution (stride=1)"
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = _as_triple(kernel_size)
        self.dilation = dilation

        raw_weight = torch.empty((out_channels, in_channels, *self.kernel_size))
        nn.init.kaiming_uniform_(raw_weight, a=math.sqrt(5))
        self.weight = nn.Parameter(raw_weight.permute(0, 2, 3, 4, 1).contiguous())

        if bias:
            self.bias = nn.Parameter(torch.empty(out_channels))
            fan_in, _ = nn.init._calculate_fan_in_and_fan_out(raw_weight)
            if fan_in != 0:
                bound = 1 / math.sqrt(fan_in)
                nn.init.uniform_(self.bias, -bound, bound)
        else:
            self.register_parameter("bias", None)

    def forward(self, x: SparseTensor) -> SparseTensor:
        kd, kh, kw = self.kernel_size
        cache_key = f"sparse_conv3d_neighbor_map_{kd}x{kh}x{kw}_dilation{self.dilation}"
        cached = x.get_spatial_cache(cache_key)
        if cached is None:
            row_idx, valid = _build_neighbor_map(x.coords, self.kernel_size, self.dilation)
            x.register_spatial_cache(cache_key, (row_idx, valid))
        else:
            row_idx, valid = cached

        gathered = x.feats[row_idx] * valid.unsqueeze(-1).to(x.feats.dtype)
        weight_flat = self.weight.reshape(self.out_channels, -1, self.in_channels)
        out = torch.einsum("kni,oki->no", gathered, weight_flat)
        if self.bias is not None:
            out = out + self.bias
        return x.replace(out)
