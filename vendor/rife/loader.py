# Checkpoint loading + variant inference for the RIFE 4.x IFNet (see ifnet.py
# for the provenance/licence note). MIT, Copyright (c) 2021 hzwer.

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import torch

from .ifnet import IFNet
from .ifnet_head import BLOCK_CHANNELS, HeadIFNet

SUPPORTED_FAMILY = "rife46 / rife47 / rife48 / rife49 and rife4.21 to rife4.26 (Practical-RIFE IFNet 4.x)"

_STRIP_PREFIXES = ("module.", "flownet.")

_TRAINING_ONLY_PREFIXES = ("teacher.", "caltime.")

_ENCODE_KEYS = {"encode.0.weight", "encode.0.bias", "encode.1.weight", "encode.1.bias"}

_HEAD_KEYS = {f"encode.cnn{i}.{part}" for i in range(4) for part in ("weight", "bias")}

_HEAD_SCALE_BASES = {4: (8.0, 4.0, 2.0, 1.0), 5: (16.0, 8.0, 4.0, 2.0, 1.0)}

_HEAD_LITE_LAST_WIDTH = 24

_HEAD_PAD_MODULO = 64


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
        if new_key.startswith(_TRAINING_ONLY_PREFIXES):
            continue
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
            f"`encode.0`/`encode.1` encoder of rife47-49, or the `encode.cnn*` "
            f"`Head` encoder of the 4.21+ line ({SUPPORTED_FAMILY})."
        )
    mid_ch = int(state["encode.0.weight"].shape[0])   # Conv2d(3, mid_ch, 3, 2, 1)
    out_ch = int(state["encode.1.weight"].shape[1])   # ConvTranspose2d(mid_ch, out_ch, 4, 2, 1)
    return (mid_ch, out_ch)


def _is_head_family(state: Dict[str, torch.Tensor]) -> bool:
    return any(key.startswith("encode.cnn") for key in state)


def _head_encode_spec(state: Dict[str, torch.Tensor]) -> Tuple[int, int]:
    present = {k for k in state if k.startswith("encode.")}
    if present != _HEAD_KEYS:
        raise ValueError(
            "checkpoint carries an unrecognised `Head` encoder layout "
            f"({sorted(present - _HEAD_KEYS)[:4] or sorted(_HEAD_KEYS - present)[:4]}...); "
            f"expected encode.cnn0 to encode.cnn3 of {SUPPORTED_FAMILY}."
        )
    return int(state["encode.cnn0.weight"].shape[0]), int(state["encode.cnn3.weight"].shape[1])


def _head_scale_base(block_specs: List[Tuple[int, int]], state: Dict[str, torch.Tensor]) -> Tuple[float, ...]:
    for idx in range(len(block_specs)):
        out = int(state[f"block{idx}.lastconv.0.weight"].shape[1])
        if out != 4 * BLOCK_CHANNELS:
            raise ValueError(
                f"block{idx} emits {out // 4} channels where the 4.21+ line emits {BLOCK_CHANNELS}: "
                "the rife4.10 to rife4.20 and rife4.24 checkpoints pass no features between blocks "
                f"and are not supported ({SUPPORTED_FAMILY})."
            )
    base = _HEAD_SCALE_BASES.get(len(block_specs))
    if base is None:
        raise ValueError(
            f"a `Head` checkpoint with {len(block_specs)} blocks is not one of {SUPPORTED_FAMILY}."
        )
    if len(block_specs) == 5 and block_specs[-1][1] == _HEAD_LITE_LAST_WIDTH:
        raise ValueError(
            "this is the rife4.25.lite layout, whose scale ladder cannot be read from its weights; "
            f"it is not supported ({SUPPORTED_FAMILY})."
        )
    return base


def _build(state: Dict[str, torch.Tensor]) -> torch.nn.Module:
    block_specs = _infer_block_specs(state)
    if _is_head_family(state):
        encode_spec = _head_encode_spec(state)
        return HeadIFNet(block_specs, encode_spec, _head_scale_base(block_specs, state), _HEAD_PAD_MODULO)
    return IFNet(block_specs, _infer_encode_spec(state))


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
    model = _build(state)

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
