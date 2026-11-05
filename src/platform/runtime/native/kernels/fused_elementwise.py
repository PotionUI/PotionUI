from __future__ import annotations

import logging
from collections.abc import Sequence

import torch
import torch.nn.functional as F
from torch import Tensor, nn

from src.platform.runtime.cuda_allocator import is_driver_oom
from src.platform.settings.runtime_flags import runtime_flag

from . import casting
from .hardware import VENDOR_CUDA, capabilities

logger = logging.getLogger(__name__)

NORM_KINDS = ("layer", "rms", "rms_fp32")
GATE_FNS = ("tanh",)
ACT_MODES = ("swiglu", "swiglu_split", "gelu_tanh", "gelu")
FUSED_ELEMENTWISE_FLAG = "native_fused_elementwise"
FUSED_DTYPES = (torch.bfloat16, torch.float16)
FUSED_OPERAND_DTYPES = (torch.bfloat16, torch.float16, torch.float32)
FUSED_MAX_NORM_WIDTH = 16384
SPAN_INDEX_CACHE_SIZE = 64

_NORM_CODES = {None: 0, "layer": 1, "rms": 2, "rms_fp32": 2}
_ACT_CODES = {"swiglu": 0, "swiglu_split": 1, "gelu_tanh": 2, "gelu": 3}
_disabled_reason: str | None = None
_kernels = None
_span_indices: dict[tuple, Tensor] = {}


def fused_elementwise_disabled_reason() -> str | None:
    return _disabled_reason


def reset_fused_elementwise_state() -> None:
    global _disabled_reason, _kernels
    _disabled_reason = None
    _kernels = None
    _span_indices.clear()


def _disable(reason: str) -> None:
    global _disabled_reason
    _disabled_reason = reason
    logger.warning(
        "fused elementwise kernels: the Triton path is off for the rest of this process (%s); "
        "the unfused code takes over", reason,
    )


def _triton_kernels():
    global _kernels
    if _kernels is None:
        from . import fused_elementwise_triton

        _kernels = fused_elementwise_triton
    return _kernels


def _fused_eligible(x: Tensor) -> bool:
    if _disabled_reason is not None or not runtime_flag(FUSED_ELEMENTWISE_FLAG):
        return False
    if x.dtype not in FUSED_DTYPES or x.dim() < 2 or x.numel() == 0 or not x.is_contiguous():
        return False
    caps = capabilities()
    if not caps.triton:
        return False
    device = caps.device(x.device)
    return device.vendor == VENDOR_CUDA and device.at_least(8, 0)


def _fused_operand(t: object, x: Tensor, indexed: bool) -> tuple[int, ...] | None:
    if not isinstance(t, Tensor) or t.device != x.device or t.dtype not in FUSED_OPERAND_DTYPES:
        return None
    if t.dim() > x.dim() or (indexed and t.dim() < 2):
        return None
    pad = x.dim() - t.dim()
    strides = [0] * pad
    for i, (n, s) in enumerate(zip(t.shape, t.stride())):
        target = x.shape[pad + i]
        if n == target or (indexed and pad + i == x.dim() - 2):
            strides.append(s)
        elif n == 1:
            strides.append(0)
        else:
            return None
    return tuple(strides)


def _group_stride(sizes: Sequence[int], strides: Sequence[int]) -> int | None:
    dims = [(n, s) for n, s in zip(sizes, strides) if n != 1]
    for (_, outer), (n, inner) in zip(dims, dims[1:]):
        if outer != inner * n:
            return None
    return dims[-1][1] if dims else 0


def _fused_layout(x: Tensor, operands: Sequence[Sequence[int]], indexed: bool) -> list[int] | None:
    lead = list(x.shape[:-1])
    splits = [len(lead) - 1] if indexed else range(len(lead) + 1)
    for p in splits:
        layout = [1]
        for n in lead[p:]:
            layout[0] *= n
        for strides in operands:
            outer = _group_stride(lead[:p], strides[:p])
            inner = strides[-2] if indexed else _group_stride(lead[p:], strides[p:-1])
            if outer is None or inner is None:
                break
            layout += [outer, inner, strides[-1]]
        else:
            return layout
    return None


def _fused_index(index: Tensor, x: Tensor) -> Tensor | None:
    if index.device != x.device or index.dtype not in (torch.int32, torch.int64) or index.dim() != 1:
        return None
    return index if index.shape[0] == x.shape[-2] and index.is_contiguous() else None


def _span_index(spans: Sequence[int], x: Tensor) -> Tensor | None:
    lengths = tuple(int(n) for n in spans)
    if x.dim() != 3 or any(n < 0 for n in lengths) or sum(lengths) != x.shape[1]:
        return None
    key = (lengths, x.device)
    index = _span_indices.get(key)
    if index is None:
        if len(_span_indices) >= SPAN_INDEX_CACHE_SIZE:
            _span_indices.clear()
        index = torch.repeat_interleave(torch.arange(len(lengths)), torch.tensor(lengths)).to(x.device)
        _span_indices[key] = index
    return index


def _span_table(tables: object, x: Tensor, count: int) -> Tensor | None:
    if not isinstance(tables, (list, tuple)) or len(tables) != count:
        return None
    parts = []
    for t in tables:
        if not isinstance(t, Tensor) or t.dim() != 3 or t.shape[1] != 1 or t.device != x.device:
            return None
        if t.shape[-1] not in (1, x.shape[-1]) or t.shape[0] not in (1, x.shape[0]):
            return None
        parts.append(t.expand(x.shape[0], 1, x.shape[-1]))
    return torch.cat(parts, dim=1)


def _fused_weight(weight: Tensor | None, x: Tensor) -> tuple[bool, Tensor | None]:
    if weight is None:
        return True, None
    ok = (weight.device == x.device and weight.dim() == 1 and weight.shape[0] == x.shape[-1]
          and weight.dtype in FUSED_OPERAND_DTYPES and weight.is_contiguous())
    return ok, weight if ok else None


def _normed_dtype(x: Tensor, norm: str | None, weight: Tensor | None, weight_cast: bool) -> torch.dtype:
    if norm == "rms_fp32" and weight is not None and not weight_cast:
        return torch.promote_types(x.dtype, weight.dtype)
    return x.dtype


def _fused_eps(eps: float | None, x: Tensor) -> float:
    return torch.finfo(x.dtype).eps if eps is None else float(eps)


def _run_fused(name: str, *args) -> Tensor | None:
    try:
        return getattr(_triton_kernels(), name)(*args)
    except Exception as exc:
        if isinstance(exc, torch.cuda.OutOfMemoryError) or is_driver_oom(exc):
            raise
        _disable(f"{type(exc).__name__}: {exc}"[:300])
        return None


def _fused_norm_modulate(
    x: Tensor, scale, shift, norm, eps, weight, weight_offset, weight_cast, scale_offset, index, spans,
) -> Tensor | None:
    if not _fused_eligible(x) or norm not in _NORM_CODES:
        return None
    if norm is not None and x.shape[-1] > FUSED_MAX_NORM_WIDTH:
        return None
    if spans is not None:
        if index is not None:
            return None
        index = _span_index(spans, x)
        if index is None:
            return None
        scale = _span_table(scale, x, len(spans))
        if shift is not None:
            shift = _span_table(shift, x, len(spans))
            if shift is None:
                return None
    elif index is not None:
        index = _fused_index(index, x)
        if index is None:
            return None
    indexed = index is not None
    sc = _fused_operand(scale, x, indexed)
    sh = None if shift is None else _fused_operand(shift, x, indexed)
    ok, w = _fused_weight(weight, x)
    if sc is None or (shift is not None and sh is None) or not ok:
        return None
    layout = _fused_layout(x, [sc, sc if sh is None else sh], indexed)
    if layout is None:
        return None
    out_dtype = torch.promote_types(_normed_dtype(x, norm, w, weight_cast), scale.dtype)
    if shift is not None:
        out_dtype = torch.promote_types(out_dtype, shift.dtype)
    return _run_fused(
        "fused_norm_modulate", x, scale, shift, w, index, layout, _NORM_CODES[norm], _fused_eps(eps, x),
        float(weight_offset), bool(weight_cast and w is not None), float(scale_offset), out_dtype,
    )


def _fused_gated_residual(x: Tensor, y: Tensor, gate, gate_fn, norm, eps, weight, weight_cast, index, spans):
    if not _fused_eligible(x) or norm not in _NORM_CODES or gate_fn not in (None, "tanh"):
        return None
    if not isinstance(y, Tensor) or y.shape != x.shape or y.device != x.device or not y.is_contiguous():
        return None
    if y.dtype not in FUSED_DTYPES or (norm is not None and x.shape[-1] > FUSED_MAX_NORM_WIDTH):
        return None
    if spans is not None:
        if index is not None:
            return None
        index = _span_index(spans, x)
        if index is None:
            return None
        gate = _span_table(gate, x, len(spans))
    elif index is not None:
        index = _fused_index(index, x)
        if index is None:
            return None
    g = _fused_operand(gate, x, index is not None)
    ok, w = _fused_weight(weight, x)
    if g is None or not ok:
        return None
    layout = _fused_layout(x, [g], index is not None)
    if layout is None:
        return None
    out_dtype = torch.promote_types(torch.promote_types(x.dtype, _normed_dtype(y, norm, w, weight_cast)), gate.dtype)
    return _run_fused(
        "fused_gated_residual", x, y, gate, w, index, layout, _NORM_CODES[norm], _fused_eps(eps, y),
        bool(weight_cast and w is not None), gate_fn == "tanh", out_dtype,
    )


def _fused_act_gate(h: Tensor, mode: str, up: Tensor | None) -> Tensor | None:
    if not _fused_eligible(h) or mode not in _ACT_CODES or not h.is_contiguous():
        return None
    if mode == "swiglu" and h.shape[-1] % 2:
        return None
    if mode == "swiglu_split":
        if not isinstance(up, Tensor) or up.shape != h.shape or up.dtype != h.dtype or not up.is_contiguous():
            return None
        if up.device != h.device:
            return None
    else:
        up = None
    return _run_fused("fused_act_gate", h, up, _ACT_CODES[mode])


def _norm_weight(x: Tensor, weight: Tensor | None, offset: float, cast: bool) -> Tensor | None:
    if weight is None:
        return None
    if offset:
        weight = weight + offset
    if cast:
        weight = casting.cast_to(weight, x.dtype, x.device)
    return weight


def _normalize(
    x: Tensor,
    norm: str | None,
    eps: float | None,
    weight: Tensor | None,
    weight_offset: float,
    weight_cast: bool,
) -> Tensor:
    if norm is None:
        return x
    w = _norm_weight(x, weight, weight_offset, weight_cast)
    if norm == "layer":
        return F.layer_norm(x, (x.shape[-1],), w, None, eps)
    if norm == "rms":
        return F.rms_norm(x, (x.shape[-1],), w, eps)
    if norm == "rms_fp32":
        xf = x.float()
        out = (xf * torch.rsqrt(xf.square().mean(-1, keepdim=True) + eps)).to(x.dtype)
        return out if w is None else out * w
    raise ValueError(f"unknown norm kind {norm!r}; expected one of {NORM_KINDS}")


def _rows(table: Tensor | None, index: Tensor | None) -> Tensor | None:
    if table is None or index is None:
        return table
    return table.index_select(-2, index)


def _offset_scale(scale: Tensor, offset: float) -> Tensor:
    return offset + scale if offset else scale


def _modulate(h: Tensor, scale: Tensor, shift: Tensor | None, addcmul: bool, inplace: bool) -> Tensor:
    if shift is None:
        return h.mul_(scale) if inplace else h * scale
    if inplace:
        return h.mul_(scale).add_(shift)
    if addcmul:
        return torch.addcmul(shift, h, scale)
    return h * scale + shift


def norm_modulate(
    x: Tensor,
    *,
    scale: Tensor | Sequence[Tensor],
    shift: Tensor | Sequence[Tensor] | None = None,
    norm: str | None = None,
    eps: float | None = 1e-6,
    weight: Tensor | None = None,
    weight_offset: float = 0.0,
    weight_cast: bool = True,
    scale_offset: float = 1.0,
    index: Tensor | None = None,
    spans: Sequence[int] | None = None,
    addcmul: bool = False,
    inplace: bool = False,
) -> Tensor:
    if not inplace:
        fused = _fused_norm_modulate(
            x, scale, shift, norm, eps, weight, weight_offset, weight_cast, scale_offset, index, spans,
        )
        if fused is not None:
            return fused
    h = _normalize(x, norm, eps, weight, weight_offset, weight_cast)
    if spans is None:
        return _modulate(
            h,
            _rows(_offset_scale(scale, scale_offset), index),
            _rows(shift, index),
            addcmul,
            inplace,
        )
    shifts = shift if shift is not None else (None,) * len(spans)
    parts = []
    start = 0
    for length, sc, sh in zip(spans, scale, shifts, strict=True):
        parts.append(_modulate(h[:, start : start + length], _offset_scale(sc, scale_offset), sh, addcmul, False))
        start += length
    return parts[0] if len(parts) == 1 else torch.cat(parts, dim=1)


def _gate(gate: Tensor, gate_fn: str | None) -> Tensor:
    if gate_fn is None:
        return gate
    if gate_fn == "tanh":
        return gate.tanh()
    raise ValueError(f"unknown gate function {gate_fn!r}; expected one of {GATE_FNS}")


def gated_residual(
    x: Tensor,
    y: Tensor,
    gate: Tensor | Sequence[Tensor],
    *,
    gate_fn: str | None = None,
    norm: str | None = None,
    eps: float | None = 1e-6,
    weight: Tensor | None = None,
    weight_cast: bool = True,
    index: Tensor | None = None,
    spans: Sequence[int] | None = None,
    addcmul: bool = False,
) -> Tensor:
    fused = _fused_gated_residual(x, y, gate, gate_fn, norm, eps, weight, weight_cast, index, spans)
    if fused is not None:
        return fused
    y = _normalize(y, norm, eps, weight, 0.0, weight_cast)
    if spans is None:
        g = _rows(_gate(gate, gate_fn), index)
        return torch.addcmul(x, y, g) if addcmul else x + y * g
    parts = []
    start = 0
    for length, g in zip(spans, gate, strict=True):
        parts.append(y[:, start : start + length] * _gate(g, gate_fn))
        start += length
    return x + (parts[0] if len(parts) == 1 else torch.cat(parts, dim=1))


def norm_args(module: nn.Module) -> dict:
    if isinstance(module, nn.RMSNorm):
        kind = "rms"
    elif isinstance(module, nn.LayerNorm) and module.bias is None:
        kind = "layer"
    else:
        raise TypeError(f"norm_args takes an RMSNorm or a bias-free LayerNorm, got {type(module).__name__}")
    return {
        "norm": kind,
        "eps": module.eps,
        "weight": module.weight,
        "weight_cast": getattr(module, "comfy_cast_weights", False),
    }


def act_gate(h: Tensor, *, mode: str, up: Tensor | None = None) -> Tensor:
    fused = _fused_act_gate(h, mode, up)
    if fused is not None:
        return fused
    if mode == "swiglu":
        gate, value = h.chunk(2, dim=-1)
        return F.silu(gate) * value
    if mode == "swiglu_split":
        return F.silu(h) * up
    if mode == "gelu_tanh":
        return F.gelu(h, approximate="tanh")
    if mode == "gelu":
        return F.gelu(h)
    raise ValueError(f"unknown activation mode {mode!r}; expected one of {ACT_MODES}")


class ActGate(nn.Module):
    def __init__(self, mode: str) -> None:
        super().__init__()
        if mode not in ACT_MODES or mode == "swiglu_split":
            raise ValueError(f"ActGate takes one input; mode must be one of {ACT_MODES[:1] + ACT_MODES[2:]}")
        self.mode = mode

    def forward(self, x: Tensor) -> Tensor:
        return act_gate(x, mode=self.mode)

    def extra_repr(self) -> str:
        return f"mode={self.mode!r}"
