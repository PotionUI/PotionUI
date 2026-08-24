"""I/O helpers: safetensors loading and state-dict introspection."""

from __future__ import annotations

from .safetensors_loader import load_torch_file
from .state_dict_utils import (
    count_blocks,
    detect_prefix,
    key_shapes,
    strip_prefix,
    weight_dtype,
)

__all__ = [
    "count_blocks",
    "detect_prefix",
    "key_shapes",
    "load_torch_file",
    "strip_prefix",
    "weight_dtype",
]
