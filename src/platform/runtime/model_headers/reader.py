from __future__ import annotations

import io
import json
import os
import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from typing import Any, BinaryIO

from .signatures import DENOISER_PREFIXES, detect_prefix

MAX_SAFETENSORS_HEADER = 16 * 1024 * 1024
MAX_SAFETENSORS_ENTRIES = 200_000
MAX_GGUF_TENSORS = 100_000
MAX_GGUF_KV = 10_000
MAX_GGUF_KEY_BYTES = 64 * 1024
MAX_GGUF_STRING_BYTES = 1024 * 1024
MAX_GGUF_CONSUMED = 32 * 1024 * 1024
MAX_GGUF_DIMS = 8
READ_BUFFER_BYTES = 1024 * 1024
GGUF_MAGIC = b"GGUF"

_GGUF_STRING = 8
_GGUF_ARRAY = 9
_GGUF_BOOL = 7
_GGUF_SCALARS = {
    0: ("<B", 1),
    1: ("<b", 1),
    2: ("<H", 2),
    3: ("<h", 2),
    4: ("<I", 4),
    5: ("<i", 4),
    6: ("<f", 4),
    7: ("<B", 1),
    10: ("<Q", 8),
    11: ("<q", 8),
    12: ("<d", 8),
}
_GGML_TYPES = {
    0: "F32",
    1: "F16",
    2: "Q4_0",
    3: "Q4_1",
    6: "Q5_0",
    7: "Q5_1",
    8: "Q8_0",
    9: "Q8_1",
    10: "Q2_K",
    11: "Q3_K",
    12: "Q4_K",
    13: "Q5_K",
    14: "Q6_K",
    15: "Q8_K",
    24: "I8",
    25: "I16",
    26: "I32",
    27: "I64",
    28: "F64",
    30: "BF16",
}


class HeaderStatus(StrEnum):
    OK = "ok"
    INVALID = "invalid"
    IO_ERROR = "io_error"


@dataclass(frozen=True)
class TensorInfo:
    dtype: str
    shape: tuple[int, ...]


@dataclass(frozen=True, eq=False)
class HeaderView:
    format: str
    tensors: Mapping[str, TensorInfo]
    metadata: Mapping[str, str] = field(default_factory=dict)
    file_size: int = 0
    filename: str = ""

    @cached_property
    def keys(self) -> frozenset[str]:
        return frozenset(self.tensors)

    @cached_property
    def denoiser_prefix(self) -> str | None:
        return detect_prefix(self.keys, DENOISER_PREFIXES)

    @cached_property
    def denoiser_keys(self) -> frozenset[str]:
        prefix = self.denoiser_prefix
        if not prefix:
            return self.keys
        return frozenset(k[len(prefix):] for k in self.keys if k.startswith(prefix))

    def shape(self, key: str) -> tuple[int, ...] | None:
        info = self.tensors.get(key)
        return info.shape if info is not None else None

    def dtype(self, key: str) -> str | None:
        info = self.tensors.get(key)
        return info.dtype if info is not None else None

    def denoiser_shape(self, key: str) -> tuple[int, ...] | None:
        return self.shape((self.denoiser_prefix or "") + key)


@dataclass(frozen=True)
class HeaderResult:
    status: HeaderStatus
    view: HeaderView | None = None
    error: str | None = None


class _Invalid(Exception):
    pass


class _Truncated(Exception):
    pass


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    data = stream.read(size)
    if len(data) != size:
        raise _Truncated()
    return data


def _load_safetensors_json(stream: BinaryIO, file_size: int) -> dict[str, Any]:
    (length,) = struct.unpack("<Q", _read_exact(stream, 8))
    if length == 0 or length > MAX_SAFETENSORS_HEADER or 8 + length > file_size:
        raise _Invalid(f"unusable safetensors header length {length}")
    raw = _read_exact(stream, length)
    try:
        header = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise _Invalid(f"header is not valid JSON: {exc}") from exc
    if not isinstance(header, dict):
        raise _Invalid("header is not a JSON object")
    if len(header) > MAX_SAFETENSORS_ENTRIES:
        raise _Invalid("header has too many entries")
    return header


def _shape_tuple(value: Any) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise _Invalid("tensor shape is not a list")
    for dim in value:
        if isinstance(dim, bool) or not isinstance(dim, int) or dim < 0:
            raise _Invalid("tensor shape has a non-integer dimension")
    return tuple(value)


def _parse_safetensors(stream: BinaryIO, file_size: int, filename: str) -> HeaderView:
    header = _load_safetensors_json(stream, file_size)
    tensors: dict[str, TensorInfo] = {}
    metadata: dict[str, str] = {}
    for name, info in header.items():
        if name == "__metadata__":
            if isinstance(info, dict):
                metadata = {k: v for k, v in info.items() if isinstance(v, str)}
            continue
        if not isinstance(info, dict) or not isinstance(info.get("dtype"), str):
            raise _Invalid(f"tensor '{name}' has no dtype")
        tensors[name] = TensorInfo(info["dtype"], _shape_tuple(info.get("shape")))
    return HeaderView("safetensors", tensors, metadata, file_size, filename)


class _Cursor:
    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._consumed = 0

    def _spend(self, size: int) -> None:
        self._consumed += size
        if self._consumed > MAX_GGUF_CONSUMED:
            raise _Invalid("gguf header exceeds the read budget")

    def read(self, size: int) -> bytes:
        self._spend(size)
        return _read_exact(self._stream, size)

    def skip(self, size: int) -> None:
        self._spend(size)
        self._stream.seek(size, io.SEEK_CUR)

    def unpack(self, fmt: str) -> Any:
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self, limit: int) -> str:
        size = self.unpack("<Q")
        if size > limit:
            raise _Invalid("gguf string exceeds its size limit")
        try:
            return self.read(size).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _Invalid("gguf string is not utf-8") from exc


def _skip_array(cur: _Cursor) -> None:
    element = cur.unpack("<I")
    count = cur.unpack("<Q")
    scalar = _GGUF_SCALARS.get(element)
    if scalar is not None:
        cur.skip(count * scalar[1])
        return
    if element != _GGUF_STRING:
        raise _Invalid(f"unsupported gguf array element type {element}")
    for _ in range(count):
        cur.skip(_string_length(cur))


def _string_length(cur: _Cursor) -> int:
    size = cur.unpack("<Q")
    if size > MAX_GGUF_STRING_BYTES:
        raise _Invalid("gguf string exceeds its size limit")
    return size


def _read_kv_value(cur: _Cursor, value_type: int) -> str | None:
    scalar = _GGUF_SCALARS.get(value_type)
    if scalar is not None:
        value = cur.unpack(scalar[0])
        if value_type == _GGUF_BOOL:
            return "true" if value else "false"
        return str(value)
    if value_type == _GGUF_STRING:
        return cur.string(MAX_GGUF_STRING_BYTES)
    if value_type == _GGUF_ARRAY:
        _skip_array(cur)
        return None
    raise _Invalid(f"unsupported gguf value type {value_type}")


def _parse_gguf(stream: BinaryIO, file_size: int, filename: str) -> HeaderView:
    cur = _Cursor(stream)
    if cur.read(4) != GGUF_MAGIC:
        raise _Invalid("missing gguf magic")
    version = cur.unpack("<I")
    if version not in (2, 3):
        raise _Invalid(f"unsupported gguf version {version}")
    tensor_count = cur.unpack("<Q")
    kv_count = cur.unpack("<Q")
    if tensor_count > MAX_GGUF_TENSORS or kv_count > MAX_GGUF_KV:
        raise _Invalid("gguf declares too many entries")

    metadata: dict[str, str] = {}
    for _ in range(kv_count):
        key = cur.string(MAX_GGUF_KEY_BYTES)
        value = _read_kv_value(cur, cur.unpack("<I"))
        if value is not None:
            metadata[key] = value

    tensors: dict[str, TensorInfo] = {}
    for _ in range(tensor_count):
        name = cur.string(MAX_GGUF_KEY_BYTES)
        n_dims = cur.unpack("<I")
        if n_dims > MAX_GGUF_DIMS:
            raise _Invalid("gguf tensor has too many dimensions")
        dims = tuple(cur.unpack("<Q") for _ in range(n_dims))
        ggml_type = cur.unpack("<I")
        cur.read(8)
        tensors[name] = TensorInfo(_GGML_TYPES.get(ggml_type, f"GGML_{ggml_type}"), tuple(reversed(dims)))
    return HeaderView("gguf", tensors, metadata, file_size, filename)


def _parse_stream(stream: BinaryIO, file_size: int, filename: str) -> HeaderView:
    magic = _read_exact(stream, 4)
    stream.seek(0)
    if magic == GGUF_MAGIC:
        return _parse_gguf(stream, file_size, filename)
    return _parse_safetensors(stream, file_size, filename)


def read_header(path: str | os.PathLike[str], *, prefix: bytes | None = None) -> HeaderResult:
    filename = Path(path).name
    try:
        file_size = os.stat(path).st_size
    except OSError as exc:
        return HeaderResult(HeaderStatus.IO_ERROR, error=str(exc))

    if prefix:
        try:
            return HeaderResult(HeaderStatus.OK, _parse_stream(io.BytesIO(prefix), file_size, filename))
        except _Truncated:
            pass
        except _Invalid as exc:
            return HeaderResult(HeaderStatus.INVALID, error=str(exc))

    try:
        with open(path, "rb", buffering=READ_BUFFER_BYTES) as handle:
            view = _parse_stream(handle, file_size, filename)
    except _Truncated:
        return HeaderResult(HeaderStatus.INVALID, error="file ends inside its header")
    except _Invalid as exc:
        return HeaderResult(HeaderStatus.INVALID, error=str(exc))
    except OSError as exc:
        return HeaderResult(HeaderStatus.IO_ERROR, error=str(exc))
    return HeaderResult(HeaderStatus.OK, view)


def read_safetensors_header(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            file_size = os.fstat(handle.fileno()).st_size
            return _load_safetensors_json(handle, file_size)
    except (_Invalid, _Truncated) as exc:
        raise ValueError(str(exc) or "file ends inside its header") from exc
