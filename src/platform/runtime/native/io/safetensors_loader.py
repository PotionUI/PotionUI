"""Single entry point for loading model weights from disk.

v1 supports safetensors only (``.safetensors`` / ``.sft``). GGUF and pickle
(``.pt``/``.ckpt``) are explicitly rejected — the native engine never runs
untrusted pickle and GGUF dequant is a later slice.
"""

from __future__ import annotations

import json
import logging
import struct
from pathlib import Path

import torch
from safetensors import safe_open
from safetensors.torch import load_file

from ..errors import NativeEngineUnsupportedError

logger = logging.getLogger(__name__)

_SAFETENSORS_SUFFIXES = {".safetensors", ".sft"}


def _read_metadata(path: Path) -> dict[str, str]:
    """Read the ``__metadata__`` header block without loading any tensors."""
    with open(path, "rb") as f:
        header_len = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(header_len))
    meta = header.get("__metadata__", {})
    return {str(k): str(v) for k, v in meta.items()}


def load_torch_file(
    path: str | Path,
    *,
    device: str | torch.device = "cpu",
) -> tuple[dict[str, torch.Tensor], dict[str, str]]:
    """Load a safetensors checkpoint into a state dict.

    Returns ``(state_dict, metadata)`` where ``metadata`` is the raw
    ``__metadata__`` header (string→string), e.g. ``format`` or a
    ``_quantization_metadata`` JSON blob for fp8 checkpoints.

    Raises ``NativeEngineUnsupportedError`` for non-safetensors files.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in _SAFETENSORS_SUFFIXES:
        raise NativeEngineUnsupportedError(
            f"native engine loads safetensors only; got '{suffix}' ({path.name}). "
            "GGUF / pickle checkpoints are not supported."
        )
    if not path.is_file():
        raise NativeEngineUnsupportedError(f"checkpoint not found: {path}")

    device_str = str(device)
    logger.debug("loading safetensors %s onto %s", path.name, device_str)
    state_dict = load_file(str(path), device=device_str)
    metadata = _read_metadata(path)
    return state_dict, metadata


def load_torch_file_prefixed(
    path: str | Path,
    prefix: str,
    *,
    device: str | torch.device = "cpu",
) -> tuple[dict[str, torch.Tensor], dict[str, str]]:
    """Load only the ``prefix``-matching tensors out of a safetensors checkpoint.

    For an all-in-one checkpoint (LTX ships DiT + VAE(s) + vocoder in one file)
    this reads ONLY the wanted component's tensors instead of materializing the
    whole state dict (tens of GB) and discarding most of it. Uses the
    ``safe_open`` + ``get_tensor(key)`` idiom (see
    ``src/pipelines/pipes/model_loader/ltx/projection.py``), which never touches
    non-matching tensor storage.

    Returned keys keep ``prefix`` intact (callers strip it themselves if their
    contract expects bare keys — see ``NativeEngineLoader._load_vae``).

    Falls back to a full read when no key matches ``prefix`` (a standalone
    single-component file, e.g. a bare-keyed VAE/audio-VAE/vocoder file) so
    this is a safe drop-in for callers that used to call :func:`load_torch_file`
    unconditionally.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in _SAFETENSORS_SUFFIXES:
        raise NativeEngineUnsupportedError(
            f"native engine loads safetensors only; got '{suffix}' ({path.name}). "
            "GGUF / pickle checkpoints are not supported."
        )
    if not path.is_file():
        raise NativeEngineUnsupportedError(f"checkpoint not found: {path}")

    device_str = str(device)
    logger.debug("loading safetensors %s (prefix=%r) onto %s", path.name, prefix, device_str)
    with safe_open(str(path), framework="pt", device=device_str) as f:
        keys = list(f.keys())
        matched = [k for k in keys if k.startswith(prefix)]
        wanted = matched if matched else keys
        state_dict = {k: f.get_tensor(k) for k in wanted}
    metadata = _read_metadata(path)
    return state_dict, metadata
