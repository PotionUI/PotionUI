from __future__ import annotations

from pathlib import Path
from typing import Dict, Mapping

import torch

from .config import IGNORED_PREFIXES
from .detect import detect_moge2_config, is_moge2_checkpoint
from .model import MoGe2Model

__all__ = ["load_moge2", "load_moge2_state_dict", "map_moge2_state_dict"]


def _not_moge2(name: str) -> ValueError:
    return ValueError(
        f"{name} is not a MoGe-2 checkpoint (no encoder.backbone.cls_token / points_head.* keys). "
        "Pixal3D's camera estimate needs moge_2_vitl_normal_fp16.safetensors from Comfy-Org/MoGe."
    )


def map_moge2_state_dict(state_dict: Mapping[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
    return {key: value for key, value in state_dict.items() if not key.startswith(IGNORED_PREFIXES)}


def load_moge2_state_dict(
    state_dict: Mapping[str, torch.Tensor], *, dtype: torch.dtype = torch.float32, name: str = "checkpoint"
) -> MoGe2Model:
    if not is_moge2_checkpoint(state_dict):
        raise _not_moge2(name)
    mapped = map_moge2_state_dict(state_dict)
    config = detect_moge2_config({key: tuple(value.shape) for key, value in mapped.items()})
    with torch.device("meta"):
        model = MoGe2Model(config)
    expected = set(model.state_dict())
    missing = sorted(expected - set(mapped))
    unexpected = sorted(set(mapped) - expected)
    if missing or unexpected:
        raise ValueError(
            f"{name}: MoGe-2 weights do not match the port ({len(missing)} missing, first few {missing[:5]}; "
            f"{len(unexpected)} unexpected, first few {unexpected[:5]})"
        )
    model.load_state_dict({key: value.to(dtype) for key, value in mapped.items()}, strict=True, assign=True)
    return model.requires_grad_(False).eval()


def load_moge2(path: str | Path, *, dtype: torch.dtype = torch.float32) -> MoGe2Model:
    from safetensors import safe_open

    path = Path(path)
    if path.suffix.lower() not in (".safetensors", ".sft"):
        raise ValueError(f"{path.name}: the MoGe-2 camera estimator loads from safetensors only")
    with safe_open(str(path), framework="pt", device="cpu") as handle:
        keys = list(handle.keys())
        if not is_moge2_checkpoint(keys):
            raise _not_moge2(path.name)
        state_dict = {key: handle.get_tensor(key) for key in keys if not key.startswith(IGNORED_PREFIXES)}
    return load_moge2_state_dict(state_dict, dtype=dtype, name=path.name)
