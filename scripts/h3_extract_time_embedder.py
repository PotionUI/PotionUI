#!/usr/bin/env python
"""Write the four ``time_embedder`` tensors of a FULL MiniMax-H3 checkpoint to
a small safetensors file.

The pruned H3 repack drops ``time_embedder`` entirely and ships an
``adaln_t_table`` lookup instead, so an adapter whose AdaLN rows were trained
against the full checkpoint cannot be applied to it without knowing what curve
that MLP produces. This sidecar is that knowledge --
``model_loader/minimax_h3``'s ``dense_time_embedder`` setting -- and is ~63 MB
at fp32 rather than the ~24 GB of the checkpoint it comes from.

Local:

    python scripts/h3_extract_time_embedder.py CHECKPOINT.safetensors -o te.safetensors

Remote, WITHOUT downloading the checkpoint -- the safetensors header is read
first, then exactly the four tensors' byte ranges are pulled with HTTP range
requests:

    python scripts/h3_extract_time_embedder.py --hf REPO FILE -o te.safetensors
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

TENSORS = (
    "time_embedder.proj_in.weight",
    "time_embedder.proj_in.bias",
    "time_embedder.proj_out.weight",
    "time_embedder.proj_out.bias",
)

# safetensors dtype tag -> (torch dtype name, bytes per element)
_DTYPES = {
    "F64": ("float64", 8), "F32": ("float32", 4), "F16": ("float16", 2), "BF16": ("bfloat16", 2),
    "I64": ("int64", 8), "I32": ("int32", 4), "I16": ("int16", 2), "I8": ("int8", 1), "U8": ("uint8", 1),
}

_HEADER_LEN_BYTES = 8


def _torch():
    import torch  # imported lazily so --help works without a torch install

    return torch


def _prefixed(header: dict, name: str) -> str | None:
    """The header key for ``name`` under whatever top-level prefix this dump uses."""
    for key in header:
        if key == name or key.endswith("." + name):
            return key
    return None


def extract_local(path: str) -> dict:
    torch = _torch()
    from safetensors.torch import load_file

    state = load_file(path)
    out = {}
    for name in TENSORS:
        key = _prefixed(state, name)
        if key is None:
            raise SystemExit(f"{Path(path).name} carries no '{name}' -- is this the FULL checkpoint?")
        out[name] = state[key].to(torch.float32).contiguous()
    return out


def _read_header(read_range) -> dict:
    length = struct.unpack("<Q", read_range(0, _HEADER_LEN_BYTES))[0]
    return json.loads(read_range(_HEADER_LEN_BYTES, _HEADER_LEN_BYTES + length))


def extract_ranged(read_range) -> dict:
    """Pull just the four tensors through a ``read_range(start, end)`` callable
    over a safetensors file, reading the header and then one range per tensor."""
    torch = _torch()
    import numpy as np

    header = _read_header(read_range)
    data_start = _HEADER_LEN_BYTES + struct.unpack("<Q", read_range(0, _HEADER_LEN_BYTES))[0]
    out = {}
    for name in TENSORS:
        key = _prefixed(header, name)
        if key is None:
            raise SystemExit(f"the remote file carries no '{name}' -- is this the FULL checkpoint?")
        entry = header[key]
        dtype_name, item_size = _DTYPES[entry["dtype"]]
        begin, end = entry["data_offsets"]
        raw = read_range(data_start + begin, data_start + end)
        shape = tuple(entry["shape"])
        expected = item_size
        for dim in shape:
            expected *= dim
        if len(raw) != expected:
            raise SystemExit(f"short read for {name}: got {len(raw)} bytes, expected {expected}")
        if dtype_name == "bfloat16":
            # numpy has no bfloat16: widen the 2-byte codes into fp32 by placing
            # them in the high half of the mantissa, which is exactly what a
            # bf16 -> fp32 cast is.
            half = np.frombuffer(raw, dtype="<u2").astype(np.uint32) << 16
            tensor = torch.from_numpy(half.view(np.float32).copy()).reshape(shape)
        else:
            tensor = torch.frombuffer(bytearray(raw), dtype=getattr(torch, dtype_name)).reshape(shape)
        out[name] = tensor.to(torch.float32).contiguous()
    return out


def _hf_range_reader(repo: str, filename: str, revision: str):
    import requests

    url = f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"
    session = requests.Session()

    def read_range(start: int, end: int) -> bytes:
        response = session.get(url, headers={"Range": f"bytes={start}-{end - 1}"}, timeout=120)
        response.raise_for_status()
        return response.content

    return read_range


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checkpoint", nargs="?", help="local FULL H3 .safetensors")
    parser.add_argument("--hf", nargs=2, metavar=("REPO", "FILE"),
                        help="read the four tensors out of a remote safetensors by byte range")
    parser.add_argument("--revision", default="main", help="revision for --hf (default: main)")
    parser.add_argument("-o", "--out", required=True, help="sidecar .safetensors to write")
    args = parser.parse_args(argv)

    if bool(args.checkpoint) == bool(args.hf):
        parser.error("give either a local checkpoint path or --hf REPO FILE, not both")

    if args.hf:
        repo, filename = args.hf
        tensors = extract_ranged(_hf_range_reader(repo, filename, args.revision))
        source = f"hf://{repo}/{filename}@{args.revision}"
    else:
        tensors = extract_local(args.checkpoint)
        source = str(Path(args.checkpoint).resolve())

    from safetensors.torch import save_file

    save_file(tensors, args.out, metadata={"source": source, "note": "MiniMax-H3 dense time_embedder"})
    total = sum(t.numel() for t in tensors.values())
    print(f"wrote {args.out}: {len(tensors)} tensors, {total} params, source {source}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
