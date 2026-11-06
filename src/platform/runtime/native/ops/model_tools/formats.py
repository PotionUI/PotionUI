from __future__ import annotations

import json
import math

import torch

from ...io.quant_formats import (
    NVFP4_BLOCK,
    build_convrot_hadamard,
    convrot_unrotate_weight,
    dequantize_nvfp4,
)
from ...io.safetensors_loader import CONVROT_W4A4, unpack_int4
from ...kernels import quantize_nvfp4_activation
from ..fp8_quant import quantize_tensor_to_fp8_scaled
from .errors import UnsupportedFormatError
from .format import SCALE2_SUFFIX, SCALE_SUFFIX, WEIGHT_SUFFIX

CONVROT_GROUPSIZE = 256
INT8_CODE_MAX = 127
INT4_CODE_MAX = 7

Shape = tuple[int, ...]
LayerSpec = tuple[str, str, Shape]


def _f32(t: torch.Tensor) -> torch.Tensor:
    return t.detach().to(torch.float32)


def _row_scale(w32: torch.Tensor, code_max: int) -> torch.Tensor:
    return (w32.abs().amax(dim=1, keepdim=True) / code_max).clamp_min(torch.finfo(torch.float32).tiny)


def _rotate(w32: torch.Tensor) -> torch.Tensor:
    hadamard = build_convrot_hadamard(CONVROT_GROUPSIZE, w32.device, torch.float32)
    return convrot_unrotate_weight(w32, hadamard, CONVROT_GROUPSIZE)


def pack_int4(codes: torch.Tensor) -> torch.Tensor:
    nibbles = codes.to(torch.int16) & 0x0F
    return (nibbles[..., 0::2] | (nibbles[..., 1::2] << 4)).to(torch.uint8).view(torch.int8)


class QuantFormat:
    key = ""
    label = ""
    name_suffix = ""
    scaling = ""
    text_encoders = False
    quality_note: str | None = None
    descriptor: dict = {}

    @property
    def descriptor_bytes(self) -> bytes:
        return json.dumps(self.descriptor).encode("utf-8")

    def eligible(self, shape: Shape) -> bool:
        return True

    def layer_specs(self, shape: Shape) -> list[LayerSpec]:
        raise NotImplementedError

    def quantize(self, weight: torch.Tensor) -> list[tuple[str, torch.Tensor]]:
        raise NotImplementedError

    def restore(self, parts: dict[str, torch.Tensor], shape: Shape) -> torch.Tensor:
        raise NotImplementedError


class Fp8Format(QuantFormat):
    text_encoders = True
    scaling = "per_tensor_absmax"

    def __init__(self, key: str, label: str, name_suffix: str, dtype: torch.dtype, dtype_name: str, comfy: str):
        self.key = key
        self.label = label
        self.name_suffix = name_suffix
        self.dtype = dtype
        self.dtype_name = dtype_name
        self.descriptor = {"format": comfy, "full_precision_matrix_mult": True}

    def layer_specs(self, shape: Shape) -> list[LayerSpec]:
        return [(WEIGHT_SUFFIX, self.dtype_name, shape), (SCALE_SUFFIX, "F32", ())]

    def quantize(self, weight: torch.Tensor) -> list[tuple[str, torch.Tensor]]:
        q, scale = quantize_tensor_to_fp8_scaled(weight, dtype=self.dtype)
        return [(WEIGHT_SUFFIX, q.contiguous()), (SCALE_SUFFIX, scale.to(torch.float32).reshape(()).contiguous())]

    def restore(self, parts: dict[str, torch.Tensor], shape: Shape) -> torch.Tensor:
        return _f32(parts[WEIGHT_SUFFIX]) * _f32(parts[SCALE_SUFFIX])


class Int8Format(QuantFormat):
    def __init__(self, convrot: bool):
        self.convrot = convrot
        if convrot:
            self.key = "int8_convrot"
            self.label = "int8_convrot"
            self.name_suffix = "-int8-convrot"
            self.scaling = "hadamard256_per_channel_absmax"
            self.descriptor = {"format": "int8_tensorwise", "convrot": True, "convrot_groupsize": CONVROT_GROUPSIZE}
        else:
            self.key = "int8"
            self.label = "int8"
            self.name_suffix = "-int8"
            self.scaling = "per_channel_absmax"
            self.descriptor = {"format": "int8_tensorwise"}

    def eligible(self, shape: Shape) -> bool:
        return not self.convrot or shape[1] % CONVROT_GROUPSIZE == 0

    def layer_specs(self, shape: Shape) -> list[LayerSpec]:
        return [(WEIGHT_SUFFIX, "I8", shape), (SCALE_SUFFIX, "F32", (shape[0], 1))]

    def quantize(self, weight: torch.Tensor) -> list[tuple[str, torch.Tensor]]:
        w32 = _f32(weight)
        if self.convrot:
            w32 = _rotate(w32)
        scale = _row_scale(w32, INT8_CODE_MAX)
        codes = torch.round(w32 / scale).clamp_(-INT8_CODE_MAX, INT8_CODE_MAX).to(torch.int8)
        return [(WEIGHT_SUFFIX, codes.contiguous()), (SCALE_SUFFIX, scale.contiguous())]

    def restore(self, parts: dict[str, torch.Tensor], shape: Shape) -> torch.Tensor:
        w = _f32(parts[WEIGHT_SUFFIX]) * _f32(parts[SCALE_SUFFIX])
        return _rotate(w) if self.convrot else w


class Int4ConvRotFormat(QuantFormat):
    key = "int4_convrot"
    label = "int4_convrot"
    name_suffix = "-int4-convrot"
    scaling = "hadamard256_per_channel_absmax_int4"
    quality_note = "INT4 output quality is unverified until a GPU check"
    descriptor = {"format": CONVROT_W4A4, "convrot_groupsize": CONVROT_GROUPSIZE}

    def eligible(self, shape: Shape) -> bool:
        return shape[1] % CONVROT_GROUPSIZE == 0

    def layer_specs(self, shape: Shape) -> list[LayerSpec]:
        return [(WEIGHT_SUFFIX, "I8", (shape[0], shape[1] // 2)), (SCALE_SUFFIX, "F32", (shape[0],))]

    def quantize(self, weight: torch.Tensor) -> list[tuple[str, torch.Tensor]]:
        w32 = _rotate(_f32(weight))
        scale = _row_scale(w32, INT4_CODE_MAX)
        codes = torch.round(w32 / scale).clamp_(-INT4_CODE_MAX, INT4_CODE_MAX).to(torch.int8)
        return [(WEIGHT_SUFFIX, pack_int4(codes).contiguous()), (SCALE_SUFFIX, scale.reshape(-1).contiguous())]

    def restore(self, parts: dict[str, torch.Tensor], shape: Shape) -> torch.Tensor:
        codes = unpack_int4(parts[WEIGHT_SUFFIX])
        return _rotate(_f32(codes) * _f32(parts[SCALE_SUFFIX]).reshape(-1, 1))


class Nvfp4Format(QuantFormat):
    key = "nvfp4"
    label = "nvfp4"
    name_suffix = "-nvfp4"
    scaling = "nvfp4_block16"
    text_encoders = True
    descriptor = {"format": "nvfp4"}

    def eligible(self, shape: Shape) -> bool:
        return shape[0] % NVFP4_BLOCK == 0 and shape[1] % NVFP4_BLOCK == 0

    def layer_specs(self, shape: Shape) -> list[LayerSpec]:
        out_f, in_f = shape
        block_rows = math.ceil(out_f / 128) * 128
        block_cols = math.ceil(in_f // NVFP4_BLOCK / 4) * 4
        return [
            (WEIGHT_SUFFIX, "U8", (out_f, in_f // 2)),
            (SCALE_SUFFIX, "F8_E4M3", (block_rows, block_cols)),
            (SCALE2_SUFFIX, "F32", ()),
        ]

    def quantize(self, weight: torch.Tensor) -> list[tuple[str, torch.Tensor]]:
        packed, block, tensor_scale = quantize_nvfp4_activation(_f32(weight))
        return [
            (WEIGHT_SUFFIX, packed.contiguous()),
            (SCALE_SUFFIX, block.contiguous()),
            (SCALE2_SUFFIX, tensor_scale.to(torch.float32).reshape(()).contiguous()),
        ]

    def restore(self, parts: dict[str, torch.Tensor], shape: Shape) -> torch.Tensor:
        out_f, in_f = shape
        return dequantize_nvfp4(parts[WEIGHT_SUFFIX], parts[SCALE_SUFFIX], parts[SCALE2_SUFFIX], out_f, in_f)


QUANT_FORMATS: dict[str, QuantFormat] = {
    fmt.key: fmt
    for fmt in (
        Fp8Format("fp8_e4m3", "fp8_e4m3", "-fp8", torch.float8_e4m3fn, "F8_E4M3", "float8_e4m3fn"),
        Fp8Format("fp8_e5m2", "fp8_e5m2", "-fp8-e5m2", torch.float8_e5m2, "F8_E5M2", "float8_e5m2"),
        Int8Format(convrot=False),
        Int8Format(convrot=True),
        Nvfp4Format(),
        Int4ConvRotFormat(),
    )
}
DEFAULT_FORMAT = "fp8_e4m3"


def get_format(key: str | None) -> QuantFormat:
    fmt = QUANT_FORMATS.get(key or DEFAULT_FORMAT)
    if fmt is None:
        raise UnsupportedFormatError(f"unknown quantization format {key!r}; expected one of {', '.join(QUANT_FORMATS)}")
    return fmt


def parse_descriptor(blob: torch.Tensor) -> dict:
    try:
        parsed = json.loads(bytes(blob.tolist()).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise UnsupportedFormatError(f"unreadable quantization descriptor ({exc})") from None
    if not isinstance(parsed, dict):
        raise UnsupportedFormatError("the quantization descriptor is not an object")
    return parsed


def format_for_descriptor(descriptor: dict) -> QuantFormat:
    for fmt in QUANT_FORMATS.values():
        if fmt.descriptor == descriptor:
            return fmt
    raise UnsupportedFormatError(f"no Model Tools format writes the descriptor {descriptor!r}")
