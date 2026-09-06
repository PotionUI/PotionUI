"""``scripts/h3_extract_time_embedder.py``: the four dense ``time_embedder``
tensors pulled out of a FULL MiniMax-H3 checkpoint into the sidecar the loader's
``dense_time_embedder`` setting takes.

The ``--hf`` transport is not exercised (no network here); the byte-range reader
it drives is, over a local file.
"""

from __future__ import annotations

import importlib.util
import json
import struct
from pathlib import Path

import pytest
import torch
from safetensors.torch import load_file, save_file

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "h3_extract_time_embedder.py"


def _module():
    spec = importlib.util.spec_from_file_location("h3_extract_time_embedder", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkpoint(path: Path) -> dict:
    """A full checkpoint's shape in miniature, with one bf16 tensor so the
    dtype widening is on the real path."""
    state = {
        "time_embedder.proj_in.weight": torch.randn(16, 8),
        "time_embedder.proj_in.bias": torch.randn(16),
        "time_embedder.proj_out.weight": torch.randn(12, 16).to(torch.bfloat16),
        "time_embedder.proj_out.bias": torch.randn(12),
        "blocks.0.attn.qkv_proj.weight": torch.randn(30, 10),
    }
    save_file(state, str(path))
    return state


def test_the_local_path_writes_exactly_the_four_tensors_at_fp32(tmp_path):
    module = _module()
    state = _checkpoint(tmp_path / "full.safetensors")
    out_path = tmp_path / "te.safetensors"

    assert module.main([str(tmp_path / "full.safetensors"), "-o", str(out_path)]) == 0

    written = load_file(str(out_path))
    assert sorted(written) == sorted(module.TENSORS)
    assert all(tensor.dtype == torch.float32 for tensor in written.values())
    torch.testing.assert_close(written["time_embedder.proj_in.weight"], state["time_embedder.proj_in.weight"])
    torch.testing.assert_close(
        written["time_embedder.proj_out.weight"], state["time_embedder.proj_out.weight"].to(torch.float32),
    )


def test_the_sidecar_records_where_it_came_from(tmp_path):
    module = _module()
    _checkpoint(tmp_path / "full.safetensors")
    out_path = tmp_path / "te.safetensors"
    module.main([str(tmp_path / "full.safetensors"), "-o", str(out_path)])

    with open(out_path, "rb") as handle:
        length = struct.unpack("<Q", handle.read(8))[0]
        header = json.loads(handle.read(length))
    assert header["__metadata__"]["source"].endswith("full.safetensors")


def test_the_range_reader_returns_the_same_tensors_as_a_whole_file_read(tmp_path):
    """What ``--hf`` runs on: header first, then one byte range per tensor, so
    a 24 GB checkpoint never has to be downloaded to extract 63 MB from it."""
    module = _module()
    _checkpoint(tmp_path / "full.safetensors")
    out_path = tmp_path / "te.safetensors"
    module.main([str(tmp_path / "full.safetensors"), "-o", str(out_path)])
    whole = load_file(str(out_path))

    data = (tmp_path / "full.safetensors").read_bytes()
    ranged = module.extract_ranged(lambda start, end: data[start:end])

    for name in module.TENSORS:
        torch.testing.assert_close(ranged[name], whole[name])


def test_a_pruned_checkpoint_is_named_as_the_wrong_input(tmp_path):
    module = _module()
    save_file({"adaln_t_table": torch.randn(9, 6)}, str(tmp_path / "pruned.safetensors"))
    with pytest.raises(SystemExit, match="time_embedder.proj_in.weight"):
        module.main([str(tmp_path / "pruned.safetensors"), "-o", str(tmp_path / "te.safetensors")])
