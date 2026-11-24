import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

import torch

from src.platform.runtime.guarded_import import guarded_from

UNION_MARKER_KEYS = (
    "task_embedding",
    "control_add_embedding.linear_1.weight",
    "spatial_ch_projs.weight",
)
UNION_LAYER_PREFIX = "transformer_layes."
COND_IN_KEY = "controlnet_cond_embedding.conv_in.weight"
COND_BLOCK_KEY = "controlnet_cond_embedding.blocks.{}.weight"
COND_BLOCK_STEPS = (1, 3, 5)

UNION_CONTROL_TYPES: Dict[str, int] = {
    "openpose": 0,
    "pose": 0,
    "depth": 1,
    "hed": 2,
    "softedge": 2,
    "pidi": 2,
    "scribble": 2,
    "ted": 2,
    "canny": 3,
    "lineart": 3,
    "anime_lineart": 3,
    "mlsd": 3,
    "normal": 4,
    "segment": 5,
    "tile": 6,
    "repaint": 7,
}

BASE_CONFIG = Path(__file__).resolve().parent / "assets" / "large" / "config.json"


def is_union_controlnet(keys: Iterable[str]) -> bool:
    names = set(keys)
    if not all(marker in names for marker in UNION_MARKER_KEYS):
        return False
    return any(name.startswith(UNION_LAYER_PREFIX) for name in names)


def union_control_index(control_type: str, num_control_type: int) -> int:
    key = str(control_type or "").strip().lower()
    if key not in UNION_CONTROL_TYPES:
        raise ValueError(
            f"The union ControlNet has no control type for '{control_type}'. "
            f"Choose one of: {', '.join(sorted(UNION_CONTROL_TYPES))}"
        )
    index = UNION_CONTROL_TYPES[key]
    if index >= num_control_type:
        raise ValueError(
            f"'{control_type}' needs the ProMax union ControlNet; this file supports {num_control_type} control types"
        )
    return index


def union_config(shapes: Mapping[str, Sequence[int]], base: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    config = dict(base) if base is not None else json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    task_shape = shapes["task_embedding"]
    layers = {
        name.split(".")[1]
        for name in shapes
        if name.startswith(UNION_LAYER_PREFIX)
    }
    config["_class_name"] = "ControlNetUnionModel"
    config["num_control_type"] = int(task_shape[0])
    config["num_trans_channel"] = int(task_shape[1])
    config["num_proj_channel"] = int(shapes["spatial_ch_projs.weight"][0])
    config["num_trans_layer"] = len(layers)
    if COND_IN_KEY in shapes and all(COND_BLOCK_KEY.format(step) in shapes for step in COND_BLOCK_STEPS):
        config["conditioning_embedding_out_channels"] = [
            int(shapes[COND_IN_KEY][0]),
            *(int(shapes[COND_BLOCK_KEY.format(step)][0]) for step in COND_BLOCK_STEPS),
        ]
    return config


def load_union_controlnet(file_path: str, dtype: torch.dtype):
    ControlNetUnionModel = guarded_from("diffusers", "ControlNetUnionModel")
    from safetensors import safe_open
    from safetensors.torch import load_file

    with safe_open(file_path, framework="pt") as handle:
        shapes = {key: handle.get_slice(key).get_shape() for key in handle.keys()}
    config = union_config(shapes)
    state = load_file(file_path)
    with torch.device("meta"):
        model = ControlNetUnionModel.from_config(config)
    state = {key: value.to(dtype) for key, value in state.items()}
    result = model.load_state_dict(state, strict=False, assign=True)
    if result.missing_keys:
        raise ValueError(f"The union ControlNet is missing weights such as {result.missing_keys[0]}")
    return model.to(dtype).eval()
