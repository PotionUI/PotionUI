# Derived from: comfy/ops.py (ComfyUI, GPL-3.0); see vendor/NOTICE.md
from __future__ import annotations

import json
import logging

import torch

logger = logging.getLogger(__name__)

FP8_DTYPES = {torch.float8_e4m3fn, torch.float8_e5m2}
E4M3_MAX = 448.0
_COMFY_QUANT_FP8_FORMATS = frozenset({"float8_e4m3fn", "float8_e5m2"})
COMFY_QUANT_INT8_TENSORWISE = "int8_tensorwise"
_CONVROT_DEFAULT_GROUPSIZE = 256
_CONVROT_HADAMARD_CACHE: dict[tuple[int, str, torch.dtype], torch.Tensor] = {}
_CONVROT_H4 = ((1, 1, 1, -1), (1, 1, -1, 1), (1, -1, 1, 1), (-1, 1, 1, 1))
NVFP4_BLOCK = 16
_F4_MAX = 6.0


def parse_comfy_quant(blob: torch.Tensor | None) -> dict | None:
    if blob is None:
        return None
    try:
        parsed = json.loads(bytes(blob.tolist()).decode("utf-8"))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_convrot_config(layer_conf: dict, layer_name: str = "") -> tuple[bool, int]:
    params_conf = layer_conf.get("params")
    if not isinstance(params_conf, dict):
        params_conf = {}
    convrot = bool(layer_conf.get("convrot", params_conf.get("convrot", False)))
    for conf in (layer_conf, params_conf):
        if "convrot_groupsize" in conf:
            return convrot, int(conf["convrot_groupsize"])
    if convrot:
        logger.info(
            "ConvRot layer '%s' states no group size (%s); using the format default %d",
            layer_name, sorted(layer_conf), _CONVROT_DEFAULT_GROUPSIZE,
        )
    return convrot, _CONVROT_DEFAULT_GROUPSIZE


def describe_quant_layers(sd: dict[str, torch.Tensor]) -> str | None:
    labels: set[str] = set()
    blobs = {bytes(t.tolist()) for k, t in sd.items() if k.endswith(".comfy_quant")}
    for raw in blobs:
        conf = parse_comfy_quant(torch.tensor(list(raw), dtype=torch.uint8))
        if conf is None:
            continue
        label = str(conf.get("format", "unknown"))
        if label == COMFY_QUANT_INT8_TENSORWISE:
            convrot, groupsize = extract_convrot_config(conf)
            if convrot:
                label = f"{label}+convrot{groupsize}"
        labels.add(label)
    if not labels:
        labels = {
            str(t.dtype).removeprefix("torch.")
            for k, t in sd.items() if k.endswith(".weight") and t.dtype in FP8_DTYPES
        }
    return ",".join(sorted(labels)) or None


def reject_ambiguous_int8(
    weight: torch.Tensor | None, scale: torch.Tensor | None, layer_name: str,
) -> None:
    if weight is None or weight.is_floating_point():
        return
    if scale is None or scale.dim() == 0:
        return
    raise ValueError(
        f"int8 layer '{layer_name}' has a per-output-channel weight_scale but no comfy_quant "
        "descriptor: plain and ConvRot-rotated codes are indistinguishable here"
    )


def check_quant_format_supported(layer_conf: dict, layer_name: str) -> None:
    fmt = layer_conf.get("format")
    if fmt in _COMFY_QUANT_FP8_FORMATS or fmt == COMFY_QUANT_INT8_TENSORWISE:
        return
    raise ValueError(f"quant format {fmt!r} is not supported yet (layer '{layer_name}')")


def _convrot_group_exponent(group_size: int) -> int:
    remainder = group_size
    exponent = 0
    while remainder > 1 and remainder % 4 == 0:
        remainder //= 4
        exponent += 1
    if group_size < 4 or remainder != 1:
        raise ValueError(f"ConvRot group size must be a power of 4, got {group_size}")
    return exponent


def build_convrot_hadamard(
    group_size: int, device: "torch.device | str", dtype: torch.dtype,
) -> torch.Tensor:
    cache_key = (group_size, str(device), dtype)
    cached = _CONVROT_HADAMARD_CACHE.get(cache_key)
    if cached is not None:
        return cached
    exponent = _convrot_group_exponent(group_size)
    base = torch.tensor(_CONVROT_H4, dtype=dtype, device=device)
    h = base
    for _ in range(exponent - 1):
        h = torch.kron(h, base)
    h = h / (group_size ** 0.5)
    _CONVROT_HADAMARD_CACHE[cache_key] = h
    return h


def convrot_unrotate_weight(weight: torch.Tensor, hadamard: torch.Tensor, group_size: int) -> torch.Tensor:
    out_f, in_f = weight.shape
    if in_f % group_size != 0:
        raise ValueError(f"in_features {in_f} not divisible by ConvRot group size {group_size}")
    n_groups = in_f // group_size
    grouped = weight.reshape(out_f, n_groups, group_size)
    return torch.matmul(grouped, hadamard).reshape(out_f, in_f)


_E2M1_LUT = torch.tensor(
    [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0,
     0.0, -0.5, -1.0, -1.5, -2.0, -3.0, -4.0, -6.0],
    dtype=torch.float32,
)
_BYTE_ARANGE_I32 = torch.arange(256, dtype=torch.int32)
_E2M1_LUT_HIGH = _E2M1_LUT[(_BYTE_ARANGE_I32 >> 4) & 0x0F]
_E2M1_LUT_LOW = _E2M1_LUT[_BYTE_ARANGE_I32 & 0x0F]
_INV_BLOCKED_CACHE: dict[tuple[int, int], torch.Tensor] = {}


def _to_blocked(m: torch.Tensor) -> torch.Tensor:
    rows, cols = m.shape
    nrb = (rows + 127) // 128
    ncb = (cols + 3) // 4
    padded = m
    if (rows, cols) != (nrb * 128, ncb * 4):
        padded = torch.zeros((nrb * 128, ncb * 4), device=m.device, dtype=m.dtype)
        padded[:rows, :cols] = m
    blocks = padded.view(nrb, 128, ncb, 4).permute(0, 2, 1, 3)
    rearranged = blocks.reshape(-1, 4, 32, 4).transpose(1, 2).reshape(-1, 32, 16)
    return rearranged.reshape(nrb * 128, ncb * 4)


def _inv_blocked_index(rows: int, cols: int) -> torch.Tensor:
    key = (rows, cols)
    inv = _INV_BLOCKED_CACHE.get(key)
    if inv is None:
        idx = _to_blocked(torch.arange(rows * cols).reshape(rows, cols).float()).flatten().long()
        inv = torch.empty(rows * cols, dtype=torch.long)
        inv[idx] = torch.arange(rows * cols)
        _INV_BLOCKED_CACHE[key] = inv
    return inv


def _unblock_scale(block_stored: torch.Tensor, out_features: int, num_blocks: int) -> torch.Tensor:
    ph, pw = block_stored.shape
    inv = _inv_blocked_index(ph, pw).to(block_stored.device)
    natural = block_stored.to(torch.float32).flatten()[inv].reshape(ph, pw)
    return natural[:out_features, :num_blocks]


def dequant_values(packed: torch.Tensor, out_features: int, in_features: int) -> torch.Tensor:
    idx = packed.to(torch.int32)
    device = packed.device
    high = _E2M1_LUT_HIGH.to(device)[idx]
    low = _E2M1_LUT_LOW.to(device)[idx]
    return torch.stack((high, low), dim=-1).view(out_features, in_features)


def scale_values(
    values: torch.Tensor, scale: torch.Tensor, out_features: int, in_features: int, num_blocks: int,
) -> torch.Tensor:
    scaled = values.view(out_features, num_blocks, NVFP4_BLOCK) * scale.view(out_features, num_blocks, 1)
    return scaled.view(out_features, in_features)


def precompute_nvfp4_scale(
    block_scale: torch.Tensor, tensor_scale: torch.Tensor, out_features: int, in_features: int,
) -> torch.Tensor:
    num_blocks = in_features // NVFP4_BLOCK
    block_nat = _unblock_scale(block_scale, out_features, num_blocks)
    return block_nat * tensor_scale.to(torch.float32)


def dequantize_nvfp4(
    packed: torch.Tensor,
    block_scale: torch.Tensor,
    tensor_scale: torch.Tensor,
    out_features: int,
    in_features: int,
) -> torch.Tensor:
    device = packed.device
    values = dequant_values(packed, out_features, in_features)
    num_blocks = in_features // NVFP4_BLOCK
    scale = _unblock_scale(block_scale, out_features, num_blocks).to(device) * tensor_scale.to(torch.float32).to(device)
    return scale_values(values, scale, out_features, in_features, num_blocks)


def nvfp4_activation_amax(x2d: torch.Tensor) -> torch.Tensor:
    return torch.linalg.vector_norm(x2d.detach(), ord=float("inf")).to(torch.float32)


def nvfp4_tensor_scale(tensor_amax: torch.Tensor) -> torch.Tensor:
    return tensor_amax.to(torch.float32).clamp(min=1e-12) / (_F4_MAX * E4M3_MAX)


def quantize_nvfp4_dynamic(
    x2d: torch.Tensor, tensor_amax: torch.Tensor | None = None,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    m, k = x2d.shape
    x = x2d.detach().reshape(m, -1, NVFP4_BLOCK).to(torch.float32, copy=True)
    block_amax = x.abs().amax(-1)
    tensor_scale = nvfp4_tensor_scale(block_amax.amax() if tensor_amax is None else tensor_amax)
    block = torch.clamp(block_amax / _F4_MAX / tensor_scale, max=E4M3_MAX).to(torch.float8_e4m3fn)
    safe_block = block.to(torch.float32).clamp(min=torch.finfo(torch.float32).tiny)
    x.div_((tensor_scale * safe_block).unsqueeze(-1))
    sign = torch.signbit(x).to(torch.uint8)
    x.abs_()
    exp = torch.log2(x).add_(1.1925).floor_().clamp_(0, 3)
    normal = exp > 0
    exp_bits = exp.to(torch.uint8)
    mant = torch.pow(2.0, exp.sub_(1.0), out=exp)
    torch.div(x, mant, out=mant).sub_(1.0).mul_(2.0)
    torch.where(normal, mant, x.mul_(2.0), out=mant)
    del x, normal, exp
    mant = mant.round_().clamp_(0, 1).to(torch.uint8)
    codes = ((sign << 3) | (exp_bits << 1) | mant).reshape(m, k)
    packed = (codes[:, 0::2] << 4) | codes[:, 1::2]
    block_sw = _to_blocked(block.to(torch.float32)).to(torch.float8_e4m3fn)
    return packed, block_sw, tensor_scale.reshape(())
