from __future__ import annotations

import builtins
import json
import struct

import pytest

from src.platform.runtime.model_headers import reader
from src.platform.runtime.model_headers.reader import (
    HeaderStatus,
    read_header,
    read_safetensors_header,
)
from tests.fixtures.model_header_fixtures import (
    gguf_bytes,
    raw_safetensors,
    write_gguf,
    write_safetensors,
)

TENSORS = {"a.weight": ("F16", (4, 8)), "b.bias": ("F32", (8,))}


def test_reads_safetensors_shapes_dtypes_and_metadata(tmp_path):
    path = write_safetensors(tmp_path / "m.safetensors", TENSORS, {"format": "pt"})
    result = read_header(path)
    assert result.status is HeaderStatus.OK
    view = result.view
    assert view.format == "safetensors"
    assert view.shape("a.weight") == (4, 8)
    assert view.dtype("b.bias") == "F32"
    assert view.metadata == {"format": "pt"}
    assert view.filename == "m.safetensors"
    assert view.file_size == path.stat().st_size


def test_sft_extension_is_read_as_safetensors(tmp_path):
    path = write_safetensors(tmp_path / "m.sft", TENSORS)
    assert read_header(path).status is HeaderStatus.OK


def test_zero_length_header_is_invalid(tmp_path):
    path = raw_safetensors(tmp_path / "m.safetensors", b"", declared_length=0)
    assert read_header(path).status is HeaderStatus.INVALID


def test_header_longer_than_the_cap_is_invalid_even_when_the_file_holds_it(tmp_path, monkeypatch):
    path = write_safetensors(tmp_path / "m.safetensors", TENSORS)
    monkeypatch.setattr(reader, "MAX_SAFETENSORS_HEADER", 16)
    assert read_header(path).status is HeaderStatus.INVALID


def test_huge_declared_length_is_invalid(tmp_path):
    path = raw_safetensors(tmp_path / "m.safetensors", b"{}", declared_length=2**63)
    assert read_header(path).status is HeaderStatus.INVALID


def test_length_beyond_file_size_is_invalid(tmp_path):
    path = raw_safetensors(tmp_path / "m.safetensors", b"{}", declared_length=500)
    assert read_header(path).status is HeaderStatus.INVALID


@pytest.mark.parametrize(
    "raw",
    [b'{"a": {"dtype": "F16", "shape": [1', b"[1, 2]", b"not json", b'{"a": {"shape": [1]}}', b'{"a": {"dtype": "F16", "shape": [-1]}}'],
)
def test_malformed_json_headers_are_invalid(tmp_path, raw):
    assert read_header(raw_safetensors(tmp_path / "m.safetensors", raw)).status is HeaderStatus.INVALID


def test_deeply_nested_json_is_invalid(tmp_path):
    raw = b"[" * 100_000 + b"]" * 100_000
    assert read_header(raw_safetensors(tmp_path / "m.safetensors", raw)).status is HeaderStatus.INVALID


def test_too_many_entries_is_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_SAFETENSORS_ENTRIES", 2)
    path = write_safetensors(tmp_path / "m.safetensors", {f"t{i}": ("F16", (1,)) for i in range(3)})
    assert read_header(path).status is HeaderStatus.INVALID


def test_declared_length_beyond_file_is_rejected_from_the_prefix_without_touching_the_file(tmp_path, monkeypatch):
    path = raw_safetensors(tmp_path / "m.safetensors", b"{}", declared_length=500)

    def forbid(*args, **kwargs):
        raise AssertionError("file was opened")

    monkeypatch.setattr(builtins, "open", forbid)
    assert read_header(path, prefix=struct.pack("<Q", 500) + b"{}").status is HeaderStatus.INVALID


def test_gguf_string_array_element_over_the_limit_is_invalid(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_GGUF_STRING_BYTES", 4)
    path = write_gguf(tmp_path / "m.gguf", {"w": ("F16", (2,))}, {"tokenizer.tokens": ["ok", "far too long"]})
    assert read_header(path).status is HeaderStatus.INVALID


def test_file_shorter_than_eight_bytes_is_invalid(tmp_path):
    path = tmp_path / "m.safetensors"
    path.write_bytes(b"abc")
    assert read_header(path).status is HeaderStatus.INVALID


def test_permission_error_is_io_error(tmp_path, monkeypatch):
    path = write_safetensors(tmp_path / "m.safetensors", TENSORS)

    def deny(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(builtins, "open", deny)
    result = read_header(path)
    assert result.status is HeaderStatus.IO_ERROR
    assert "denied" in result.error


def test_missing_file_is_io_error(tmp_path):
    assert read_header(tmp_path / "gone.safetensors").status is HeaderStatus.IO_ERROR


def test_prefix_is_parsed_without_opening_the_file(tmp_path, monkeypatch):
    path = write_safetensors(tmp_path / "m.safetensors", TENSORS)
    prefix = path.read_bytes()

    def forbid(*args, **kwargs):
        raise AssertionError("file was opened")

    monkeypatch.setattr(builtins, "open", forbid)
    result = read_header(path, prefix=prefix)
    assert result.status is HeaderStatus.OK
    assert result.view.shape("a.weight") == (4, 8)


def test_short_prefix_falls_back_to_the_file(tmp_path):
    path = write_safetensors(tmp_path / "m.safetensors", TENSORS)
    result = read_header(path, prefix=path.read_bytes()[:12])
    assert result.status is HeaderStatus.OK
    assert result.view.shape("b.bias") == (8,)


def test_invalid_prefix_is_final(tmp_path):
    path = write_safetensors(tmp_path / "m.safetensors", TENSORS)
    bad = struct.pack("<Q", 0) + b"xxxx"
    assert read_header(path, prefix=bad).status is HeaderStatus.INVALID


def test_reads_gguf_with_reversed_dims_and_scalar_metadata(tmp_path):
    kv = {
        "general.architecture": "flux",
        "general.file_type": 7,
        "flag": True,
        "tokenizer.tokens": ["a", "bb", "ccc"],
        "tokenizer.scores": [0.5, 0.25],
    }
    path = write_gguf(tmp_path / "m.gguf", {"w": ("Q8_0", (3, 5, 7))}, kv)
    result = read_header(path)
    assert result.status is HeaderStatus.OK
    view = result.view
    assert view.format == "gguf"
    assert view.shape("w") == (3, 5, 7)
    assert view.dtype("w") == "Q8_0"
    assert view.metadata["general.architecture"] == "flux"
    assert view.metadata["flag"] == "true"
    assert "tokenizer.tokens" not in view.metadata
    assert "tokenizer.scores" not in view.metadata


def test_gguf_dims_are_stored_reversed_on_disk(tmp_path):
    body = gguf_bytes({"w": ("F32", (2, 9))})
    assert struct.pack("<QQ", 9, 2) in body


def test_gguf_v2_is_accepted_and_v1_rejected(tmp_path):
    v2 = write_gguf(tmp_path / "v2.gguf", {"w": ("F16", (2,))}, version=2)
    v1 = write_gguf(tmp_path / "v1.gguf", {"w": ("F16", (2,))}, version=1)
    assert read_header(v2).status is HeaderStatus.OK
    assert read_header(v1).status is HeaderStatus.INVALID


def test_gguf_bad_magic_is_not_gguf(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(b"GGUX" + b"\x00" * 64)
    assert read_header(path).status is HeaderStatus.INVALID


def test_gguf_truncated_is_invalid(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(gguf_bytes({"w": ("F16", (2,))}, {"k": "v"})[:-6])
    assert read_header(path).status is HeaderStatus.INVALID


def test_gguf_prefix_tap_and_short_prefix_fallback(tmp_path):
    path = write_gguf(tmp_path / "m.gguf", {"w": ("F16", (2, 3))}, {"general.architecture": "sdxl"})
    data = path.read_bytes()
    assert read_header(path, prefix=data).view.shape("w") == (2, 3)
    assert read_header(path, prefix=data[:20]).view.shape("w") == (2, 3)


@pytest.mark.parametrize(
    ("cap", "value"),
    [
        ("MAX_GGUF_TENSORS", 2),
        ("MAX_GGUF_KV", 1),
        ("MAX_GGUF_KEY_BYTES", 4),
        ("MAX_GGUF_STRING_BYTES", 4),
        ("MAX_GGUF_CONSUMED", 40),
        ("MAX_GGUF_DIMS", 1),
    ],
)
def test_each_gguf_cap_trips(tmp_path, monkeypatch, cap, value):
    monkeypatch.setattr(reader, cap, value)
    path = write_gguf(
        tmp_path / "m.gguf",
        {"w1": ("F16", (2, 3)), "w2": ("F16", (2,)), "w3": ("F16", (1,))},
        {"general.architecture": "flux-long-string", "second": "x"},
    )
    assert read_header(path).status is HeaderStatus.INVALID


def test_gguf_string_array_is_skipped_without_reading_elements(tmp_path):
    vocab = [f"tok{i}" for i in range(500)]
    path = write_gguf(tmp_path / "m.gguf", {"w": ("F16", (2,))}, {"tokenizer.tokens": vocab, "general.architecture": "flux"})
    result = read_header(path)
    assert result.status is HeaderStatus.OK
    assert result.view.metadata == {"general.architecture": "flux"}


def test_read_safetensors_header_returns_raw_dict(tmp_path):
    path = write_safetensors(tmp_path / "m.safetensors", TENSORS, {"format": "pt"})
    header = read_safetensors_header(path)
    assert header["a.weight"]["shape"] == [4, 8]
    assert header["__metadata__"] == {"format": "pt"}
    assert json.dumps(header)


def test_read_safetensors_header_is_bounded(tmp_path):
    path = raw_safetensors(tmp_path / "m.safetensors", b"{}", declared_length=2**63)
    with pytest.raises(ValueError):
        read_safetensors_header(path)
