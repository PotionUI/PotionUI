"""Per-component size accounting for the TRELLIS.2 depot files.

``file_size_gb`` is the right pre-load estimate everywhere else, because every
other family's cache entry IS a whole file. TRELLIS.2 breaks that assumption:
one 8GB diffusion file holds four flow DiTs under four key prefixes, and the
shape VAE holds two decoders, so handing ``acquire`` the file size for each of
them would tell admission control the run needs roughly four times the VRAM it
does — enough to evict models that would have fit.

The safetensors header carries every tensor's dtype, shape and byte range, so
the exact size of one prefix's slice is a header read: 8 bytes of length, then
the JSON. Nothing is deserialised and no tensor data is touched.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Optional

_BYTES_PER_GB = 1024**3
_HEADER_LENGTH_BYTES = 8


def prefix_size_gb(path: Any, prefix: str) -> Optional[float]:
    """Size in GB of the tensors under ``prefix`` in ``path``, or None.

    None (rather than an exception) for anything unreadable — this is an
    eviction hint, and a run must not fail because a size could not be
    estimated. ``acquire`` treats None as "unknown" already.
    """
    if not path:
        return None
    try:
        with Path(path).open("rb") as handle:
            (length,) = struct.unpack("<Q", handle.read(_HEADER_LENGTH_BYTES))
            header = json.loads(handle.read(length))
    except Exception:
        return None

    total = 0
    for key, entry in header.items():
        if key == "__metadata__" or not key.startswith(prefix):
            continue
        try:
            start, end = entry["data_offsets"]
        except (KeyError, TypeError, ValueError):
            continue
        total += end - start
    return total / _BYTES_PER_GB if total else None
