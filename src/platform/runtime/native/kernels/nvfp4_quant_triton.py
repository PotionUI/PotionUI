from __future__ import annotations

import torch
import triton
import triton.language as tl
from triton.language.extra import libdevice

from ..io.quant_formats import E4M3_MAX, NVFP4_AMAX_FLOOR, NVFP4_BLOCK, NVFP4_SCALE_MAX

BLOCK_M = 16
BLOCK_K = 128
NUM_WARPS = 4
_F32_TINY = torch.finfo(torch.float32).tiny
_INV_F4_MAX = (torch.tensor(1.0, dtype=torch.float32) / torch.tensor(6.0, dtype=torch.float32)).item()
_INV_SCALE_MAX = (torch.tensor(1.0, dtype=torch.float32) / torch.tensor(NVFP4_SCALE_MAX, dtype=torch.float32)).item()


@triton.jit
def _nan_max(a, b):
    return tl.where((a > b) | (a != a), a, b)


@triton.jit
def _nvfp4_quant_kernel(
    x_ptr, packed_ptr, scale_ptr, tensor_scale_ptr, amax_ptr,
    m, k, stride_m, scale_col_blocks,
    inv_f4_max, e4m3_max, f32_tiny, amax_floor, inv_scale_max,
    BLOCK_M: tl.constexpr, BLOCK_K: tl.constexpr,
):
    NB: tl.constexpr = BLOCK_K // 16
    amax = tl.load(amax_ptr).to(tl.float32)
    tensor_scale = tl.where(amax < amax_floor, amax_floor, amax) * inv_scale_max
    tl.store(tensor_scale_ptr, tensor_scale, mask=(tl.program_id(0) == 0) & (tl.program_id(1) == 0))
    rows = tl.program_id(0) * BLOCK_M + tl.arange(0, BLOCK_M)
    cols = tl.program_id(1) * BLOCK_K + tl.arange(0, BLOCK_K)
    row_ok = rows < m
    x = tl.load(
        x_ptr + rows[:, None].to(tl.int64) * stride_m + cols[None, :],
        mask=row_ok[:, None] & (cols[None, :] < k), other=0.0,
    ).to(tl.float32)
    xb = tl.reshape(x, (BLOCK_M, NB, 16))
    block_amax = tl.reduce(tl.abs(xb), 2, _nan_max)
    ratio = tl.div_rn(block_amax * inv_f4_max, tensor_scale)
    ratio = tl.where(ratio > e4m3_max, e4m3_max, ratio)
    block = ratio.to(tl.float8e4nv, fp_downcast_rounding="rtne")
    block_f = block.to(tl.float32)
    safe = tl.where(block_f < f32_tiny, f32_tiny, block_f)
    y = tl.div_rn(xb, (tensor_scale * safe)[:, :, None])
    sign = (y.to(tl.int32, bitcast=True) >> 31) & 1
    a = tl.abs(y)
    e = libdevice.floor(libdevice.log2(a) + 1.1925)
    e = tl.where(e < 0.0, 0.0, e)
    e = tl.where(e > 3.0, 3.0, e)
    e_bits = tl.where(e == e, e, 0.0).to(tl.int32)
    step = tl.where(e == 1.0, 1.0, tl.where(e == 2.0, 2.0, 4.0))
    mant = tl.where(e > 0.0, (tl.div_rn(a, step) - 1.0) * 2.0, a * 2.0)
    mant = libdevice.rint(mant)
    mant = tl.where(mant < 0.0, 0.0, mant)
    mant = tl.where(mant > 1.0, 1.0, mant)
    m_bits = tl.where(mant == mant, mant, 0.0).to(tl.int32)
    codes = (sign << 3) | (e_bits << 1) | m_bits
    high, low = tl.split(tl.reshape(codes, (BLOCK_M, BLOCK_K // 2, 2)))
    packed = ((high << 4) | low).to(tl.uint8)
    half_cols = tl.program_id(1) * (BLOCK_K // 2) + tl.arange(0, BLOCK_K // 2)
    tl.store(
        packed_ptr + rows[:, None].to(tl.int64) * (k // 2) + half_cols[None, :], packed,
        mask=row_ok[:, None] & (half_cols[None, :] < k // 2),
    )
    bcols = tl.program_id(1) * NB + tl.arange(0, NB)
    r = rows[:, None].to(tl.int64)
    c = bcols[None, :]
    offset = ((r // 128) * scale_col_blocks + c // 4) * 512 + (r % 32) * 16 + ((r % 128) // 32) * 4 + c % 4
    code = tl.where(row_ok[:, None] & (c < k // 16), block.to(tl.uint8, bitcast=True), 0)
    tl.store(scale_ptr + offset, code, mask=c < scale_col_blocks * 4)


def quantize(x2d: torch.Tensor, tensor_amax: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    m, k = x2d.shape
    padded_rows = (m + 127) // 128 * 128
    col_blocks = (k // NVFP4_BLOCK + 3) // 4
    device = x2d.device
    packed = torch.empty((m, k // 2), dtype=torch.uint8, device=device)
    block_sw = torch.empty((padded_rows, col_blocks * 4), dtype=torch.float8_e4m3fn, device=device)
    tensor_scale = torch.empty((), dtype=torch.float32, device=device)
    grid = (padded_rows // BLOCK_M, triton.cdiv(k, BLOCK_K))
    _nvfp4_quant_kernel[grid](
        x2d, packed, block_sw.view(torch.uint8), tensor_scale, tensor_amax,
        m, k, x2d.stride(0), col_blocks,
        _INV_F4_MAX, E4M3_MAX, _F32_TINY, NVFP4_AMAX_FLOOR, _INV_SCALE_MAX,
        BLOCK_M=BLOCK_M, BLOCK_K=BLOCK_K, num_warps=NUM_WARPS, enable_fp_fusion=False,
    )
    return packed, block_sw, tensor_scale
