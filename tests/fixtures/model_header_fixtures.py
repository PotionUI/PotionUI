from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from src.platform.runtime.model_headers.reader import HeaderView, TensorInfo

_GGML_IDS = {"F32": 0, "F16": 1, "Q8_0": 8, "Q4_K": 12, "BF16": 30}


def tensor_specs(shapes: dict[str, tuple[int, ...]], dtype: str = "F16") -> dict[str, tuple[str, tuple[int, ...]]]:
    return {key: (dtype, tuple(shape)) for key, shape in shapes.items()}


def write_safetensors(
    path: Path,
    tensors: dict[str, tuple[str, tuple[int, ...]]],
    metadata: dict[str, str] | None = None,
) -> Path:
    header: dict[str, Any] = {}
    if metadata is not None:
        header["__metadata__"] = metadata
    for name, (dtype, shape) in tensors.items():
        header[name] = {"dtype": dtype, "shape": list(shape), "data_offsets": [0, 0]}
    raw = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(raw)) + raw)
    return path


def raw_safetensors(path: Path, raw: bytes, declared_length: int | None = None) -> Path:
    length = len(raw) if declared_length is None else declared_length
    path.write_bytes(struct.pack("<Q", length) + raw)
    return path


def _gguf_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def gguf_kv(key: str, value: Any) -> bytes:
    body = _gguf_string(key)
    if isinstance(value, bool):
        return body + struct.pack("<IB", 7, int(value))
    if isinstance(value, int):
        return body + struct.pack("<IQ", 10, value)
    if isinstance(value, float):
        return body + struct.pack("<If", 6, value)
    if isinstance(value, str):
        return body + struct.pack("<I", 8) + _gguf_string(value)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        packed = b"".join(_gguf_string(v) for v in value)
        return body + struct.pack("<IIQ", 9, 8, len(value)) + packed
    if isinstance(value, list):
        packed = b"".join(struct.pack("<f", float(v)) for v in value)
        return body + struct.pack("<IIQ", 9, 6, len(value)) + packed
    raise TypeError(type(value))


def gguf_bytes(
    tensors: dict[str, tuple[str, tuple[int, ...]]],
    kv: dict[str, Any] | None = None,
    version: int = 3,
    tensor_count: int | None = None,
    kv_count: int | None = None,
) -> bytes:
    kv = kv or {}
    out = b"GGUF" + struct.pack(
        "<IQQ",
        version,
        len(tensors) if tensor_count is None else tensor_count,
        len(kv) if kv_count is None else kv_count,
    )
    for key, value in kv.items():
        out += gguf_kv(key, value)
    for name, (dtype, shape) in tensors.items():
        dims = tuple(reversed(shape))
        out += _gguf_string(name)
        out += struct.pack("<I", len(dims))
        out += b"".join(struct.pack("<Q", d) for d in dims)
        out += struct.pack("<IQ", _GGML_IDS.get(dtype, 0), 0)
    return out


def write_gguf(
    path: Path,
    tensors: dict[str, tuple[str, tuple[int, ...]]],
    kv: dict[str, Any] | None = None,
    **options: Any,
) -> Path:
    path.write_bytes(gguf_bytes(tensors, kv, **options))
    return path


def make_view(
    shapes: dict[str, tuple[int, ...]],
    dtype: str = "F16",
    fmt: str = "safetensors",
    metadata: dict[str, str] | None = None,
) -> HeaderView:
    return HeaderView(
        fmt,
        {key: TensorInfo(dtype, tuple(shape)) for key, shape in shapes.items()},
        metadata or {},
    )
