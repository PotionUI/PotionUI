# Checkpoint loading + variant inference for the RIFE 4.x IFNet (see ifnet.py
# for the provenance/licence note). MIT, Copyright (c) 2021 hzwer.

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import torch

from .ifnet import IFNet

SUPPORTED_FAMILY = "rife46 / rife47 / rife48 / rife49 (Practical-RIFE IFNet 4.x)"

_STRIP_PREFIXES = ("module.", "flownet.")

_ENCODE_KEYS = {"encode.0.weight", "encode.0.bias", "encode.1.weight", "encode.1.bias"}


def _read_state_dict(source: Union[str, Path, Dict[str, Any]]) -> Dict[str, torch.Tensor]:
    if isinstance(source, dict):
        raw = source
    else:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"RIFE checkpoint not found: {path}")
        if path.suffix.lower() == ".safetensors":
            from safetensors.torch import load_file
            raw = load_file(str(path))
        else:
            raw = torch.load(str(path), map_location="cpu", weights_only=True)

    if isinstance(raw, dict) and "state_dict" in raw and isinstance(raw["state_dict"], dict):
        raw = raw["state_dict"]

    cleaned: Dict[str, torch.Tensor] = OrderedDict()
    for key, value in raw.items():
        new_key = key
        changed = True
        while changed:
            changed = False
            for prefix in _STRIP_PREFIXES:
                if new_key.startswith(prefix):
                    new_key = new_key[len(prefix):]
                    changed = True
        cleaned[new_key] = value
    return cleaned


def _infer_block_specs(state: Dict[str, torch.Tensor]) -> List[Tuple[int, int]]:
    indices: List[int] = []
    i = 0
    while True:
        w = state.get(f"block{i}.conv0.0.0.weight")
        if w is None:
            break
        indices.append(i)
        i += 1
    if not indices:
        raise ValueError(
            "state dict has no `block0.conv0.*` weights — this is not a "
            f"{SUPPORTED_FAMILY} checkpoint."
        )
    specs: List[Tuple[int, int]] = []
    for idx in indices:
        w = state[f"block{idx}.conv0.0.0.weight"]  # [c//2, in_planes, 3, 3]
        in_planes = int(w.shape[1])
        c = int(w.shape[0]) * 2
        specs.append((in_planes, c))
    return specs


def _infer_encode_spec(state: Dict[str, torch.Tensor]) -> Optional[Tuple[int, int]]:
    present = {k for k in state if k.startswith("encode.")}
    # rife46 ships no encoder at all; rife47-49 ship exactly these four keys.
    if not present:
        return None
    if present != _ENCODE_KEYS:
        raise ValueError(
            "checkpoint carries an unrecognised `encode.*` feature-encoder layout "
            f"({sorted(present)[:4]}...); expected the two-layer "
            f"`encode.0`/`encode.1` encoder of {SUPPORTED_FAMILY}. The rife4.10+ "
            "line ships a different (multi-scale `Head`) encoder and is not "
            "supported."
        )
    mid_ch = int(state["encode.0.weight"].shape[0])   # Conv2d(3, mid_ch, 3, 2, 1)
    out_ch = int(state["encode.1.weight"].shape[1])   # ConvTranspose2d(mid_ch, out_ch, 4, 2, 1)
    return (mid_ch, out_ch)


def load_ifnet(
    source: Union[str, Path, Dict[str, Any]],
    device: str = "cpu",
    dtype: Optional[torch.dtype] = None,
) -> IFNet:
    """Build an :class:`IFNet` matching ``source`` (a checkpoint path or state
    dict) and load its weights. Handles ``module.``/``flownet.`` prefixes,
    ``.pth`` (``weights_only=True``) and ``.safetensors``, and derives the
    per-block channel widths + optional feature encoder from the state dict so
    the rife46/47/48/49 variants all load under their own published key names.
    Raises ``ValueError`` naming the supported family for a checkpoint whose keys
    don't match."""
    state = _read_state_dict(source)
    block_specs = _infer_block_specs(state)
    encode_spec = _infer_encode_spec(state)
    model = IFNet(block_specs, encode_spec)

    try:
        model.load_state_dict(state, strict=True)
    except RuntimeError as exc:
        raise ValueError(
            f"state dict is not a loadable {SUPPORTED_FAMILY} checkpoint: {exc}"
        ) from exc

    model.eval()
    model.to(device)
    if dtype is not None:
        model.to(dtype)
    return model
