from __future__ import annotations

import torch
import triton
import triton.language as tl
from torch import Tensor
from torch.library import triton_op, wrap_triton
from triton.language.extra import libdevice

ACT_SWIGLU = 0
ACT_BLOCK = 1024


def _block(d: int) -> tuple[int, int]:
    block = triton.next_power_of_2(d)
    return block, 4 if block <= 2048 else 8 if block <= 8192 else 16


def _direct(kernel):
    return kernel


@triton.jit
def _operand_offset(r, n1, s0, s1, idx_ptr, HAS_INDEX: tl.constexpr):
    i1 = r % n1
    if HAS_INDEX:
        i1 = tl.load(idx_ptr + i1).to(tl.int64)
    return (r // n1) * s0 + i1 * s1


@triton.jit
def _normalize(x, mask, d, eps, NORM: tl.constexpr):
    if NORM == 1:
        mean = tl.sum(x, axis=0) / d
        xc = tl.where(mask, x - mean, 0.0)
        return xc * libdevice.rsqrt(tl.sum(xc * xc, axis=0) / d + eps)
    if NORM == 2:
        return x * libdevice.rsqrt(tl.sum(x * x, axis=0) / d + eps)
    return x


@triton.jit
def _weighted(h, w_ptr, cols, mask, offset, ROUND_TO, HAS_W: tl.constexpr, W_ROUND: tl.constexpr):
    if HAS_W:
        w = tl.load(w_ptr + cols, mask=mask, other=0.0).to(tl.float32) + offset
        if W_ROUND:
            w = w.to(ROUND_TO).to(tl.float32)
        h = h * w
    return h


@triton.jit
def _norm_modulate_kernel(
    x_ptr, out_ptr, w_ptr, sc_ptr, sh_ptr, idx_ptr,
    d, n1, ss0, ss1, ssc, hs0, hs1, hsc,
    eps, weight_offset, scale_offset,
    NORM: tl.constexpr, HAS_W: tl.constexpr, W_ROUND: tl.constexpr,
    HAS_SHIFT: tl.constexpr, HAS_INDEX: tl.constexpr, BLOCK_D: tl.constexpr,
):
    eps = eps.to(tl.float32)
    r = tl.program_id(0).to(tl.int64)
    cols = tl.arange(0, BLOCK_D)
    mask = cols < d
    x = tl.load(x_ptr + r * d + cols, mask=mask, other=0.0).to(tl.float32)
    h = _normalize(x, mask, d, eps, NORM)
    h = _weighted(h, w_ptr, cols, mask, weight_offset.to(tl.float32), x_ptr.dtype.element_ty, HAS_W, W_ROUND)
    s_off = _operand_offset(r, n1, ss0, ss1, idx_ptr, HAS_INDEX)
    h = h * (tl.load(sc_ptr + s_off + cols * ssc, mask=mask, other=0.0).to(tl.float32) + scale_offset.to(tl.float32))
    if HAS_SHIFT:
        h_off = _operand_offset(r, n1, hs0, hs1, idx_ptr, HAS_INDEX)
        h = h + tl.load(sh_ptr + h_off + cols * hsc, mask=mask, other=0.0).to(tl.float32)
    tl.store(out_ptr + r * d + cols, h.to(out_ptr.dtype.element_ty), mask=mask)


@triton.jit
def _gated_residual_kernel(
    x_ptr, y_ptr, g_ptr, out_ptr, w_ptr, idx_ptr,
    d, n1, gs0, gs1, gsc, eps,
    NORM: tl.constexpr, HAS_W: tl.constexpr, W_ROUND: tl.constexpr,
    TANH: tl.constexpr, HAS_INDEX: tl.constexpr, BLOCK_D: tl.constexpr,
):
    eps = eps.to(tl.float32)
    r = tl.program_id(0).to(tl.int64)
    cols = tl.arange(0, BLOCK_D)
    mask = cols < d
    y = tl.load(y_ptr + r * d + cols, mask=mask, other=0.0).to(tl.float32)
    y = _weighted(_normalize(y, mask, d, eps, NORM), w_ptr, cols, mask, 0.0, y_ptr.dtype.element_ty, HAS_W, W_ROUND)
    g = tl.load(g_ptr + _operand_offset(r, n1, gs0, gs1, idx_ptr, HAS_INDEX) + cols * gsc, mask=mask, other=0.0)
    g = g.to(tl.float32)
    if TANH:
        g = libdevice.tanh(g)
    x = tl.load(x_ptr + r * d + cols, mask=mask, other=0.0).to(tl.float32)
    tl.store(out_ptr + r * d + cols, (x + y * g).to(out_ptr.dtype.element_ty), mask=mask)


@triton.jit
def _act_kernel(h_ptr, up_ptr, out_ptr, width, h_row, MODE: tl.constexpr, BLOCK: tl.constexpr):
    r = tl.program_id(0).to(tl.int64)
    cols = tl.program_id(1) * BLOCK + tl.arange(0, BLOCK)
    mask = cols < width
    a = tl.load(h_ptr + r * h_row + cols, mask=mask, other=0.0).to(tl.float32)
    if MODE == 0:
        b = tl.load(h_ptr + r * h_row + width + cols, mask=mask, other=0.0).to(tl.float32)
        out = a / (1.0 + libdevice.exp(-a)) * b
    elif MODE == 1:
        b = tl.load(up_ptr + r * width + cols, mask=mask, other=0.0).to(tl.float32)
        out = a / (1.0 + libdevice.exp(-a)) * b
    elif MODE == 2:
        inner = 0.7978845608028654 * (a + 0.044715 * a * a * a)
        out = 0.5 * a * (1.0 + libdevice.tanh(inner))
    else:
        out = 0.5 * a * (1.0 + libdevice.erf(a * 0.7071067811865476))
    tl.store(out_ptr + r * width + cols, out.to(out_ptr.dtype.element_ty), mask=mask)


def _norm_modulate(
    launch, x: Tensor, scale: Tensor, shift: Tensor | None, weight: Tensor | None, index: Tensor | None,
    layout: list[int], norm: int, eps: float, weight_offset: float, weight_round: bool, scale_offset: float,
    out_dtype: torch.dtype,
) -> Tensor:
    out = torch.empty(x.shape, dtype=out_dtype, device=x.device)
    d = x.shape[-1]
    block, warps = _block(d)
    launch(_norm_modulate_kernel)[(x.numel() // d,)](
        x, out, weight if weight is not None else x, scale, shift if shift is not None else scale,
        index if index is not None else x,
        d, *layout, eps, weight_offset, scale_offset,
        NORM=norm, HAS_W=weight is not None, W_ROUND=weight_round,
        HAS_SHIFT=shift is not None, HAS_INDEX=index is not None, BLOCK_D=block, num_warps=warps,
    )
    return out


@triton_op("potionui::fused_norm_modulate", mutates_args=())
def _norm_modulate_op(
    x: Tensor, scale: Tensor, shift: Tensor | None, weight: Tensor | None, index: Tensor | None,
    layout: list[int], norm: int, eps: float, weight_offset: float, weight_round: bool, scale_offset: float,
    out_dtype: torch.dtype,
) -> Tensor:
    return _norm_modulate(
        wrap_triton, x, scale, shift, weight, index, layout, norm, eps, weight_offset, weight_round, scale_offset,
        out_dtype,
    )


@_norm_modulate_op.register_fake
def _(x, scale, shift, weight, index, layout, norm, eps, weight_offset, weight_round, scale_offset, out_dtype):
    return torch.empty(x.shape, dtype=out_dtype, device=x.device)


def fused_norm_modulate(*args) -> Tensor:
    if torch.compiler.is_compiling():
        return _norm_modulate_op(*args)
    return _norm_modulate(_direct, *args)


def _gated_residual(
    launch, x: Tensor, y: Tensor, gate: Tensor, weight: Tensor | None, index: Tensor | None,
    layout: list[int], norm: int, eps: float, weight_round: bool, tanh: bool, out_dtype: torch.dtype,
) -> Tensor:
    out = torch.empty(x.shape, dtype=out_dtype, device=x.device)
    d = x.shape[-1]
    block, warps = _block(d)
    launch(_gated_residual_kernel)[(x.numel() // d,)](
        x, y, gate, out, weight if weight is not None else x, index if index is not None else x,
        d, *layout, eps,
        NORM=norm, HAS_W=weight is not None, W_ROUND=weight_round,
        TANH=tanh, HAS_INDEX=index is not None, BLOCK_D=block, num_warps=warps,
    )
    return out


@triton_op("potionui::fused_gated_residual", mutates_args=())
def _gated_residual_op(
    x: Tensor, y: Tensor, gate: Tensor, weight: Tensor | None, index: Tensor | None,
    layout: list[int], norm: int, eps: float, weight_round: bool, tanh: bool, out_dtype: torch.dtype,
) -> Tensor:
    return _gated_residual(wrap_triton, x, y, gate, weight, index, layout, norm, eps, weight_round, tanh, out_dtype)


@_gated_residual_op.register_fake
def _(x, y, gate, weight, index, layout, norm, eps, weight_round, tanh, out_dtype):
    return torch.empty(x.shape, dtype=out_dtype, device=x.device)


def fused_gated_residual(*args) -> Tensor:
    if torch.compiler.is_compiling():
        return _gated_residual_op(*args)
    return _gated_residual(_direct, *args)


def _act_gate(launch, h: Tensor, up: Tensor | None, mode: int) -> Tensor:
    width = h.shape[-1] // 2 if mode == ACT_SWIGLU else h.shape[-1]
    out = torch.empty((*h.shape[:-1], width), dtype=h.dtype, device=h.device)
    launch(_act_kernel)[(out.numel() // width, triton.cdiv(width, ACT_BLOCK))](
        h, up if up is not None else h, out, width, h.shape[-1], MODE=mode, BLOCK=ACT_BLOCK, num_warps=4,
    )
    return out


@triton_op("potionui::fused_act_gate", mutates_args=())
def _act_gate_op(h: Tensor, up: Tensor | None, mode: int) -> Tensor:
    return _act_gate(wrap_triton, h, up, mode)


@_act_gate_op.register_fake
def _(h, up, mode):
    width = h.shape[-1] // 2 if mode == ACT_SWIGLU else h.shape[-1]
    return torch.empty((*h.shape[:-1], width), dtype=h.dtype, device=h.device)


def fused_act_gate(*args) -> Tensor:
    if torch.compiler.is_compiling():
        return _act_gate_op(*args)
    return _act_gate(_direct, *args)
